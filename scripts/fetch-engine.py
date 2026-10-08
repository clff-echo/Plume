#!/usr/bin/env python3
"""Fetch a pinned upstream binary, verifying its release asset digest."""
import hashlib
import io
from pathlib import Path, PurePosixPath
import platform
import tarfile
import urllib.request

VERSION = "v0.4.2"
DIGESTS = {
    "x86_64": "6769a83f022fb23f78177f62cb25863741d0d15d5e8be71a5096b3acc022649f",
    "aarch64": "8763b9a18d9ae48ac9285ff1f32cf23552a49758ad7a5a8f5eca444e4745f3a1",
}


def main():
    arch = platform.machine()
    if platform.system() != "Linux" or arch not in DIGESTS:
        raise SystemExit("Architecture non fournie. Installe Spotifyd depuis ta distribution.")
    name = f"spotifyd-linux-{arch}-default.tar.gz"
    url = f"https://github.com/Spotifyd/spotifyd/releases/download/{VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read(50_000_001)
    if hashlib.sha256(data).hexdigest() != DIGESTS[arch]:
        raise SystemExit("Empreinte incorrecte : téléchargement rejeté.")
    target = Path(__file__).resolve().parent.parent/"vendor"
    target.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if PurePosixPath(m.name).name == "spotifyd" and m.isfile()]
        if len(members) != 1:
            raise SystemExit("Archive inattendue.")
        output = target/"spotifyd"
        output.write_bytes(archive.extractfile(members[0]).read())
        output.chmod(0o755)
    print("Moteur audio vérifié et prêt :", output)
    helper_hash = {
        "x86_64": "e68dce205e995f80107149c55ace7b93125dbf73805363b7b0f9bfe8475c50ba",
        "aarch64": "c8e02b108ed4d0abc8d705510deaa2db1dfb03af60489b67cd037cd864cb5e53",
    }[arch]
    url = f"https://github.com/aome510/spotify-player/releases/download/v0.25.1/spotify_player-{arch}-unknown-linux-gnu.tar.gz"
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read(50_000_001)
    if hashlib.sha256(data).hexdigest() != helper_hash:
        raise SystemExit("Empreinte du composant de connexion incorrecte.")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if PurePosixPath(m.name).name == "spotify_player" and m.isfile()]
        if len(members) != 1:
            raise SystemExit("Archive de connexion inattendue.")
        output = target/"spotify_player"
        output.write_bytes(archive.extractfile(members[0]).read())
        output.chmod(0o755)
    print("Connexion par navigateur prête :", output)


if __name__ == "__main__":
    main()
