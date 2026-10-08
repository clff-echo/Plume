"""Network and state, independent of the GTK main thread."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from .platform_support import WINDOWS, config_home

API = "https://api.spotify.com/v1"
REDIRECT = "http://127.0.0.1:43821/callback"
SCOPES = "user-read-private user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private playlist-read-collaborative user-library-read"


class UserError(Exception):
    pass


class AuthRequired(UserError):
    """Only an invalid or missing session should send the user through login."""


class RateLimited(UserError):
    def __init__(self, delay):
        self.retry_at = time.monotonic() + max(1, delay)
        super().__init__(f"Spotify demande une pause de {max(1, delay)} s. Ta connexion est conservée.")


def retry_delay(headers):
    try:
        return max(1, int(headers.get("Retry-After", "30")))
    except (ValueError, TypeError):
        return 30


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


class Settings:
    def __init__(self, root=None):
        self.root = private_dir(root or os.environ.get("PLUME_HOME") or
                                config_home())
        self.path = self.root/"settings.json"
        self.data = {"client_id": "", "lyrics": False, "bitrate": 160, "device_name": "Plume · Windows" if WINDOWS else "Plume · Linux"}
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def save(self):
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
        temp.replace(self.path)


class TokenVault:
    """Persist refresh tokens only in the OS secret service; never plaintext."""
    def __init__(self):
        self.secret = self.schema = None
        self.memory = {}
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            self.secret = Secret
            self.schema = Secret.Schema.new("io.plume.Client", Secret.SchemaFlags.NONE,
                                            {"client": Secret.SchemaAttributeType.STRING})
        except (ImportError, ValueError):
            pass

    def call(self, function, *args):
        from gi.repository import Gio
        cancellation = Gio.Cancellable()
        timer = threading.Timer(8, cancellation.cancel)
        timer.daemon = True
        timer.start()
        try:
            return function(*args, cancellation)
        finally:
            timer.cancel()

    def get(self, client):
        if client in self.memory:
            return self.memory[client]
        if self.secret:
            try:
                return self.call(self.secret.password_lookup_sync, self.schema, {"client": client})
            except Exception as exc:
                raise UserError("Le trousseau Linux ne répond pas ou reste verrouillé. Déverrouille-le puis réessaie ; ta session est conservée.") from exc
        return None

    def put(self, client, token):
        self.memory[client] = token
        if self.secret:
            try:
                return self.call(self.secret.password_store_sync, self.schema, {"client": client},
                    self.secret.COLLECTION_DEFAULT, "Plume — Spotify", token)
            except Exception:
                pass
        return False

    def clear(self, client):
        self.memory.pop(client, None)
        if self.secret:
            try:
                self.call(self.secret.password_clear_sync, self.schema, {"client": client})
            except Exception:
                pass


def read_json(url, method="GET", headers=None, data=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            # Bound responses, including third-party lyrics. No unlimited reads.
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise UserError("Réponse trop volumineuse. Réduis la taille de la page.")
            return json.loads(raw) if raw else None
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise UserError("Connexion impossible. Vérifie Internet puis réessaie.") from exc


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


class OAuth:
    def __init__(self, settings, vault=None):
        self.settings = settings
        self.vault = vault or TokenVault()
        self.access = None
        self.expires = 0
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.persisted = True
        self.retry_at = 0

    @property
    def client(self):
        return self.settings.data["client_id"].strip()

    def exchange(self, fields):
        try:
            payload = read_json("https://accounts.spotify.com/api/token", "POST",
                {"Content-Type": "application/x-www-form-urlencoded"},
                urllib.parse.urlencode({"client_id": self.client, **fields}).encode())
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 429:
                delay = retry_delay(exc.headers)
                self.retry_at = time.monotonic() + delay
                raise RateLimited(delay) from exc
            if exc.code in (400, 401):
                raise AuthRequired("Ta session a expiré. Connecte-toi à nouveau à Spotify.") from exc
            raise UserError("Spotify ne répond pas pour le moment. Ta connexion est conservée.") from exc
        self.access = payload["access_token"]
        self.expires = time.monotonic() + payload.get("expires_in", 3600) - 60
        if payload.get("refresh_token"):
            self.store_refresh(payload["refresh_token"])
        return self.access

    def store_refresh(self, refresh):
        self.persisted = bool(self.vault.put(self.client, refresh))
        path = self.settings.root / "session.json"
        if self.persisted:
            path.unlink(missing_ok=True)
        elif self.settings.data.get("session_kind") == "browser":
            temporary = path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump({"client_id": self.client, "refresh_token": refresh}, file)
            temporary.replace(path)

    def accept_browser_session(self, path, token):
        """Import only the session created by our bundled login component."""
        client = path.name.removesuffix("_token.json")
        if not re.fullmatch(r"[a-f0-9]{32}", client) or not token.get("refresh_token") or not token.get("access_token"):
            raise UserError("Session Spotify incomplète. Réessaie la connexion.")
        with self.lock:
            self.settings.data["client_id"] = client
            self.settings.data["session_kind"] = "browser"
            self.settings.data["session_id"] = secrets.token_hex(16)
            self.settings.save()
            self.access = token["access_token"]
            # Respect the helper's expiry instead of refreshing a one-hour token
            # after only 30 seconds. Unknown cache formats keep the safe fallback.
            remaining = token.get("expires_in", 0)
            if isinstance(remaining, dict):
                remaining = remaining.get("secs", 0)
            expires_at = token.get("expires_at")
            if isinstance(expires_at, str):
                try:
                    from datetime import datetime, timezone
                    remaining = (datetime.fromisoformat(expires_at.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError):
                    remaining = 0
            if not isinstance(remaining, (float, int)):
                remaining = 0
            self.expires = time.monotonic() + max(30, min(3600, remaining) - 60)
            self.store_refresh(token["refresh_token"])
            path.unlink(missing_ok=True)

    def token(self, force=False):
        with self.lock:
            if not force and self.access and time.monotonic() < self.expires:
                return self.access
            if time.monotonic() < self.retry_at:
                raise RateLimited(int(self.retry_at - time.monotonic()) + 1)
            refresh = self.vault.get(self.client)
            if not refresh and self.settings.data.get("session_kind") == "browser":
                try:
                    saved = json.loads((self.settings.root / "session.json").read_text())
                    if saved.get("client_id") == self.client:
                        refresh = saved.get("refresh_token")
                except (OSError, ValueError):
                    pass
            if not refresh:
                raise AuthRequired("Connecte-toi à Spotify pour retrouver ta musique.")
            return self.exchange({"grant_type": "refresh_token", "refresh_token": refresh})

    def login(self, open_browser):
        if not re.fullmatch(r"[a-fA-F0-9]{32}", self.client):
            raise UserError("Copie le Client ID de 32 caractères depuis le tableau de bord Spotify.")
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        result = {}
        self.cancelled.clear()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Codes and tokens must never enter logs.

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path != "/callback" or not secrets.compare_digest(query.get("state", [""])[0], state):
                    self.send_error(400, "Invalid OAuth state")
                    return
                if "error" in query:
                    result["error"] = "Connexion annulée dans Spotify."
                elif query.get("code"):
                    result["code"] = query["code"][0]
                else:
                    self.send_error(400)
                    return
                body = "<!doctype html><meta charset='utf-8'><title>Plume</title><h1>Tu peux revenir dans Plume.</h1><p>Cette fenêtre peut être fermée.</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(body)

        try:
            server = http.server.HTTPServer(("127.0.0.1", 43821), Handler)
        except OSError as exc:
            raise UserError("Impossible d’ouvrir le port local 43821. Vérifie qu’il est libre et autorisé.") from exc
        server.timeout = 0.5
        with server:
            url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
                "client_id": self.client, "response_type": "code", "redirect_uri": REDIRECT,
                "code_challenge_method": "S256", "code_challenge": pkce_challenge(verifier),
                "state": state, "scope": SCOPES})
            open_browser(url)
            deadline = time.monotonic() + 180
            while not result and not self.cancelled.is_set() and time.monotonic() < deadline:
                server.handle_request()
        if self.cancelled.is_set():
            raise UserError("Connexion annulée.")
        if "error" in result:
            raise UserError(result["error"])
        if "code" not in result:
            raise UserError("La connexion a expiré après 3 minutes. Réessaie.")
        with self.lock:
            return self.exchange({"grant_type": "authorization_code", "code": result["code"],
                                  "redirect_uri": REDIRECT, "code_verifier": verifier})

    def logout(self):
        self.cancelled.set()
        with self.lock:
            self.vault.clear(self.client)
            (self.settings.root / "session.json").unlink(missing_ok=True)
            self.access, self.expires = None, 0
            self.settings.data.pop("session_id", None)
            self.settings.save()


class SpotifyAPI:
    def __init__(self, oauth):
        self.oauth = oauth
        self.retry_at = 0

    def request(self, path, method="GET", body=None, **params):
        # No caller-controlled host: pagination URLs cannot leak the bearer token.
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("API paths must be relative")
        # The bundled client uses an established extended-quota application.
        # Its playlist endpoint retains the original /tracks contract.
        if (self.oauth.settings.data.get("session_kind") == "browser"
                and method == "GET" and re.fullmatch(r"/playlists/[A-Za-z0-9]+/items", path)):
            path = path.removesuffix("items") + "tracks"
        if time.monotonic() < self.retry_at:
            raise RateLimited(int(self.retry_at-time.monotonic())+1)
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        for attempt in range(2):
            token = self.oauth.token(force=attempt == 1)
            try:
                return read_json(url, method, {"Authorization": "Bearer " + token,
                    "Content-Type": "application/json"},
                    json.dumps(body).encode() if body is not None else None)
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code == 401 and attempt == 0:
                    continue
                if exc.code == 429:
                    delay = retry_delay(exc.headers)
                    self.retry_at = time.monotonic() + delay
                    raise RateLimited(delay) from exc
                if exc.code == 401:
                    raise AuthRequired("Ta session a expiré. Connecte-toi à nouveau à Spotify.") from exc
                messages = {401: "Session expirée. Reconnecte-toi dans Réglages.",
                    403: "Spotify refuse cette action. Vérifie ton abonnement Premium ou réessaie avec une autre sélection.",
                    404: "Aucun lecteur actif, ou contenu indisponible. Sélectionne un appareil dans Appareils."}
                raise UserError(messages.get(exc.code, f"Spotify est indisponible (erreur {exc.code}). Réessaie.")) from exc


def track_from_row(row):
    return row.get("item") or row.get("track") or {}


def parse_lrc(text):
    lines = []
    for line in text.splitlines():
        stamps = re.findall(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]", line)
        words = re.sub(r"\[[^\]]*\]", "", line).strip()
        for minutes, seconds, fraction in stamps:
            if int(seconds) >= 60:
                continue
            timestamp = int(minutes)*60_000 + int(seconds)*1000 + int((fraction or "0").ljust(3, "0"))
            lines.append((timestamp, words))
    return sorted(lines)


class Lyrics:
    def __init__(self):
        self.cache = OrderedDict()

    def get(self, track):
        key = (track.get("id"), track.get("name"), track.get("duration_ms"))
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        params = {"track_name": track.get("name", ""),
                  "artist_name": ", ".join(a["name"] for a in track.get("artists", [])),
                  "album_name": track.get("album", {}).get("name", ""),
                  "duration": round(track.get("duration_ms", 0)/1000)}
        try:
            data = read_json("https://lrclib.net/api/get?" + urllib.parse.urlencode(params),
                             headers={"User-Agent": "Plume/0.1.0 (independent Linux music client)"})
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 404:
                data = None
            else:
                raise UserError("Le service de paroles est temporairement indisponible.") from exc
        result = {"lines": parse_lrc((data or {}).get("syncedLyrics") or ""),
                  "plain": (data or {}).get("plainLyrics") or "",
                  "instrumental": (data or {}).get("instrumental", False)}
        self.cache[key] = result
        while len(self.cache) > 5:
            self.cache.popitem(last=False)
        return result


def format_time(ms):
    seconds = max(0, int(ms or 0)//1000)
    return f"{seconds//60}:{seconds%60:02}"
