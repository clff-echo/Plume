import json
from pathlib import Path
import tempfile
import unittest
from plume.cache import LibraryCache, MAX_FILES, MAX_MEMORY
from plume.core import Settings


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings=Settings(self.temp.name)
        self.settings.data['client_id']='a'*32
        self.cache=LibraryCache(self.settings)
        self.profile={'id':'fixture','display_name':'Test'}
        self.page={'items':[{'id':'one','name':'One','uri':'spotify:playlist:one','tracks':{'total':2}}],'total':1}
        self.params={'limit':24,'offset':0}
        self.cache.activate(self.profile)

    def test_profile_and_pages_survive_restart_for_same_session(self):
        self.cache.put('/me/playlists',self.params,self.page)
        other=LibraryCache(Settings(self.temp.name))
        self.assertEqual(other.remembered_profile(),self.profile)
        other.activate(self.profile)
        self.assertEqual(other.get('/me/playlists',self.params)[0],self.page)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.cache.root.glob('*.json')))

    def test_new_authorization_cannot_reuse_previous_account(self):
        self.cache.put('/me/playlists',self.params,self.page)
        self.settings.data['session_id']='different-authorization'
        self.assertIsNone(self.cache.remembered_profile())
        self.cache.activate({'id':'someone-else'})
        self.assertIsNone(self.cache.get('/me/playlists',self.params))

    def test_audio_account_change_invalidates_profile(self):
        audio=self.settings.root/'audio'
        audio.mkdir()
        (audio/'credentials.json').write_text(json.dumps({'username':'different','auth_data':'synthetic'}))
        self.assertIsNone(self.cache.remembered_profile())

    def test_cache_is_bounded_and_logout_removes_metadata(self):
        for i in range(MAX_FILES+5):
            self.cache.put('/playlists/'+str(i),self.params,self.page)
        self.assertLessEqual(len(list(self.cache.root.glob('*.json'))),MAX_FILES+1)
        self.assertLessEqual(sum(map(len,self.cache.memory.values())),MAX_MEMORY)
        self.cache.clear()
        self.assertEqual(list(self.cache.root.iterdir()),[])
        self.assertIsNone(self.cache.remembered_profile())

    def test_track_cache_preserves_playback_fields_without_unneeded_data(self):
        track={'id':'x','uri':'spotify:track:x','name':'X','duration_ms':180000,'artists':[{'id':'a','name':'Artist'}],
               'album':{'id':'b','name':'Album','images':[]},'is_playable':True,'unused':'x'*5000}
        self.cache.put('/me/tracks',{}, {'items':[{'item':track}], 'total':1})
        restored=self.cache.get('/me/tracks',{})[0]['items'][0]['track']
        self.assertEqual(restored['uri'],track['uri'])
        self.assertEqual(restored['duration_ms'],180000)
        self.assertNotIn('unused',restored)

    def test_corrupt_cache_is_a_miss(self):
        self.cache.put('/me/playlists',self.params,self.page)
        file=self.cache.root/(self.cache.key('/me/playlists',self.params)+'.json')
        file.write_text('not json')
        self.cache.memory.clear()
        self.assertIsNone(self.cache.get('/me/playlists',self.params))
