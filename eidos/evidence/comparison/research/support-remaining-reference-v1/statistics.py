"""Fixed-label metrics and paired template-component bootstrap, stdlib only."""
import collections,math,random


def macro(truth,predicted,labels,weights=None):
    weights=[1]*len(truth) if weights is None else weights
    actual=collections.Counter();pred=collections.Counter();tp=collections.Counter()
    for g,p,w in zip(truth,predicted,weights,strict=True):
        actual[g]+=w;pred[p]+=w
        if g==p:tp[g]+=w
    return math.fsum(2*tp[c]/(actual[c]+pred[c]) if actual[c]+pred[c] else 0. for c in labels)/len(labels)


def percentile(values,q):
    values=sorted(values);at=(len(values)-1)*q;lo=math.floor(at);hi=math.ceil(at)
    return values[lo]+(at-lo)*(values[hi]-values[lo])


def bootstrap(gold,left,right,labels,replicates=2000,seed=113):
    groups=collections.defaultdict(list)
    for i,row in enumerate(gold):groups[row['label'],row['group_hash']].append(i)
    strata=collections.defaultdict(list)
    for (label,_),indices in groups.items():
        truth=[0]*7;lp=[0]*7;lt=[0]*7;rp=[0]*7;rt=[0]*7;lc=rc=0
        for i in indices:
            g=gold[i]['label'];j=labels.index(g);truth[j]+=1
            if left[i] in labels:lp[labels.index(left[i])]+=1
            if right[i] in labels:rp[labels.index(right[i])]+=1
            if left[i]==g:lt[j]+=1;lc+=1
            if right[i]==g:rt[j]+=1;rc+=1
        strata[label].append([len(indices),lc,rc]+truth+lp+lt+rp+rt)
    rng=random.Random(seed);accuracy=[];f1=[]
    for _ in range(replicates):
        total=[0]*38
        for label in sorted(strata):
            values=strata[label]
            for _ in range(len(values)):
                chosen=values[rng.randrange(len(values))]
                for j,v in enumerate(chosen):total[j]+=v
        n,lc,rc=total[:3];truth=total[3:10];lp=total[10:17];lt=total[17:24];rp=total[24:31];rt=total[31:38]
        accuracy.append((lc-rc)/n)
        left_f=math.fsum(2*t/(a+p) if a+p else 0 for a,p,t in zip(truth,lp,lt))/7
        right_f=math.fsum(2*t/(a+p) if a+p else 0 for a,p,t in zip(truth,rp,rt))/7
        f1.append(left_f-right_f)
    return {'replicates':replicates,'seed':seed,'stratified_by_gold_label':True,'unit':'whole connected template/text components',
        'groups_per_label':{k:len(v) for k,v in strata.items()},
        'accuracy_difference_95_percentile':[percentile(accuracy,q) for q in (.025,.975)],
        'fixed_seven_macro_f1_difference_95_percentile':[percentile(f1,q) for q in (.025,.975)],
        'exchangeability_assumed_not_verified':True,'individual_approximate_intervals_not_joint_guarantees':True}
