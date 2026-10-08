"""Use spotify-player's bundled browser login; no developer setup for the user.

The pinned helper establishes its Web API session and a compatible librespot
session. It runs only during login, with streaming disabled. Plume then resumes
its small Spotifyd engine. No browser cookies or unrelated app sessions are read.
"""
import json
import os
from pathlib import Path
import re
import queue
import threading
import subprocess
import tempfile
import time

from .core import UserError, private_dir

from .platform_support import ROOT, WINDOWS, process_options
TOKEN_FILE = re.compile(r"^[a-f0-9]{32}_token\.json$")


def completed_session(cache):
    credentials = cache / "credentials.json"
    try:
        saved = json.loads(credentials.read_text())
        if not saved.get("auth_data"):
            return None
        for path in cache.glob("*_token.json"):
            if not TOKEN_FILE.fullmatch(path.name) or path.stat().st_size > 65_536:
                continue
            token = json.loads(path.read_text())
            if token.get("access_token") and token.get("refresh_token"):
                return path, token
    except (OSError, ValueError):
        return None
    return None


def login_with_browser(engine, browser):
    binary = ROOT / "vendor" / ("spotify_player.exe" if WINDOWS else "spotify_player")
    if not binary.is_file():
        raise UserError("Le composant de connexion manque. Réinstalle la dernière version de Plume.")
    config = private_dir(engine.settings.root / "login-helper")
    # No custom client ID: use the project's documented standard login flow.
    (config / "app.toml").write_text(
        'enable_streaming = "Never"\n'
        'enable_media_control = false\n'
        'enable_cover_image_cache = false\n'
        'app_refresh_duration_in_ms = 1000\n'
        '[device]\nname = "Plume"\naudio_cache = false\nbitrate = 160\n')
    (config / "app.toml").chmod(0o600)
    # Open URLs ourselves, so the browser is outside Plume's memory budget.
    # This shim is local to this one subprocess, never installed into the system PATH.
    env = {**os.environ, "PATH": str(ROOT / "scripts" / "login-shims") + os.pathsep + os.environ.get("PATH", ""),
           "RUST_LOG": "off"}
    engine.stop()
    staging = tempfile.TemporaryDirectory(prefix="login-", dir=engine.settings.root)
    cache = private_dir(Path(staging.name))
    with engine.lock:
        if engine.closed or engine.login_cancelled.is_set():
            staging.cleanup()
            raise UserError("Connexion annulée.")
        command = [str(binary), "-c", str(config), "-C", str(cache)]
        if WINDOWS:
            command.append("authenticate")
        process = subprocess.Popen(command,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, **process_options())
        engine.auth_process = process
    # Windows pipes cannot be registered with selectors. A bounded reader works
    # on both platforms and never accumulates authorization output indefinitely.
    lines = queue.Queue(maxsize=64)
    reader_done = threading.Event()
    def read_output():
        try:
            while not reader_done.is_set():
                line = process.stdout.readline(32768)
                if not line:
                    break
                while not reader_done.is_set():
                    try:
                        lines.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        pass
        except (OSError, ValueError):
            pass
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    opened = set()
    deadline = time.monotonic() + 300
    try:
        while time.monotonic() < deadline:
            if engine.closed or engine.login_cancelled.is_set():
                raise UserError("Connexion annulée.")
            try:
                line = lines.get(timeout=0.15)
            except queue.Empty:
                line = b""
            match = re.search(rb"Browse to: (https://accounts\.spotify\.com/authorize\?[^\s\x1b]+)", line)
            if match:
                url = match.group(1).decode("ascii")
                if url not in opened:
                    opened.add(url)
                    engine.notify("Valide la connexion dans ton navigateur. Une seconde autorisation peut suivre.")
                    # The Windows helper opens the OS browser itself.
                    if not WINDOWS:
                        browser(url)
            session = completed_session(cache)
            if session:
                source, token = session
                temporary = engine.cache / "credentials.tmp"
                temporary.write_bytes((cache / "credentials.json").read_bytes())
                temporary.chmod(0o600)
                temporary.replace(engine.cache / "credentials.json")
                target = engine.cache / source.name
                target.write_bytes(source.read_bytes())
                target.chmod(0o600)
                return target, token
            if process.poll() is not None:
                raise UserError("Spotify n’a pas terminé la connexion. Ferme toute autre connexion Spotify en cours, puis réessaie.")
        raise UserError("La connexion a expiré. Clique sur Se connecter à Spotify pour réessayer.")
    finally:
        reader_done.set()
        engine._stop_process(process)
        reader.join(timeout=3)
        process.stdout.close()
        engine.auth_process = None
        staging.cleanup()
