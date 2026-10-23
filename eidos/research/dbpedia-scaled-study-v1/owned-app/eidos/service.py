"""Dependency-free, bounded local HTTP transport; TLS required off loopback."""
import argparse
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import sqlite3
import ssl
import threading
import time

from .contracts import ContractError, parse_json, validate_request
from .engine import Engine
from .pool import Pool, WorkerError
from .store import Conflict, Store

API_VERSION = "0.2.0"
ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/ui.js": ("ui.js", "text/javascript; charset=utf-8"),
          "/transport.js": ("transport.js", "text/javascript; charset=utf-8"),
          "/eidos.js": ("eidos.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def signature(secret, key_id, timestamp, nonce, method, path, body):
    message = "\n".join((key_id, str(timestamp), nonce, method, path, hashlib.sha256(body).hexdigest()))
    return hmac.new(secret, message.encode(), "sha256").hexdigest()


def private_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_size > 64000:
        raise ValueError("Configuration must be a private regular file of at most64KB")
    return parse_json(path.read_text(), maximum=64000)


class Application:
    def __init__(self, config_path):
        config = private_file(config_path)
        if set(config) != {"format", "registry_root", "releases", "tenants", "keys", "audit_key", "state_dir", "workers"} or config["format"] != "eidos-service-v1":
            raise ValueError("Unsupported service configuration")
        self.root = Path(config["registry_root"]).resolve(strict=True)
        self.releases = {}
        if not 1 <= len(config["releases"]) <= 16:
            raise ValueError("Release inventory limit")
        for release, relative in config["releases"].items():
            path = self.root / relative
            if not ID.fullmatch(release) or Path(relative).is_absolute() or ".." in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("Release must remain within configured root")
            engine = Engine.from_registry(path)
            self.releases[release] = {"path": path, "registry_sha256":engine.registry_sha256, "models": [m.identity for m in engine.models.values()],
                "profiles":[{"model":m.identity,"labels":m.data["labels"],"languages":m.data["languages"],
                             "candidate_counts":sorted(map(int,m.data["temperatures"])) if "temperatures" in m.data else [len(m.data["labels"])],
                             "consequential":m.data["consequential"],"domain":m.data["domain"]} for m in engine.models.values()]}
        self.tenants = config["tenants"]
        self.keys = config["keys"]
        if not 1 <= len(self.tenants) <= 100 or not 1 <= len(self.keys) <= 200:
            raise ValueError("Tenant/key inventory limit")
        for tenant, policy in self.tenants.items():
            if not ID.fullmatch(tenant) or set(policy) != {"initial_release", "allowed_releases"} or not policy["allowed_releases"] or policy["initial_release"] not in policy["allowed_releases"] or not set(policy["allowed_releases"]) <= set(self.releases):
                raise ValueError("Invalid tenant release policy")
        for key_id, key in self.keys.items():
            if not ID.fullmatch(key_id) or set(key) != {"tenant", "secret", "scopes"} or key["tenant"] not in self.tenants or not HEX.fullmatch(key["secret"]) or not isinstance(key["scopes"], list) or not set(key["scopes"]) <= {"decide", "review", "models", "admin"}:
                raise ValueError("Invalid key policy")
        if not isinstance(config["audit_key"], str) or not HEX.fullmatch(config["audit_key"]):
            raise ValueError("Audit key must be32random bytes encoded as hex")
        self.store = Store(config["state_dir"], bytes.fromhex(config["audit_key"]))
        self.pool = None
        try:
            self.store.verify()
            for tenant, policy in self.tenants.items():
                release = self.store.current_release(tenant, policy["initial_release"])
                if release not in policy["allowed_releases"]:
                    raise ValueError("Persisted release is outside current policy")
            self.pool = Pool(config["workers"])
            for release in self.releases:
                self.check_worker(release)
        except BaseException:
            if self.pool is not None:
                self.pool.close()
            self.store.close()
            raise
        self.rate_lock = threading.Lock()
        self.rate = {}

    def check_worker(self, release):
        # Explicit readiness is the only admission path allowed to load a cold
        # worker. User-controlled request fields cannot enable that path.
        for _ in self.pool.workers:
            result = self.pool.prepare(self.releases[release]["path"],timeout=5,expected_registry_sha256=self.releases[release]["registry_sha256"])
            if not result["ok"]:
                raise WorkerError("model_readiness_failed")

    def authenticate(self, headers, method, path, body):
        # Four unambiguous headers; no cookies or implicit browser identity.
        names = ("X-Eidos-Key", "X-Eidos-Time", "X-Eidos-Nonce", "X-Eidos-Signature")
        if any(len(headers.get_all(name, [])) != 1 for name in names):
            raise PermissionError("authentication_required")
        key_id, timestamp, nonce, mac = [headers[name] for name in names]
        if key_id not in self.keys or not re.fullmatch(r"[0-9]{10}", timestamp) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", nonce) or not HEX.fullmatch(mac):
            raise PermissionError("authentication_failed")
        now = time.time()
        if abs(now - int(timestamp)) > 60:
            raise PermissionError("authentication_expired")
        key = self.keys[key_id]
        expected = signature(bytes.fromhex(key["secret"]), key_id, timestamp, nonce, method, path, body)
        if not hmac.compare_digest(mac, expected):
            raise PermissionError("authentication_failed")
        with self.rate_lock:
            minute = int(now // 60)
            old_minute, count = self.rate.get(key_id, (minute, 0))
            count = count + 1 if old_minute == minute else 1
            self.rate[key_id] = (minute, count)
            if count > 120:
                raise Conflict("rate_limit")
        self.store.reserve_nonce(key_id, nonce, now)
        return key

    def handle(self, method, path, headers, body):
        key = self.authenticate(headers, method, path, body)
        tenant = key["tenant"]
        scopes = {("POST", "/v1/decisions"): "decide", ("POST", "/v1/reviews"): "review",
                  ("GET", "/v1/reviews"): "review", ("GET", "/v1/models"): "models", ("POST", "/v1/releases"): "admin"}
        scope = scopes.get((method, path))
        if scope is None:
            return 404, {"error": "route_unavailable"}
        if scope not in key["scopes"]:
            raise PermissionError("scope_denied")
        policy = self.tenants[tenant]
        release = self.store.current_release(tenant, policy["initial_release"])
        if method == "GET":
            if body:
                raise ContractError("GET body refused")
            if path == "/v1/reviews":
                return 200, {"items":self.store.history(tenant),"limit":50,"items_byte_limit":950000,"raw_input_retained":False}
            return 200, {"active_release": release, "allowed_releases": policy["allowed_releases"],
                         "models": self.releases[release]["models"], "profiles":self.releases[release]["profiles"], "service_version": API_VERSION}
        obj = parse_json(body.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ContractError("Object required")
        if path == "/v1/releases":
            if set(obj) != {"release"} or obj["release"] not in policy["allowed_releases"]:
                raise ContractError("Release is not allowlisted")
            # Recheck artifact integrity before durable promotion/rollback.
            Engine.from_registry(self.releases[obj["release"]]["path"],expected_registry_sha256=self.releases[obj["release"]]["registry_sha256"])
            self.check_worker(obj["release"])
            self.store.promote(tenant, obj["release"])
            return 200, {"active_release": obj["release"]}
        if path == "/v1/reviews":
            if set(obj) != {"request_id", "action"} or not isinstance(obj["request_id"], str) or not 1 <= len(obj["request_id"]) <= 256 or obj["action"] not in ("accepted", "rejected"):
                raise ContractError("Invalid review contract")
            return 200, self.store.review(tenant, obj["request_id"], obj["action"])
        validate_request(obj)
        if obj["tenant_id"] != tenant:
            raise PermissionError("tenant_mismatch")
        digest = hashlib.sha256(body).hexdigest()
        cached = self.store.begin(tenant, obj["request_id"], digest, release)
        if cached is not None:
            return 200, cached
        try:
            result = self.pool.execute(self.releases[release]["path"], obj,
                                       timeout=min(5, obj.get("budget_ms", 1000) / 1000 + .5),
                                       expected_registry_sha256=self.releases[release]["registry_sha256"])
            if not result["ok"]:
                raise ContractError("Invalid decision request")
            response = result["result"]
            self.store.finish(tenant, obj["request_id"], response)
            return 200, response
        except BaseException:
            self.store.cancel(tenant, obj["request_id"])
            raise

    def close(self):
        self.pool.close()
        self.store.close()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 32

    def __init__(self, address, app, *, max_connections=12):
        self.app = app
        self.slots = threading.BoundedSemaphore(max_connections)
        self.web = Path(__file__).resolve().parents[1] / "web"
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(8, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            super().process_request_thread(request, address)
        finally:
            watchdog.cancel()
            self.slots.release()

    def handle_error(self, request, client_address):
        # HTTP parser errors may contain user text. No inherited traceback logs.
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EIDOS"
    sys_version = ""

    def log_message(self, *_):
        pass

    def handle_expect_100(self):
        self.send_error(417)
        return False

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {"error": "invalid_http_request"})

    def respond(self, code, value, content_type="application/json; charset=utf-8"):
        payload = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def dispatch(self):
        try:
            if self.request_version != "HTTP/1.1" or len(self.headers.get_all("Host", [])) != 1 or len(self.headers["Host"]) > 255:
                raise ContractError("HTTP/1.1 Host required")
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"{self.server.server_address[0]}:{port}"}
            if self.headers["Host"] not in hosts:
                raise PermissionError("host_denied")
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise ContractError("Ambiguous framing refused")
            raw_length = self.headers.get("Content-Length", "0")
            if not re.fullmatch(r"[0-9]{1,6}", raw_length) or int(raw_length) > 128000:
                self.respond(413, {"error": "body_limit"})
                return
            length = int(raw_length)
            if self.command == "GET" and length:
                raise ContractError("GET body refused")
            if self.command == "POST" and (len(self.headers.get_all("Content-Type", [])) != 1 or self.headers["Content-Type"].lower() != "application/json"):
                raise ContractError("JSON Content-Type required")
            if "Origin" in self.headers:
                if len(self.headers.get_all("Origin", [])) != 1:
                    raise PermissionError("origin_denied")
                scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
                if self.headers["Origin"] != scheme + "://" + self.headers["Host"]:
                    raise PermissionError("origin_denied")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ContractError("Incomplete request")
            if self.command == "GET" and self.path == "/healthz":
                self.respond(200, {"status": "ok", "service_version": API_VERSION})
            elif self.command == "GET" and self.path in ASSETS:
                name, content_type = ASSETS[self.path]
                self.respond(200, (self.server.web / name).read_bytes(), content_type)
            else:
                code, response = self.server.app.handle(self.command, self.path, self.headers, body)
                self.respond(code, response)
        except PermissionError as exc:
            self.respond(403, {"error": str(exc)})
        except Conflict as exc:
            self.respond(429 if str(exc) == "rate_limit" else 409, {"error": str(exc)})
        except WorkerError as exc:
            self.respond(504 if str(exc) == "worker_timeout" else 503, {"error": str(exc)})
        except (ContractError, UnicodeError, TypeError, KeyError, ValueError):
            self.respond(400, {"error": "invalid_request"})
        except (TimeoutError, socket.timeout):
            self.respond(408, {"error": "request_timeout"})
        except (OSError, RuntimeError, sqlite3.Error):
            self.respond(503, {"error": "service_unavailable"})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description="EIDOS local service. Private configuration file required.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    try:
        loopback = ipaddress.ip_address(args.bind).is_loopback
        if not loopback and not (args.tls_cert and args.tls_key):
            parser.error("TLS is required for an external bind")
        if ":" in args.bind:
            parser.error("This release supports IPv4 listeners")
        app = Application(args.config)
        server = Server((args.bind, args.port), app)
        if args.tls_cert or args.tls_key:
            if not (args.tls_cert and args.tls_key):
                parser.error("Both certificate and key required")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.tls_cert, args.tls_key)
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.close()
    except (OSError, ValueError, Conflict, WorkerError, sqlite3.Error):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
