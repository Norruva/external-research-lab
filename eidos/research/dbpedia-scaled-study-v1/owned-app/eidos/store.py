"""Tenant-scoped SQLite state. Raw request text is never persisted."""
import hashlib
import hmac
import json
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3
import threading
import time


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, directory, audit_key, *, retention=86400, max_records=100000):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError("State directory must be private (mode0700)")
        self.path = directory / "state.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Symlink database refused")
        if self.path.exists() and self.path.stat().st_size > 128*1024**2:
            raise ValueError("State database exceeds128MiBbudget")
        self.lock = threading.RLock()
        self.audit_key = audit_key
        self.retention = retention
        self.max_records = max_records
        self.instance_fd = os.open(directory/"instance.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(self.instance_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            os.close(self.instance_fd);self.instance_fd=None
            raise ValueError("State directory already has a running instance")
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise

    def initialize(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError("State database must be private (mode0600)")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS nonce(key_id TEXT, hash TEXT, expires REAL, PRIMARY KEY(key_id,hash));
                CREATE TABLE IF NOT EXISTS decision(tenant TEXT, id TEXT, hash TEXT, response TEXT, created REAL,
                    release TEXT, review TEXT, PRIMARY KEY(tenant,id));
                CREATE TABLE IF NOT EXISTS release(tenant TEXT PRIMARY KEY, version TEXT);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts REAL, tenant TEXT, event TEXT,
                    ref TEXT, detail TEXT, previous TEXT, mac TEXT);
            """)
            # A crash leaves no executable work. Remove interrupted reservations.
            db.execute("DELETE FROM decision WHERE response IS NULL")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA max_page_count=32768")
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, tenant, event, ref, detail):
        last = db.execute("SELECT seq,mac FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (last[0] + 1, last[1]) if last else (1, "0" * 64)
        if seq > self.max_records:
            raise Conflict("audit_capacity_archive_required")
        now = time.time()
        detail = json.dumps(detail, sort_keys=True, separators=(",", ":"))
        fields = [seq, now, tenant, event, ref, detail, previous]
        mac = hmac.new(self.audit_key, json.dumps(fields, separators=(",", ":")).encode(), "sha256").hexdigest()
        db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)", (*fields, mac))

    def reserve_nonce(self, key_id, nonce, now):
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM nonce WHERE expires < ?", (now,))
            if db.execute("SELECT count(*) FROM nonce").fetchone()[0] >= self.max_records:
                raise Conflict("nonce_capacity")
            try:
                db.execute("INSERT INTO nonce VALUES(?,?,?)", (key_id, hashlib.sha256(nonce.encode()).hexdigest(), now + 121))
            except sqlite3.IntegrityError as exc:
                raise Conflict("request_replay") from exc

    def current_release(self, tenant, initial):
        with self.lock, self.connection() as db:
            db.execute("INSERT OR IGNORE INTO release VALUES(?,?)", (tenant, initial))
            return db.execute("SELECT version FROM release WHERE tenant=?", (tenant,)).fetchone()[0]

    def promote(self, tenant, release):
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO release VALUES(?,?) ON CONFLICT(tenant) DO UPDATE SET version=excluded.version", (tenant, release))
            self.event(db, tenant, "release_changed", release, {})

    def begin(self, tenant, request_id, digest, release):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT hash,response FROM decision WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise Conflict("idempotency_conflict")
                if row[1] is None:
                    raise Conflict("request_in_progress")
                return json.loads(row[1])
            if db.execute("SELECT count(*) FROM decision").fetchone()[0] >= self.max_records:
                raise Conflict("state_capacity_run_retention")
            db.execute("INSERT INTO decision VALUES(?,?,?,NULL,?,?,NULL)", (tenant, request_id, digest, time.time(), release))
            return None

    def finish(self, tenant, request_id, response):
        encoded = json.dumps(response, separators=(",", ":"), allow_nan=False)
        with self.lock, self.connection() as db:
            db.execute("UPDATE decision SET response=? WHERE tenant=? AND id=?", (encoded, tenant, request_id))
            self.event(db, tenant, "decision_completed", hashlib.sha256(request_id.encode()).hexdigest(),
                       {"count": len(response["decisions"]), "trace_id": response["trace_id"]})

    def cancel(self, tenant, request_id):
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM decision WHERE tenant=? AND id=? AND response IS NULL", (tenant, request_id))

    def review(self, tenant, request_id, action):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT response,review FROM decision WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
            if not row or row[0] is None:
                raise Conflict("decision_unavailable")
            if row[1] is not None and row[1] != action:
                raise Conflict("review_already_final")
            if row[1] is None:
                db.execute("UPDATE decision SET review=? WHERE tenant=? AND id=?", (action, tenant, request_id))
                self.event(db, tenant, "review_recorded", hashlib.sha256(request_id.encode()).hexdigest(), {"action": action})
            return {"request_id": request_id, "review": action, "training_data_created": False, "action_executed": False}

    def history(self, tenant):
        with self.lock, self.connection() as db:
            rows=db.execute("SELECT id,created,release,review,response FROM decision WHERE tenant=? AND response IS NOT NULL ORDER BY created DESC LIMIT 50",(tenant,))
            result=[];encoded_bytes=0
            for request_id,created,release,review,response in rows:
                response=json.loads(response)
                item={"request_id":request_id,"created":created,"release":release,"review":review,"trace_id":response["trace_id"],
                      "decisions":[{key:row[key] for key in ("id","kind","status","candidate_value","confidence","review_required","reasons")} for row in response["decisions"]]}
                size=len(json.dumps(item,separators=(',',':'),allow_nan=False).encode())+1
                if encoded_bytes+size>950000:break
                encoded_bytes+=size;result.append(item)
            return result

    def verify(self):
        with self.lock, self.connection() as db:
            previous, expected_seq = "0" * 64, 1
            for row in db.execute("SELECT * FROM audit ORDER BY seq"):
                fields, mac = list(row[:-1]), row[-1]
                want = hmac.new(self.audit_key, json.dumps(fields, separators=(",", ":")).encode(), "sha256").hexdigest()
                if row[0] != expected_seq or row[-2] != previous or not hmac.compare_digest(mac, want):
                    raise Conflict("audit_integrity_failed")
                previous, expected_seq = mac, expected_seq + 1
            return {"events": expected_seq - 1, "head": previous}

    def purge(self, now=None):
        # Keep the audit chain intact; its bounded size is an explicit capacity gate.
        now = time.time() if now is None else now
        with self.lock, self.connection() as db:
            count = db.execute("DELETE FROM decision WHERE created < ?", (now - self.retention,)).rowcount
            db.execute("DELETE FROM nonce WHERE expires < ?", (now,))
            return count

    def close(self):
        if self.instance_fd is not None:
            os.close(self.instance_fd)
            self.instance_fd=None
