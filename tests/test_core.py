import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request

from plume.core import OAuth, Settings, SpotifyAPI, Lyrics, UserError, AuthRequired, RateLimited, pkce_challenge, parse_lrc, track_from_row
from plume.engine import Engine, memory_usage, open_browser
from plume.browser_login import completed_session, login_with_browser


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(self.temp.name)
        self.settings.data['client_id'] = 'a'*32
        self.vault = Mock()
        self.vault.get.return_value = 'refresh'
        self.oauth = OAuth(self.settings, self.vault)

    def test_pkce_rfc7636_vector(self):
        self.assertEqual(pkce_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
                         'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')

    def test_settings_permissions_no_tokens(self):
        self.settings.save()
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('access_token', self.settings.path.read_text())

    def test_browser_session_import_and_private_fallback(self):
        self.vault.put.return_value = False
        path = self.settings.root / ('b'*32 + '_token.json')
        path.write_text('{}')
        self.oauth.accept_browser_session(path, {'access_token':'fresh', 'refresh_token':'renew'})
        self.assertEqual(self.oauth.token(), 'fresh')
        self.assertFalse(path.exists())
        self.assertEqual(self.settings.data['session_kind'], 'browser')
        saved = self.settings.root/'session.json'
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('access_token', saved.read_text())
        self.oauth.logout()
        self.assertFalse(saved.exists())

    def test_fresh_helper_token_is_not_refreshed_after_thirty_seconds(self):
        import time
        path = self.settings.root / ('b'*32 + '_token.json')
        self.oauth.accept_browser_session(path, {'access_token':'fresh','refresh_token':'renew',
            'expires_in':{'secs':3600,'nanos':0}})
        self.assertGreater(self.oauth.expires - time.monotonic(), 3500)

    def test_saved_browser_session_refreshes_without_another_login(self):
        self.settings.data['session_kind'] = 'browser'
        (self.settings.root/'session.json').write_text(json.dumps({'client_id':'a'*32,'refresh_token':'saved'}))
        self.vault.get.return_value = None
        with patch('plume.core.read_json', return_value={'access_token':'renewed','expires_in':3600}) as request:
            self.assertEqual(self.oauth.token(), 'renewed')
            self.assertIn(b'refresh_token=saved', request.call_args.args[3])

    def test_login_requires_both_sessions(self):
        cache = self.settings.root
        (cache/('a'*32+'_token.json')).write_text(json.dumps({'access_token':'a','refresh_token':'b'}))
        self.assertIsNone(completed_session(cache))
        (cache/'credentials.json').write_text(json.dumps({'auth_data':'test'}))
        self.assertIsNotNone(completed_session(cache))

    def test_helper_stages_then_imports_without_using_old_audio_account(self):
        root = self.settings.root/'fake-helper'
        (root/'vendor').mkdir(parents=True)
        binary = root/'vendor'/'spotify_player'
        binary.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
