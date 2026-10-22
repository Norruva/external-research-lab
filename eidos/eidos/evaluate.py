"""Train, calibrate, evaluate and retain EIDOS evidence without external data."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import statistics
import time

from .data import PROFILES, challenges, make_corpora, request
from .engine import Engine
from .metrics import calculate, grouped_accuracy_interval
from .model import calibrate, canonical, digest, nll, train


def write(path,value):
    path.write_text(json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+"\n")


def evaluate_model(model,examples,kind):
    labels=model.data["labels"]; rows=[]
    engine=Engine({model.data["id"]:model})
    for example in examples:
        decision=engine.decide(request(model.data["id"],example["text"],kind,language=example.get("language","en")))["decisions"][0]
        z,p=model.predict(example["text"])
        best=max(range(len(p)),key=lambda i:p[i]); target=labels.index(example["label"])
        rows.append({"id":example["id"],"group":example["phrase_family"],"target":example["label"],"probabilities":p,
            "nll":nll([z],[target],model.data["temperature"]),"answered":decision["status"]=="answered",
            "correct":labels[best]==example["label"],"decision":decision})
    metrics=calculate(rows,labels)
    metrics["accuracy_group_bootstrap95"]=grouped_accuracy_interval(rows)
    if kind=="SCORE":
        anchors={"low":0,"medium":50,"high":100}
        metrics["numeric_mae_all"]=sum(abs(r["decision"]["candidate_value"]-anchors[r["target"]]) for r in rows)/len(rows)
        metrics["numeric_semantics"]="mean of synthetic priority anchor mixture, not measured legal risk"
    return metrics,rows


def svg_chart(path,title,series,x_label,y_label):
    # Standalone SVG artifact, no browser/library/dependency needed.
    width,height=600,380; left,top,plotw,ploth=65,45,490,265
    def point(x,y):return f"{left+x*plotw:.2f},{top+(1-y)*ploth:.2f}"
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',f'<text x="65" y="25" font-size="16">{title}</text>',
           f'<path d="M{left} {top}V{top+ploth}H{left+plotw}" fill="none" stroke="#333"/>',
           f'<text x="220" y="360">{x_label}</text>',f'<text transform="translate(20,250) rotate(-90)">{y_label}</text>']
    for label,color,points in series:
        if points:lines.append(f'<polyline points="{" ".join(point(x,y) for x,y in points)}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x,y in points:
            px,py=point(x,y).split(',')
            lines.append(f'<circle cx="{px}" cy="{py}" r="3" fill="{color}"/>')
        lines.append(f'<text x="380" y="{30+18*series.index((label,color,points))}" fill="{color}" font-size="11">{label}</text>')
    lines.append('</svg>');path.write_text('\n'.join(lines)+'\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path("artifacts"))
    args=parser.parse_args(argv); output=args.output;output.mkdir(parents=True,exist_ok=True)
    corpora=make_corpora();write(output/"datasets.json",corpora)
    write(output/"split-manifest.json",{p:{s:{"n":len(rows),"sha256":digest(rows),"groups":len({r['phrase_family'] for r in rows})} for s,rows in parts.items()} for p,parts in corpora.items()})
    models={};before={};after={};calibration={};registry=[];determinism={};all_rows={}
    start=time.monotonic()
    for index,(profile,parts) in enumerate(corpora.items()):
        kind={"workflow-v1":"ROUTE","completeness-v1":"BINARY","priority-v1":"SCORE"}[profile]
        model=train(parts["train"],PROFILES[profile],seed=17+index)
        before[profile],_=evaluate_model(model,parts["test"],kind)
        calibration[profile]=calibrate(model,parts["calibration"])
        after[profile],rows=evaluate_model(model,parts["test"],kind);all_rows[profile]=rows
        # Trace IDs/timestamps/latency are deliberately excluded from semantic
        # reproducibility: compare model content + public decision values/probs.
        reproduced=train(parts["train"],PROFILES[profile],seed=17+index)
        calibrate(reproduced,parts["calibration"])
        determinism[profile]=canonical(model.data)==canonical(reproduced.data)
        assert determinism[profile]
        filename=profile+".json";filehash=model.save(output/filename)
        registry.append({"id":profile,"file":filename,"sha256":filehash})
        models[profile]=model
        write(output/(profile+"-predictions.json"),rows)
        reliability=after[profile]["reliability"]
        svg_chart(output/(profile+"-reliability.svg"),profile+" synthetic reliability",[("ideal","#999",[(0,0),(1,1)]),("test","#175ecc",[(b["confidence"],b["accuracy"]) for b in reliability if b["n"]])],"mean confidence","observed accuracy")
        risk=after[profile]["risk_coverage"]
        svg_chart(output/(profile+"-risk-coverage.svg"),profile+" synthetic confidence sweep",[("test","#a53535",sorted((r["coverage"],r["risk"]) for r in risk if r["risk"] is not None))],"coverage","observed error risk")
    write(output/"registry.json",{"format":"eidos-registry-v1","models":registry})
    engine=Engine.from_registry(output)
    challenge_results=[]
    for item in challenges():
        decision=engine.decide(request("workflow-v1",item["text"],"ROUTE",language=item["language"]))["decisions"][0]
        best=max(decision["distribution"],key=lambda r:r["probability"])["class_id"] if decision["distribution"] else None
        challenge_results.append({**item,"top_class":best,"correct_if_labeled":best==item["label"] if item["label"] and best is not None else None,"decision":decision})
    # Class imbalance is an independently labeled stress set, not production priors.
    workflow=corpora["workflow-v1"]
    imbalance=[r for r in workflow["train"] if r["label"]=="regulatory" or int(r["id"].rsplit('-',1)[-1])<3]
    skewed=train(imbalance,PROFILES["workflow-v1"],seed=71,balance=False)
    balanced=train(imbalance,PROFILES["workflow-v1"],seed=71,balance=True)
    imbalance_metrics={"train_counts":{label:sum(r["label"]==label for r in imbalance) for label in skewed.data["labels"]},
        "unweighted":evaluate_model(skewed,workflow["test"],"ROUTE")[0],"inverse_frequency":evaluate_model(balanced,workflow["test"],"ROUTE")[0]}
    # CPU end-to-end latency includes schema/policy/model hashing/metadata, warmed CLI-free.
    demo=request("workflow-v1","regulatory requirement statutory compliance notice","ROUTE")
    for _ in range(10):engine.decide(demo)
    latencies=[]
    for _ in range(200):
        t=time.perf_counter_ns();engine.decide(demo);latencies.append((time.perf_counter_ns()-t)/1e6)
    latencies.sort()
    performance={"n":len(latencies),"p50_ms":statistics.median(latencies),"p95_ms":latencies[int(.95*len(latencies))-1],
        "serial_requests_per_second":1000/statistics.mean(latencies),"max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "elapsed_training_evaluation_seconds":time.monotonic()-start,"runtime":platform.python_version(),"platform":platform.platform(),
        "caveat":"single process warm synthetic request; maxRSS covers whole train/eval process, not isolated per-model resident memory"}
    # Explicit instance counts distinguish sample multiplicity from few phrase families.
    results={"experiment":"EIDOS original synthetic supervised proof of concept","before_temperature":before,"after_temperature":after,
        "calibration":calibration,"deterministic_model_content":determinism,"challenge_results":challenge_results,
        "class_imbalance":imbalance_metrics,"performance":performance,
        "limitations":["Authored synthetic text only; repeated phrase families reduce independent sample size.",
        "Train/calibration/test differ in entity/template/phrase family; lexical task vocabulary intentionally overlaps.",
        "No primary external benchmark, pretrained semantic encoder, real regulatory validation or general multilingual claim.",
        "Wilson intervals treat rows as independent; grouped-bootstrap intervals are also provided and cannot repair synthetic selection bias.",
        "Confidence gates are heuristic and can accept OOD or negation errors; no distribution-free guarantee.",
        "Test risk-coverage curves are descriptive and were not used to tune thresholds."]}
    write(output/"results.json",results);write(output/"challenge-results.json",challenge_results)
    write(output/"example-request.json",demo);write(output/"example-response.json",engine.decide(demo))
    modelcard={"name":"EIDOS sparse synthetic profiles","version":"0.1.0","training":"original Unicode word/bigram TF-IDF and seeded SGD from initialization",
        "pretrained_components":[],"datasets":"original authored synthetic corpus; no external outputs/standards/private data",
        "intended_use":"offline research and interface demonstration only","not_validated_for":["legal determinations","production automation","cross-language semantics","arbitrary zero-shot taxonomies"],
        "model_lineage":registry,"calibration_claim":"temperature fitted on synthetic calibration; no production calibrated-confidence field claimed",
        "metrics_file":"results.json","data_manifest":"split-manifest.json","dependencies":"Python standard library"}
    write(output/"model-card.json",modelcard)
    print(json.dumps({"profiles":{p:{k:after[p][k] for k in ["n","accuracy","macro_f1","coverage","selective_accuracy","nll","ece"]} for p in models},
        "determinism":determinism,"performance":performance,"challenges":[{"category":r["category"],"status":r["decision"]["status"],"correct":r["correct_if_labeled"]} for r in challenge_results]},indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
