"""Typed scoring and deterministic review policy; deliberately no action executor."""
import collections
import datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import time
import uuid

from .contracts import ContractError, parse_json, validate_request, validate_response
from .model import Model

LOGGER = logging.getLogger("eidos.audit")


def selected_text(state, path):
    for component in path:
        if isinstance(state, dict) and component in state:
            state = state[component]
        elif isinstance(state, list) and component.isdigit() and int(component) < len(state):
            state = state[int(component)]
        else:
            raise ContractError("State path unavailable")
    if not isinstance(state, str) or len(state) > 12000:
        raise ContractError("Selected state must be text within12000character budget")
    return state


def policy(profile, confidence, margin, coverage, language):
    reasons = []
    # A disabled gate is explicit: floating-point softmax can round to exactly1.
    # Threshold1 must never turn insufficient validation into an accepted answer.
    if profile["thresholds"]["probability"] == 1 or profile["thresholds"]["margin"] == 1:
        reasons.append("acceptance_gate_unavailable")
    if language not in profile["languages"]:
        reasons.append("unsupported_language")
    if coverage < profile["thresholds"]["coverage"] or coverage == 0:
        reasons.append("insufficient_lexical_support")
    if confidence < profile["thresholds"]["probability"]:
        reasons.append("low_probability")
    if margin < profile["thresholds"]["margin"] or margin == 0:
        reasons.append("ambiguous_margin")
    return reasons


