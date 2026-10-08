"""One supervised audio process, no embedded browser or unbounded logs."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

from .core import UserError, private_dir

from .platform_support import ROOT, WINDOWS, process_options


def validate_browser_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.hostname not in {
        "accounts.spotify.com", "developer.spotify.com", "open.spotify.com", "lrclib.net"}:
        raise UserError("Adresse externe non autorisée.")


def open_browser(url):
    validate_browser_url(url)
    if WINDOWS:
        import webbrowser
        if not webbrowser.open(url):
            raise UserError("Impossible d’ouvrir le navigateur par défaut.")
        return
    # A new user unit keeps the existing browser outside Plume's memory cgroup.
    if os.environ.get("PLUME_CAPPED") == "1" and shutil.which("systemd-run"):
        result = subprocess.run(["systemd-run", "--user", "--collect", "--quiet",
            "--unit=plume-browser-" + str(time.time_ns()), "xdg-open", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode == 0:
            return
        raise UserError("Impossible d’ouvrir le navigateur via la session Linux.")
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Engine:
    def __init__(self, settings, notify=lambda _: None):
        self.settings = settings
        self.notify = notify
        self.process = None
        self.auth_process = None
        self.cache = private_dir(settings.root/"audio")
        self.config = settings.root/"spotifyd.conf"
        self.config.write_text("[global]\n")
        self.config.chmod(0o600)
        bundled = ROOT/"vendor"/("librespot.exe" if WINDOWS else "spotifyd")
        self.binary = str(bundled) if bundled.exists() else shutil.which("librespot.exe" if WINDOWS else "spotifyd")
        self.lock = threading.RLock()
        self.stopping = False
        self.closed = False
        self.login_cancelled = threading.Event()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def command(self):
        if not self.binary:
            raise UserError("Moteur audio absent. Lance scripts/fetch-engine.py, puis réouvre Plume.")
        if WINDOWS:
            return [self.binary, "--name", self.settings.data["device_name"],
                    "--system-cache", str(self.cache), "--disable-audio-cache",
                    "--bitrate", str(self.settings.data["bitrate"]),
                    "--initial-volume", "50", "--device-type", "computer", "--backend", "rodio"]
        return [self.binary, "--no-daemon", "--config-path", str(self.config),
                "--cache-path", str(self.cache), "--no-audio-cache", "--device-name",
                self.settings.data["device_name"], "--device-type", "computer",
                "--bitrate", str(self.settings.data["bitrate"]), "--initial-volume", "50",
                "--volume-controller", "soft-volume", "--use-mpris", "--dbus-type", "session"]

    def start(self):
        with self.lock:
            if self.closed or self.running:
                return
            self.stopping = False
            self.process = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, env={**os.environ, "RUST_LOG": "warn"}, **process_options())
            threading.Thread(target=self._drain, args=(self.process,), daemon=True).start()

    def _drain(self, process):
        # Drain to avoid a full stderr pipe. Never retain authentication output.
        while process.stderr.readline():
            pass
        process.stderr.close()
        code = process.wait()
        if not self.stopping and self.process is process and code:
            self.notify("Le moteur audio s’est arrêté. Vérifie la connexion audio dans Réglages.")

    @staticmethod
    def _stop_process(process):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def stop(self):
        with self.lock:
            self.stopping = True
            self._stop_process(self.process)
            self._stop_process(self.auth_process)

    def close(self):
        with self.lock:
            self.closed = True
            self.login_cancelled.set()
            self.stop()

    def authenticate(self, browser=open_browser):
        from .browser_login import login_with_browser
        return login_with_browser(self, browser)

    def logout(self):
        self.login_cancelled.set()
        self.stop()
        # Only files owned by this app's dedicated engine cache.
        for path in self.cache.glob("*credentials*.json"):
            path.unlink()
        for path in self.cache.glob("*_token.json"):
            path.unlink()


def memory_usage():
    """Total RSS for this process and descendants (shared pages counted twice)."""
    if WINDOWS:
        import psutil
        parent = psutil.Process()
        total = 0
        for process in [parent, *parent.children(recursive=True)]:
            try:
                total += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total
    pids = {os.getpid()}
    processes = {}
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            values = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
            processes[int(path.parent.name)] = (int(values["PPid"]),
                int(values.get("VmRSS", "0 kB").split()[0])*1024)
        except (OSError, ValueError, KeyError):
            pass
    while True:
        children = {pid for pid, (ppid, _) in processes.items() if ppid in pids}
        new = children - pids
        if not new:
            break
        pids.update(new)
    rss = sum(processes.get(pid, (0, 0))[1] for pid in pids)
    return rss


def memory_limit():
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                path = Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "memory.max"
                value = path.read_text().strip()
                return None if value == "max" else int(value)
    except (OSError, ValueError):
        pass
    return None
