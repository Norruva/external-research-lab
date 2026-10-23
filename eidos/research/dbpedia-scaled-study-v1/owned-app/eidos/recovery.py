"""Offline, authenticated state snapshots. No configuration or keys are bundled."""
from contextlib import closing
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import tempfile
import time

from .contracts import parse_json

MAGIC = b"EIDOSBK1\n"
DOMAIN = b"EIDOS offline snapshot v1\x00"
MAX_DB = 128 * 1024**2
MAX_META = 4096
MAX_RECORDS = 100000
HEX = re.compile(r"[0-9a-f]{64}\Z")
COLUMNS = {
    "nonce": ["key_id", "hash", "expires"],
    "decision": ["tenant", "id", "hash", "response", "created", "release", "review"],
    "release": ["tenant", "version"],
    "audit": ["seq", "ts", "tenant", "event", "ref", "detail", "previous", "mac"],
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _key(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Recovery requires the configured 32-byte audit key")


def _path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Recovery paths cannot contain symlinks")
    return path


def _parent(path):
    path = _path(path)
    p = path.parent
    info = p.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Recovery destination parent must be an owned private directory")
    return path


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def _database(path, key):
    """Read a standalone, already authenticated SQLite image without modifying it."""
    _key(key)
    path = _path(path)
    if not 0 < path.stat().st_size <= MAX_DB:
        raise ValueError("Snapshot database size is outside the supported budget")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        deadline = time.monotonic() + 120
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.enable_load_extension(False)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2_000_000)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cell_size_check=ON")
        if db.execute("PRAGMA integrity_check(1)").fetchall() != [("ok",)]:
            raise ValueError("Snapshot SQLite integrity check failed")
        schema = db.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if len(schema) != len(COLUMNS) or {row[1] for row in schema} != set(COLUMNS):
            raise ValueError("Snapshot schema is not an EIDOS state database")
        for kind, name, sql in schema:
            if kind != "table" or not sql.lstrip().upper().startswith("CREATE TABLE "):
                raise ValueError("Snapshot contains unsupported schema objects")
            if [row[1] for row in db.execute('PRAGMA table_info("' + name + '")')] != COLUMNS[name]:
                raise ValueError("Snapshot table columns differ from the supported schema")
        counts = {name: db.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] for name in COLUMNS}
        if any(count > MAX_RECORDS for count in counts.values()):
            raise ValueError("Snapshot exceeds the supported record budget")
        previous, sequence = "0" * 64, 1
        for row in db.execute("SELECT seq,ts,tenant,event,ref,detail,previous,mac FROM audit ORDER BY seq"):
            fields, mac = list(row[:-1]), row[-1]
            if (type(row[0]) is not int or row[0] != sequence or type(row[1]) not in (int, float)
                    or not math.isfinite(row[1]) or any(not isinstance(v, str) for v in row[2:])
                    or not HEX.fullmatch(mac) or row[-2] != previous):
                raise ValueError("Snapshot audit chain is malformed")
            expected = hmac.new(key, json.dumps(fields, separators=(",", ":")).encode(), "sha256").hexdigest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("Snapshot audit authentication failed")
            previous, sequence = mac, sequence + 1
        return {"audit": {"events": sequence - 1, "head": previous}, "rows": counts}


