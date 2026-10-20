import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from app.services.tvvoo_service import tvvoo_service, LiveStreamSession, LiveManifestManager


class TestLiveManifestManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = LiveManifestManager(tvvoo_service)

    def test_rewrite_manifest(self):
        raw_m3u8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:8.0,
chunk_100.ts
#EXTINF:8.0,
https://cdn.example.com/chunk_101.ts
#EXTINF:8.0,
chunk_102.ts
"""
        base_url = "https://edge.vavoo.to/live/"
        rewritten = self.manager._rewrite_manifest(raw_m3u8, base_url)
        lines = rewritten.splitlines()

        self.assertIn("#EXTM3U", lines)
        self.assertIn("https://edge.vavoo.to/live/chunk_100.ts", lines)
        self.assertIn("https://cdn.example.com/chunk_101.ts", lines)
        self.assertIn("https://edge.vavoo.to/live/chunk_102.ts", lines)

    @patch.object(tvvoo_service, "resolve_clean_stream_url")
    @patch.object(LiveManifestManager, "_fetch_upstream")
    async def test_get_live_manifest_lifecycle(self, mock_fetch, mock_resolve):
        mock_resolve.return_value = {
            "url": "https://edge.vavoo.to/hls/index.m3u8",
            "valid_until": 9999999999.0,
        }
        mock_fetch.return_value = "#EXTM3U\n#EXTINF:8.0,\nseg1.ts\n"

        play_url = "https://vavoo.to/play/123/index.m3u8"
        manifest = await self.manager.get_live_manifest(play_url, client_ip="1.2.3.4")

        self.assertIsNotNone(manifest)
        self.assertIn("https://edge.vavoo.to/hls/seg1.ts", manifest)

        # Immediate repeat should hit cache (throttling)
        manifest2 = await self.manager.get_live_manifest(play_url, client_ip="1.2.3.4")
        self.assertEqual(manifest, manifest2)
        mock_fetch.assert_called_once()

    @patch.object(tvvoo_service, "get_live_manifest")
    async def test_fastapi_live_manifest_endpoint(self, mock_get_manifest):
        from app.main import app
        mock_get_manifest.return_value = "#EXTM3U\n#EXTINF:8.0,\nhttps://edge.vavoo.to/seg1.ts\n"

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/live/manifest.m3u8?url=https://vavoo.to/play/123")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("content-type"), "application/vnd.apple.mpegurl")
            self.assertIn("https://edge.vavoo.to/seg1.ts", resp.text)

    @patch.object(tvvoo_service, "get_live_manifest")
    async def test_fastapi_live_manifest_easyproxy_fallback(self, mock_get_manifest):
        """Verifies seamless HTTP 307 redirect to EasyProxy when native No-Freeze resolution fails."""
        import base64
        from app.main import app

        mock_get_manifest.return_value = None  # Simulate upstream resolution failure

        config_str = base64.b64encode("https://myep.example.com|mysecret|Europe/Rome".encode()).decode()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
            resp = await client.get(f"/{config_str}/live/manifest.m3u8?url=https://vavoo.to/play/calcio_1")
            self.assertEqual(resp.status_code, 307)
            location = resp.headers.get("location")
            self.assertIn("myep.example.com", location)
            self.assertIn("host=Vavoo", location)
            self.assertIn("api_password=mysecret", location)
            self.assertIn("redirect_stream=true", location)
