"""New one-attempt reference launcher. Default is static check, never inference."""
import argparse,hashlib,json,os,resource,shutil,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path('/workspace/eidos');STUDY=ROOT/'research/support-remaining-reference-v1'
PREPARED=STUDY/'prepared-manifest-v1.json';PLAN=ROOT/'research/production/reference-snips-remaining-resource-plan-v1.json'
AUTH=STUDY/'authorization-v1.json';OUTPUT=ROOT/'evaluation/production/support-remaining-reference-v1/v1'
PYTHON=Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/python')
GUARD=r'''
import errno,hashlib,json,os,pathlib,resource,runpy,socket,sys
assert os.getuid()==1000 and sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
assert os.getcwd()=='/work' and {n for _,n in socket.if_nameindex()}=={'lo'}
assert not any(pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt'))
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','TOKENIZERS_PARALLELISM','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','MALLOC_ARENA_MAX'}
with socket.socket() as probe:
 try:probe.connect(('198.51.100.1',443))
 except OSError as e:assert e.errno==errno.ENETUNREACH
 else:raise AssertionError('Unexpected outbound route')
for p,pin in json.loads(pathlib.Path('/freeze.json').read_bytes()).items():
 path=pathlib.Path(p);assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==pin
 assert os.statvfs(path).f_flag & os.ST_RDONLY
stage=sys.argv[1];assert stage in ('predict','score')
resource.setrlimit(resource.RLIMIT_CPU,((3000,3000) if stage=='predict' else (120,120)))
resource.setrlimit(resource.RLIMIT_AS,((4*1024**3,)*2 if stage=='predict' else (512*1024**2,)*2))
sys.path[:]=['/controls']+[p for p in sys.path if p.startswith('/tooling/') or p.startswith('/usr/')]
sys.argv=['/controls/control.py',stage];runpy.run_path('/controls/control.py',run_name='__main__')
'''


