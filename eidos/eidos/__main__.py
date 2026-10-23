"""Versioned stdin/stdout CLI. No network listener or action execution."""
import argparse
import json
import logging
import sys
from .contracts import ContractError, parse_json
from .engine import Engine


def main():
    parser = argparse.ArgumentParser(description="EIDOS independent decision engine")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.audit:
        logging.basicConfig(stream=sys.stderr,level=logging.INFO,format="%(message)s")
    try:
        engine = Engine.from_registry(args.registry,tenants=(args.tenant,))
        result = engine.decide(parse_json(sys.stdin.read(128001)))
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except (ContractError,OSError,UnicodeError) as error:
        # User/model payloads are never included in errors or audit events.
        print(json.dumps({"schema_version":"1.0","error":{"code":"invalid_request_or_model","message":str(error)}}),file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
