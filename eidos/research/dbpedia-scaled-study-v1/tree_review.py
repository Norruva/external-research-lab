"""Gold-key-separated, pinned offline adapters for actual primary predictions.

Only standard-library modules are imported until every executable/model tree
has been completely verified against an externally supplied manifest hash.
The outer reviewed runner must provide isolated, immutable input mounts and a
private output directory. This program never reads an answer key or scores it.
"""
import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import resource
import socket
import stat
import sys
import time
import traceback


TREE_FORMAT = "eidos-large-complete-tree-v1"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_MEMBERS = 50000
MAX_DIRECTORIES = 50000
MAX_LARGE_TREE = 11 * 1024 * 1024 * 1024 // 10
MAX_SOURCE_TREE = 32 * 1024 * 1024
MAX_INPUTS = 16 * 1024 * 1024
CHUNK = 1024 * 1024
LAYA_SOURCE = "ca5045a3f1fac06fc6daa5cf3371110f322f5255"
LAYA_REVISION = "1c5edc17a7acd8701df6fc341c0d179f1c62c982"
LAYA_WEIGHTS = "891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c"
SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class AdapterContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AdapterContractError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(_value):
        raise AdapterContractError("Nonfinite JSON number")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise AdapterContractError("Nonfinite JSON number")
        return number
    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid,
                          parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AdapterContractError):
            raise
        raise AdapterContractError("Malformed bounded JSON") from exc


