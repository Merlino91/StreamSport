import tempfile
import unittest
from pathlib import Path

from app.services.db_service import DBService


class TestDBServiceAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test_async_events.db"
        self.service = DBService(db_path=self.db_path)

    def tearDown(self):
        self.service.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    async def test_async_upsert_and_get_match(self):
        matches = [
            {
                "id": "match_async_1",
                "title": "Milan vs Inter",
                "category": "football",
                "date": 2000000000000,
                "sources": [{"id": "s1", "name": "Sky Sport"}],
                "competition": "Serie A",
            },
            {
                "id": "match_async_2",
                "title": "Juventus vs Roma",
                "category": "football",
                "date": 2000000005000,
                "sources": [],
                "competition": "Serie A",
            },
        ]

        # 1. Test upsert_matches_async
        await self.service.upsert_matches_async(matches)

        # 2. Test get_match_by_id_async
        m1 = await self.service.get_match_by_id_async("match_async_1")
        self.assertIsNotNone(m1)
        self.assertEqual(m1["title"], "Milan vs Inter")
        self.assertEqual(m1["competition"], "Serie A")
        self.assertEqual(len(m1["sources"]), 1)

        # 3. Test get_active_matches_async
        active = await self.service.get_active_matches_async()
        self.assertEqual(len(active), 2)

        # 4. Test update_match_poster_async
        await self.service.update_match_poster_async("match_async_1", "https://img.test/poster1.jpg")
        m1_updated = await self.service.get_match_by_id_async("match_async_1")
        self.assertEqual(m1_updated["poster"], "https://img.test/poster1.jpg")

        # 5. Test delete_matches_by_ids_async
        deleted = await self.service.delete_matches_by_ids_async(["match_async_1"])
        self.assertEqual(deleted, 1)

        m1_after_del = await self.service.get_match_by_id_async("match_async_1")
        self.assertIsNone(m1_after_del)

    async def test_async_poster_cache(self):
        q_key = "Inter vs Milan"
        thumb_url = "https://img.test/derby.jpg"

        # 1. Save to cache async
        await self.service.save_poster_to_cache_async(q_key, thumb_url, "found")

        # 2. Retrieve from cache async
        cached = await self.service.get_poster_from_cache_async(q_key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0], "found")
        self.assertEqual(cached[1], thumb_url)

        # 3. Save failed lookup
        await self.service.save_poster_to_cache_async("Unknown vs Mystery", None, "not_found")
        failed = await self.service.get_poster_from_cache_async("Unknown vs Mystery")
        self.assertIsNotNone(failed)
        self.assertEqual(failed[0], "not_found")
        self.assertIsNone(failed[1])

        # 4. Clear failed cache async
        await self.service.clear_failed_poster_cache_async()
        failed_cleared = await self.service.get_poster_from_cache_async("Unknown vs Mystery")
        self.assertIsNone(failed_cleared)

        # Found entry remains intact
        still_found = await self.service.get_poster_from_cache_async(q_key)
        self.assertIsNotNone(still_found)

    async def test_async_purge_expired_matches(self):
        past_match = {
            "id": "old_match",
            "title": "Historical Match",
            "category": "football",
            "date": 1000000000,  # Far in the past
            "sources": [],
        }
        await self.service.upsert_matches_async([past_match])

        purged = await self.service.purge_expired_matches_async(max_age_hours=24)
        self.assertGreaterEqual(purged, 1)

        m = await self.service.get_match_by_id_async("old_match")
        self.assertIsNone(m)

    def test_connection_pooling_and_close(self):
        # 1. Connection is reused on the same thread
        conn1 = self.service._get_connection()
        conn2 = self.service._get_connection()
        self.assertIs(conn1, conn2)

        # 2. Total all_conns contains this connection
        self.assertIn(conn1, self.service._all_conns)

        # 3. Close properly closes and resets thread-local state
        self.service.close()
        self.assertIsNone(getattr(self.service._local, "conn", None))
        self.assertEqual(len(self.service._all_conns), 0)

        # 4. Next call re-creates a clean connection
        conn3 = self.service._get_connection()
        self.assertIsNotNone(conn3)
        self.assertIsNot(conn1, conn3)


if __name__ == "__main__":
    unittest.main()
