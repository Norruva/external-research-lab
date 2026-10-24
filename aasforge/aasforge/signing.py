"""JWS signing per IDTA-01002 3.2 "Signatures": RS256, ``typ: JWS``, ``x5c`` certificate chain,
``sigT`` signature timestamp and ``sid`` signature id, AAS JSON as the embedded payload.

The payload is the RFC 8785 canonical form of the object, so the signature does not depend on
key order or whitespace; verifiers must check the embedded payload, never a re-serialisation
(tools rewrite lexical forms, see dpp-rea/aas-compat round-trip results).
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from . import canonical


class SignatureError(ValueError):
    pass


class Signer:
    def __init__(self, key, chain: list):
        self.key = key
        self.chain = chain

    @classmethod
    def load_or_create(cls, directory: str | Path, common_name: str = 'aasforge'):
        directory = Path(directory)
        key_path, cert_path = directory / 'signing-key.pem', directory / 'signing-cert.pem'
        if key_path.exists() and cert_path.exists():
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            chain = x509.load_pem_x509_certificates(cert_path.read_bytes())
            return cls(key, chain)
        signer = cls.self_signed(common_name)
        directory.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(signer.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
        key_path.chmod(0o600)
        cert_path.write_bytes(b''.join(c.public_bytes(serialization.Encoding.PEM) for c in signer.chain))
        return signer

    @classmethod
    def self_signed(cls, common_name: str = 'aasforge', days: int = 825):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=days))
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                             crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        return cls(key, [cert])

    def x5c(self) -> list[str]:
        return [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in self.chain]

    def sign(self, payload: dict, signed_at: datetime | None = None, typ: str = 'JWS', extra: dict | None = None) -> str:
        header = {'alg': 'RS256', 'typ': typ, 'x5c': self.x5c(), 'sid': str(uuid.uuid4()),
                  'sigT': (signed_at or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')}
        header.update(extra or {})
        encoded = (canonical.b64url_bytes(canonical.canonical_bytes(header)) + '.' +
                   canonical.b64url_bytes(canonical.canonical_bytes(payload)))
        signature = self.key.sign(encoded.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
        return encoded + '.' + canonical.b64url_bytes(signature)

    def certificate_pem(self) -> str:
        return ''.join(c.public_bytes(serialization.Encoding.PEM).decode() for c in self.chain)


def verify(jws: str, trusted: list | None = None) -> dict:
    """Verify a compact JWS; returns {'header', 'payload', 'certificate_subject', 'trusted'}.

    ``trusted`` is a list of PEM certificates (trust anchors). Without it the signature is checked
    against the embedded x5c leaf only and ``trusted`` is reported False.
    """
    try:
        header_b64, payload_b64, signature_b64 = jws.strip().split('.')
        header = json.loads(canonical.b64url_to_bytes(header_b64))
        payload = json.loads(canonical.b64url_to_bytes(payload_b64))
        signature = canonical.b64url_to_bytes(signature_b64)
    except (ValueError, json.JSONDecodeError) as error:
        raise SignatureError('malformed JWS') from error
    if header.get('alg') != 'RS256' or not header.get('x5c'):
        raise SignatureError('only RS256 JWS with an x5c header is accepted')
    leaf = x509.load_der_x509_certificate(base64.b64decode(header['x5c'][0]))
    try:
        leaf.public_key().verify(signature, f'{header_b64}.{payload_b64}'.encode('ascii'), padding.PKCS1v15(),
                                 hashes.SHA256())
    except InvalidSignature as error:
        raise SignatureError('signature does not match') from error
    signed_at = header.get('sigT')
    if signed_at:
        moment = datetime.fromisoformat(signed_at.replace('Z', '+00:00'))
        if not (leaf.not_valid_before_utc <= moment <= leaf.not_valid_after_utc):
            raise SignatureError('certificate was not valid at sigT')
    is_trusted = False
    for pem in trusted or []:
        for anchor in x509.load_pem_x509_certificates(pem.encode() if isinstance(pem, str) else pem):
            if anchor.fingerprint(hashes.SHA256()) == leaf.fingerprint(hashes.SHA256()):
                is_trusted = True
            elif anchor.subject == leaf.issuer:
                try:
                    leaf.verify_directly_issued_by(anchor)
                    is_trusted = True
                except (ValueError, TypeError, InvalidSignature):
                    pass
    return {'header': {k: v for k, v in header.items() if k != 'x5c'}, 'payload': payload,
            'certificate_subject': leaf.subject.rfc4514_string(), 'trusted': is_trusted,
            'payload_digest': canonical.digest(payload)}
