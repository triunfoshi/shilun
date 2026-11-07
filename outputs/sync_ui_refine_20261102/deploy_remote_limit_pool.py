"""Reviewed minimal ECS overlay deployment; run only after root authorizes it.

This program never reads or prints data.env. It only passes the existing file
path to Docker Compose. It never manually writes a scheduler manifest or starts
the futures sync job. The running scheduler must publish its own manifest.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


APP_ROOT = Path('/opt/shilun-data')
BASE_RELEASE = APP_ROOT / 'releases/20261102-futures-scheduler-v1'
NEW_RELEASE = APP_ROOT / 'releases/20261102-limitpool-scheduler-v1'
BASE_IMAGE = 'shilun-data:futures-scheduler-20261102'
NEW_IMAGE = 'shilun-data:limitpool-scheduler-20261102'
CONTAINER = 'shilun-data-data-sync-1'
STAGE = Path(__file__).resolve().parent
MODULES = ('shilun/jobs/cloud_scheduler.py', 'shilun/jobs/limit_pool_sync_job.py')
BASE_SCHEDULER_SHA = '63f4976222b3d755401163d9076dbc5b416b4b6b9f61cf98d3e5e8346c12b13b'
PROVIDER_SHA = '6bc02262cb6207e8c3aa3f3e131fa5a98b71d04ebbd941a226bcf36fccfa14d1'
EXPECTED_JOBS = {
    'tushare_morning_0500', 'tushare_morning_0700', 'price_limits_0845',
    'minute_bars_1515', 'tushare_close_1715', 'tushare_close_1915',
    'daily_review_retry', 'cffex_positions_1740', 'cffex_positions_1940',
    'cffex_positions_2340', 'scheduler_manifest_heartbeat', 'limit_pool_1535', 'limit_pool_1735', 'limit_pool_1935',
}


def run(args, *, env=None, timeout=120, show_output=False):
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    if result.returncode:
        # Arbitrary Docker/connection errors can contain environment content.
        # Preserve failure stage and exit code without printing those details.
        raise RuntimeError(f'{args[0]} exited {result.returncode}')
    if show_output and result.stdout:
        print(result.stdout, flush=True)
    return result.stdout


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_idle():
    # Docker requires PID in the requested ps fields. Requesting only args can
    # fail with "Couldn't find PID field in ps output" before we can inspect it.
    rows = [line.split(None, 2) for line in run(
        ['docker', 'top', CONTAINER, '-eo', 'pid,ppid,args']
    ).splitlines()[1:] if line.strip()]
    allowed = (
        '/sbin/docker-init -- python -m shilun.jobs.cloud_scheduler',
        'python -m shilun.jobs.cloud_scheduler',
    )
    if len(rows) != 2 or any(
        len(row) != 3 or not row[0].isdigit() or not row[1].isdigit()
        or not row[2].startswith(allowed) for row in rows
    ):
        raise RuntimeError('Scheduler is not idle; refusing to interrupt child tasks')


def compose(release):
    return ['docker', 'compose', '-f', str(release / 'deploy/data-sync.compose.yaml')]


def atomic_current(release):
    temporary = APP_ROOT / ('current.next-' + str(os.getpid()))
    temporary.symlink_to(release)
    os.replace(temporary, APP_ROOT / 'current')


def main():
    manifest = json.loads((STAGE / 'source-manifest.json').read_text())
    if sha(Path(__file__)) != manifest['deployment_script_sha256']:
        raise RuntimeError('Deployment program hash differs from reviewed manifest')
    if set(manifest['module_sha256']) != set(MODULES):
        raise RuntimeError('Overlay manifest is not the exact two-module whitelist')
    for name in MODULES:
        if sha(STAGE / name) != manifest['module_sha256'][name]:
            raise RuntimeError('Overlay source hash mismatch')
    runtime_order = ('shilun/jobs/cloud_scheduler.py', 'shilun/jobs/cffex_positions_sync_job.py', 'shilun/jobs/scheduler_manifest_job.py', 'shilun/data/cffex_positions.py', 'shilun/jobs/limit_pool_sync_job.py')
    expected_fingerprint = hashlib.sha256(b''.join(((STAGE if name in MODULES else BASE_RELEASE) / name).read_bytes() for name in runtime_order)).hexdigest()
    if manifest['release_fingerprint'] != expected_fingerprint:
        raise RuntimeError('Overlay fingerprint mismatch')
    if (APP_ROOT / 'current').resolve() != BASE_RELEASE:
        raise RuntimeError('Current release changed since preflight')
    actual_base_image = run(['docker', 'inspect', '--format', '{{.Config.Image}}', CONTAINER]).strip()
    if actual_base_image != BASE_IMAGE:
        raise RuntimeError('Running base image changed since preflight')
    base_image_id = run(['docker', 'inspect', '--format', '{{.Image}}', CONTAINER]).strip()
    tagged_base_image_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', BASE_IMAGE]).strip()
    if base_image_id != tagged_base_image_id or not base_image_id.startswith('sha256:40f12f4346ea'):
        raise RuntimeError('Base image tag no longer identifies the running preflight image')
    if sha(BASE_RELEASE / MODULES[0]) != BASE_SCHEDULER_SHA:
        raise RuntimeError('Base scheduler source changed since preflight')
    if sha(BASE_RELEASE / 'shilun/data/providers/cffex_positions_provider.py') != PROVIDER_SHA:
        raise RuntimeError('Base provider source changed since preflight')
    if NEW_RELEASE.exists():
        raise RuntimeError('New release already exists; refusing to overwrite it')

    print('PRECHECK source whitelist, base release and image verified', flush=True)
    shutil.copytree(BASE_RELEASE, NEW_RELEASE, symlinks=True)
    for name in MODULES:
        destination = NEW_RELEASE / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STAGE / name, destination)
    shutil.copy2(STAGE / 'source-manifest.json', NEW_RELEASE / 'limitpool-source-manifest.json')

    dockerfile = 'FROM ' + BASE_IMAGE + '\n' + ''.join(
        f'COPY --chown=shilun:shilun {name} /app/{name}\n' for name in MODULES
    )
    (STAGE / 'Dockerfile.overlay').write_text(dockerfile)
    (NEW_RELEASE / 'deploy/Dockerfile.limitpool-overlay').write_text(dockerfile)
    compose_path = NEW_RELEASE / 'deploy/data-sync.compose.yaml'
    text = compose_path.read_text()
    if text.count('image: ' + BASE_IMAGE) != 1:
        raise RuntimeError('Compose image differs from reviewed baseline')
    old_command = 'command: ["python", "-m", "shilun.jobs.cloud_scheduler", "--data-only", "--location", "ECS"]'
    new_command = 'command: ["python", "-m", "shilun.jobs.cloud_scheduler", "--data-only", "--location", "ECS"]'
    if text.count(old_command) != 1:
        raise RuntimeError('Compose command differs from reviewed baseline')
    text = text.replace('image: ' + BASE_IMAGE, 'image: ' + NEW_IMAGE)
    text = text.replace(old_command, new_command)
    text = text.replace('dockerfile: deploy/Dockerfile.futures-overlay\n', 'dockerfile: deploy/Dockerfile.limitpool-overlay\n')
    compose_path.write_text(text)
    task_env = dict(os.environ)
    task_env['SHILUN_ENV_FILE'] = '/opt/shilun-data/shared/data.env'
    run([*compose(NEW_RELEASE), 'config', '--quiet'], env=task_env)
    print('BUILD derived image with two modules and no dependency installation', flush=True)
    run(['docker', 'build', '--network', 'none', '-f', str(STAGE / 'Dockerfile.overlay'), '-t', NEW_IMAGE, str(STAGE)], timeout=300)
    image_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', NEW_IMAGE]).strip()

    precheck = '''
import hashlib,json,socket
from pathlib import Path
def deny(*args,**kwargs): raise AssertionError('Unexpected network IO in construction')
socket.socket.connect=deny
socket.create_connection=deny
from shilun.jobs.cloud_scheduler import build_scheduler,scheduler_manifest
import shilun.jobs.cffex_positions_sync_job
import shilun.jobs.scheduler_manifest_job
import shilun.jobs.limit_pool_sync_job
from shilun.data.providers import cffex_positions_provider
s=build_scheduler(data_only=True,location='ECS')
m=scheduler_manifest(s,data_only=True,location='ECS')
expected=EXPECTED_JOBS
assert {j['id'] for j in m['jobs']} == set(expected)
assert len(m['jobs']) == 14
assert m['release_fingerprint'] == EXPECTED_FINGERPRINT
assert m['location'] == 'ECS' and m['data_only'] is True
for j in m['jobs']:
 if j['id'].startswith(('cffex_positions_', 'limit_pool_')): assert j['executor'] == 'evidence'
 if j['id'] == 'scheduler_manifest_heartbeat': assert j['executor'] == 'maintenance'
files=EXPECTED_MODULES
assert all(hashlib.sha256((Path('/app')/p).read_bytes()).hexdigest()==h for p,h in files.items())
print(json.dumps({'precheck':'passed_no_network_io','jobs':sorted(expected),'release_fingerprint':m['release_fingerprint']}))
'''.replace('EXPECTED_JOBS', repr(sorted(EXPECTED_JOBS))).replace('EXPECTED_FINGERPRINT', repr(expected_fingerprint)).replace('EXPECTED_MODULES', repr(manifest['module_sha256']))
    run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', NEW_IMAGE, '-c', precheck], show_output=True)
    print('ACTIVATE checking idle scheduler immediately before recreation', flush=True)
    assert_idle()
    if (APP_ROOT / 'current').resolve() != BASE_RELEASE:
        raise RuntimeError('Current release changed during build')
    activation_started = datetime.now(timezone.utc)
    pointer_changed = False
    activation_attempted = False
    try:
        activation_attempted = True
        run([*compose(NEW_RELEASE), 'up', '-d', '--no-build', '--no-deps', 'data-sync'], env=task_env)
        # Read the actual database document published by the running scheduler.
        # This helper performs no insert/update or simulated publication.
        verify = '''
import json
from datetime import datetime,timezone
from pymongo import MongoClient
from shilun.common.config import load_config
c=load_config()
client=MongoClient(c.mongo_uri,serverSelectionTimeoutMS=10000,socketTimeoutMS=10000)
try:
 m=client[c.mongo_db]['sync_state'].find_one({'dataset':'cloud_scheduler_manifest','scope':'active'}) or {}
 assert m.get('status')=='running' and m.get('location')=='ECS' and m.get('data_only') is True
 assert m.get('release_fingerprint')==EXPECTED_FINGERPRINT
 assert {j['id'] for j in m.get('jobs',[])}==set(EXPECTED_JOBS)
 updated=datetime.fromisoformat(m['updated_at']).astimezone(timezone.utc)
 assert updated >= datetime.fromisoformat(ACTIVATION_STARTED)
 print(json.dumps({k:m.get(k) for k in ('dataset','scope','status','location','data_only','release_fingerprint','updated_at','jobs')},ensure_ascii=False))
finally: client.close()
'''.replace('EXPECTED_FINGERPRINT', repr(expected_fingerprint)).replace('EXPECTED_JOBS', repr(sorted(EXPECTED_JOBS))).replace('ACTIVATION_STARTED', repr(activation_started.isoformat()))
        runtime_manifest = None
        for attempt in range(20):
            try:
                runtime_manifest = json.loads(run(['docker', 'exec', CONTAINER, 'python', '-c', verify], timeout=20))
                break
            except (RuntimeError, json.JSONDecodeError):
                time.sleep(2)
        if runtime_manifest is None:
            raise RuntimeError('Running scheduler did not publish a verified fresh ECS manifest')
        if run(['docker', 'inspect', '--format', '{{.Config.Image}}', CONTAINER]).strip() != NEW_IMAGE:
            raise RuntimeError('Running container image differs from release')
        atomic_current(NEW_RELEASE)
        pointer_changed = True
        receipt = {
            'status': 'deployed', 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'previous_release': str(BASE_RELEASE), 'previous_image': BASE_IMAGE,
            'previous_image_id': base_image_id,
            'current_release': str(NEW_RELEASE), 'current_image': NEW_IMAGE,
            'image_id': image_id, 'module_sha256': manifest['module_sha256'],
            'runtime_manifest': runtime_manifest,
            'scope': 'two-module overlay; no manual sync job invocation',
        }
        (NEW_RELEASE / 'limitpool-deploy-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        print('DEPLOY_RECEIPT ' + json.dumps(receipt,ensure_ascii=False), flush=True)
    except Exception:
        if activation_attempted:
            print('ROLLBACK restoring previous Compose/image; no database deletion', flush=True)
            # This path is used immediately during activation validation, before
            # any intentional futures job run. Do not use it as a later rollback
            # command while scheduled children are running.
            run([*compose(BASE_RELEASE), 'up', '-d', '--no-build', '--no-deps', 'data-sync'], env=task_env)
            if pointer_changed:
                atomic_current(BASE_RELEASE)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('DEPLOY_FAILED ' + type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
