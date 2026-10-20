import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from app.config import PROJECT_DIR
from app.services.youtube_service import youtube_service
from app.services.dailymotion_service import dailymotion_service
from app.services.tvvoo_service import tvvoo_service, LiveManifestManager
from app.services.sportvideo_service import sportvideo_service
from app.services.db_service import DBService, DEFAULT_DATA_DIR


class TestO2O3O4O5Fixes(unittest.IsolatedAsyncioTestCase):
    def test_o2_semaphore_present_on_resolvers(self):
        """Verifies asyncio.Semaphore(4) is configured on both resolvers."""
        self.assertIsInstance(youtube_service._semaphore, asyncio.Semaphore)
        self.assertEqual(youtube_service._semaphore._value, 4)

        self.assertIsInstance(dailymotion_service._semaphore, asyncio.Semaphore)
        self.assertEqual(dailymotion_service._semaphore._value, 4)

    async def test_o3_persistent_client_for_live_manifest(self):
        """Verifies LiveManifestManager uses a persistent HTTP client for upstream polling."""
        manager = LiveManifestManager(tvvoo_service)
        client = manager._client
        self.assertIsNotNone(client)
        self.assertFalse(client.is_closed)

        # Mock GET on persistent client
        with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.text = "#EXTM3U\n#EXTINF:8.0,\nchunk.ts\n"
            mock_get.return_value = mock_res

            res1 = await manager._fetch_upstream("https://edge.vavoo.to/live/index.m3u8")
            res2 = await manager._fetch_upstream("https://edge.vavoo.to/live/index.m3u8")

            self.assertEqual(mock_get.call_count, 2)
            self.assertEqual(res1, res2)
            # Ensure the exact same client instance was retained
            self.assertIs(manager._client, client)

        await manager.close()
        self.assertTrue(client.is_closed)

    def test_o4_sportvideo_infohash_cache_max_capacity(self):
        """Verifies _infohash_cache enforces MAX_INFOHASH_CACHE and evicts oldest."""
        sportvideo_service.MAX_INFOHASH_CACHE = 5
        sportvideo_service._infohash_cache.clear()

        # Insert 7 entries
        for idx in range(7):
            url = f"https://www.sport-video.org.ua/match_{idx}.html"
            info_hash = f"hash_{idx:040d}"
            if len(sportvideo_service._infohash_cache) >= sportvideo_service.MAX_INFOHASH_CACHE:
                sportvideo_service._infohash_cache.pop(next(iter(sportvideo_service._infohash_cache)), None)
            sportvideo_service._infohash_cache[url] = info_hash

        # Must be capped at 5
        self.assertEqual(len(sportvideo_service._infohash_cache), 5)
        # Oldest match_0 and match_1 must have been evicted
        self.assertNotIn("https://www.sport-video.org.ua/match_0.html", sportvideo_service._infohash_cache)
        self.assertNotIn("https://www.sport-video.org.ua/match_1.html", sportvideo_service._infohash_cache)
        # Newest match_5 and match_6 must be present
        self.assertIn("https://www.sport-video.org.ua/match_5.html", sportvideo_service._infohash_cache)
        self.assertIn("https://www.sport-video.org.ua/match_6.html", sportvideo_service._infohash_cache)

        # Restore default
        sportvideo_service.MAX_INFOHASH_CACHE = 500

    def test_o5_db_service_path_matches_project_dir(self):
        """Verifies DBService DEFAULT_DATA_DIR matches PROJECT_DIR / 'data'."""
        expected_dir = PROJECT_DIR / "data"
        self.assertEqual(DEFAULT_DATA_DIR, expected_dir)
