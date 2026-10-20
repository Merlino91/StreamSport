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


if __name__ == "__main__":
    unittest.main()
