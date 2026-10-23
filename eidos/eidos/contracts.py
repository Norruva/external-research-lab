"""Strict finite JSON and the local JSON Schema vocabulary, no remote resolvers."""
import json
import math
from pathlib import Path


class ContractError(ValueError):
    pass


def finite_json(value, *, max_nodes=10000, max_text=24000):
    active, nodes, chars = set(), 0, 0

    def visit(item, depth=0):
        nonlocal nodes, chars
        nodes += 1
        if nodes > max_nodes or depth > 24:
            raise ContractError("JSON node/depth budget exceeded")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, (int, float)):
            try:
                if not math.isfinite(item):
                    raise ContractError("Nonfinite number")
            except OverflowError as exc:
                raise ContractError("Number exceeds finite range") from exc
        elif isinstance(item, str):
            chars += len(item)
        elif isinstance(item, (list, dict)):
            if id(item) in active:
                raise ContractError("Cyclic JSON")
            active.add(id(item))
            if isinstance(item, dict):
                if any(not isinstance(k, str) for k in item):
                    raise ContractError("JSON keys must be strings")
                for key, child in item.items():
                    chars += len(key)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(id(item))
        else:
            raise ContractError("Only JSON values supported")
        if chars > max_text:
            raise ContractError("JSON text budget exceeded; no truncation performed")

    visit(value)


def parse_json(text, maximum=128000):
    if len(text) > maximum:
        raise ContractError("Serialized JSON size limit exceeded")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ContractError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ContractError("Nonfinite JSON constant")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Malformed JSON") from exc


SCHEMA = json.loads(Path(__file__).with_name("decision-api.schema.json").read_text())


def validate(value, schema=None, path="$", root=None):
    """Implement only the published schema's keywords; no downloads/formats/code."""
    root = SCHEMA if root is None else root
    schema = root if schema is None else schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ContractError("External schema reference refused")
        return validate(value, root["$defs"][ref.split("/")[-1]], path, root)
    for condition in schema.get("allOf", []):
        validate(value, condition, path, root)
    if "oneOf" in schema:
        successes = 0
        for variant in schema["oneOf"]:
            try:
                validate(value, variant, path, root)
                successes += 1
            except ContractError:
                pass
        if successes != 1:
            raise ContractError(f"{path}: expected exactly one supported schema variant")
    if "const" in schema:
        expected = schema["const"]
        if value != expected or (isinstance(expected, bool) and type(value) is not bool):
            raise ContractError(f"{path}: incompatible constant/version")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: unsupported enum")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    matches = {"null": value is None, "boolean": type(value) is bool,
               "number": type(value) in (int, float), "integer": type(value) is int,
               "string": isinstance(value, str), "array": isinstance(value, list),
               "object": isinstance(value, dict)}
    if types and not any(matches[t] for t in types):
        raise ContractError(f"{path}: invalid value type")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - set(value):
            raise ContractError(f"{path}: missing required fields")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ContractError(f"{path}: unknown fields")
        for key, child in value.items():
            if key in properties:
                validate(child, properties[key], path + "." + key, root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ContractError(f"{path}: array size out of bounds")
        if "items" in schema:
            for index, child in enumerate(value):
                validate(child, schema["items"], f"{path}[{index}]", root)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ContractError(f"{path}: string size out of bounds")
        if schema.get("minLength", 0) and not value.strip():
            raise ContractError(f"{path}: blank string")
    if type(value) in (int, float):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ContractError(f"{path}: number out of bounds")


def validate_request(request):
    finite_json(request)
    validate(request, SCHEMA["$defs"]["Request"])
    ids = [q["id"] for q in request["questions"]]
    source_ids = [s["id"] for s in request["sources"]]
    if len(ids) != len(set(ids)) or len(source_ids) != len(set(source_ids)):
        raise ContractError("Duplicate question/source IDs")
    for source in request["sources"]:
        if "sha256" in source and any(c not in "0123456789abcdef" for c in source["sha256"]):
            raise ContractError("Source SHA256 must be lowercase hex")


def validate_response(response):
    # Output may expand one bounded request into up to32x64 scored rows.
    finite_json(response, max_nodes=50000, max_text=1_000_000)
    validate(response, SCHEMA["$defs"]["Response"])
    for decision in response["decisions"]:
        if decision["status"] == "abstained" and decision["value"] is not None:
            raise ContractError("Abstained answer must be null")
        probabilities = [r["probability"] for r in decision["distribution"]]
        if probabilities and not math.isclose(math.fsum(probabilities), 1, abs_tol=1e-12):
            raise ContractError("Probability normalization error")
