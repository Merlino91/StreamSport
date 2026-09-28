import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from app.main import app
from app.services.banner_service import banner_service
from app.services.catalog_service import catalog_service

client = TestClient(app)


class BannerServiceTestCase(unittest.IsolatedAsyncioTestCase):

    async def test_generate_dark_canvas_with_upcoming_and_tvvoo(self):
        match_id = "test_ostiamare_forli_banner"
        match = {
            "id": match_id,
            "title": "Ostia Mare Lidocalcio vs Forlì",
            "competition": "Serie D • Girone D",
            "poster": None,
            "sources": [{"source": "tvvoo", "id": "sky_sport_calcio"}],
        }

        # Clean existing cache if any
        cache_path = banner_service.get_cache_path(match_id, "upcoming", "20:45", True)
        if cache_path.exists():
            cache_path.unlink()

        # Generate poster
        poster_bytes = await banner_service.get_or_create_poster(
            match=match,
            match_id=match_id,
            status="upcoming",
            time_str="20:45",
            has_tvvoo=True,
        )

        self.assertIsNotNone(poster_bytes)
        self.assertGreater(len(poster_bytes), 5000)
        self.assertTrue(cache_path.exists())

        # Test cache hit (should return same bytes directly from disk)
        cached_bytes = await banner_service.get_or_create_poster(
            match=match,
            match_id=match_id,
            status="upcoming",
            time_str="20:45",
            has_tvvoo=True,
        )
        self.assertEqual(poster_bytes, cached_bytes)

        # Cleanup
        if cache_path.exists():
            cache_path.unlink()

    async def test_generate_live_poster(self):
        match_id = "test_live_inter_milan"
        match = {
            "id": match_id,
            "title": "Inter vs Milan",
            "competition": "Serie A",
            "poster": None,
            "sources": [],
        }

        cache_path = banner_service.get_cache_path(match_id, "live", "", False)
        if cache_path.exists():
            cache_path.unlink()

        poster_bytes = await banner_service.get_or_create_poster(
            match=match,
            match_id=match_id,
            status="live",
            time_str="",
            has_tvvoo=False,
        )

        self.assertIsNotNone(poster_bytes)
        self.assertGreater(len(poster_bytes), 5000)
        self.assertTrue(cache_path.exists())

        # Cleanup
        if cache_path.exists():
            cache_path.unlink()

    def test_poster_http_route(self):
        # Register temporary mock match in catalog_service
        match_id = "http_route_test_match"
        mock_match = {
            "id": match_id,
            "title": "Juventus vs Napoli",
            "competition": "Serie A",
            "poster": None,
            "sources": [{"source": "tvvoo"}],
        }
        catalog_service._cached_matches.append(mock_match)

        try:
            resp = client.get(f"/poster/{match_id}.jpg?s=upcoming&t=20:45&tv=1")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("content-type"), "image/jpeg")
            self.assertIn("max-age=1800", resp.headers.get("cache-control", ""))
            self.assertGreater(len(resp.content), 5000)
        finally:
            catalog_service._cached_matches.remove(mock_match)
            c_p = banner_service.get_cache_path(match_id, "upcoming", "20:45", True)
            if c_p.exists():
                c_p.unlink()

    def test_catalog_includes_dynamic_poster_url(self):
        mock_match = {
            "id": "match_catalog_banner_test",
            "title": "Real Madrid vs Barcelona",
            "category": "football",
            "date": 1790000000000,
            "poster": None,
            "sources": [{"source": "tvvoo"}],
        }
        item = catalog_service.build_meta_item(mock_match, "La Liga", base_url="https://addon.test")
        self.assertIn("poster", item)
        self.assertIn("background", item)
        self.assertTrue(item["poster"].startswith("https://addon.test/poster/match_catalog_banner_test.jpg?s="))
        self.assertIn("tv=1", item["poster"])


if __name__ == "__main__":
    unittest.main()
