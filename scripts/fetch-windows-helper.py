#!/usr/bin/env python3
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile
url = 'https://github.com/aome510/spotify-player/releases/download/v0.25.1/spotify_player-x86_64-pc-windows-msvc.zip'
with urllib.request.urlopen(url, timeout=120) as response:
    data = response.read(100_000_001)
if hashlib.sha256(data).hexdigest() != '946dfe069f3cd530548e07a86bbf71928a7983f30933e202b179a4ef3e5e16cb':
    raise SystemExit('Windows helper checksum mismatch')
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    names = [n for n in archive.namelist() if Path(n).name == 'spotify_player.exe']
    if len(names) != 1: raise SystemExit('Unexpected helper archive')
    target = Path(__file__).resolve().parents[1]/'vendor/spotify_player.exe'
    target.write_bytes(archive.read(names[0]))
print('Windows login helper verified')
