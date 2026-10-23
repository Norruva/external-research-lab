"""Evaluation from public probabilities and labels, separate from model features."""
import math
import random


def wilson(correct,n,z=1.959963984540054):
    if n==0:
        return None
    p=correct/n; denominator=1+z*z/n
    center=(p+z*z/(2*n))/denominator
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denominator
    return [max(0,center-radius),min(1,center+radius)]


def calculate(rows,labels):
    n=len(rows); matrix=[[0 for _ in labels] for _ in labels]
    nll=[]; brier=[]; bins=[[] for _ in range(10)]
    accepted=[]; points=[]
    for row in rows:
        y=labels.index(row["target"]); probs=row["probabilities"]
        best=labels.index(row["prediction"]) if "prediction" in row else max(range(len(probs)),key=lambda i:probs[i])
        conf=probs[best]
        correct=int(best==y); matrix[y][best]+=1
        nll.append(row["nll"])
        brier.append(math.fsum((p-float(i==y))**2 for i,p in enumerate(probs)))
        bins[min(9,int(conf*10))].append((conf,correct))
        points.append((conf,correct))
        if row["answered"]:accepted.append(correct)
    per_class=[]
    for i,label in enumerate(labels):
        tp=matrix[i][i]; support=sum(matrix[i]); predicted=sum(r[i] for r in matrix)
        precision=tp/predicted if predicted else 0; recall=tp/support if support else 0
        f1=2*precision*recall/(precision+recall) if precision+recall else 0
        per_class.append({"label":label,"precision":precision,"recall":recall,"f1":f1,"support":support,
            "false_positive":predicted-tp,"false_negative":support-tp})
    reliability=[{"lower":i/10,"upper":(i+1)/10,"n":len(bucket),
                  "confidence":math.fsum(p for p,_ in bucket)/len(bucket) if bucket else None,
                  "accuracy":sum(c for _,c in bucket)/len(bucket) if bucket else None}
                 for i,bucket in enumerate(bins)]
    risk=[]
    for threshold in [i/20 for i in range(21)]:
        kept=[correct for conf,correct in points if conf>=threshold]
        risk.append({"threshold":threshold,"accepted":len(kept),"coverage":len(kept)/n,
            "risk":1-sum(kept)/len(kept) if kept else None,"accuracy_wilson95":wilson(sum(kept),len(kept))})
    correct=sum(matrix[i][i] for i in range(len(labels)))
    return {"n":n,"accuracy":correct/n,"accuracy_wilson95":wilson(correct,n),
        "macro_f1":sum(r["f1"] for r in per_class)/len(labels),
        "weighted_f1":sum(r["f1"]*r["support"] for r in per_class)/n,
        "per_class":per_class,"confusion_matrix":{"labels":labels,"counts":matrix},
        "nll":math.fsum(nll)/n,"brier":math.fsum(brier)/n,
        "ece":math.fsum(len(b)/n*abs(sum(p for p,_ in b)/len(b)-sum(c for _,c in b)/len(b)) for b in bins if b),
        "coverage":len(accepted)/n,"selective_accuracy":sum(accepted)/len(accepted) if accepted else None,
        "selective_accuracy_wilson95":wilson(sum(accepted),len(accepted)),
        "reliability":reliability,"risk_coverage":risk}


def grouped_accuracy_interval(rows,seed=17,draws=1000):
    """Bootstrap phrase families, acknowledging synthetic within-family dependence."""
    groups={}
    for row in rows:groups.setdefault(row["group"],[]).append(int(row["correct"]))
    keys=sorted(groups); rng=random.Random(seed); samples=[]
    for _ in range(draws):
        values=[v for key in rng.choices(keys,k=len(keys)) for v in groups[key]]
        samples.append(sum(values)/len(values))
    samples.sort()
    return {"groups":len(keys),"draws":draws,"interval95":[samples[int(.025*draws)],samples[min(draws-1,int(.975*draws))]],
            "warning":"Few synthetic template groups; not a real-world population confidence guarantee"}
