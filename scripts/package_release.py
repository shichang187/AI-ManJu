"""Build this paused snapshot from committed files, never the local runtime tree."""
from pathlib import Path
import hashlib
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = 'v0.8.0-paused.20261015'

def build():
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    count = 0
    with zipfile.ZipFile(out / f'AI-ManJu-{TAG}-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            if not name or name.startswith(('Novel2ComicDrama/output/', 'Novel2ComicDrama/data/private/', 'samples/')):
                continue
            if Path(name).name.startswith('.env') or name.endswith(('.downloading', '.pyc')):
                continue
            content = subprocess.check_output(['git', 'show', f'HEAD:{name}'], cwd=ROOT)
            archive.writestr(f'AI-ManJu/{name}', content)
            count += 1
    for name, source in [('borrow-a-second-v3.mp4', 'samples/borrow-a-second/borrow-a-second-v3.mp4'), ('PAUSE-NOTES.md', 'docs/pause-2026-10-15.md')]:
        (out / name).write_bytes(subprocess.check_output(['git', 'show', f'HEAD:{source}'], cwd=ROOT))
    names = [f'AI-ManJu-{TAG}-source.zip', 'borrow-a-second-v3.mp4', 'PAUSE-NOTES.md']
    (out / 'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256((out/name).read_bytes()).hexdigest()}  {name}\n' for name in names), encoding='utf-8')
    print(f'Packaged {count} source files and labelled sample; private/runtime files excluded.')

if __name__ == '__main__':
    build()