c=pathlib.Path(sys.argv[sys.argv.index('-C')+1])
assert not (c/'credentials.json').exists()
(c/'credentials.json').write_text(json.dumps({'username':'new-account','auth_data':'test'}))
(c/('b'*32+'_token.json')).write_text(json.dumps({'access_token':'test','refresh_token':'test'}))
''')
        binary.chmod(0o755)
        engine = Engine(self.settings)
        (engine.cache/'credentials.json').write_text(json.dumps({'username':'old-account','auth_data':'old'}))
        with patch('plume.browser_login.ROOT', root):
            path, token = login_with_browser(engine, Mock())
        self.assertEqual(json.loads((engine.cache/'credentials.json').read_text())['username'], 'new-account')
        self.assertTrue(path.is_file())
        self.assertEqual(token['access_token'], 'test')
        self.assertIsNone(engine.auth_process)
        self.assertEqual(list(self.settings.root.glob('login-*')), [self.settings.root/'login-helper'])

    def test_established_browser_client_playlist_endpoint(self):
        self.settings.data['session_kind'] = 'browser'
        self.oauth.access = 'token'
        self.oauth.expires = float('inf')
        with patch('plume.core.read_json', return_value={'items':[]}) as request:
            SpotifyAPI(self.oauth).request('/playlists/abc123/items', limit=50)
            self.assertIn('/playlists/abc123/tracks?', request.call_args.args[0])

    def test_refresh_rotates_and_caches(self):
        with patch('plume.core.read_json', return_value={'access_token': 'access', 'refresh_token': 'new', 'expires_in': 3600}) as network:
            self.assertEqual(self.oauth.token(), 'access')
            self.assertEqual(self.oauth.token(), 'access')
            self.assertEqual(network.call_count, 1)
            self.vault.put.assert_called_once_with('a'*32, 'new')

    def test_oauth_rejects_wrong_state_then_accepts_real_callback(self):
        observed = []
        callbacks = []
        def browser(url):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertIn('code_challenge', params)
            def callback():
                try:
                    urllib.request.urlopen('http://127.0.0.1:43821/callback?code=bad&state=wrong', timeout=3)
                except urllib.error.HTTPError as exc:
                    observed.append(exc.code)
                    exc.close()
                url = 'http://127.0.0.1:43821/callback?' + urllib.parse.urlencode({'code':'valid', 'state':params['state'][0]})
                with urllib.request.urlopen(url, timeout=3) as response:
                    observed.append(response.status)
            worker = threading.Thread(target=callback, daemon=True)
            callbacks.append(worker)
            worker.start()
        with patch.object(self.oauth, 'exchange', return_value='ok') as exchange:
            self.assertEqual(self.oauth.login(browser), 'ok')
            self.assertEqual(exchange.call_args.args[0]['code'], 'valid')
        for worker in callbacks:
            worker.join(timeout=4)
        self.assertEqual(observed, [400, 200])

    def test_401_refresh_and_retry_once(self):
        oauth = Mock()
        oauth.token.side_effect = ['old', 'new']
        api = SpotifyAPI(oauth)
        error = urllib.error.HTTPError('url', 401, 'unauthorized', {}, io.BytesIO())
        with patch('plume.core.read_json', side_effect=[error, {'id':'me'}]) as network:
            self.assertEqual(api.request('/me'), {'id':'me'})
            self.assertEqual(network.call_count, 2)
            self.assertEqual(network.call_args.args[2]['Authorization'], 'Bearer new')
        oauth.token.assert_called_with(force=True)

    def test_429_cooldown_prevents_more_network_calls(self):
        api = SpotifyAPI(Mock())
        api.oauth.token.return_value = 'token'
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After':'45'}, io.BytesIO())
        with patch('plume.core.read_json', side_effect=error) as network:
            for _ in range(2):
                with self.assertRaises(UserError):
                    api.request('/me')
            self.assertEqual(network.call_count, 1)

    def test_token_rate_limit_preserves_session(self):
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After':'12'}, io.BytesIO())
        with patch('plume.core.read_json', side_effect=error):
            with self.assertRaises(RateLimited):
                self.oauth.token()
        self.vault.clear.assert_not_called()
        self.assertTrue(self.oauth.client)

    def test_invalid_refresh_requires_login(self):
        error = urllib.error.HTTPError('url', 400, 'invalid_grant', {}, io.BytesIO())
        with patch('plume.core.read_json', side_effect=error):
            with self.assertRaises(AuthRequired):
                self.oauth.token()

    def test_api_rejects_external_pagination_url(self):
        for path in ['https://evil.test/steal', '//evil.test', 'me']:
            with self.assertRaises(ValueError):
                SpotifyAPI(Mock()).request(path)

    def test_playlist_2026_and_legacy(self):
        self.assertEqual(track_from_row({'item': {'name':'New'}})['name'], 'New')
        self.assertEqual(track_from_row({'track': {'name':'Old'}})['name'], 'Old')
        self.assertEqual(track_from_row({'item':None}), {})

    def test_lrc_multiple_timestamps_and_fractions(self):
        self.assertEqual(parse_lrc('[ar:Example]\n[00:01.5][01:02.050]Hello\n[00:03.12]World\n[00:99]Bad'),
                         [(1500,'Hello'),(3120,'World'),(62050,'Hello')])

    def test_lyrics_404_cache_bounded_no_auth(self):
        lyrics = Lyrics()
        with patch('plume.core.read_json', return_value={'syncedLyrics':'[00:01]Test'}) as network:
            for i in range(10):
                lyrics.get({'id':str(i),'name':'Test','artists':[]})
            self.assertEqual(len(lyrics.cache), 5)
            self.assertNotIn('Authorization', network.call_args.kwargs['headers'])
        with patch('plume.core.read_json', side_effect=urllib.error.HTTPError('url',404,'missing',{},io.BytesIO())):
            self.assertEqual(lyrics.get({'id':'missing'})['lines'], [])

    def test_engine_command_cache_and_identity(self):
        engine = Engine(self.settings)
        engine.binary = '/engine'
        command = engine.command()
        self.assertIn('--no-audio-cache', command)
        self.assertIn('--no-daemon', command)
        self.assertIn('Plume · Linux', command)
        self.assertNotIn('--password', command)

    def test_engine_shutdown_reaps_child(self):
        import subprocess
        engine = Engine(self.settings)
        engine.process = subprocess.Popen(['sleep', '30'])
        engine.stop()
        self.assertIsNotNone(engine.process.poll())
        engine.close()
        with patch('plume.engine.subprocess.Popen') as launch:
            engine.start()
            launch.assert_not_called()

    def test_browser_rejects_untrusted_links(self):
        with self.assertRaises(UserError):
            open_browser('https://evil.test/')

    def test_memory_includes_child_process(self):
        import subprocess, time
        before = memory_usage()
        child = subprocess.Popen(['python3','-c','import time; a=bytearray(20_000_000); print("ok",flush=True); time.sleep(10)'], stdout=subprocess.PIPE)
        try:
            child.stdout.readline()
            self.assertGreater(memory_usage()-before, 18_000_000)
        finally:
            child.terminate()
            child.wait()
            child.stdout.close()


if __name__ == '__main__':
    unittest.main()
