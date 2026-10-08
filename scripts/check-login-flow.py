#!/usr/bin/env python3
"""Exercise the GTK login gate with isolated, synthetic account responses."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['GSK_RENDERER'] = 'cairo'
runtime = tempfile.TemporaryDirectory(prefix='plume-login-test-')
os.environ['PLUME_HOME'] = runtime.name
from plume.app import Plume, GLib
from plume.core import UserError, AuthRequired, RateLimited

app = Plume()
checks = []
errors = []
release = threading.Event()
urls = []
launches = []
phase = 0
calls = []
started = time.monotonic()
app.engine.start = Mock()
app.engine.logout = Mock()
app.oauth.logout = Mock()
app.oauth.accept_browser_session = Mock()
app.browser.open = lambda url, window, done, choose=False: (urls.append(url), done('Navigateur indisponible · test'))
app.browser.close = Mock()
app.api.request = lambda path, *args, **kwargs: {
    '/me': {'id':'fixture', 'display_name':'Compte de test'},
    '/me/playlists': {'items':[{'id':'test','name':'Playlist personnelle','uri':'spotify:playlist:test'}], 'total':1},
    '/me/player': None,
}.get(path, {'items':[]})


def authenticate(browser):
    launches.append(True)
    browser('https://accounts.spotify.com/authorize?fixture=first')
    while not release.wait(.02):
        if app.engine.login_cancelled.is_set():
            raise UserError('Connexion annulée.')
    browser('https://accounts.spotify.com/authorize?fixture=second')
    (app.engine.cache/'credentials.json').write_text(json.dumps({'username':'fixture','auth_data':'synthetic'}))
    return app.engine.cache/'synthetic_token.json', {'access_token':'synthetic'}


app.engine.authenticate = authenticate


def check():
    global phase, calls
    try:
        if time.monotonic() - started > 50:
            raise AssertionError('Login flow timed out')
        if phase == 0:
            assert app.stack.get_visible_child_name() == 'welcome'
            for page in ('library','liked','search','lyrics','devices','settings'):
                app.navigate(page)
                assert app.stack.get_visible_child_name() == 'welcome'
            checks.append('all music pages gated before login')
            app.login()
            app.login()
            phase = 1
        elif phase == 1 and urls:
            assert len(launches) == 1
            assert app.logging_in and not app.connected
            assert app.login_fallback.get_visible()
            assert 'indisponible' in app.login_status.get_text()
            checks.append('browser failure retains active login and retry link; duplicate clicks ignored')
            release.set()
            phase = 2
        elif phase == 2 and app.connected and app.library_items:
            assert app.stack.get_visible_child_name() == 'music'
            assert app.page == 'library'
            assert app.library_items[0]['name'] == 'Playlist personnelle'
            assert app.login_url is None
            checks.append('verified account opens personal library automatically')
            app.logout()
            phase = 3
        elif phase == 3 and not app.restoring:
            assert app.stack.get_visible_child_name() == 'welcome'
            assert not app.connected and not app.library_items
            assert app.login_btn.get_sensitive()
            checks.append('logout returns to welcome and removes library')
            release.clear()
            urls.clear()
            app.login()
            phase = 4
        elif phase == 4 and urls:
            app.cancel_login()
            phase = 5
        elif phase == 5 and not app.logging_in:
            assert not app.connected
            assert app.login_url is None
            assert not app.login_fallback.get_visible()
            assert app.login_btn.get_sensitive()
            checks.append('cancellation clears expired URL and enables retry')
            app.api.request = Mock(side_effect=AuthRequired('Session expirée'))
            app.restore_session()
            phase = 6
        elif phase == 6 and not app.restoring:
            assert app.stack.get_visible_child_name() == 'welcome'
            assert app.login_btn.get_sensitive()
            checks.append('expired saved session returns to sign-in')
            calls = []
            def delayed_api(path, *args, **kwargs):
                if path == '/me':
                    calls.append(path)
                    if len(calls) == 1:
                        raise RateLimited(1)
                    return {'id':'fixture', 'display_name':'Compte de test'}
                if path == '/me/playlists':
                    return {'items':[{'id':'restored','name':'Bibliothèque retrouvée'}], 'total':1}
                return None
            app.api.request = delayed_api
            app.restore_session()
            phase = 7
        elif phase == 7 and app.session_retry_source:
            assert app.restoring and app.session_pending
            assert not app.login_btn.get_sensitive()
            assert not app.connected
            assert 'conservée' in app.login_status.get_text()
            checks.append('rate limit retains session and schedules retry without browser login')
            phase = 8
        elif phase == 8 and app.connected and app.library_items:
            assert app.stack.get_visible_child_name() == 'music'
            assert app.library_items[0]['name'] == 'Bibliothèque retrouvée'
            assert len(launches) == 2
            checks.append('scheduled retry recovers account and playlists without reauthorization')
            app.connected = False
            app.library_cache.clear()
            calls = []
            def unavailable(*args, **kwargs):
                calls.append(True)
                raise RateLimited(1)
            app.api.request = unavailable
            app.restore_session()
            phase = 9
        elif phase == 9 and not app.restoring:
            assert not app.verifying and app.login_btn.get_sensitive()
            assert app.session_pending and app.session_error
            assert not app.session_retry_source and not app.session_watchdog
            assert len(calls) == 3
            checks.append('persistent Spotify limit stops after three attempts; retry button usable')
            app.restore_session()
            app.end_session_wait('Test timeout', True)
            phase = 10
        elif phase == 10 and not app.verifying:
            assert app.login_btn.get_sensitive()
            assert not app.session_retry_source and not app.session_watchdog
            checks.append('timeout invalidates late replies and cancels scheduled retries')
            app.quit()
            return False
    except Exception:
        errors.append(traceback.format_exc())
        release.set()
        app.quit()
        return False
    return True


GLib.timeout_add(100, check)
app.run([])
if len(checks) != 10 and not errors:
    errors.append('Not all login scenarios ran; use an isolated D-Bus session.')
report={'checks':checks,'errors':errors,'real_account_used':False}
(ROOT/'verification'/'login-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
runtime.cleanup()
raise SystemExit(bool(errors))
