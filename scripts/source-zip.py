#!/usr/bin/env python3
from pathlib import Path
import zipfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plume import __version__
root = Path(__file__).resolve().parents[1]
(root/'dist').mkdir(exist_ok=True)
output = root/'dist'/f'Plume-{__version__}-source.zip'
allowed = {'plume','assets','tests','scripts','packaging','.github','vendor'}
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if not path.is_file() or set(rel.parts)&{'__pycache__','.git','dist','build','.runtime','verification'}: continue
        if len(rel.parts)>1 and rel.parts[0] not in allowed: continue
        if rel.parts[0]=='vendor' and 'LICENSE' not in path.name: continue
        if path.suffix in {'.pyc','.exe','.zip','.deb'}: continue
        archive.write(path, f'Plume-{__version__}/{rel.as_posix()}')
print(output)