def sha(path):
    p=Path(path);assert p.is_file() and not any(v.is_symlink() for v in (p,*p.parents))
    h=hashlib.sha256()
    with p.open('rb') as f:
        while block:=f.read(1024**2):h.update(block)
    return h.hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def write(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n')


def check():
    manifest=read(PREPARED);assert manifest['format']=='eidos-support-reference-prepared-v1'
    for relative,pin in manifest['files'].items():
        p=ROOT/relative;assert '..' not in Path(relative).parts and not Path(relative).is_absolute()
        assert p.stat().st_size==pin['bytes'] and sha(p)==pin['sha256']
    for absolute,pin in manifest['external_files'].items():
        assert absolute in (str(PYTHON/'bin/python3.12'),str(PYTHON/'lib/libpython3.12.so.1.0'))
        p=Path(absolute);assert p.stat().st_size==pin['bytes'] and sha(p)==pin['sha256']
    assert sha(PLAN)==manifest['files'][str(PLAN.relative_to(ROOT))]['sha256']
    # This source-only helper imports stdlib and never imports reference code.
    sys.path.insert(0,str(STUDY));from primary_predict import verify_large_tree,validate_rights
    verified={}
    for name,tree in manifest['trees'].items():
        verified[name]=verify_large_tree(tree['root'],tree['manifest'],tree['sha256'])['manifest']
    validate_rights(read(manifest['rights_file']),verified['model'],verified['laya-source'])
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute-approved',action='store_true');args=parser.parse_args()
    assert Path.cwd()==ROOT and os.getuid()==1000 and sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
    operation_started=time.monotonic();active=[None]
    if args.execute_approved:
        def deadline(_signum,_frame):
            process=active[0]
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
            raise TimeoutError('New comparison whole-operation deadline reached; no retry authorized')
        signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,5400)
        resource.setrlimit(resource.RLIMIT_CPU,(480,3600));resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,4*1024**3))
    manifest=check()
    if not args.execute_approved:
        print(json.dumps({'complete':True,'mode':'static-preparation-check-only','files_checked':len(manifest['files']),
            'tree_manifests_checked':len(manifest['trees']),'prepared_manifest_sha256':sha(PREPARED),'plan_sha256':sha(PLAN),
            'model_or_repository_code_executed':False,'reference_execution_requires_new_approval':True}));return 0
    authorization=read(AUTH)
    assert set(authorization)=={'scope','approved','plan_sha256','prepared_manifest_sha256','user_instruction_sha256','approved_at_utc'}
    assert authorization['scope']=='eidos-snips-remaining-reference-v1' and authorization['approved'] is True
    assert authorization['plan_sha256']==sha(PLAN) and authorization['prepared_manifest_sha256']==sha(PREPARED)
    assert type(authorization['user_instruction_sha256'])is str and len(authorization['user_instruction_sha256'])==64
    OUTPUT.mkdir(mode=0o700,parents=True,exist_ok=False) # One slot; never overwrite/resume.
    frozen={str(ROOT/p):pin['sha256'] for p,pin in manifest['files'].items()};frozen.update({str(PREPARED):sha(PREPARED),str(AUTH):sha(AUTH)})
    write(OUTPUT/'prelaunch.json',{'complete':True,'files':frozen,'trees':manifest['trees'],'authorization_sha256':sha(AUTH),
        'planned_cpu_seconds':3600,'whole_wall_seconds':5400,'automatic_retry':False,'aggregate_cgroup_guarantee':False})
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_CPU,(480,3600));resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,)*2)
    started=operation_started;summaries=[]
    for stage,wall in [('predict',4200),('score',180)]:
        assert frozen=={p:sha(p) for p in frozen};remaining=5400-(time.monotonic()-started);assert remaining>wall
        out=OUTPUT/stage;out.mkdir(mode=0o700);payload=out/'data';payload.mkdir(mode=0o700)
        controls=out/'controls';controls.mkdir(mode=0o700)
        for name in ('control.py','primary_predict.py','statistics.py'):shutil.copyfile(STUDY/name,controls/name)
        mounts=[(controls,'/controls'),(STUDY/'labels.json','/labels.json')];pins={'files':{'/labels.json':sha(STUDY/'labels.json')},'gold_class_counts':{'AddToPlaylist': 170, 'BookRestaurant': 365, 'GetWeather': 343, 'PlayMusic': 300, 'RateBook': 205, 'SearchCreativeWork': 151, 'SearchScreeningEvent': 287}}
        if stage=='predict':
            pins['trees']={name:t['sha256'] for name,t in manifest['trees'].items()}
            for name,t in manifest['trees'].items():mounts += [(Path(t['root']),'/'+name),(Path(t['manifest']),'/'+name+'-manifest.json')]
            mounts += [(Path(manifest['rights_file']),'/rights.json'),(STUDY/'inputs.jsonl','/inputs.jsonl')]
            pins['files'].update({'/rights.json':sha(manifest['rights_file']),'/inputs.jsonl':sha(STUDY/'inputs.jsonl')})
        else:
            receipt=read(OUTPUT/'predict/data/result.json');assert receipt['complete'] and sha(OUTPUT/'predict/data/predictions.jsonl')==receipt['predictions_sha256']
            mounts += [(STUDY/'gold.jsonl','/gold.jsonl'),(STUDY/'eidos.jsonl','/eidos.jsonl'),
                (OUTPUT/'predict/data/predictions.jsonl','/laya.jsonl'),(OUTPUT/'predict/data/result.json','/prediction-result.json')]
            for origin,target in mounts[2:]:
                if origin.is_file():pins['files'][target]=sha(origin)
        pinfile=out/'pins.json';write(pinfile,pins);mounts.append((pinfile,'/pins.json'))
        freeze={}
        # Complete large trees are verified by the pinned source helper, bounded
        # streaming; only small control/data/manifest files use the guard hash.
        for origin,target in mounts:
            if origin==controls:
                for p in controls.iterdir():freeze[target+'/'+p.name]=sha(p)
            elif origin.is_file():freeze[target]=sha(origin)
        freeze_path=out/'freeze.json';write(freeze_path,freeze)
        command=['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--uid','1000','--gid','1000','--cap-drop','ALL',
            '--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib','--ro-bind','/usr/lib64','/usr/lib64',
            '--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/work','--dir','/home/research']
        for origin,target in mounts:command+=['--ro-bind',str(origin),target]
        command+=['--ro-bind',str(freeze_path),'/freeze.json','--bind',str(payload),'/output'];python='/usr/bin/python3'
        if stage=='predict':
            python='/tooling/python/bin/python3.12';command+=['--ro-bind',str(PYTHON/'bin/python3.12'),python,
                '--ro-bind',str(PYTHON/'lib/libpython3.12.so.1.0'),'/tooling/python/lib/libpython3.12.so.1.0',
                '--ro-bind',str(PYTHON/'lib/python3.12'),'/tooling/python/lib/python3.12','--tmpfs','/tooling/python/lib/python3.12/site-packages']
        command+=['--clearenv'];env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','HOME':'/home/research'}
        if stage=='predict':env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MALLOC_ARENA_MAX='1')
        for k,v in env.items():command+=['--setenv',k,v]
        command+=['--chdir','/work','--',python,'-S','-B','-c',GUARD,stage]
        command=['/usr/bin/strace','-f','-qq','-e','trace=network','-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(out/'network.trace'),*command]
        print(json.dumps({'stage_started':stage}),flush=True);begin=time.monotonic()
        process=subprocess.Popen(command,env={'PATH':'/usr/bin:/bin'},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        active[0]=process
        try:stdout,stderr=process.communicate(timeout=wall);code=process.returncode
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate(timeout=5);code=124
        (out/'stdout.log').write_bytes(stdout);(out/'stderr.log').write_bytes(stderr)
        active[0]=None
        ip=[line for line in (out/'network.trace').read_text().splitlines() if 'connect(' in line and 'AF_INET' in line]
        probes=[line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
        result=read(payload/'result.json') if (payload/'result.json').exists() else {}
        complete=code==0 and result.get('complete') is True and len(ip)==len(probes)==1
        summary={'complete':complete,'stage':stage,'exit_code':code,'elapsed_seconds':time.monotonic()-begin,
            'expected_refused_probe':len(probes),'unexpected_ip_connections':len(ip)-len(probes),
            'result_sha256':sha(payload/'result.json') if (payload/'result.json').exists() else None}
        write(out/'execution-summary.json',summary);summaries.append(summary)
        if not complete:break
    unchanged=frozen=={p:sha(p) for p in frozen};check() # Full immutable tree verification again.
    complete=len(summaries)==2 and all(s['complete'] for s in summaries) and unchanged
    result={'complete':complete,'stages':summaries,'inputs_unchanged':unchanged,'elapsed_seconds':time.monotonic()-started,
        'automatic_retry':False,'aggregate_cgroup_guarantee':False,'mission_complete':False,'production_certified':False}
    write(OUTPUT/'execution-summary.json',result);signal.setitimer(signal.ITIMER_REAL,0);print(json.dumps(result,indent=2));return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
