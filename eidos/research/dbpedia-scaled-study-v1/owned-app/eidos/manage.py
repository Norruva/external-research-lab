"""Explicit local provisioning and authenticated CLI. Never fetches credentials."""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import ssl
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from .service import private_file, signature


def main():
    parser=argparse.ArgumentParser(description="EIDOS local lifecycle tools")
    commands=parser.add_subparsers(dest="command",required=True)
    init=commands.add_parser("init-local",help="Create NEW private local demo configuration; never overwrite existing keys")
    init.add_argument("--config",required=True);init.add_argument("--state-dir",required=True);init.add_argument("--registry-root",required=True)
    init.add_argument("--release-path",default="models");init.add_argument("--tenant",default="demo");init.add_argument("--key-id",default="demo-operator")
    maintain=commands.add_parser("maintain",help="Verify audit and purge expired responses with service STOPPED")
    maintain.add_argument("--config",required=True)
    backup=commands.add_parser("backup",help="Create a private authenticated state snapshot with service STOPPED")
    backup.add_argument("--config",required=True);backup.add_argument("--output",required=True)
    restore=commands.add_parser("restore",help="Verify a backup and restore into a NEW state directory; no service startup")
    restore.add_argument("--config",required=True);restore.add_argument("--backup",required=True)
    restore.add_argument("--state-dir",required=True);restore.add_argument("--sha256",required=True)
    call=commands.add_parser("call",help="Sign an explicit same-host API request with the configured operator key")
    call.add_argument("--config",required=True);call.add_argument("--key-id",default="demo-operator");call.add_argument("--url",default="http://127.0.0.1:8765")
    call.add_argument("--ca-file");call.add_argument("--path",choices=("/v1/decisions","/v1/reviews","/v1/models","/v1/releases"),required=True)
    args=parser.parse_args()
    if args.command=="init-local":
        config={"format":"eidos-service-v1","registry_root":str(Path(args.registry_root).resolve()),"releases":{"local":args.release_path},
                "tenants":{args.tenant:{"initial_release":"local","allowed_releases":["local"]}},
                "keys":{args.key_id:{"tenant":args.tenant,"secret":secrets.token_hex(32),"scopes":["models","decide","review","admin"]}},
                "audit_key":secrets.token_hex(32),"state_dir":str(Path(args.state_dir).resolve()),"workers":2}
        path=Path(args.config)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as stream:json.dump(config,stream,indent=2)
        print(json.dumps({"configuration_created":str(path),"key_values_printed":False,"scope":"local demo; provision scoped keys separately for deployment"}))
        return 0
    config=private_file(args.config)
    if args.command=="restore":
        from .recovery import restore
        try:
            result=restore(args.backup,args.state_dir,bytes.fromhex(config["audit_key"]),args.sha256)
        except (ValueError,OSError,sqlite3.Error) as error:
            print(json.dumps({"error":"restore_failed","category":type(error).__name__,"state_overwritten":False}),file=sys.stderr)
            return 2
        print(json.dumps(result));return 0
    if args.command=="backup":
        from .store import Store
        from .recovery import backup
        try:
            if not (Path(config["state_dir"])/"state.sqlite3").is_file():
                raise ValueError("No existing state database to back up")
            store=Store(config["state_dir"],bytes.fromhex(config["audit_key"]))
            try:result=backup(store,args.output)
            finally:store.close()
        except (ValueError,OSError,sqlite3.Error) as error:
            print(json.dumps({"error":"backup_failed","category":type(error).__name__,"existing_backup_overwritten":False}),file=sys.stderr)
            return 2
        print(json.dumps(result));return 0
    if args.command=="maintain":
        from .store import Store
        store=Store(config["state_dir"],bytes.fromhex(config["audit_key"]))
        try:
            head=store.verify();deleted=store.purge()
            print(json.dumps({"audit":head,"expired_responses_deleted":deleted,"audit_events_retained":True}))
        finally:store.close()
        return 0
    key=config["keys"][args.key_id]
    parts=urlsplit(args.url)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("","/"):
        parser.error("URL must contain only scheme, host and port")
    method="GET" if args.path=="/v1/models" else "POST"
    if parts.scheme=="http" and parts.hostname not in ("127.0.0.1","localhost"):
        parser.error("Plain HTTP is restricted to loopback")
    if parts.scheme not in ("http","https"):parser.error("HTTP(S) required")
    body=b"" if method=="GET" else sys.stdin.buffer.read(128001)
    if len(body)>128000:parser.error("Request exceeds128KB")
    timestamp=str(int(time.time()));nonce=secrets.token_hex(16)
    headers={"Content-Type":"application/json","X-Eidos-Key":args.key_id,"X-Eidos-Time":timestamp,"X-Eidos-Nonce":nonce,
             "X-Eidos-Signature":signature(bytes.fromhex(key["secret"]),args.key_id,timestamp,nonce,method,args.path,body)}
    if parts.scheme=="https":
        connection=http.client.HTTPSConnection(parts.hostname,parts.port or 443,timeout=10,context=ssl.create_default_context(cafile=args.ca_file))
    else:connection=http.client.HTTPConnection(parts.hostname,parts.port or 80,timeout=10)
    try:
        connection.request(method,args.path,body=body,headers=headers)
        response=connection.getresponse();payload=response.read(2_000_001)
        if len(payload)>2_000_000:raise ValueError("Response limit exceeded")
        sys.stdout.buffer.write(payload+b"\n")
        return 0 if response.status==200 else 2
    finally:connection.close()


if __name__=="__main__":raise SystemExit(main())
