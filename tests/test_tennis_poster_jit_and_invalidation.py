import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.banner_service import banner_service
from app.services.catalog_service import catalog_service
from app.config import BANNERS_DIR


client = TestClient(app)


class TestTennisPosterJitAndInvalidation(unittest.TestCase):
    def test_invalidate_match_sanitization(self):
        """Verifies invalidate_match sanitizes IDs with colons and deletes matching files on disk."""
        raw_id = "sky:6000:live-wta-250-guangzhou:1793351100"
        sanitized = banner_service._sanitize_id(raw_id)
        test_file = BANNERS_DIR / f"{sanitized}_upcoming_1200_0_v7.jpg"
        test_fallback = BANNERS_DIR / f"{sanitized}_upcoming_1200_0_v7.fallback"
        
        test_file.touch()
        test_fallback.touch()
        self.assertTrue(test_file.exists())
        self.assertTrue(test_fallback.exists())

        # Invalidate using raw ID containing colons
        banner_service.invalidate_match(raw_id)

        self.assertFalse(test_file.exists())
        self.assertFalse(test_fallback.exists())

    def test_get_dynamic_poster_jit_tennis(self):
        """Verifies get_dynamic_poster triggers on-demand generation for tennis matches lacking artwork."""
        fake_id = "test_tennis_jit_match_123"
        fake_match = {
            "id": fake_id,
            "title": "Jannik Sinner vs Carlos Alcaraz",
            "category": "tennis",
            "poster": "",
            "_genre": "ATP",
            "date": 1700000000000,
        }
        catalog_service.update_cached_match(fake_match)

        with patch("app.services.tennis_poster_service.tennis_poster_service.generate_poster", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "/posters/tennis_fake_v3.jpg"
            with patch.object(banner_service, "get_or_create_poster", new_callable=AsyncMock) as mock_banner:
                mock_banner.return_value = b"fake_jpeg_bytes"

                res = client.get(f"/poster/{fake_id}.jpg")
                self.assertEqual(res.status_code, 200)
                mock_gen.assert_awaited_once_with(fake_id, "Jannik Sinner vs Carlos Alcaraz", "ATP")
                self.assertEqual(fake_match.get("poster"), "/posters/tennis_fake_v3.jpg")


if __name__ == "__main__":
    unittest.main()
