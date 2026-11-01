import time
import unittest
from app.services.catalog_service import catalog_service
from app.services.stream_service import stream_service


class TestEmptyCatalogs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Set a dummy match in Serie A
        self.dummy_match = {
            "id": "match-test-1",
            "title": "Inter vs Juventus",
            "date": 2000000000000,
            "_catalog": "calcio_italiano",
            "_genre": "Serie A",
            "sources": [{"id": "1", "name": "Source 1", "url": "http://stream.test"}],
        }
        catalog_service._cached_matches = [self.dummy_match]

    async def test_empty_genre_returns_empty_list(self):
        # Serie C has 0 matches -> must return empty list []
        metas = await catalog_service.get_catalog(
            catalog_id="calcio_italiano",
            genre_filter="Serie C",
            skip=0,
        )
        self.assertEqual(metas, [])

    async def test_empty_catalog_returns_empty_list(self):
        # Motori has 0 matches -> must return empty list []
        metas = await catalog_service.get_catalog(
            catalog_id="motori",
            genre_filter=None,
            skip=0,
        )
        self.assertEqual(metas, [])

    async def test_non_empty_genre_returns_real_items(self):
        # Serie A has 1 match
        metas = await catalog_service.get_catalog(
            catalog_id="calcio_italiano",
            genre_filter="Serie A",
            skip=0,
        )
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["id"], "streamsport:match-test-1")
        self.assertIn("Inter vs Juventus", metas[0]["name"])

    async def test_empty_pagination_returns_empty(self):
        # Pagination beyond page 0 on empty list should return []
        metas = await catalog_service.get_catalog(
            catalog_id="calcio_italiano",
            genre_filter="Serie C",
            skip=50,
        )
        self.assertEqual(len(metas), 0)

    async def test_no_meta_detail_for_unknown_empty_slug(self):
        meta = await catalog_service.get_meta_detail("streamsport:empty:calcio_italiano:serie_c")
        self.assertIsNone(meta)



    def test_is_real_match_official_vs_third_party(self):
        now = time.time() * 1000
        # Official upcoming match with 0 sources -> must be allowed in catalog
        off_upcoming = {
            "id": "diretta-abc123",
            "flashscore_id": "abc123",
            "title": "Virtus Bologna vs Milano",
            "date": now + 7200000,
            "category": "basketball",
            "_silo": "basketball",
            "sources": []
        }
        self.assertTrue(catalog_service.is_real_match(off_upcoming))

        # Official live match with 0 sources -> must be allowed for JIT stream fallback
        off_live = {
            "id": "diretta-abc123",
            "flashscore_id": "abc123",
            "title": "Virtus Bologna vs Milano",
            "date": now - 1800000,
            "category": "basketball",
            "_silo": "basketball",
            "sources": []
        }
        self.assertTrue(catalog_service.is_real_match(off_live))

        # Official concluded match with 0 sources -> must NOT be allowed (no replay)
        off_concluded = {
            "id": "diretta-abc123",
            "flashscore_id": "abc123",
            "title": "Virtus Bologna vs Milano",
            "date": now - 36000000,
            "category": "basketball",
            "_silo": "basketball",
            "sources": []
        }
        self.assertFalse(catalog_service.is_real_match(off_concluded))

        # Third-party provider match with 0 sources -> must NOT be allowed
        provider_no_src = {
            "id": "daddylive-12345",
            "title": "Random Event",
            "date": now + 7200000,
            "sources": []
        }
        self.assertFalse(catalog_service.is_real_match(provider_no_src))

    async def test_official_upcoming_match_without_sources_appears_in_catalog(self):
        now = time.time() * 1000
        off_upcoming = {
            "id": "diretta-xyz789",
            "flashscore_id": "xyz789",
            "title": "Trento vs Roma",
            "date": now + 7200000,
            "_catalog": "basket",
            "_genre": "LBA Serie A",
            "sources": []
        }
        catalog_service._cached_matches.append(off_upcoming)
        metas = await catalog_service.get_catalog(
            catalog_id="basket",
            genre_filter="LBA Serie A",
            skip=0,
        )
        self.assertGreaterEqual(len(metas), 1)
        self.assertTrue(any("Trento vs Roma" in m["name"] for m in metas))

if __name__ == "__main__":
    unittest.main()
