"""Bounded, private JSON IPC worker. No network, shell, plugins or credentials."""
import argparse
import json
import resource
import sys

from .contracts import ContractError, parse_json
from .engine import Engine

MAX_FRAME = 2_000_000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry")
    parser.add_argument("--registry-sha256")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    # CPU limit is cumulative for a recycled worker, not a per-request claim.
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    try:
        engine = Engine.from_registry(args.registry, tenants=("worker",), expected_registry_sha256=args.registry_sha256)
        print('{"ready":true}', flush=True)
        for _ in range(250):
            raw = sys.stdin.buffer.readline(128_002)
            if not raw:
                return 0
            if len(raw) > 128_001 or not raw.endswith(b"\n"):
                return 2
            try:
                request = parse_json(raw.decode())
                tenant = request["tenant_id"]
                engine.tenants = frozenset((tenant,))
                response = {"ok": True, "result": engine.decide(request)}
            except (ContractError, UnicodeError, KeyError, TypeError, ValueError):
                response = {"ok": False, "error": "invalid_request"}
            encoded = json.dumps(response, separators=(",", ":"), allow_nan=False).encode()
            if len(encoded) > MAX_FRAME:
                return 2
            sys.stdout.buffer.write(encoded + b"\n")
            sys.stdout.buffer.flush()
        return 0
    except (ContractError, OSError, ValueError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
