"""Read-only local CLI for nearest-support diagnostics around actual Engine output."""
import argparse
import json
import sys

from .contracts import ContractError,parse_json
from .engine import Engine,selected_text
from .support_memory import SupportMemory


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--registry',required=True);parser.add_argument('--tenant',default='support-research')
    args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(128001);request=parse_json(raw.decode(),maximum=128000)
        engine=Engine.from_registry(args.registry,tenants=(args.tenant,));response=engine.decide(request);evidence=[]
        for question,decision in zip(request['questions'],response['decisions'],strict=True):
            model=engine.models.get(question.get('profile'))
            if not isinstance(model,SupportMemory) or not decision['distribution']:
                evidence.append({'id':question['id'],'status':'unavailable'});continue
            text=selected_text(request['state'],question['state_path']);labels=[r['class_id'] for r in decision['distribution']]
            evidence.append({'id':question['id'],'status':'diagnostic_only','classes':model.evidence(text,labels),
                'review_required':True,'action_authorized':False,'correctness_guarantee':False})
        print(json.dumps({'format':'eidos-support-explanation-v1','engine_response':response,'evidence':evidence,
            'review_required':True,'action_authorized':False},sort_keys=True,allow_nan=False));return 0
    except (ContractError,ValueError,TypeError,OSError,UnicodeError,KeyError):
        print('{"error":"support_explanation_refused"}',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