class Engine:
    def __init__(self, models, *, tenants=("demo",)):
        self.models = dict(models)
        self.tenants = frozenset(tenants)

    @classmethod
    def from_registry(cls, directory, *, tenants=("demo",), expected_registry_sha256=None):
        directory = Path(directory)
        registry_path = directory/"registry.json"
        if registry_path.is_symlink() or not registry_path.is_file() or registry_path.stat().st_size > 64000:
            raise ContractError("Invalid registry file/size")
        registry_bytes = registry_path.read_bytes()
        registry_digest = hashlib.sha256(registry_bytes).hexdigest()
        if expected_registry_sha256 is not None and registry_digest != expected_registry_sha256:
            raise ContractError("Registry changed after release pinning")
        registry = parse_json(registry_bytes.decode(), maximum=64000)
        if set(registry) != {"format","models"} or registry["format"] != "eidos-registry-v1":
            raise ContractError("Unsupported registry version")
        if not isinstance(registry["models"],list) or not 1 <= len(registry["models"]) <= 16:
            raise ContractError("Registry model count outside bounds")
        models = {}
        for entry in registry["models"]:
            if set(entry) != {"id","file","sha256"} or Path(entry["file"]).name != entry["file"] or entry["file"] in (".",".."):
                raise ContractError("Registry path/entry refused")
            path = directory/entry["file"]
            if path.is_symlink() or path.resolve().parent != directory.resolve():
                raise ContractError("Model must remain within registry directory")
            try:
                model = Model.load(path,entry["sha256"])
            except (KeyError, TypeError, AttributeError, OverflowError) as exc:
                raise ContractError("Invalid model document") from exc
            if model.data["id"] != entry["id"] or entry["id"] in models:
                raise ContractError("Model identity/duplicate mismatch")
            models[entry["id"]] = model
        engine = cls(models,tenants=tenants)
        engine.registry_sha256 = registry_digest
        return engine

    @staticmethod
    def abstention(question, reason, model=None):
        return {"id":question["id"],"kind":question["kind"],"status":"abstained","value":None,
                "candidate_value":None,"distribution":[],"confidence":None,"calibrated_confidence":None,
                "uncertainty":{},"review_required":True,"reasons":[reason],
                "model":model.identity if model else None,"evidence_refs":[],"evidence_scope":"caller_supplied_unverified"}

    def decide(self, request, *, clock=time.monotonic):
        start = clock()
        validate_request(request)
        if request["tenant_id"] not in self.tenants:
            raise ContractError("Tenant not configured; body identity is not authentication")
        trace = str(uuid.uuid4())
        decisions = []
        budget = request.get("budget_ms",1000)/1000
        for q in request["questions"]:
            if clock()-start > budget:
                decisions.append(self.abstention(q,"deadline_exceeded")); continue
            if q["kind"] == "ABSTAIN":
                # No free-text request reason is copied to logs or modeled evidence.
                decisions.append(self.abstention(q,"caller_requested_abstention")); continue
            model = self.models.get(q["profile"])
            if model is None:
                decisions.append(self.abstention(q,"model_unavailable")); continue
            if request["language"] not in model.data["languages"]:
                decisions.append(self.abstention(q,"unsupported_language",model)); continue
            try:
                text = selected_text(request["state"],q["state_path"])
            except ContractError:
                decisions.append(self.abstention(q,"unsupported_state_path_or_size",model)); continue
            labels = model.data["labels"]
            if q["kind"] in ("CHOICE","ROUTE"):
                options = [(x["class_id"],x["id"],x["id"]) for x in q["choices"]]
                if len({x["id"] for x in q["choices"]}) != len(options):
                    raise ContractError("Duplicate choice IDs")
            elif q["kind"] == "BINARY":
                options = [(q["negative_class"],"false",False),(q["positive_class"],"true",True)]
            else:
                if not q["minimum"] < q["maximum"]:
                    raise ContractError("Score bounds must increase")
                values = [x["value"] for x in q["anchors"]]
                if len(set(values)) != len(values) or any(not q["minimum"] <= x <= q["maximum"] for x in values):
                    raise ContractError("Distinct anchors must lie inside score bounds")
                if q["value_type"] == "integer" and any(float(x).is_integer() is False for x in values+[q["minimum"],q["maximum"]]):
                    raise ContractError("Integer scores require integral anchors/bounds")
                options = [(x["class_id"],x["class_id"],x["value"]) for x in q["anchors"]]
            if hasattr(model,"supports"):
                labels = [c for c,_,_ in options]
                valid_classes = model.supports(labels)
            else:
                valid_classes = len(options) == len(labels) and len({x[0] for x in options}) == len(labels) and {x[0] for x in options} == set(labels)
            if not valid_classes:
                decisions.append(self.abstention(q,"unvalidated_class_set",model)); continue
            z,p = model.predict_subset(text, labels) if hasattr(model,"predict_subset") else model.predict(text)
            best = max(range(len(labels)),key=lambda i:p[i])
            ordered = sorted(p,reverse=True)
            margin = ordered[0]-ordered[1]
            coverage = model.coverage(text)
            reasons = policy(model.data,ordered[0],margin,coverage,request["language"])
            by_class = {c:(ident,value) for c,ident,value in options}
            uncertainty = {"entropy":-math.fsum(v*math.log(v) for v in p if v),"margin":margin,"lexical_coverage":coverage}
            if q["kind"] == "SCORE":
                mean = math.fsum(p[i]*by_class[c][1] for i,c in enumerate(labels))
                value = int(round(mean)) if q["value_type"] == "integer" else mean
                value = max(q["minimum"],min(q["maximum"],value))
                value = int(value) if q["value_type"] == "integer" else float(value)
                uncertainty["score_stddev"] = math.sqrt(math.fsum(p[i]*(by_class[c][1]-mean)**2 for i,c in enumerate(labels)))
            else:
                value = by_class[labels[best]][1]
            rows = {c:{"id":by_class[c][0],"class_id":c,"value":by_class[c][1],"logit":z[i],"probability":p[i]} for i,c in enumerate(labels)}
            decision = {"id":q["id"],"kind":q["kind"],"status":"abstained" if reasons else "answered",
                "value":None if reasons else value,"candidate_value":value,"distribution":[rows[c] for c,_,_ in options],
                "confidence":ordered[0],"calibrated_confidence":None,"uncertainty":uncertainty,
                "review_required":bool(reasons) or model.data["consequential"],"reasons":reasons,
                "model":model.identity,"evidence_refs":[s["id"] for s in request["sources"]],"evidence_scope":"caller_supplied_unverified"}
            decisions.append(decision)
        duration = max(0,(clock()-start)*1000)
        result = {"schema_version":"1.0","input_version":request["input_version"],"request_id":request["request_id"],
            "trace_id":trace,"tenant_id":request["tenant_id"],"timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "decisions":decisions,"authorization":"recommendation_only_no_action_authorized",
            "execution":{"engine_version":"0.2.0","duration_ms":duration,"network_used":False,"payload_logged":False}}
        validate_response(result)
        # Allowlisted event fields, all generated by engine. No request ID, tenant,
        # source URI, document, prompt, choices or model feature strings are logged.
        LOGGER.info(json.dumps({"event":"decision_batch","trace_id":trace,"count":len(decisions),
            "statuses":dict(collections.Counter(d["status"] for d in decisions)),"duration_ms":round(duration,3)},sort_keys=True))
        return result
