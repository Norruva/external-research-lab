"""Fixed worker count, bounded admission and enforced wall deadlines over IPC."""
import json
import os
from pathlib import Path
import queue
import selectors
import subprocess
import sys
import threading
import time


class WorkerError(RuntimeError):
    pass


def readiness_request():
    return {"schema_version": "1.0", "input_version": "readiness", "request_id": "readiness",
            "tenant_id": "readiness", "language": "en", "state": None, "sources": [],
            "questions": [{"id": "ready", "kind": "ABSTAIN", "reason": "readiness check"}]}


def receive(process, deadline, maximum=2_000_001):
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise WorkerError("worker_timeout")
            block = os.read(process.stdout.fileno(), min(65536, maximum + 1 - len(result)))
            if not block:
                raise WorkerError("worker_stopped")
            result.extend(block)
            if len(result) > maximum:
                raise WorkerError("worker_response_limit")
            if b"\n" in block:
                if not result.endswith(b"\n") or result.count(b"\n") != 1:
                    raise WorkerError("worker_protocol")
                return json.loads(result)


class Worker:
    def __init__(self):
        self.process = None
        self.registry = None
        self.registry_hash = None
        self.calls = 0

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
        self.process = None

    def needs_restart(self, registry, expected_registry_sha256):
        return (self.registry != registry or self.registry_hash != expected_registry_sha256
                or self.process is None or self.process.poll() is not None or self.calls >= 240)

    def run(self, registry, request, deadline, expected_registry_sha256=None, *, allow_restart=False):
        try:
            if self.needs_restart(registry, expected_registry_sha256):
                if not allow_restart:
                    raise WorkerError("workers_busy")
                self.stop()
                # Original code only. No shell; secrets/host environment never inherited.
                command=[sys.executable,"-S","-B","-m","eidos.worker",str(registry)]
                if expected_registry_sha256 is not None:command += ["--registry-sha256",expected_registry_sha256]
                self.process = subprocess.Popen(
                    command,
                    cwd=Path(__file__).resolve().parents[1], env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                self.registry = registry
                self.registry_hash = expected_registry_sha256
                self.calls = 0
                if receive(self.process, deadline, 100) != {"ready": True}:
                    raise WorkerError("worker_start_failed")
            frame = json.dumps(request, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            if len(frame) > 128_001:
                raise WorkerError("worker_request_limit")
            # Nonblocking writes also share the deadline; a full pipe cannot hang a thread.
            os.set_blocking(self.process.stdin.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                view = memoryview(frame)
                while view:
                    left = deadline - time.monotonic()
                    if left <= 0 or not selector.select(left):
                        raise WorkerError("worker_timeout")
                    try:
                        count = os.write(self.process.stdin.fileno(), view)
                        view = view[count:]
                    except BlockingIOError:
                        continue
            result = receive(self.process, deadline)
            self.calls += 1
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise WorkerError("worker_protocol")
            return result
        except (OSError, ValueError, WorkerError) as exc:
            self.stop()
            if isinstance(exc, WorkerError):
                raise
            raise WorkerError("worker_failed") from exc


class Pool:
    def __init__(self, size=2):
        if type(size) is not int or not 1 <= size <= 4:
            raise ValueError("Between one and four workers required")
        self.workers = [Worker() for _ in range(size)]
        self.available = queue.Queue(size)
        for worker in self.workers:
            self.available.put(worker)
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False
        self._maintenance = set()
        self._retired = set()

    def execute(self, registry, request, timeout=2, expected_registry_sha256=None):
        return self._execute(registry, request, timeout, expected_registry_sha256, allow_cold=False)

    def prepare(self, registry, timeout=5, expected_registry_sha256=None):
        """Explicit internal readiness; caller-supplied request fields grant no bypass."""
        return self._execute(registry, readiness_request(), timeout, expected_registry_sha256, allow_cold=True)

    def _execute(self, registry, request, timeout, expected_registry_sha256, *, allow_cold):
        with self._condition:
            if self._closed:
                raise WorkerError("pool_closed")
        deadline = time.monotonic() + timeout
        try:
            worker = self.available.get(timeout=min(.1, timeout))
        except queue.Empty as exc:
            with self._condition:
                if self._closed:
                    raise WorkerError("pool_closed") from exc
            raise WorkerError("workers_busy") from exc
        with self._condition:
            if self._closed:
                raise WorkerError("pool_closed")
            self._active += 1
        try:
            if not allow_cold and worker.needs_restart(registry, expected_registry_sha256):
                # No user request or payload is replayed by maintenance.
                raise WorkerError("workers_busy")
            return worker.run(registry, request, deadline, expected_registry_sha256,
                              allow_restart=allow_cold)
        finally:
            with self._condition:
                self._active -= 1
                try:
                    if not self._closed:
                        if allow_cold or not worker.needs_restart(registry, expected_registry_sha256):
                            self.available.put(worker)
                        else:
                            thread = threading.Thread(target=self._maintain,
                                args=(worker, registry, expected_registry_sha256),
                                name="eidos-worker-readiness", daemon=True)
                            self._maintenance.add(thread)
                            try:
                                thread.start()
                            except (RuntimeError, OSError):
                                # Keep a failed slot out of admission. Preserve the
                                # already-computed result; restart recovers capacity.
                                self._maintenance.discard(thread)
                                self._retired.add(worker)
                finally:
                    self._condition.notify_all()

    def _maintain(self, worker, registry, expected_registry_sha256):
        try:
            with self._condition:
                if self._closed:
                    return
            result = worker.run(registry, readiness_request(), time.monotonic() + 5,
                                expected_registry_sha256, allow_restart=True)
            if result.get("ok") is not True:
                worker.stop()
        except Exception:
            # One bounded attempt per lease. A later caller may request another
            # readiness attempt; there is no autonomous retry loop.
            worker.stop()
        finally:
            with self._condition:
                if not self._closed:
                    # Failed readiness may return an unready slot. execute()
                    # rejects it before any decision and schedules readiness.
                    self.available.put(worker)
                self._maintenance.discard(threading.current_thread())
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            while self._active or self._maintenance:
                self._condition.wait()
        for worker in self.workers:
            worker.stop()
