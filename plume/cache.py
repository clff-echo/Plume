"""Bounded metadata cache tied to a verified account and this exact login.

No audio, access tokens or refresh tokens are stored here. A new authorization
rotates the namespace, and logout removes these disposable files.
"""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from .core import private_dir

MAX_ENTRY = 256_000
MAX_MEMORY = 1_000_000
MAX_DISK = 8_000_000
MAX_FILES = 48


def compact_page(data):
    def trim_track(track):
        if not isinstance(track, dict):
            return None
        result = {k: track[k] for k in ('id','uri','name','duration_ms','is_local','is_playable','type') if k in track}
        result['artists'] = [{k: a[k] for k in ('id','name','uri') if k in a} for a in track.get('artists', [])]
        album = track.get('album') or {}
        result['album'] = {k: album[k] for k in ('id','name','images') if k in album}
        return result
    items = []
    for item in data.get('items', []):
        if not isinstance(item, dict):
            items.append(None)
        elif 'item' in item or 'track' in item:
            items.append({'track': trim_track(item.get('item') or item.get('track'))})
        else:
            items.append({k: item[k] for k in ('id','uri','name','description','images','tracks','items','snapshot_id','type') if k in item})
    return {**{k: data[k] for k in ('total','next','offset','limit') if k in data}, 'items': items}


class LibraryCache:
    def __init__(self, settings):
        self.settings = settings
        self.root = private_dir(settings.root / 'library-cache')
        self.scope = None
        self.memory = OrderedDict()

    def audio_fingerprint(self):
        try:
            raw = (self.settings.root/'audio'/'credentials.json').read_bytes()
            if len(raw) > 65_536:
                return None
            return hashlib.sha256(raw).hexdigest()
        except OSError:
            return None

    def remembered_profile(self):
        session = self.settings.data.get('session_id')
        if not session:
            return None
        try:
            path = self.root/'profile.json'
            if path.stat().st_size > 16_384:
                return None
            cached = json.loads(path.read_text())
            if cached.get('session') != session or cached.get('client') != self.settings.data.get('client_id'):
                return None
            if cached.get('audio') != self.audio_fingerprint():
                return None
            profile = cached.get('profile')
            return profile if isinstance(profile, dict) and profile.get('id') else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def write(path, payload):
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
        temporary.replace(path)

    def activate(self, profile):
        if not profile.get('id'):
            return
        session = self.settings.data.get('session_id')
        if not session:
            session = secrets.token_hex(16)
            self.settings.data['session_id'] = session
            self.settings.save()
        self.scope = hashlib.sha256((session + ':' + profile['id']).encode()).hexdigest()
        compact = {k: profile[k] for k in ('id','display_name') if k in profile}
        try:
            self.write(self.root/'profile.json', json.dumps({'session':session,
                'client':self.settings.data.get('client_id'), 'audio':self.audio_fingerprint(),
                'profile':compact}, ensure_ascii=False).encode())
        except OSError:
            pass  # Caching must never turn a working sign-in into an error.

    def key(self, path, params):
        if not self.scope:
            return None
        request = json.dumps([self.scope, path, params], sort_keys=True)
        return hashlib.sha256(request.encode()).hexdigest()

    def get(self, path, params):
        key = self.key(path, params)
        if not key:
            return None
        raw = self.memory.get(key)
        try:
            if raw is None:
                file = self.root/(key+'.json')
                if file.stat().st_size > MAX_ENTRY:
                    return None
                raw = file.read_bytes()
                self.remember(key, raw)
            else:
                self.memory.move_to_end(key)
            stored = json.loads(raw)
            if stored.get('scope') != self.scope:
                return None
            age = max(0, time.time() - stored['saved'])
            return stored['data'], age
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def remember(self, key, raw):
        self.memory[key] = raw
        self.memory.move_to_end(key)
        while len(self.memory) > 16 or sum(map(len, self.memory.values())) > MAX_MEMORY:
            self.memory.popitem(last=False)

    def put(self, path, params, data):
        key = self.key(path, params)
        if not key or not isinstance(data, dict) or 'items' not in data:
            return
        raw = json.dumps({'scope':self.scope,'saved':time.time(),'data':compact_page(data)}, ensure_ascii=False).encode()
        if len(raw) > MAX_ENTRY:
            return
        self.remember(key, raw)
        try:
            self.write(self.root/(key+'.json'), raw)
            pages = sorted((p for p in self.root.glob('*.json') if p.name != 'profile.json'), key=lambda p:p.stat().st_mtime, reverse=True)
            size = 0
            for index, page in enumerate(pages):
                size += page.stat().st_size
                if index >= MAX_FILES or size > MAX_DISK:
                    page.unlink(missing_ok=True)
        except OSError:
            pass

    def clear(self):
        self.scope = None
        self.memory.clear()
        for path in self.root.glob('*'):
            if path.is_file() and path.suffix in ('.json','.tmp'):
                path.unlink(missing_ok=True)
