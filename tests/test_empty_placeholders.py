import unittest
from unittest.mock import patch
from app.services.catalog_service import catalog_service
from app.services.stream_service import stream_service


class TestEmptyPlaceholders(unittest.IsolatedAsyncioTestCase):
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

    async def test_empty_genre_placeholder(self):
        # Serie C has 0 matches
        metas = await catalog_service.get_catalog(
            catalog_id="calcio_italiano",
            genre_filter="Serie C",
            skip=0,
        )
        self.assertEqual(len(metas), 1)
        item = metas[0]
        self.assertEqual(item["name"], "Nessun evento di Serie C in programma oggi")
        self.assertEqual(item["id"], "streamsport:empty:calcio_italiano:serie_c")
        self.assertEqual(item["genres"], ["Serie C"])
        self.assertIn("poster", item)

    async def test_empty_catalog_placeholder(self):
        # Motori has 0 matches
        metas = await catalog_service.get_catalog(
            catalog_id="motori",
            genre_filter=None,
            skip=0,
        )
        self.assertEqual(len(metas), 1)
        item = metas[0]
        self.assertEqual(item["name"], "Nessun evento di Motori in programma oggi")
        self.assertEqual(item["id"], "streamsport:empty:motori:all")
        self.assertEqual(item["genres"], ["Motori"])

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

    async def test_meta_detail_for_empty_placeholder(self):
        meta = await catalog_service.get_meta_detail("streamsport:empty:calcio_italiano:serie_c")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "Nessun evento di Serie C in programma oggi")
        self.assertIn("poster", meta)
        self.assertIn("background", meta)

    async def test_stream_for_empty_placeholder(self):
        streams = await stream_service.get_streams_for_event(
            "streamsport:empty:calcio_italiano:serie_c",
            ep_url=None,
        )
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["name"], "ℹ️ Nessun evento disponibile")
        self.assertTrue(streams[0].get("behaviorHints", {}).get("notWebReady"))


if __name__ == "__main__":
    unittest.main()
