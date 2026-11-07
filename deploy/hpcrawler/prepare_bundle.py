"""Build a code-only deployment archive. Never include credentials or live state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    assets = Path(__file__).resolve().parent
    files = [
        'package.json', 'package-lock.json', 'monitor.js', 'monitor-config.js',
        'hupu-user.js', 'utils.js', 'config.js', 'crawler.js',
        'test/monitor.test.js', 'test/hupu-user.test.js', 'test/utils.test.js',
    ]
    deployment_files = ['Dockerfile', 'compose.yaml', '.dockerignore', 'healthcheck.js', 'probe.js', 'README.md']
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit('输出文件已存在；请使用新的文件名，避免覆盖旧部署包。')
    with tempfile.TemporaryDirectory(prefix='hpcrawler-bundle-') as temporary:
        stage = Path(temporary) / 'hpcrawler'
        stage.mkdir()
        manifest = {}
        for root, names in [(source, files), (assets, deployment_files)]:
            for name in names:
                src = root / name
                if not src.is_file() or src.is_symlink():
                    raise SystemExit(f'缺少文件或遇到符号链接：{name}')
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, destination)
                manifest[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        (stage / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
        with tarfile.open(output, mode='x:gz') as archive:
            archive.add(stage, arcname='hpcrawler')
    print(f'部署代码包：{output}')
    print('不包含飞书 Webhook、历史语料或运行状态；切换时单独传输。')


if __name__ == '__main__':
    main()
