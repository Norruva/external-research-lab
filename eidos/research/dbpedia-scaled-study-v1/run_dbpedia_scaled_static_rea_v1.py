"""Passively inspect the scaled-cohort proposal, controls and authored-fixture evidence with pinned REA.

This launcher executes the reviewed REA tool only. Archive members are input
bytes and are never imported, installed, evaluated or run.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import resource
import signal
import subprocess
import tarfile
import time
import zipfile

from run_laya_mechanism_rea import tooling_inventory, digest_file, REA, NODE, NODE_SHA256

ROOT = Path('/workspace/eidos')
PREFIX = 'eidos-dbpedia-scaled-review-v1'
TOOLING_SHA = '09aefc3c81b2df18773fca04824b1297a95f794978d1e3f84515a41e1cf907a6'
TOOLING_ENTRIES = 25132
GUARD = r'''
import errno,hashlib,json,os,pathlib,resource,socket,subprocess,sys
assert os.getuid()==1000 and os.getcwd()=='/tmp' and sys.flags.no_site and sys.flags.dont_write_bytecode
assert set(os.environ)<={'PATH','LC_ALL','HOME','PWD'}
assert {name for _,name in socket.if_nameindex()}=={'lo'}
assert all(not pathlib.Path(p).exists() for p in ('/workspace','/root','/run','/etc','/sys','/opt','/model','/dataset'))
with socket.socket() as probe:
    try:probe.connect(('198.51.100.1',443))
    except OSError as error:assert error.errno==errno.ENETUNREACH
    else:raise AssertionError('Unexpected outbound route')
assert hashlib.sha256(pathlib.Path('/tooling/node').read_bytes()).hexdigest()==sys.argv[1]
manifest_bytes=pathlib.Path('/input-manifest.json').read_bytes()
assert hashlib.sha256(manifest_bytes).hexdigest()==sys.argv[3]
inventory=json.loads(manifest_bytes)
assert type(inventory) is dict and 1<=len(inventory)<=100
paths=list(pathlib.Path('/target/release').rglob('*'))
assert all(not p.is_symlink() and (p.is_file() or p.is_dir()) for p in paths)
actual={p.relative_to('/target/release').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths if p.is_file()}
assert actual==inventory
assert hashlib.sha256(pathlib.Path('/target/release.zip').read_bytes()).hexdigest()==sys.argv[2]
resource.setrlimit(resource.RLIMIT_CPU,(30,30))
command=['/tooling/node','--max-old-space-size=256','/tooling/rea/scripts/rea.mjs',
         'inspect-artifact','/target/release.zip','--integrity-policy','fail','--json']
print(json.dumps({'passive_only':True,'target_execution':False,'command':command}),file=sys.stderr)
raise SystemExit(subprocess.run(command,stdin=subprocess.DEVNULL,timeout=35).returncode)
'''


def archive_files(artifact, expected_count):
    files = {}
    total = 0
    with tarfile.open(artifact, 'r:gz') as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            assert member.isfile() and not member.pax_headers and not path.is_absolute()
            assert len(path.parts) >= 2 and path.parts[0] == PREFIX and '..' not in path.parts
            name = str(PurePosixPath(*path.parts[1:]))
            assert name not in files and 0 <= member.size <= 16000000
            assert len(files) < expected_count
            total += member.size
            assert total <= 32*1024**2
            data = archive.extractfile(member).read(member.size+1)
            assert len(data) == member.size
            files[name] = data
    assert len(files) == expected_count
    return files


def main():
    assert Path.cwd() == ROOT and os.getuid() == 1000 and platform.system() == 'Linux'
    artifact = ROOT/(PREFIX+'.tar.gz')
    receipt = ROOT/(PREFIX+'-receipt.json')
    assert artifact.is_file() and not artifact.is_symlink()
    assert receipt.is_file() and not receipt.is_symlink()
    export = json.loads(receipt.read_bytes())
    count = export['files_verified']
    assert type(count) is int and 1 <= count <= 100
    assert export['complete'] and digest_file(artifact) == export['sha256']
    export_sha = digest_file(receipt)
    files = archive_files(artifact, count)
    entries = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    assert entries['MANIFEST.json'] == export['manifest_sha256']
    internal = json.loads(files['MANIFEST.json'])
    assert set(internal['files']) == set(files)-{'MANIFEST.json'}
    for name, entry in internal['files'].items():
        assert entry['sha256'] == entries[name] and entry['bytes'] == len(files[name])
    output = ROOT/'evaluation/production/dbpedia-scaled-study-v1/rea-static-v1'
    target = ROOT/'research/dbpedia-scaled-study-v1/rea-target-static-v1'
    analysis_zip = ROOT/'research/dbpedia-scaled-study-v1/analysis-v1.zip'
    assert not output.exists() and not output.is_symlink()
    assert not target.exists() and not target.is_symlink()
    assert not analysis_zip.exists() and not analysis_zip.is_symlink()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, data in files.items():
        destination = target/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(data)
    with zipfile.ZipFile(analysis_zip, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(entries):
            item = zipfile.ZipInfo(PREFIX+'/'+name, date_time=(1980,1,1,0,0,0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, files[name])
    with zipfile.ZipFile(analysis_zip) as archive:
        assert len(archive.infolist()) == count
        assert {item.filename.removeprefix(PREFIX+'/'):hashlib.sha256(archive.read(item)).hexdigest()
                for item in archive.infolist()} == entries
    zip_sha = digest_file(analysis_zip)
    manifest = output/'target-file-sha256.json'
    manifest.write_text(json.dumps(entries, sort_keys=True, separators=(',',':')))
    manifest_sha = digest_file(manifest)
    before = tooling_inventory()
    assert before['inventory_sha256'] == TOOLING_SHA and len(before['entries']) == TOOLING_ENTRIES
    assert digest_file(NODE) == NODE_SHA256
    command = ['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--uid','1000','--gid','1000','--cap-drop','ALL',
        '--ro-bind','/usr/bin','/usr/bin','--ro-bind','/usr/lib','/usr/lib','--ro-bind','/usr/lib64','/usr/lib64',
        '--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home/research','--dir','/tooling','--dir','/tooling/rea',
        '--ro-bind',str(NODE),'/tooling/node']
    for name in ('scripts','dist','node_modules','package.json'):
        command += ['--ro-bind',str(REA/name),'/tooling/rea/'+name]
    command += ['--dir','/target','--ro-bind',str(target),'/target/release','--ro-bind',str(analysis_zip),'/target/release.zip',
        '--ro-bind',str(manifest),'/input-manifest.json','--clearenv','--setenv','PATH','/usr/bin:/bin',
        '--setenv','LC_ALL','C.UTF-8','--setenv','HOME','/home/research','--chdir','/tmp','--',
        '/usr/bin/python3','-S','-B','-c',GUARD,NODE_SHA256,zip_sha,manifest_sha]
    command = ['/usr/bin/strace','-f','-qq','-e','trace=network',
        '-e','raw=sendto,recvfrom,sendmsg,recvmsg,sendmmsg,recvmmsg','-o',str(output/'network.trace'),*command]
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_CPU,(120,120))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    started = time.monotonic()
    process = subprocess.Popen(command, env={'PATH':'/usr/bin:/bin'}, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=45)
        code = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        code = 124
    (output/'stdout.json').write_bytes(stdout)
    (output/'stderr.log').write_bytes(stderr)
    after = tooling_inventory()
    unchanged = before == after and digest_file(NODE) == NODE_SHA256
    unchanged = unchanged and digest_file(artifact) == export['sha256'] and digest_file(receipt) == export_sha
    unchanged = unchanged and digest_file(analysis_zip) == zip_sha and digest_file(manifest) == manifest_sha
    unchanged = unchanged and entries == {p.relative_to(target).as_posix():digest_file(p)
                                          for p in target.rglob('*') if p.is_file()}
    trace = (output/'network.trace').read_text(errors='replace')
    ip = [line for line in trace.splitlines() if 'connect(' in line and 'AF_INET' in line]
    probes = [line for line in ip if 'inet_addr("198.51.100.1")' in line and 'ENETUNREACH' in line]
    if not unchanged or len(ip) != 1 or len(probes) != 1:
        code = code or 1
    summary = {'complete':code == 0,'exit_code':code,'elapsed_seconds':time.monotonic()-started,
        'artifact_sha256':export['sha256'],'export_receipt_sha256':export_sha,
        'inspected':'lossless ZIP of the %d exact preparation archive members' % count,
        'analysis_zip_sha256':zip_sha,'target_manifest_sha256':manifest_sha,'target_files':count,
        'inputs_unchanged':unchanged,'tooling_inventory_sha256':TOOLING_SHA,
        'tooling_entries':TOOLING_ENTRIES,'node_sha256':NODE_SHA256,
        'source_commit':'e09a503f44fba1639b71907a018d71161360f5ae',
        'target_installed_imported_or_executed':False,'expected_refused_preflights':len(probes),
        'unexpected_ip_connections':len(ip)-len(probes),'evidence_sha256':hashlib.sha256(stdout).hexdigest()}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    print(stderr.decode(errors='replace')[-2000:])
    return code


if __name__ == '__main__':
    raise SystemExit(main())
