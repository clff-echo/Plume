import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from plume.core import Settings
from plume.engine import Engine
from plume import platform_support

class PlatformTests(unittest.TestCase):
    def test_windows_engine_has_windows_audio_options(self):
        with tempfile.TemporaryDirectory() as temp, patch('plume.engine.WINDOWS', True):
            engine = Engine(Settings(temp))
            engine.binary = 'librespot.exe'
            cmd = engine.command()
            self.assertIn('--system-cache', cmd)
            self.assertIn('--disable-audio-cache', cmd)
            self.assertNotIn('--use-mpris', cmd)
            self.assertIn('rodio', cmd)
            engine.close()

    def test_windows_settings_use_local_appdata(self):
        with patch.object(platform_support, 'WINDOWS', True), patch.dict('os.environ', {'LOCALAPPDATA':'C:/Users/Test/AppData/Local'}):
            self.assertEqual(platform_support.config_home(), Path('C:/Users/Test/AppData/Local/Plume'))

    def test_untrusted_browser_url_rejected_on_windows(self):
        from plume.engine import open_browser
        from plume.core import UserError
        with patch('plume.engine.WINDOWS', True), patch('webbrowser.open') as browser:
            with self.assertRaises(UserError): open_browser('https://malicious.example/')
            browser.assert_not_called()

if __name__ == '__main__': unittest.main()
