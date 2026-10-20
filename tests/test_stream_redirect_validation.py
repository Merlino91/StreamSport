import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app, is_trusted_stream_redirect


class TestStreamRedirectValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_is_trusted_stream_redirect(self):
        # YouTube / Google CDN trusted
        self.assertTrue(is_trusted_stream_redirect("https://rr1---sn-oxunx-hxae.googlevideo.com/videoplayback?id=123", ("googlevideo.com", "youtube.com")))
        self.assertTrue(is_trusted_stream_redirect("https://www.youtube.com/watch?v=123", ("googlevideo.com", "youtube.com")))

        # Dailymotion CDN trusted
        self.assertTrue(is_trusted_stream_redirect("https://proxy-01.dmcdn.net/sec(abc)/video/123.m3u8", ("dmcdn.net", "dailymotion.com")))
        self.assertTrue(is_trusted_stream_redirect("https://www.dailymotion.com/cdn/manifest.m3u8", ("dmcdn.net", "dailymotion.com")))

        # Untrusted / spoofed domains
        self.assertFalse(is_trusted_stream_redirect("https://attacker.evil.com/video.m3u8", ("googlevideo.com", "youtube.com")))
        self.assertFalse(is_trusted_stream_redirect("https://googlevideo.com.attacker.com/video.m3u8", ("googlevideo.com", "youtube.com")))
        self.assertFalse(is_trusted_stream_redirect("https://fake-dmcdn.net/video.m3u8", ("dmcdn.net", "dailymotion.com")))
        self.assertFalse(is_trusted_stream_redirect("javascript:alert(1)", ("dmcdn.net", "dailymotion.com")))

    def test_dailymotion_invalid_id_format(self):
        # Invalid format: too short or special chars
        res = self.client.get("/dailymotion/stream/a.m3u8")
        self.assertEqual(res.status_code, 400)
        self.assertIn("non valido", res.json()["detail"])

        res2 = self.client.get("/dailymotion/stream/invalid*id.m3u8")
        self.assertEqual(res2.status_code, 400)
        self.assertIn("non valido", res2.json()["detail"])

    def test_youtube_invalid_id_format(self):
        # Too short (not 11 chars)
        res = self.client.get("/youtube/stream/short.m3u8")
        self.assertEqual(res.status_code, 400)
        self.assertIn("non valido", res.json()["detail"])

        # Too long
        res2 = self.client.get("/youtube/stream/too_long_youtube_id_12345.m3u8")
        self.assertEqual(res2.status_code, 400)
        self.assertIn("non valido", res2.json()["detail"])

    @patch("app.main.dailymotion_service.resolve_stream_url")
    def test_dailymotion_untrusted_redirect_blocked(self, mock_resolve):
        mock_resolve.return_value = "https://malicious-site.com/exploit.m3u8"
        res = self.client.get("/dailymotion/stream/x8abcde.m3u8", follow_redirects=False)
        self.assertEqual(res.status_code, 502)
        self.assertIn("non attendibile", res.json()["detail"])

    @patch("app.main.youtube_service.resolve_stream_manifest")
    def test_youtube_untrusted_redirect_blocked(self, mock_resolve):
        mock_resolve.return_value = (None, "https://malicious-site.com/exploit.m3u8")
        res = self.client.get("/youtube/stream/dQw4w9WgXcQ.m3u8", follow_redirects=False)
        self.assertEqual(res.status_code, 502)
        self.assertIn("non attendibile", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
