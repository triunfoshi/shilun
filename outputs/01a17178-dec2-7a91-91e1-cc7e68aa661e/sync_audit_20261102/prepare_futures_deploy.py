"""Freeze and package only the four reviewed runtime modules; no network IO.

Run after implementation agent explicitly freezes code and tests pass. Any
subsequent module change invalidates the generated manifest and requires a new
review and prepare run before upload.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile


ROOT = Path('/Users/shibicheng/shilun_standalone')
OUT = Path(__file__).resolve().parent
MODULES = (
    'shilun/jobs/cloud_scheduler.py',
    'shilun/data/cffex_positions.py',
    'shilun/jobs/cffex_positions_sync_job.py',
    'shilun/jobs/scheduler_manifest_job.py',
)
ORDER = (MODULES[0], MODULES[2], MODULES[3], MODULES[1])
manifest = {
    'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
    'new_release': '20261102-futures-scheduler-v1',
    'new_image': 'shilun-data:futures-scheduler-20261102',
    'module_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in MODULES},
    'release_fingerprint': hashlib.sha256(b''.join((ROOT / p).read_bytes() for p in ORDER)).hexdigest(),
    'deployment_script_sha256': hashlib.sha256((OUT / 'deploy_remote_futures.py').read_bytes()).hexdigest(),
}
(OUT / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
with tarfile.open(OUT / 'futures-deploy-bundle.tar.gz', 'w:gz') as archive:
    for name in MODULES:
        archive.add(ROOT / name, arcname=name, recursive=False)
    archive.add(OUT / 'source-manifest.json', arcname='source-manifest.json', recursive=False)
    archive.add(OUT / 'deploy_remote_futures.py', arcname='deploy_remote_futures.py', recursive=False)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
