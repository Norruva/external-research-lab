"""Versioned general-inventory safe linear runtime, copied from owned linear.py.
Only import paths, format tag, label cap and count digit bound differ.
Original SHA256: 81c65cf5105cf8b986de39a6eb59f4147e1acb62c0ec6918ce924b854aef7e36.
"""
from array import array
import collections
import hashlib
import math
from pathlib import Path
import re
import struct
import sys
import unicodedata

from eidos.contracts import ContractError, finite_json, parse_json
from eidos.model import canonical, softmax, words

FEATURES = "unicode-word-bigram-char345-tfidf-v2"
MAGIC = b"EIDOSW2\x00"
MAX_META = 12_000_000
MAX_WEIGHTS = 16_000_000


def terms(text):
    tokens = words(text)
    result = ["u:" + token for token in tokens]
    result += ["b:" + a + " " + b for a, b in zip(tokens, tokens[1:])]
    normalized = " " + " ".join(tokens) + " "
    for size in (3, 4, 5):
        result.extend("c:" + normalized[i:i+size] for i in range(len(normalized)-size+1))
    return result


class LinearModel:
    def __init__(self, data, weights):
        self.data = data
        self.weights = weights
        self.index = {term: i for i, term in enumerate(data["vocabulary"])}
        self.label_index = {label: i for i, label in enumerate(data["labels"])}
        self.identity = {"id": data["id"], "version": data["version"], "sha256": hashlib.sha256(canonical(data)).hexdigest(),
                         "feature_version": FEATURES,
                         "calibration_status": "temperature_fitted_heldout_scope_only" if data["calibration"] else "unfitted"}

    def vector(self, text):
        counts = collections.Counter(terms(text))
        raw = {self.index[t]: math.log1p(n) * self.data["idf"][self.index[t]] for t, n in counts.items() if t in self.index}
        norm = math.sqrt(math.fsum(v*v for v in raw.values()))
        return {i: value/norm for i, value in raw.items()} if norm else {}

    def coverage(self, text):
        tokens = words(text)
        return sum("u:" + word in self.index for word in tokens) / len(tokens) if tokens else 0.0

    def logits(self, text, labels=None):
        labels = self.data["labels"] if labels is None else labels
        if len(set(labels)) != len(labels) or not set(labels) <= set(self.label_index) or len(labels) < 2:
            raise ContractError("Invalid candidate labels")
        vector = self.vector(text)
        width = len(self.index)
        bias_offset = width * len(self.label_index)
        return [self.weights[bias_offset + self.label_index[label]] +
                math.fsum(self.weights[self.label_index[label]*width+i]*v for i,v in vector.items()) for label in labels]

    def supports(self, labels):
        return len(set(labels)) == len(labels) and set(labels) <= set(self.label_index) and str(len(labels)) in self.data["temperatures"]

    def predict_subset(self, text, labels):
        if not self.supports(labels):
            raise ContractError("Candidate count/calibration regime not supported")
        z = self.logits(text, labels)
        return z, softmax(z, self.data["temperatures"][str(len(labels))])

    def predict(self, text):
        return self.predict_subset(text, self.data["labels"])

    @classmethod
    def load(cls, path, digest):
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > MAX_META:
            raise ContractError("Invalid linear metadata file")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ContractError("Linear metadata integrity failure")
        data = parse_json(payload.decode(), maximum=MAX_META)
        finite_json(data, max_nodes=250000, max_text=8_000_000)
        expected = {"format", "id", "version", "feature_version", "labels", "vocabulary", "idf", "weights_file", "weights_sha256",
                    "temperatures", "training", "calibration", "languages", "domain", "consequential", "thresholds"}
        if not isinstance(data, dict) or set(data) != expected or data["format"] != "eidos-linear-general-v1" or data["feature_version"] != FEATURES:
            raise ContractError("Unsupported linear format")
        labels, vocabulary, idf = data["labels"], data["vocabulary"], data["idf"]
        if not isinstance(labels,list) or not isinstance(vocabulary,list) or not isinstance(idf,list):
            raise ContractError("Invalid dimensions")
        k, n = len(labels), len(vocabulary)
        if not 2 <= k <= 256 or not 1 <= n <= 50000 or len(set(labels)) != k or len(set(vocabulary)) != n or len(idf) != n:
            raise ContractError("Invalid linear dimensions")
        if any(not isinstance(x,str) or not x or len(x)>512 for x in labels+vocabulary+data["languages"]):
            raise ContractError("Invalid linear metadata strings")
        if not data["languages"] or type(data["consequential"]) is not bool:
            raise ContractError("Invalid linear policy")
        if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<v<=1e6 for v in idf):
            raise ContractError("Invalid IDF")
        if set(data["thresholds"]) != {"probability","margin","coverage"} or any(type(v) not in (float,int) or not 0<=v<=1 for v in data["thresholds"].values()):
            raise ContractError("Invalid linear thresholds")
        if not isinstance(data["temperatures"],dict) or not data["temperatures"] or str(k) not in data["temperatures"]:
            raise ContractError("Missing candidate count regimes")
        for count, temperature in data["temperatures"].items():
            if not re.fullmatch(r"[0-9]{1,3}",count) or not 2<=int(count)<=k or str(int(count)) != count or type(temperature) not in (int,float) or not math.isfinite(temperature) or not .25<=temperature<=4:
                raise ContractError("Invalid candidate count temperature")
        filename = data["weights_file"]
        if not isinstance(filename,str) or Path(filename).name != filename or filename in (".",".."):
            raise ContractError("Invalid weights path")
        weights_path = path.parent / filename
        expected_size = 16 + 4 * (k*n+k)
        if weights_path.is_symlink() or expected_size>MAX_WEIGHTS or weights_path.stat().st_size != expected_size:
            raise ContractError("Invalid weight size/path")
        blob = weights_path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != data["weights_sha256"] or blob[:8] != MAGIC or struct.unpack("<II",blob[8:16]) != (k,n):
            raise ContractError("Weights integrity/header failure")
        weights = array("f"); weights.frombytes(blob[16:])
        if sys.byteorder != "little":
            weights.byteswap()
        if any(not math.isfinite(x) or abs(x)>1e6 for x in weights):
            raise ContractError("Invalid linear weights")
        return cls(data,weights)


def write_model(directory, data, weights):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ContractError("Refusing to overwrite nonempty model directory")
    directory.mkdir(parents=True,exist_ok=True)
    data = dict(data)
    k, n = len(data["labels"]),len(data["vocabulary"])
    if len(weights) != k*n+k:
        raise ContractError("Export shape mismatch")
    values = array("f",weights)
    if sys.byteorder != "little":
        values.byteswap()
    blob = MAGIC + struct.pack("<II",k,n) + values.tobytes()
    data["weights_file"] = "weights.f32"
    data["weights_sha256"] = hashlib.sha256(blob).hexdigest()
    metadata = canonical(data)
    if len(metadata)>MAX_META or len(blob)>MAX_WEIGHTS:
        raise ContractError("Export budget exceeded")
    (directory/"weights.f32").write_bytes(blob)
    digest = hashlib.sha256(metadata).hexdigest()
    (directory/"model.json").write_bytes(metadata)
    # Validate the exported bytes before making a loadable registry.
    cls = LinearModel.load(directory/"model.json",digest)
    registry = {"format":"eidos-registry-v1","models":[{"id":data["id"],"file":"model.json","sha256":digest}]}
    (directory/"registry.json").write_bytes(canonical(registry))
    return cls
