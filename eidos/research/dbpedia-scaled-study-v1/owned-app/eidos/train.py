"""Train a new owned profile from explicitly supplied, rights-cleared JSON data."""
import argparse
import json
import math
from pathlib import Path
import re
import sys
import unicodedata
from .contracts import ContractError, parse_json
from .metrics import calculate
from .model import calibrate, digest, nll, train


def validate_splits(parts):
    if not isinstance(parts,dict) or set(parts)!={"train","calibration","test"}:
        raise ContractError("Data must contain exactly train/calibration/test splits")
    for name,rows in parts.items():
        if not isinstance(rows,list) or not rows:
            raise ContractError("Every split must be a nonempty list")
        for row in rows:
            if not isinstance(row,dict) or not {"id","group_id","text","label"}<=set(row) or any(not isinstance(row[x],str) or not row[x].strip() for x in ["id","group_id","text","label"]):
                raise ContractError("Rows require nonempty id/group_id/text/label strings")
        if len({r["id"] for r in rows})!=len(rows):
            raise ContractError("Duplicate record IDs within split")
    for a,b in [("train","calibration"),("train","test"),("calibration","test")]:
        for field in ["id","group_id"]:
            if {r[field] for r in parts[a]} & {r[field] for r in parts[b]}:
                raise ContractError("Group/record leakage across splits")
        norm=lambda text:" ".join(unicodedata.normalize("NFKC",text).casefold().split())
        if {norm(r["text"]) for r in parts[a]} & {norm(r["text"]) for r in parts[b]}:
            raise ContractError("Normalized exact text leakage across splits")
    labels={r["label"] for r in parts["train"]}
    if any({r["label"] for r in rows}!=labels for rows in parts.values()):
        raise ContractError("Every split must cover exactly the trained labels")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,required=True)
    parser.add_argument("--profile",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--seed",type=int,default=17)
    args=parser.parse_args(argv)
    try:
        with args.data.open() as handle:
            parts=parse_json(handle.read(5_000_001),maximum=5_000_000)
        validate_splits(parts)
        with args.profile.open() as handle:
            profile=parse_json(handle.read(128001))
        if not isinstance(profile,dict) or not isinstance(profile.get("id"),str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}",profile["id"]):
            raise ContractError("Profile id must be a short portable identifier")
        if args.output.exists() and any(args.output.iterdir()):
            raise ContractError("Training output must be new or empty; existing artifacts preserved")
        model=train(parts["train"],profile,seed=args.seed)
        calibration=calibrate(model,parts["calibration"])
        measured=[]
        for row in parts["test"]:
            z,p=model.predict(row["text"])
            ordered=sorted(p,reverse=True);coverage=model.coverage(row["text"])
            threshold=model.data["thresholds"]
            measured.append({"target":row["label"],"probabilities":p,"nll":nll([z],[model.data["labels"].index(row["label"])],model.data["temperature"]),
                "answered":coverage>=threshold["coverage"] and ordered[0]>=threshold["probability"] and ordered[0]-ordered[1]>=threshold["margin"]})
        report={"profile":profile,"splits":{k:{"n":len(v),"sha256":digest(v)} for k,v in parts.items()},"calibration":calibration,
                "test":calculate(measured,model.data["labels"]),"claim":"Caller-supplied data validation only; no production or data-rights certification"}
        args.output.mkdir(parents=True,exist_ok=True)
        sha=model.save(args.output/"model.json")
        registry={"format":"eidos-registry-v1","models":[{"id":profile["id"],"file":"model.json","sha256":sha}]}
        for name,data in [("registry.json",registry),("training-report.json",report)]:
            (args.output/name).write_text(json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+"\n")
        print(json.dumps({"profile":profile["id"],"model_file_sha256":sha,"test_n":len(parts["test"]),"test_accuracy":report["test"]["accuracy"]}))
        return 0
    except (ContractError,OSError,UnicodeError) as exc:
        print(json.dumps({"error":{"code":"training_input_error","message":str(exc)}}),file=sys.stderr)
        return 2


if __name__=="__main__":raise SystemExit(main())