def backup(store, output):
    """Caller must hold the Store instance lock; the running service must stop first."""
    _key(store.audit_key)
    output = _parent(output)
    if output.exists():
        raise FileExistsError("Refusing to replace an existing backup")
    if store.instance_fd is None:
        raise ValueError("Backup requires an open exclusively owned Store")
    with tempfile.TemporaryDirectory(prefix=".eidos-backup-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        snapshot = temporary / "state.sqlite3"
        fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with store.lock:
            head = store.verify()
            with store.connection() as source, closing(sqlite3.connect(snapshot)) as target:
                deadline = time.monotonic() + 120
                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Snapshot copy exceeded its wall budget")
                source.backup(target, pages=256, progress=progress)
        checked = _database(snapshot, store.audit_key)
        if checked["audit"] != head:
            raise ValueError("Snapshot audit head changed during backup")
        manifest = {"format": "eidos-offline-state-v1", "created_unix_seconds": int(time.time()),
                    "database_bytes": snapshot.stat().st_size, "database_sha256": _digest(snapshot), **checked}
        mac = hmac.new(store.audit_key, DOMAIN + canonical(manifest), "sha256").hexdigest()
        metadata = canonical({"manifest": manifest, "mac": mac})
        if len(metadata) > MAX_META:
            raise ValueError("Snapshot metadata exceeds its budget")
        staged = temporary / "backup.eidos"
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream, snapshot.open("rb") as source:
            stream.write(MAGIC + struct.pack(">I", len(metadata)) + metadata)
            for block in iter(lambda: source.read(1024**2), b""):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        digest = _digest(staged)
        # Same-filesystem exclusive publication; existing destinations never change.
        os.link(staged, output, follow_symlinks=False)
        try:
            _sync_directory(output.parent)
        except BaseException:
            output.unlink()
            raise
    return {"backup_created": str(output), "backup_sha256": digest, "audit": head,
            "rows": checked["rows"], "encrypted": False, "configuration_or_keys_included": False}


def restore(backup_path, destination, key, expected_sha256):
    """Verify and restore into a NEW state directory; do not modify configuration."""
    _key(key)
    if not isinstance(expected_sha256, str) or not HEX.fullmatch(expected_sha256):
        raise ValueError("Restore requires the independently retained backup SHA256")
    backup_path = _path(backup_path)
    destination = _parent(destination)
    if destination.exists():
        raise FileExistsError("Restore requires a nonexistent state directory")
    fd = os.open(backup_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or not len(MAGIC) + 4 < info.st_size <= MAX_DB + MAX_META + len(MAGIC) + 4):
            raise ValueError("Backup must be an owned private regular file within the size budget")
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError("Unsupported backup format")
        length = struct.unpack(">I", source.read(4))[0]
        if not 1 <= length <= MAX_META:
            raise ValueError("Invalid backup metadata length")
        raw_metadata = source.read(length)
        envelope = parse_json(raw_metadata.decode("utf-8"), maximum=MAX_META)
        if not isinstance(envelope, dict) or set(envelope) != {"manifest", "mac"}:
            raise ValueError("Invalid snapshot envelope")
        manifest, mac = envelope["manifest"], envelope["mac"]
        fields = {"format", "created_unix_seconds", "database_bytes", "database_sha256", "audit", "rows"}
        if (not isinstance(manifest, dict) or set(manifest) != fields or manifest["format"] != "eidos-offline-state-v1"
                or type(manifest["created_unix_seconds"]) is not int
                or manifest["created_unix_seconds"] < 0
                or type(manifest["database_bytes"]) is not int or not 0 < manifest["database_bytes"] <= MAX_DB
                or not isinstance(manifest["database_sha256"], str) or not HEX.fullmatch(manifest["database_sha256"])
                or not isinstance(mac, str) or not HEX.fullmatch(mac)):
            raise ValueError("Invalid snapshot manifest")
        audit, rows = manifest["audit"], manifest["rows"]
        if (not isinstance(audit, dict) or set(audit) != {"events", "head"}
                or type(audit["events"]) is not int or not 0 <= audit["events"] <= MAX_RECORDS
                or not isinstance(audit["head"], str) or not HEX.fullmatch(audit["head"])
                or not isinstance(rows, dict) or set(rows) != set(COLUMNS)
                or any(type(v) is not int or not 0 <= v <= MAX_RECORDS for v in rows.values())):
            raise ValueError("Invalid snapshot audit or record metadata")
        expected_mac = hmac.new(key, DOMAIN + canonical(manifest), "sha256").hexdigest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Snapshot manifest authentication failed")
        if info.st_size != len(MAGIC) + 4 + length + manifest["database_bytes"]:
            raise ValueError("Snapshot size differs from authenticated metadata")
        digest = hashlib.sha256(MAGIC + struct.pack(">I", length) + raw_metadata)
        database_digest = hashlib.sha256()
        with tempfile.TemporaryDirectory(prefix=".eidos-restore-", dir=destination.parent) as temporary:
            temporary = Path(temporary)
            snapshot = temporary / "state.sqlite3"
            fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as target:
                remaining = manifest["database_bytes"]
                while remaining:
                    block = source.read(min(remaining, 1024**2))
                    if not block:
                        raise ValueError("Snapshot database was truncated during reading")
                    target.write(block)
                    digest.update(block)
                    database_digest.update(block)
                    remaining -= len(block)
                if source.read(1):
                    raise ValueError("Snapshot has unexpected trailing bytes")
                target.flush()
                os.fsync(target.fileno())
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                raise ValueError("Backup differs from the independently retained SHA256")
            if not hmac.compare_digest(database_digest.hexdigest(), manifest["database_sha256"]):
                raise ValueError("Snapshot database digest differs from authenticated metadata")
            # No SQLite parser is reached before complete hash and MAC validation.
            checked = _database(snapshot, key)
            if checked != {"audit": manifest["audit"], "rows": manifest["rows"]}:
                raise ValueError("Snapshot database metadata does not match its signed manifest")
            destination.mkdir(mode=0o700)
            published = False
            try:
                os.link(snapshot, destination / "state.sqlite3", follow_symlinks=False)
                published = True
                _sync_directory(destination)
                _sync_directory(destination.parent)
            except BaseException:
                if published:
                    (destination / "state.sqlite3").unlink()
                destination.rmdir()
                raise
    return {"restored_state_directory": str(destination), "backup_sha256": expected_sha256,
            "audit": checked["audit"], "rows": checked["rows"], "configuration_changed": False,
            "pending_requests_cleared_on_normal_startup": True, "service_started": False}
