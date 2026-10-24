"""Canonical JSON (RFC 8785 JCS subset), stable digests and API identifier encoding."""
from __future__ import annotations

import base64
import hashlib
import json
import math


def canonical_bytes(value) -> bytes:
    """Serialise JSON per RFC 8785: sorted keys, no whitespace, UTF-8, ECMAScript numbers.

    AAS JSON carries element values as strings, so the number rules only matter for ValueOnly
    payloads; integral floats are written without a fraction as JCS requires.
    """
    return json.dumps(_normalise_numbers(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _normalise_numbers(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('non-finite numbers are not representable in canonical JSON')
        return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value
    if isinstance(value, dict):
        return {k: _normalise_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise_numbers(v) for v in value]
    return value


def digest(value) -> str:
    return 'sha256:' + hashlib.sha256(canonical_bytes(value)).hexdigest()


def b64url_encode(text: str) -> str:
    """Encode an identifier for use in an API path segment (base64url without padding)."""
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii').rstrip('=')


def b64url_decode(segment: str) -> str:
    padded = segment + '=' * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f'identifier segment is not base64url: {segment!r}') from error


def b64url_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def b64url_to_bytes(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))
