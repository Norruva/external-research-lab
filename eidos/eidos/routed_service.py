"""Explicit v4 artifact adapter for the owned signed loopback service.

Only model loading changes: the existing v2 signed request wire is reused.
The development server's socket watchdog is not a CPU interruption guarantee.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time

from .routed import RoutedEngine
from .decision_service import DecisionApplication, DecisionServer, FORMAT, HEX, KEY


def _path(value):
    path = Path(value).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_or_parent_path_refused')
    return path


def initialize_local(config_path, registry, *, key_id='routed-operator'):
    """Create one fresh private configuration without printing its secret."""
    if type(key_id) is not str or not KEY.fullmatch(key_id):
        raise ValueError('invalid_key_identifier')
    destination, registry = _path(config_path), _path(registry)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError('new_configuration_path_required')
    engine = RoutedEngine.from_registry(registry)
    pin = getattr(engine, 'registry_sha256', None)
    if type(pin) is not str or not HEX.fullmatch(pin):
        raise ValueError('registry_pin_unavailable')
    config = {'format': FORMAT, 'registry': str(registry), 'registry_sha256': pin,
              'key_id': key_id, 'secret': secrets.token_hex(32)}
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, sort_keys=True, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {'configuration_created': str(destination), 'key_values_printed': False,
            'registry_sha256': pin, 'model': engine.identity,
            'scope': 'loopback_development_review_only'}


class RoutedApplication(DecisionApplication):
    def __init__(self, config_path, *, engine_factory=None, clock=time.time):
        super().__init__(config_path,
            engine_factory=RoutedEngine.from_registry if engine_factory is None else engine_factory,
            clock=clock)


def main():
    parser = argparse.ArgumentParser(description='Original EIDOS v4 signed loopback development service')
    parser.add_argument('--config', required=True)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--init-local', action='store_true')
    parser.add_argument('--registry')
    parser.add_argument('--key-id', default='routed-operator')
    args = parser.parse_args()
    server = None
    try:
        if args.init_local:
            if not args.registry:
                parser.error('--registry is required with --init-local')
            print(json.dumps(initialize_local(args.config, args.registry, key_id=args.key_id)))
            return 0
        if args.registry:
            parser.error('--registry applies only to --init-local')
        if not 0 <= args.port <= 65535:
            parser.error('port outside supported range')
        server = DecisionServer(('127.0.0.1', args.port), RoutedApplication(args.config))
        print(json.dumps({'service': FORMAT, 'model_runtime': 'routed-v4',
                          'bind': '127.0.0.1', 'port': server.server_address[1],
                          'review_required': True, 'key_values_printed': False}), flush=True)
        server.serve_forever(poll_interval=.2)
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, RuntimeError):
        print('{"error":"routed_service_refused"}', file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
