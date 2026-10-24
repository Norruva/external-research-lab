"""Authentication and IDTA-01004 Access Rule evaluation.

Authentication produces a ``claims`` dict from (in order) a Bearer JWT (HS256 with a shared secret
or RS256 with configured public keys), an ``X-API-Key`` mapped to claims, or anonymous.
Credentials are never read from the query string.

Authorization evaluates the access-rule document (the JSON form shared with the Query Language):
a request is allowed when at least one ALLOW rule grants the right, its OBJECTS cover the target,
its ACL attributes are satisfied and its FORMULA is true. Rules never overwrite each other
(BaSyx keys rules by role/action/type and silently drops duplicates). FILTER/FILTERLIST fragments
of the allowing rules prune submodel elements: an element survives if *any* allowing rule keeps it.
"""
from __future__ import annotations

import copy
import fnmatch
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256

from . import canonical, elements, formula

RIGHTS = ('CREATE', 'READ', 'UPDATE', 'DELETE', 'EXECUTE', 'VIEW')
OBJECT_ID = re.compile(r'^\$(aas|sm|cd|sme|aasdesc|smdesc)\("((?:[^"\\]|\\.)*)"\)(?:\.(.+))?$')
KIND_ROOT = {'shell': 'aas', 'submodel': 'sm', 'concept-description': 'cd', 'aas-descriptor': 'aasdesc',
             'sm-descriptor': 'smdesc'}


class AuthError(PermissionError):
    pass


@dataclass
class Subject:
    claims: dict = field(default_factory=dict)
    anonymous: bool = True

    @property
    def name(self):
        return self.claims.get('sub') or self.claims.get('email') or ('anonymous' if self.anonymous else 'unknown')


@dataclass
class Target:
    route: str
    kind: str | None = None
    obj: dict | None = None
    path: str | None = None


@dataclass
class Decision:
    allowed: bool
    filters: list | None = None          # None: no pruning; else list per allowing rule of [(fragment, condition)]
    referables: list | None = None       # None: whole object; else idShortPaths explicitly granted
    rule_ids: list = field(default_factory=list)


# -- authentication -------------------------------------------------------------------------------
class Authenticator:
    def __init__(self, hs256_secret: str | None = None, rs256_public_keys: list | None = None,
                 api_keys: dict | None = None, issuer: str | None = None, audience: str | None = None,
                 clock_skew: int = 60):
        self.secret = hs256_secret.encode() if hs256_secret else None
        self.public_keys = []
        for pem in rs256_public_keys or []:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            self.public_keys.append(load_pem_public_key(pem.encode() if isinstance(pem, str) else pem))
        self.api_keys = {k: v for k, v in (api_keys or {}).items()}
        self.issuer, self.audience, self.skew = issuer, audience, clock_skew

    def authenticate(self, headers) -> Subject:
        authorization = headers.get('Authorization') or headers.get('authorization')
        if authorization and authorization.lower().startswith('bearer '):
            return Subject(self.verify_jwt(authorization.split(None, 1)[1].strip()), anonymous=False)
        api_key = headers.get('X-API-Key') or headers.get('x-api-key')
        if api_key:
            for known, claims in self.api_keys.items():
                if hmac.compare_digest(known.encode(), api_key.encode()):
                    return Subject(dict(claims), anonymous=False)
            raise AuthError('unknown API key')
        return Subject({}, anonymous=True)

    def verify_jwt(self, token: str) -> dict:
        try:
            header_b64, payload_b64, signature_b64 = token.split('.')
            header = json.loads(canonical.b64url_to_bytes(header_b64))
            payload = json.loads(canonical.b64url_to_bytes(payload_b64))
            signature = canonical.b64url_to_bytes(signature_b64)
        except (ValueError, json.JSONDecodeError) as error:
            raise AuthError('malformed token') from error
        signed = f'{header_b64}.{payload_b64}'.encode()
        alg = header.get('alg')
        if alg == 'HS256' and self.secret:
            if not hmac.compare_digest(hmac.new(self.secret, signed, sha256).digest(), signature):
                raise AuthError('bad token signature')
        elif alg == 'RS256' and self.public_keys:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            for key in self.public_keys:
                try:
                    key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
                    break
                except InvalidSignature:
                    continue
            else:
                raise AuthError('bad token signature')
        else:
            raise AuthError(f'token algorithm {alg!r} not accepted')
        now = time.time()
        if 'exp' in payload and now > payload['exp'] + self.skew:
            raise AuthError('token expired')
        if 'nbf' in payload and now < payload['nbf'] - self.skew:
            raise AuthError('token not yet valid')
        if self.issuer and payload.get('iss') != self.issuer:
            raise AuthError('wrong issuer')
        if self.audience and self.audience not in (payload.get('aud') if isinstance(payload.get('aud'), list)
                                                   else [payload.get('aud')]):
            raise AuthError('wrong audience')
        return payload


