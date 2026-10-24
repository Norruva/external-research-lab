"""Original TF-IDF + multiclass SGD model, trained entirely from initialization."""
import collections
import hashlib
import json
import math
import random
import re
import unicodedata
from pathlib import Path

from .contracts import ContractError, finite_json, parse_json

FEATURE_VERSION = "unicode-word-bigram-tfidf-v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def words(text):
    if not isinstance(text, str) or len(text) > 12000:
        raise ContractError("Selected input must be text of at most12000characters; no truncation")
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def terms(text):
    tokens = words(text)
    return ["u:" + word for word in tokens] + ["b:" + a + " " + b for a, b in zip(tokens, tokens[1:])]


def softmax(logits, temperature=1.0):
    maximum = max(logits)
    values = [math.exp((x - maximum) / temperature) for x in logits]
    total = math.fsum(values)
    return [x / total for x in values]


class Model:
    def __init__(self, data):
        self.data = data
        self.index = {term: i for i, term in enumerate(data["vocabulary"])}

    @property
    def identity(self):
        return {"id": self.data["id"], "version": self.data["version"],
                "sha256": digest(self.data), "feature_version": FEATURE_VERSION,
                "calibration_status": "temperature_fitted_synthetic_only" if self.data["calibration"] else "unfitted"}

    def vector(self, text):
        counts = collections.Counter(terms(text))
        raw = {self.index[t]: math.log1p(n) * self.data["idf"][self.index[t]] for t, n in counts.items() if t in self.index}
        norm = math.sqrt(math.fsum(v*v for v in raw.values()))
        return {i: v / norm for i, v in raw.items()} if norm else {}

    def coverage(self, text):
        tokens = words(text)
        return sum("u:" + word in self.index for word in tokens) / len(tokens) if tokens else 0.0

    def logits(self, text):
        vector = self.vector(text)
        return [bias + math.fsum(row[i]*v for i, v in vector.items())
                for row, bias in zip(self.data["weights"], self.data["bias"])]

    def predict(self, text):
        z = self.logits(text)
        return z, softmax(z, self.data["temperature"])

    def save(self, path):
        encoded = json.dumps(self.data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if len(encoded.encode()) > 5_000_000:
            raise ContractError("Serialized model exceeds the5MBartifact budget")
        Path(path).write_text(encoded)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def load(cls, path, sha256):
        if Path(path).stat().st_size > 12_000_000:
            raise ContractError("Model exceeds size budget")
        payload = Path(path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise ContractError("Model size/integrity check failed")
        d = parse_json(payload.decode(), maximum=12_000_000)
        if isinstance(d,dict) and d.get("format") == "eidos-support-memory-v1":
            from .support_memory import SupportMemory
            return SupportMemory.load(path, sha256)
        if isinstance(d,dict) and d.get("format") == "eidos-linear-general-v1":
            from .general_linear import LinearModel
            return LinearModel.load(path, sha256)
        if isinstance(d,dict) and d.get("format") == "eidos-linear-v2":
            from .linear import LinearModel
            return LinearModel.load(path, sha256)
        if len(payload) > 5_000_000:
            raise ContractError("V1 model size budget exceeded")
        finite_json(d, max_nodes=500000, max_text=1_000_000)
        expected = {"format","id","version","feature_version","labels","vocabulary","idf","weights","bias","temperature","training","calibration","languages","domain","consequential","thresholds"}
        if not isinstance(d, dict) or set(d) != expected or d["format"] != "eidos-sparse-v1" or d["feature_version"] != FEATURE_VERSION:
            raise ContractError("Unsupported model format")
        k, n = len(d["labels"]), len(d["vocabulary"])
        if not 2 <= k <= 64 or not 1 <= n <= 5000 or len(set(d["labels"])) != k or len(set(d["vocabulary"])) != n:
            raise ContractError("Invalid model dimensions")
        if len(d["weights"]) != k or len(d["bias"]) != k or len(d["idf"]) != n or any(len(row) != n for row in d["weights"]):
            raise ContractError("Model matrix shape mismatch")
        nums = d["idf"] + d["bias"] + [v for row in d["weights"] for v in row] + [d["temperature"]]
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in nums):
            raise ContractError("Invalid model numbers")
        if not .25 <= d["temperature"] <= 4 or any(v <= 0 for v in d["idf"]):
            raise ContractError("Invalid model scale")
        if not isinstance(d["consequential"], bool) or not d["languages"] or any(not isinstance(x,str) or not x for x in d["labels"]+d["vocabulary"]+d["languages"]):
            raise ContractError("Invalid model metadata")
        if set(d["thresholds"]) != {"probability","margin","coverage"} or any(type(v) not in (int,float) or not 0<=v<=1 for v in d["thresholds"].values()):
            raise ContractError("Invalid model thresholds")
        return cls(d)


def train(examples, profile, seed=17, epochs=45, rate=.4, l2=.001, balance=True):
    if not isinstance(examples,(list,tuple)) or not 1 <= len(examples) <= 20000:
        raise ContractError("Training requires a bounded nonempty example list")
    if type(seed) is not int or type(epochs) is not int or not 1 <= epochs <= 200 or type(balance) is not bool:
        raise ContractError("Invalid seed/epochs/balancing configuration")
    if type(rate) not in (int,float) or type(l2) not in (int,float) or not math.isfinite(rate) or not math.isfinite(l2) or not 0 < rate <= 1 or not 0 <= l2 <= .1:
        raise ContractError("Invalid training data/config")
    if any(not isinstance(row,dict) or not isinstance(row.get("text"),str) or not isinstance(row.get("label"),str) or not row["label"].strip() or len(row["label"])>256 for row in examples):
        raise ContractError("Training rows require text and nonempty string labels")
    if not isinstance(profile,dict) or set(profile)!={"id","languages","domain","consequential"} or type(profile["consequential"]) is not bool or not isinstance(profile["languages"],list) or not profile["languages"] or any(not isinstance(x,str) or not x.strip() for x in [profile["id"],profile["domain"],*profile["languages"]]):
        raise ContractError("Invalid profile metadata")
    labels = sorted({row["label"] for row in examples})
    if not 2 <= len(labels) <= 64:
        raise ContractError("Between two and64classes required")
    counts = collections.Counter(row["label"] for row in examples)
    df = collections.Counter(term for row in examples for term in set(terms(row["text"])))
    vocabulary = sorted(df)
    if not 1 <= len(vocabulary) <= 5000:
        raise ContractError("Training vocabulary outside supported bounds")
    rng = random.Random(seed)
    data = {"format":"eidos-sparse-v1","id":profile["id"],"version":"1.0.0","feature_version":FEATURE_VERSION,
            "labels":labels,"vocabulary":vocabulary,"idf":[math.log((1+len(examples))/(1+df[t]))+1 for t in vocabulary],
            "weights":[[rng.uniform(-.01,.01) for _ in vocabulary] for _ in labels],"bias":[0.0]*len(labels),
            "temperature":1.0,"training":{"seed":seed,"epochs":epochs,"rate":rate,"l2":l2,"class_balance":balance,"n":len(examples),"dataset_sha256":digest(examples)},
            "calibration":None,"languages":profile["languages"],"domain":profile["domain"],"consequential":profile["consequential"],
            "thresholds":{"probability":.65,"margin":.15,"coverage":.35}}
    model = Model(data)
    prepared = [(model.vector(row["text"]), labels.index(row["label"]), row["label"]) for row in examples]
    order = list(range(len(prepared)))
    for epoch in range(epochs):
        rng.shuffle(order)
        step = rate / math.sqrt(1+epoch/10)
        for index in order:
            x, y, label = prepared[index]
            z = [b+math.fsum(w[i]*v for i,v in x.items()) for w,b in zip(data["weights"],data["bias"])]
            p = softmax(z)
            sample_weight = len(examples)/(len(labels)*counts[label]) if balance else 1.0
            for k, row in enumerate(data["weights"]):
                delta = sample_weight*(p[k]-float(k==y))
                for i,v in x.items():
                    row[i] -= step*delta*v
                data["bias"][k] -= step*delta
        for row in data["weights"]:
            for i in range(len(row)):
                row[i] *= 1-step*l2
    return model


def nll(logits, targets, temperature):
    values = []
    for z,y in zip(logits,targets):
        maximum = max(z)
        centered = [(v-maximum)/temperature for v in z]
        values.append(math.log(math.fsum(math.exp(v) for v in centered))-centered[y])
    return math.fsum(values)/len(values)


def calibrate(model, examples):
    if not examples or any(row["label"] not in model.data["labels"] for row in examples):
        raise ContractError("Invalid calibration data")
    z = [model.logits(row["text"]) for row in examples]
    y = [model.data["labels"].index(row["label"]) for row in examples]
    grid = sorted({1.0, .25, 4.0, *(math.exp(math.log(.25)+i*math.log(16)/80) for i in range(81))})
    temperature = min(grid,key=lambda t:(nll(z,y,t),abs(math.log(t))))
    temperature = min(4,max(.25,temperature))
    result = {"n":len(examples),"dataset_sha256":digest(examples),"nll_before":nll(z,y,1),"nll_after":nll(z,y,temperature),"temperature":temperature,"scope":"synthetic_only_not_production_validated"}
    model.data["temperature"] = temperature
    model.data["calibration"] = result
    return result
