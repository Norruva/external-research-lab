"""Command line: ``python -m aasforge <command>``.

  serve             run the HTTP server (AAS API under /api/v3.1, DPP under /dpp, resolver under /01)
  import FILE...    import AASX/JSON/XML (3.0, 3.1 or 3.2) into the store; prints the loss report
  export            write the environment as json|xml|aasx (--version 3.0|3.1|3.2) or an OPC UA nodeset
  verify FILE       structural and constraint verification with the released aas-core3.1
  token             issue a development HS256 token (needs --secret)
  sign FILE         sign an AAS JSON object as JWS (IDTA-01002 3.2 signatures)
  verify-signature  verify a JWS read from a file or stdin
  dpp-create FILE   create a passport from a JSON spec (see examples/battery_passport.py)
  access-rules      print the generated IDTA-01004 tier rules for the current store
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import security, signing, versioning
from .app import Config, Forge


def _forge(args, **overrides):
    config = Config.from_env(**{k: v for k, v in {
        'data_dir': args.data_dir, 'database': args.database, 'verification': getattr(args, 'verification', None),
        **overrides}.items() if v is not None})
    return Forge(config)


def cmd_serve(args):
    forge = _forge(args, host=args.host, port=args.port, public_url=args.public_url, access_rules=args.access_rules,
                   hs256_secret=args.secret, opcua_endpoint=args.opcua, opcua_writable=args.opcua_writable or None,
                   mqtt_url=args.mqtt)
    server = forge.serve()
    print(f'aasforge listening on http://{forge.config.host}:{forge.config.port} '
          f'(API /api/v3.1, passports /dpp, resolver /01, MCP /mcp)'
          + (f'; OPC UA {forge.config.opcua_endpoint}' if forge.config.opcua_endpoint else '')
          + (f'; MQTT events to {forge.config.mqtt_url.split("@")[-1]}' if forge.config.mqtt_url else ''), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


def cmd_import(args):
    forge = _forge(args)
    ok = True
    for name in args.files:
        report = forge.repository.import_bytes(Path(name).read_bytes(), name, actor='cli')
        ok &= report.ok
        print(json.dumps({'file': name, **{k: v for k, v in report.as_dict().items() if k != 'ids'}}, ensure_ascii=False))
    return 0 if ok else 2


def cmd_export(args):
    forge = _forge(args)
    aas_ids = args.aas or None
    if args.format == 'nodeset':
        from . import opcua
        data = opcua.export_nodeset(forge, {'sub': 'cli', 'dpp_tier': 'economic-operator'} if args.all else None)
    elif args.format == 'aasx':
        data = forge.repository.package(aas_ids, version=args.version)
    else:
        env = forge.repository.environment(aas_ids, version=args.version)
        data = (versioning.to_xml(env, '3.0' if args.version == '3.0' else '3.1') if args.format == 'xml'
                else json.dumps(env, indent=1, ensure_ascii=False)).encode()
    (Path(args.output).write_bytes(data) if args.output else sys.stdout.buffer.write(data))
    return 0


def cmd_verify(args):
    data = Path(args.file).read_bytes()
    if args.file.lower().endswith('.aasx') or data[:2] == b'PK':
        from . import aasx
        package = aasx.read(data)
        normalised, repairs = package.normalised, package.repairs
    else:
        normalised = versioning.parse_json(data) if data.lstrip()[:1] == b'{' else versioning.parse_xml(data)
        repairs = []
    errors = versioning.verify(normalised.environment)
    print(json.dumps({'sourceVersion': normalised.source_version, 'format': normalised.source_format,
                      'repairs': repairs, 'notes': normalised.notes, 'sidecarFields': len(normalised.sidecar),
                      'constraintViolations': len(errors), 'first': errors[:10]}, indent=1, ensure_ascii=False))
    return 0 if not errors else 1


def cmd_token(args):
    claims = json.loads(args.claims)
    print(security.make_hs256_token(claims, args.secret, args.lifetime))
    return 0


def cmd_sign(args):
    signer = signing.Signer.load_or_create(Path(args.data_dir) / 'keys')
    print(signer.sign(json.loads(Path(args.file).read_text())))
    return 0


def cmd_verify_signature(args):
    text = Path(args.file).read_text() if args.file != '-' else sys.stdin.read()
    trusted = [Path(p).read_text() for p in args.trust]
    try:
        result = signing.verify(text, trusted)
    except signing.SignatureError as error:
        print(json.dumps({'valid': False, 'error': str(error)}))
        return 1
    result.pop('payload')
    print(json.dumps({'valid': True, **result}, indent=1))
    return 0


def cmd_dpp_create(args):
    from .dpp import passport
    forge = _forge(args)
    spec = json.loads(Path(args.file).read_text())
    print(json.dumps(passport.create(forge.repository, spec, actor='cli'), indent=1))
    return 0


def cmd_access_rules(args):
    from .dpp import tiers
    forge = _forge(args)
    print(json.dumps(tiers.access_rules(forge.store.iter_all('submodel')), indent=1))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='aasforge', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-dir', default=None, help='data directory (default: var)')
    parser.add_argument('--database', default=None, help="SQLite path or ':memory:'")
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('serve')
    p.add_argument('--host', default=None)
    p.add_argument('--port', type=int, default=None)
    p.add_argument('--public-url', default=None)
    p.add_argument('--access-rules', default=None, help="IDTA-01004 rule file or 'dpp'")
    p.add_argument('--secret', default=None, help='HS256 secret for bearer tokens')
    p.add_argument('--verification', choices=['reject', 'warn', 'off'], default=None)
    p.add_argument('--opcua', default=None, metavar='ENDPOINT', help='also serve OPC UA, e.g. opc.tcp://0.0.0.0:4840/aasforge/')
    p.add_argument('--opcua-writable', action='store_true', help='let authorised OPC UA clients write values')
    p.add_argument('--mqtt', default=None, metavar='URL', help='publish change events, e.g. mqtt://broker:1883')
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser('import')
    p.add_argument('files', nargs='+')
    p.add_argument('--verification', choices=['reject', 'warn', 'off'], default=None)
    p.set_defaults(func=cmd_import)
    p = sub.add_parser('export')
    p.add_argument('--format', choices=['json', 'xml', 'aasx', 'nodeset'], default='json')
    p.add_argument('--all', action='store_true', help='nodeset: export as an economic operator (everything), '
                                                       'not as an anonymous reader')
    p.add_argument('--version', choices=['3.0', '3.1', '3.2'], default='3.1')
    p.add_argument('--aas', action='append', help='limit to these shell ids')
    p.add_argument('-o', '--output')
    p.set_defaults(func=cmd_export)
    p = sub.add_parser('verify')
    p.add_argument('file')
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser('token')
    p.add_argument('--secret', required=True)
    p.add_argument('--claims', default='{"sub": "dev", "dpp_tier": "economic-operator"}')
    p.add_argument('--lifetime', type=int, default=3600)
    p.set_defaults(func=cmd_token)
    p = sub.add_parser('sign')
    p.add_argument('file')
    p.set_defaults(func=cmd_sign)
    p = sub.add_parser('verify-signature')
    p.add_argument('file', nargs='?', default='-')
    p.add_argument('--trust', action='append', default=[], help='PEM trust anchor')
    p.set_defaults(func=cmd_verify_signature)
    p = sub.add_parser('dpp-create')
    p.add_argument('file')
    p.set_defaults(func=cmd_dpp_create)
    p = sub.add_parser('access-rules')
    p.set_defaults(func=cmd_access_rules)
    args = parser.parse_args(argv)
    if args.data_dir is None:
        args.data_dir = 'var'
    return args.func(args) or 0


if __name__ == '__main__':
    sys.exit(main())