def make_hs256_token(claims: dict, secret: str, lifetime: int = 3600) -> str:
    """Issue a test/dev token (production deployments use an external identity provider)."""
    header = canonical.b64url_bytes(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    body = dict(claims)
    body.setdefault('iat', int(time.time()))
    body.setdefault('exp', int(time.time()) + lifetime)
    payload = canonical.b64url_bytes(json.dumps(body).encode())
    signature = hmac.new(secret.encode(), f'{header}.{payload}'.encode(), sha256).digest()
    return f'{header}.{payload}.{canonical.b64url_bytes(signature)}'


# -- access rules ---------------------------------------------------------------------------------
class AccessRules:
    def __init__(self, document: dict | None):
        self.enabled = document is not None
        document = document or {}
        inner = document.get('AllAccessPermissionRules') if isinstance(document, dict) else None
        if document and inner is None:
            raise ValueError('access rules must be an object with an "AllAccessPermissionRules" member')
        errors = formula.validate(inner, 'AllAccessPermissionRules') if document else []
        if errors:
            raise ValueError('invalid access rules: ' + '; '.join(errors[:5]))
        body = document.get('AllAccessPermissionRules', {})
        self.attributes = {d['name']: d['attributes'] for d in body.get('DEFATTRIBUTES', [])}
        self.acls = {d['name']: d['acl'] for d in body.get('DEFACLS', [])}
        self.formulas = {d['name']: d['formula'] for d in body.get('DEFFORMULAS', [])}
        self.objects = {}
        for definition in body.get('DEFOBJECTS', []):
            self.objects[definition['name']] = definition.get('objects') or [
                o for name in definition.get('USEOBJECTS', []) for o in self.objects.get(name, [])]
        self.rules = [self._compile(i, r) for i, r in enumerate(body.get('rules', []))]

    def _compile(self, index, rule):
        acl = rule.get('ACL') or self.acls[rule['USEACL']]
        attributes = acl.get('ATTRIBUTES') or [a for n in acl.get('USEATTRIBUTES', []) for a in self.attributes[n]]
        objects = rule.get('OBJECTS') or [o for n in rule.get('USEOBJECTS', []) for o in self.objects[n]]
        rule_formula = rule.get('FORMULA') or self.formulas.get(rule.get('USEFORMULA'), {'$boolean': True})
        filters = []
        for spec in ([rule['FILTER']] if 'FILTER' in rule else []) + list(rule.get('FILTERLIST', [])):
            condition = spec.get('CONDITION') or self.formulas[spec['USEFORMULA']]
            filters.append((spec['FRAGMENT'], condition))
        rights = set(acl['RIGHTS'])
        if 'ALL' in rights:
            rights = set(RIGHTS)
        return {'id': index, 'rights': rights, 'access': acl['ACCESS'], 'attributes': attributes,
                'objects': objects, 'formula': rule_formula, 'filters': filters}

    @staticmethod
    def _attributes_ok(attributes, subject: Subject) -> bool:
        for attribute in attributes:
            if 'CLAIM' in attribute and attribute['CLAIM'] not in subject.claims:
                return False
            if attribute.get('GLOBAL') == 'ANONYMOUS':
                continue
        if subject.anonymous and not any(a.get('GLOBAL') == 'ANONYMOUS' for a in attributes):
            return False
        return True

    @staticmethod
    def _object_match(objects, target: Target):
        """Return (matches, referable path or None)."""
        if not objects:
            return True, None
        for item in objects:
            if 'ROUTE' in item:
                pattern = item['ROUTE']
                if pattern == '*' or fnmatch.fnmatchcase(target.route, pattern) or \
                        target.route == pattern or target.route.startswith(pattern.rstrip('/') + '/'):
                    return True, None
            for key in ('IDENTIFIABLE', 'DESCRIPTOR', 'REFERABLE'):
                if key not in item or target.obj is None:
                    continue
                match = OBJECT_ID.match(item[key])
                if not match:
                    continue
                root, identifier, path = match.groups()
                identifier = identifier.replace('\\"', '"').replace('\\\\', '\\')
                expected = 'sm' if root == 'sme' else root
                if KIND_ROOT.get(target.kind) == expected and fnmatch.fnmatchcase(target.obj.get('id', ''), identifier):
                    return True, path if root == 'sme' else None
            if 'FRAGMENT' in item and target.kind == 'submodel':
                path = item['FRAGMENT'].split('#')[0][len('$sme'):].lstrip('.')
                return True, path or None
        return False, None

    def candidate_formulas(self, subject: Subject, right: str):
        """FORMULAs of the rules that could allow ``right`` to ``subject`` (OBJECTS not considered),
        with the claims to evaluate them under; ``None`` when access control is off. Used to build
        SQL prefilters: an object is allowed only if one of these formulas holds for it."""
        if not self.enabled:
            return None
        claims = {**subject.claims, 'anonymous': subject.anonymous}
        return [r['formula'] for r in self.rules if r['access'] == 'ALLOW' and right in r['rights']
                and self._attributes_ok(r['attributes'], subject)], claims

    def decide(self, subject: Subject, right: str, target: Target, now: datetime | None = None) -> Decision:
        if not self.enabled:
            return Decision(True)
        allowing, referables, unrestricted_refs = [], [], False
        for rule in self.rules:
            if rule['access'] != 'ALLOW' or right not in rule['rights']:
                continue
            if not self._attributes_ok(rule['attributes'], subject):
                continue
            ok, referable = self._object_match(rule['objects'], target)
            if not ok:
                continue
            ctx = formula.Context(target.kind or 'route', target.obj or {}, claims=subject.claims,
                                  now=now or datetime.now(timezone.utc))
            ctx.claims = {**subject.claims, 'anonymous': subject.anonymous}
            if formula.evaluate(ctx, rule['formula']) is not True:
                continue
            allowing.append(rule)
            if referable:
                referables.append(referable)
            else:
                unrestricted_refs = True
        if not allowing:
            return Decision(False)
        filters = None if any(not r['filters'] for r in allowing) else [r['filters'] for r in allowing]
        return Decision(True, filters, None if unrestricted_refs else referables, [r['id'] for r in allowing])


def restrict_submodel(submodel: dict, decision: Decision, claims: dict | None = None) -> dict:
    """Apply a Decision's referable grants and FILTER fragments to a submodel copy."""
    if decision.filters is None and decision.referables is None:
        return submodel
    result = copy.deepcopy(submodel)
    if decision.referables is not None:
        keep_prefixes = decision.referables
        outside = []
        for path in elements.all_paths(result):
            covered = any(path == k or path.startswith(k + '.') or path.startswith(k + '[') for k in keep_prefixes)
            ancestor = any(k.startswith(path + '.') or k.startswith(path + '[') for k in keep_prefixes)
            if not covered and not ancestor:
                outside.append(path)
        elements.remove_many(result, outside)
    if decision.filters is not None:
        before = set(elements.all_paths(result))
        removed_sets = []
        for rule_filters in decision.filters:
            candidate = copy.deepcopy(result)
            for fragment, condition in rule_filters:
                formula.apply_filter(formula.Context('submodel', candidate, claims=claims or {}), fragment, condition)
            removed_sets.append(before - set(elements.all_paths(candidate)))
        drop = set.intersection(*removed_sets) if removed_sets else set()
        elements.remove_many(result, drop)
    return result
