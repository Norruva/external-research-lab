"""Original sparse support-set classifier: bounded stdlib-only local evidence."""
from array import array
import collections
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys

from .contracts import ContractError,finite_json,parse_json
from .general_linear import FEATURES,terms
from .model import canonical,softmax,words

MAGIC=b'EIDOSM1\x00'
MAX_META=12_000_000
MAX_BINARY=16_000_000
FIELDS={'format','id','version','feature_version','labels','vocabulary','idf','weights_file','weights_sha256',
        'temperatures','training','calibration','languages','domain','consequential','thresholds',
        'rows','nnz','top_k','scale','support_per_class','support_ids'}


def read_regular(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 1<=info.st_size<=maximum:
            raise ContractError('Invalid support artifact file/size')
        blob=stream.read(maximum+1)
    if len(blob)>maximum:raise ContractError('Support artifact exceeds bound')
    return blob


def finite(value,low,high):return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def top_score(similarities,scale=10.,top_k=5):
    if type(top_k)is not int or top_k!=5 or not isinstance(similarities,list) or len(similarities)<top_k or scale!=10.:
        raise ContractError('Invalid support aggregation')
    if any(not finite(v,0.,1.00001) for v in similarities):raise ContractError('Invalid support similarity')
    top=sorted(similarities,reverse=True)[:top_k];peak=scale*top[0]
    return peak+math.log(math.fsum(math.exp(scale*v-peak) for v in top)/top_k)


class SupportMemory:
    def __init__(self,data,offsets,classes,indices,values):
        self.data=data;self.offsets=offsets;self.classes=classes;self.indices=indices;self.values=values
        self.index={t:i for i,t in enumerate(data['vocabulary'])};self.label_index={v:i for i,v in enumerate(data['labels'])}
        self.class_rows=[[] for _ in data['labels']];self.postings=collections.defaultdict(list)
        for row,c in enumerate(classes):
            self.class_rows[c].append(row)
            for at in range(offsets[row],offsets[row+1]):self.postings[indices[at]].append((row,values[at]))
        self.identity={'id':data['id'],'version':data['version'],'sha256':hashlib.sha256(canonical(data)).hexdigest(),
            'feature_version':FEATURES,'calibration_status':'unfitted'}

    def vector(self,text):
        counts=collections.Counter(terms(text))
        raw={self.index[t]:math.log1p(n)*self.data['idf'][self.index[t]] for t,n in counts.items() if t in self.index}
        norm=math.sqrt(math.fsum(v*v for v in raw.values()))
        return {i:v/norm for i,v in raw.items()} if norm else {}

    def coverage(self,text):
        tokens=words(text)
        return sum('u:'+t in self.index for t in tokens)/len(tokens) if tokens else 0.

    def similarities(self,text):
        result=[0.]*self.data['rows']
        for index,value in sorted(self.vector(text).items()):
            for row,weight in self.postings.get(index,()):result[row]+=value*weight
        return result

    def supports(self,labels):
        return len(labels)==len(set(labels)) and set(labels)<=set(self.label_index) and str(len(labels)) in self.data['temperatures']

    def logits(self,text,labels=None):
        labels=self.data['labels'] if labels is None else labels
        if not self.supports(labels):raise ContractError('Unsupported support candidate set')
        similarities=self.similarities(text)
        return [top_score([similarities[r] for r in self.class_rows[self.label_index[label]]]) for label in labels]

    def predict_subset(self,text,labels):
        z=self.logits(text,labels)
        return z,softmax(z,self.data['temperatures'][str(len(labels))])

    def predict(self,text):return self.predict_subset(text,self.data['labels'])

    def evidence(self,text,labels,maximum_classes=3):
        if not self.supports(labels) or type(maximum_classes)is not int or not 1<=maximum_classes<=3:
            raise ContractError('Unsupported support evidence request')
        similarity=self.similarities(text);scores={label:top_score([similarity[r] for r in self.class_rows[self.label_index[label]]]) for label in labels}
        result=[]
        for label in sorted(labels,key=lambda v:(-scores[v],v))[:maximum_classes]:
            chosen=sorted(self.class_rows[self.label_index[label]],key=lambda r:(-similarity[r],self.data['support_ids'][r]))[:5]
            result.append({'class_id':label,'score':scores[label],'neighbors':[{'support_id':self.data['support_ids'][r],'cosine':similarity[r]} for r in chosen],
                'meaning':'nearest support similarities, not a truth or correctness explanation'})
        return result

    @classmethod
    def load(cls,path,expected):
        if type(expected)is not str or re.fullmatch('[0-9a-f]{64}',expected)is None:raise ContractError('Invalid support digest')
        path=Path(path);payload=read_regular(path,MAX_META)
        if hashlib.sha256(payload).hexdigest()!=expected:raise ContractError('Support metadata integrity failure')
        d=parse_json(payload.decode(),maximum=MAX_META);finite_json(d,max_nodes=250000,max_text=8_000_000)
        if type(d)is not dict or set(d)!=FIELDS or d['format']!='eidos-support-memory-v1' or d['feature_version']!=FEATURES:
            raise ContractError('Unsupported support format')
        if any(type(d[key])is not str or not 1<=len(d[key])<=1000 for key in ('id','version','domain')) or type(d['training'])is not dict:
            raise ContractError('Invalid support identity/provenance')
        labels=d['labels'];vocab=d['vocabulary'];idf=d['idf'];rows=d['rows'];nnz=d['nnz']
        if not isinstance(labels,list) or not isinstance(vocab,list) or not isinstance(idf,list):raise ContractError('Invalid support dimensions')
        k,n=len(labels),len(vocab)
        if any(type(x)is not str or not 1<=len(x)<=512 for x in labels+vocab):raise ContractError('Invalid support strings')
        if not 2<=k<=256 or not 1<=n<=10000 or len(idf)!=n or len(set(labels))!=k or len(set(vocab))!=n:
            raise ContractError('Invalid support dimensions')
        if any(not finite(x,1e-12,1e6) for x in idf):raise ContractError('Invalid support IDF')
        if type(rows)is not int or rows!=20*k or type(nnz)is not int or not rows<=nnz<=2_000_000:
            raise ContractError('Invalid support count')
        if type(d['support_per_class'])is not int or d['support_per_class']!=20 or type(d['top_k'])is not int or d['top_k']!=5 or not finite(d['scale'],10.,10.):
            raise ContractError('Invalid support policy')
        ids=d['support_ids']
        if not isinstance(ids,list) or len(ids)!=rows or any(type(v)is not str or re.fullmatch('support-[0-9a-f]{32}',v)is None for v in ids) or len(set(ids))!=rows:
            raise ContractError('Invalid support IDs')
        if d['calibration'] is not None or d['languages']!=['en'] or d['consequential'] is not True:
            raise ContractError('Invalid support scope/authority')
        if d['thresholds']!={'probability':1.,'margin':1.,'coverage':.35} or any(type(v) not in (int,float) for v in d['thresholds'].values()) or type(d['temperatures'])is not dict:
            raise ContractError('Support acceptance must stay disabled')
        if str(k) not in d['temperatures'] or any(not re.fullmatch('[0-9]{1,3}',c) or str(int(c))!=c or not 2<=int(c)<=k or not finite(t,1.,1.) for c,t in d['temperatures'].items()):
            raise ContractError('Invalid support candidate regime')
        name=d['weights_file']
        if type(name)is not str or Path(name).name!=name or name in ('.','..'):raise ContractError('Invalid support binary path')
        blob=read_regular(path.parent/name,MAX_BINARY)
        size=24+4*(rows+1)+4*rows+8*nnz
        if len(blob)!=size or hashlib.sha256(blob).hexdigest()!=d['weights_sha256'] or blob[:8]!=MAGIC or struct.unpack('<IIII',blob[8:24])!=(k,n,rows,nnz):
            raise ContractError('Support binary integrity/header failure')
        cursor=24;parts=[]
        for code,count in [('I',rows+1),('I',rows),('I',nnz),('f',nnz)]:
            part=array(code);assert part.itemsize==4;part.frombytes(blob[cursor:cursor+count*4]);cursor+=count*4
            if sys.byteorder!='little':part.byteswap()
            parts.append(part)
        offsets,classes,indices,values=parts
        if offsets[0]!=0 or offsets[-1]!=nnz or any(a>=b for a,b in zip(offsets,offsets[1:])) or any(c>=k for c in classes):
            raise ContractError('Invalid support offsets/classes')
        if collections.Counter(classes)!=dict.fromkeys(range(k),20):raise ContractError('Unbalanced support counts')
        if any(i>=n for i in indices) or any(not finite(v,1e-12,1.00001) for v in values):raise ContractError('Invalid support vector')
        for row in range(rows):
            left,right=offsets[row],offsets[row+1]
            if any(indices[i]>=indices[i+1] for i in range(left,right-1)) or abs(math.fsum(float(v)**2 for v in values[left:right])-1)>1e-5:
                raise ContractError('Invalid support vector ordering/norm')
        return cls(d,offsets,classes,indices,values)


def write_memory(directory,data,records):
    directory=Path(directory)
    if directory.exists() and any(directory.iterdir()):raise ContractError('Refusing to overwrite support artifact')
    directory.mkdir(parents=True,exist_ok=True)
    offsets=[0];classes=[];indices=[];values=[];ids=[]
    for c,ident,pairs in records:
        classes.append(c);ids.append(ident)
        for i,v in pairs:indices.append(i);values.append(v)
        offsets.append(len(indices))
    d=dict(data);rows=len(records);nnz=len(indices)
    blob=MAGIC+struct.pack('<IIII',len(d['labels']),len(d['vocabulary']),rows,nnz)
    for code,values_ in [('I',offsets),('I',classes),('I',indices),('f',values)]:
        part=array(code,values_)
        if sys.byteorder!='little':part.byteswap()
        blob+=part.tobytes()
    if len(blob)>MAX_BINARY:raise ContractError('Support binary exceeds bound')
    d.update(rows=rows,nnz=nnz,top_k=5,scale=10.,support_per_class=20,support_ids=ids,
        weights_file='support.sparse',weights_sha256=hashlib.sha256(blob).hexdigest())
    payload=canonical(d)
    (directory/'support.sparse').write_bytes(blob);(directory/'model.json').write_bytes(payload)
    model=SupportMemory.load(directory/'model.json',hashlib.sha256(payload).hexdigest())
    registry={'format':'eidos-registry-v1','models':[{'id':d['id'],'file':'model.json','sha256':hashlib.sha256(payload).hexdigest()}]}
    (directory/'registry.json').write_bytes(canonical(registry))
    return model
