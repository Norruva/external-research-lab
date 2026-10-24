"""Typed review-only decoding and CLI for the original routed v4 artifact."""
import argparse
import json
import sys

from .decision import DecisionEngine
from .decision_contract import MAX_REQUEST_BYTES


class RoutedEngine(DecisionEngine):
    @classmethod
    def from_registry(cls, directory, *, expected_registry_sha256=None):
        from .routed_artifact import load_artifact
        artifact = load_artifact(directory, expected_registry_sha256)
        return cls(artifact.model, artifact.identity, artifact.registry_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Original routed-memory decision model')
    parser.add_argument('--registry', required=True)
    parser.add_argument('--registry-sha256')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('request_byte_budget_exceeded')
        engine = RoutedEngine.from_registry(args.registry,
                    expected_registry_sha256=args.registry_sha256)
        response = engine.decide(raw)
        sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')) + '\n')
        return 0
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, OverflowError,
            RecursionError, MemoryError):
        sys.stderr.write('{"error":"routed_decision_refused","reason":"invalid_request_or_artifact"}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