def _pin(value):
    if type(value) is not str or SHA.fullmatch(value) is None:
        raise AdapterContractError("Invalid external SHA-256 pin")
    return value


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def regular(path, *, directory=False):
    if ".." in Path(path).parts:
        raise AdapterContractError("Parent traversal is refused")
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise AdapterContractError("Symlink paths are refused")
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise AdapterContractError("Unexpected filesystem member type")
    return path


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def stream_hash(path, maximum):
    """Hash bounded regular files in <=1 MiB chunks, detecting concurrent edits."""
    path = regular(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            raise AdapterContractError("File byte budget exceeded")
        while True:
            block = stream.read(CHUNK)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise AdapterContractError("File byte budget exceeded")
            digest.update(block)
        after = os.fstat(stream.fileno())
    if _identity(before) != _identity(after) or total != before.st_size:
        raise AdapterContractError("File changed while hashing")
    return {"bytes": total, "sha256": digest.hexdigest()}


def read_pinned(path, expected_sha256, maximum):
    _pin(expected_sha256)
    path = regular(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            raise AdapterContractError("Pinned file byte budget exceeded")
        payload = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (_identity(before) != _identity(after) or len(payload) != before.st_size
            or len(payload) > maximum or hashlib.sha256(payload).hexdigest() != expected_sha256):
        raise AdapterContractError("Pinned file integrity mismatch")
    return payload


def _relative(name):
    if type(name) is not str or not name or len(name) > 4096 or "\\" in name or "\x00" in name:
        raise AdapterContractError("Invalid inventory path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name or name == ".":
        raise AdapterContractError("Unsafe inventory path")
    return path


def verify_large_tree(root, manifest_path, expected_sha256, *, maximum_bytes=MAX_LARGE_TREE):
    """Require complete canonical inventories, including dist-info and code files."""
    root = regular(root, directory=True)
    payload = read_pinned(manifest_path, expected_sha256, MAX_MANIFEST)
    manifest = strict_json(payload)
    if (type(manifest) is not dict or set(manifest) != {"format", "files"}
            or manifest["format"] != TREE_FORMAT or canonical(manifest) != payload):
        raise AdapterContractError("Unsupported complete-tree manifest")
    declared = manifest["files"]
    if type(declared) is not dict or not 1 <= len(declared) <= MAX_MEMBERS:
        raise AdapterContractError("Inventory member limit")
    allowed_directories, declared_total = set(), 0
    for name, entry in declared.items():
        relative = _relative(name)
        if (type(entry) is not dict or set(entry) != {"bytes", "sha256"}
                or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= maximum_bytes):
            raise AdapterContractError("Invalid inventory entry")
        _pin(entry["sha256"])
        declared_total += entry["bytes"]
        if declared_total > maximum_bytes:
            raise AdapterContractError("Inventory byte budget exceeded")
        allowed_directories.update(str(parent) for parent in relative.parents if str(parent) != ".")
        if len(allowed_directories) > MAX_DIRECTORIES:
            raise AdapterContractError("Inventory directory limit")
    files, directories, actual_total = {}, set(), 0
    pending = [root]
    while pending:
        current = pending.pop()
        for path in current.iterdir():
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            status = path.lstat()
            if stat.S_ISLNK(status.st_mode):
                raise AdapterContractError("Symlink inventory member refused")
            if stat.S_ISREG(status.st_mode):
                actual_total += status.st_size
                if len(files) >= MAX_MEMBERS or actual_total > maximum_bytes:
                    raise AdapterContractError("Actual tree budget exceeded")
                files[relative] = path
            elif stat.S_ISDIR(status.st_mode):
                if len(directories) >= MAX_DIRECTORIES:
                    raise AdapterContractError("Actual directory limit")
                directories.add(relative)
                pending.append(path)
            else:
                raise AdapterContractError("Special inventory member refused")
    if set(files) != set(declared) or directories != allowed_directories:
        raise AdapterContractError("Missing, shadow or undeclared inventory member")
    for name, entry in declared.items():
        if stream_hash(files[name], maximum_bytes) != entry:
            raise AdapterContractError("Inventory member content mismatch")
    return {"manifest": manifest, "manifest_sha256": expected_sha256,
            "files": len(files), "directories": len(directories), "bytes": actual_total}


def _readonly(path):
    if not os.statvfs(path).f_flag & os.ST_RDONLY:
        raise AdapterContractError("Inference inputs must be mounted read-only")


def _isolation_guard(reference):
    if any(Path(name).exists() for name in ("/workspace", "/root", "/run", "/etc")):
        raise AdapterContractError("Host paths must be absent in the inference namespace")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise AdapterContractError("Inference requires a private network namespace")
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise AdapterContractError("Inference requires Python -S -B")
    if reference:
        if any(os.environ.get(name) != "1" for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
            raise AdapterContractError("Reference inference must be explicitly offline")
        if any(name in os.environ for name in ("HF_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "AWS_ACCESS_KEY_ID")):
            raise AdapterContractError("Reference namespace contains a credential variable")


def validate_rights(rights, model_inventory, source_inventory):
    if (type(rights) is not dict or rights.get("license_reviewed") is not True
            or rights.get("model_revision") != LAYA_REVISION or rights.get("source_commit") != LAYA_SOURCE):
        raise AdapterContractError("Reference rights/source/revision gate is incomplete")
    models = {name: entry["sha256"] for name, entry in model_inventory["files"].items()}
    sources = {name: entry["sha256"] for name, entry in source_inventory["files"].items()}
    if rights.get("files") != models or rights.get("source_files") != sources or models.get("model.safetensors") != LAYA_WEIGHTS:
        raise AdapterContractError("Rights gate differs from the complete pinned artifacts")
    required = {"rl_agent_config.json", "model.safetensors", "encoder/config.json",
                "tokenizer/tokenizer_config.json", "tokenizer/tokenizer.json"}
    if not required <= set(models) or not {"laya/__init__.py", "laya/agent.py", "laya/common.py", "laya/confidence.py"} <= set(sources):
        raise AdapterContractError("Required reference members are missing")


def validate_prediction(probabilities, prediction, answered, diagnostics, options):
    if (type(probabilities) not in (list, tuple) or len(probabilities) != 20
            or any(not _number(p) or not 0 <= p <= 1 for p in probabilities)
            or not math.isclose(math.fsum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-10)
            or type(prediction) is not str or prediction not in options or type(answered) is not bool
            or type(diagnostics) is not dict):
        raise AdapterContractError("Invalid prediction contract")
    try:
        encoded = canonical(diagnostics)
    except (ValueError, TypeError, RecursionError) as exc:
        raise AdapterContractError("Invalid prediction diagnostics JSON") from exc
    if len(encoded) > 16384:
        raise AdapterContractError("Prediction diagnostics byte limit")
    strict_json(encoded)


class EidosAdapter:
    def __init__(self, directory, metadata_sha256, labels):
        metadata = strict_json(read_pinned(Path(directory) / "model.json", metadata_sha256, 12_000_000))
        if metadata.get("format") == "eidos-linear-general-v1":
            import generic_linear
            if regular(generic_linear.__file__) != regular(Path(__file__).with_name("generic_linear.py")):
                raise AdapterContractError("Unexpected generic runtime source")
            LinearModel = generic_linear.LinearModel
        elif metadata.get("format") == "eidos-linear-v2":
            from eidos.linear import LinearModel
        else:
            raise AdapterContractError("Unsupported owned model format")
        self.model = LinearModel.load(Path(directory) / "model.json", metadata_sha256)
        if list(self.model.data["labels"]) != list(labels):
            raise AdapterContractError("EIDOS and primary label inventories differ")
        self._declared_regime = "20" in self.model.data["temperatures"]
        self.metadata = {"adapter": "original-linear-forced-choice", "model_id": self.model.data["id"],
                         "model_metadata_sha256": metadata_sha256, "answered_policy": "always_false_no_acceptance_gate",
                         "temperature_source": "declared_20_candidate_regime" if self._declared_regime else "uncalibrated_default_temperature_one",
                         "temperature": self.model.data["temperatures"].get("20", 1.0),
                         "calibration_metadata_present": self.model.data["calibration"] is not None,
                         "quality_or_risk_certificate": False}

    def predict(self, case):
        from eidos.model import softmax
        options = case["options"]
        if self._declared_regime:
            _, probabilities = self.model.predict_subset(case["text"], options)
        else:
            probabilities = softmax(self.model.logits(case["text"], options), 1.0)
        index = min(range(len(options)), key=lambda i: (-probabilities[i], options[i]))
        return probabilities, options[index], False, {
            "forced_choice": True, "acceptance_gate_used": False,
            "review_required": True, "action_authorized": False,
            "temperature_source": self.metadata["temperature_source"],
            "calibration_or_coverage_claim": False}


def _new_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _parser():
    parser = argparse.ArgumentParser(description="Pinned gold-key-separated primary inference")
    parser.add_argument("engine", choices=("eidos", "laya"))
    for name in ("inputs", "inputs-sha256", "labels", "labels-sha256", "code-root", "code-manifest", "code-manifest-sha256",
                 "application-root", "application-manifest", "application-manifest-sha256", "model-root", "model-manifest",
                 "model-manifest-sha256", "output", "receipt", "protocol-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--threads", type=int, choices=(1,4), default=4)
    for name in ("model-metadata-sha256", "laya-source", "laya-source-manifest", "laya-source-manifest-sha256",
                 "deps-root", "deps-manifest", "deps-manifest-sha256", "rights-manifest", "rights-manifest-sha256"):
        parser.add_argument("--" + name)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    started = time.monotonic()
    processed, output_hash, bindings, adapter = 0, None, {}, None
    receipt_allowed = False
    output, receipt = Path(args.output).absolute(), Path(args.receipt).absolute()
    # These paths are validated before any target import or output mutation.
    try:
        parent = regular(output.parent, directory=True)
        if receipt.parent != parent or output == receipt or any(p.exists() or p.is_symlink() for p in (output, receipt)):
            raise AdapterContractError("Distinct exclusive prediction/receipt paths in one private directory are required")
        receipt_allowed = True
        _isolation_guard(args.engine == "laya")
        _pin(args.protocol_sha256)
        specifications = [("code", args.code_root, args.code_manifest, args.code_manifest_sha256, MAX_SOURCE_TREE),
                          ("application", args.application_root, args.application_manifest, args.application_manifest_sha256, MAX_SOURCE_TREE),
                          ("model", args.model_root, args.model_manifest, args.model_manifest_sha256, MAX_LARGE_TREE)]
        if args.engine == "laya":
            required = (args.laya_source, args.laya_source_manifest, args.laya_source_manifest_sha256,
                        args.deps_root, args.deps_manifest, args.deps_manifest_sha256,
                        args.rights_manifest, args.rights_manifest_sha256)
            if not all(required):
                raise AdapterContractError("Reference requires all source/dependency/rights pins")
            specifications.extend([("laya_source", args.laya_source, args.laya_source_manifest, args.laya_source_manifest_sha256, MAX_SOURCE_TREE),
                                   ("dependencies", args.deps_root, args.deps_manifest, args.deps_manifest_sha256, MAX_LARGE_TREE)])
        elif not args.model_metadata_sha256 or any((args.laya_source, args.deps_root, args.rights_manifest)):
            raise AdapterContractError("EIDOS requires its metadata pin and no reference roots")
        verified = {}
        for name, root, manifest_path, pin, maximum in specifications:
            _readonly(root)
            _readonly(manifest_path)
            verified[name] = verify_large_tree(root, manifest_path, pin, maximum_bytes=maximum)
            bindings[name] = {key: value for key, value in verified[name].items() if key != "manifest"}
        script = regular(__file__)
        if script != regular(Path(args.code_root) / "primary_predict.py"):
            raise AdapterContractError("Adapter is not executing from its pinned code tree")
        for path in (args.inputs, args.labels):
            _readonly(path)
        input_payload = read_pinned(args.inputs, args.inputs_sha256, MAX_INPUTS)
        labels_payload = read_pinned(args.labels, args.labels_sha256, 65536)
        labels = strict_json(labels_payload)
        if canonical(labels) != labels_payload:
            raise AdapterContractError("Label inventory must be canonical JSON")
        bindings.update({"inputs_sha256": args.inputs_sha256, "inputs_bytes": len(input_payload),
                         "labels_sha256": args.labels_sha256, "protocol_sha256": args.protocol_sha256,
                         "adapter_source_sha256": stream_hash(script, MAX_SOURCE_TREE)["sha256"]})
        if args.engine == "laya":
            _readonly(args.rights_manifest)
            rights = strict_json(read_pinned(args.rights_manifest, args.rights_manifest_sha256, MAX_MANIFEST))
            validate_rights(rights, verified["model"]["manifest"], verified["laya_source"]["manifest"])
            bindings["rights_manifest_sha256"] = args.rights_manifest_sha256
        else:
            _pin(args.model_metadata_sha256)
            if verified["model"]["manifest"]["files"].get("model.json", {}).get("sha256") != args.model_metadata_sha256:
                raise AdapterContractError("EIDOS metadata differs from the complete model pin")
            bindings["model_metadata_sha256"] = args.model_metadata_sha256
        # No project or third-party import occurs before this complete closure.
        sys.path.insert(0, str(regular(args.application_root, directory=True)))
        sys.path.insert(0, str(regular(args.code_root, directory=True)))
        import primary_cases as cases
        if regular(cases.__file__) != regular(Path(args.code_root) / "primary_cases.py"):
            raise AdapterContractError("Case helper was imported from an unexpected source")
        rows = cases.validate_inputs(cases.read_jsonl(args.inputs), labels)
        if args.engine == "eidos":
            adapter = EidosAdapter(args.model_root, args.model_metadata_sha256, labels)
        else:
            sys.path.insert(0, str(regular(args.deps_root, directory=True)))
            # Reuse the reviewed public API and decode semantics, without its
            # legacy case loader or gold-bearing run() function.
            from benchmarks import predict as reviewed_adapter
            if regular(reviewed_adapter.__file__) != regular(Path(args.application_root) / "benchmarks" / "predict.py"):
                raise AdapterContractError("Reference adapter was imported from an unexpected source")
            adapter = reviewed_adapter.Laya(args.model_root, args.laya_source, args.rights_manifest,threads=args.threads)
        import eidos.linear
        if regular(eidos.linear.__file__) != regular(Path(args.application_root) / "eidos" / "linear.py"):
            raise AdapterContractError("EIDOS core was imported from an unexpected source")
        load_seconds = time.monotonic() - started
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        inference_started = time.monotonic()
        output_bytes = 0
        with os.fdopen(descriptor, "wb") as stream:
            for case in rows:
                begin = time.perf_counter()
                probabilities, prediction, answered, diagnostics = adapter.predict(case)
                latency = (time.perf_counter() - begin) * 1000
                validate_prediction(probabilities, prediction, answered, diagnostics, case["options"])
                record = {"case_id": case["case_id"], "input_row_sha256": hashlib.sha256(cases.canonical_bytes(case)).hexdigest(),
                          "text_sha256": case["text_sha256"], "options": list(case["options"]),
                          "probabilities": list(probabilities), "prediction": prediction, "answered": answered,
                          "latency_ms": latency, "diagnostics": diagnostics}
                block = canonical(record) + b"\n"
                if len(block) > 65536 or output_bytes + len(block) > 64 * 1024 * 1024:
                    raise AdapterContractError("Prediction output byte budget exceeded")
                stream.write(block)
                stream.flush()
                output_bytes += len(block)
                processed += 1
            os.fsync(stream.fileno())
        output_hash = stream_hash(output, 64 * 1024 * 1024)
        elapsed = time.monotonic() - started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        hardware = {"platform": platform.system(), "machine": platform.machine(), "python": platform.python_version(),
                    "cpu_affinity_count": len(os.sched_getaffinity(0)), "requested_cpu_thread_cap": args.threads,
                    "inference_threads": args.threads if args.engine == "laya" else 1, "device": "cpu"}
        if args.engine == "laya":
            torch = sys.modules["torch"]
            hardware.update(torch_threads=torch.get_num_threads(), torch_interop_threads=torch.get_num_interop_threads())
        result = {"format": "eidos-primary-prediction-receipt-v1", "complete": True, "engine": args.engine,
                  "rows": processed, "bindings": bindings, "predictions": output_hash, "adapter": adapter.metadata,
                  "hardware": hardware, "load_and_integrity_seconds": load_seconds,
                  "inference_seconds": time.monotonic() - inference_started, "elapsed_seconds": elapsed,
                  "process_cpu_seconds": usage.ru_utime + usage.ru_stime, "peak_rss_kib": usage.ru_maxrss,
                  "scope": "prediction_only_no_quality_or_risk_claim", "gold_key_mounted_by_this_program": False,
                  "mission_complete": False, "production_certified": False,
                  "note": "Input projection only; outer mount/trace receipts establish isolation and full process resource accounting"}
        _new_json(receipt, result)
        print(canonical({"complete": True, "engine": args.engine, "rows": processed,
                         "predictions_sha256": output_hash["sha256"]}).decode())
        return 0
    except Exception as exc:
        # Retain partial rows and a separate failure receipt. Do not echo case
        # text or target exceptions that might include inference inputs.
        result = {"format": "eidos-primary-prediction-receipt-v1", "complete": False, "engine": args.engine,
                  "rows": processed, "bindings": bindings, "error_type": type(exc).__name__,
                  "elapsed_seconds": time.monotonic() - started, "scope": "prediction_failed_no_quality_claim",
                  "mission_complete": False, "production_certified": False}
        # Locations and fixed categories diagnose native/model-load failures
        # without storing exception messages, source lines or inference text.
        result['failure_frames'] = [{'file': Path(frame.filename).name,
                                     'function': frame.name, 'line': frame.lineno}
                                    for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
        diagnostic = str(exc).lower()
        result['error_category'] = (
            'memory_allocation' if any(s in diagnostic for s in ('allocate memory', 'not enough memory', 'out of memory'))
            else 'readonly_write' if any(s in diagnostic for s in ('read-only file system', 'permission denied'))
            else 'state_dict_mismatch' if 'state_dict' in diagnostic
            else 'unclassified')
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result['failure_resources'] = {'peak_rss_kib': usage.ru_maxrss,
                                       'cpu_seconds': usage.ru_utime + usage.ru_stime,
                                       'address_space_limit_bytes': resource.getrlimit(resource.RLIMIT_AS)[0]}
        # Own-process numeric memory totals only; never read /proc environment.
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                key, _, value = line.partition(':')
                if key in ('VmPeak', 'VmSize', 'VmRSS', 'VmData'):
                    result['failure_resources'][key + '_kib'] = int(value.strip().split()[0])
        except (OSError, ValueError):
            pass
        try:
            if receipt_allowed and output.exists() and output.is_file() and not output.is_symlink():
                result["partial_predictions"] = stream_hash(output, 64 * 1024 * 1024)
            if receipt_allowed and not receipt.exists() and not receipt.is_symlink():
                _new_json(receipt, result)
        except Exception:
            pass
        print(canonical({"complete": False, "engine": args.engine, "error_type": type(exc).__name__}).decode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
