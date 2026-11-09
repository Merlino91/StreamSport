import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import time
import httpx

from app.services.streamfree_service import streamfree_service, StreamFreeService
from app.services.provider_normalizers import clean_streamfree_event
from app.services.stream_service import stream_service
from app.services.catalog_service import catalog_service


class TestStreamFreeService(unittest.IsolatedAsyncioTestCase):
    """
    Test suite for StreamFree provider integration:
    - Mirror discovery and DoH fallback
    - Schedule ingestion and canonical normalization
    - 24/7 linear channels mapping
    - HLS stream resolution with signed security tokens
    - Top-Down catalog linking and EasyProxy stream generation
    """

    def setUp(self):
        streamfree_service._cache = []
        streamfree_service._cache_time = 0.0
        streamfree_service._last_content_hash = ""
        streamfree_service._active_mirror = "strmfree.pk"
        streamfree_service._last_mirror_check = 0.0
        streamfree_service._resolved_streams_cache.clear()

    async def test_active_mirror_discovery_from_directory(self):
        """Verifies that dynamic mirror discovery checks the directory URL and picks the first mirror."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["strmfree.st", "strmfree.pk", "streamfree.top"]

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            active = await streamfree_service.get_active_mirror(force=True)
            self.assertEqual(active, "strmfree.st")

    async def test_active_mirror_fallback_on_directory_failure(self):
        """Verifies that if directory URL fails, candidate mirrors are probed in cascade."""
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 500

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200

        def side_effect(url, **kwargs):
            if "strmfree.link" in url or "strmfree.pk" in url:
                return mock_resp_fail
            if "strmfree.st" in url:
                return mock_resp_ok
            return mock_resp_fail

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=side_effect):
            active = await streamfree_service.get_active_mirror(force=True)
            self.assertEqual(active, "strmfree.st")

    def test_parse_streams_data_and_posters_omitted(self):
        """Verifies that /streams JSON is parsed into canonical events and posters remain None as requested."""
        now_sec = int(time.time())
        fake_data = {
            "streams": {
                "soccer": [
                    {
                        "id": "18433",
                        "stream_key": "berkhamsted-vs-plymouth-parkway",
                        "name": "Berkhamsted vs Plymouth Parkway",
                        "match_timestamp": now_sec + 3600,
                        "category": "soccer",
                        "league": "FA Cup",
                        "thumbnail_url": "https://img.streamfree.top/thumbs/fa-cup.jpg",
                        "viewers": 150,
                    }
                ],
                "racing": [
                    {
                        "id": "9901",
                        "stream_key": "skyf1",
                        "name": "Sky Sports F1",
                        "match_timestamp": 0,
                        "category": "racing",
                        "league": "F1",
                        "thumbnail_url": "https://img.streamfree.top/thumbs/f1.jpg",
                        "viewers": 1200,
                    }
                ],
            }
        }

        matches = streamfree_service.parse_streams_data(fake_data, "strmfree.st")
        self.assertEqual(len(matches), 2)

        # 1. Match check
        soccer_m = next(m for m in matches if m["_stream_key"] == "berkhamsted-vs-plymouth-parkway")
        self.assertEqual(soccer_m["category"], "football")
        self.assertEqual(soccer_m["_silo"], "football")
        self.assertIsNotNone(soccer_m["teams"])
        self.assertEqual(soccer_m["teams"]["home"]["name"], "Berkhamsted")
        self.assertEqual(soccer_m["teams"]["away"]["name"], "Plymouth Parkway")
        # Posters must be strictly None (per user instructions)
        self.assertIsNone(soccer_m["poster"])
        self.assertEqual(len(soccer_m["sources"]), 1)
        self.assertEqual(soccer_m["sources"][0]["source"], "streamfree")

        # 2. 24/7 Channel check
        f1_m = next(m for m in matches if m["_stream_key"] == "skyf1")
        self.assertEqual(f1_m["category"], "motor-sports")
        self.assertTrue(f1_m.get("is_24_7"))
        self.assertIsNone(f1_m["poster"])

    def test_provider_normalizer_clean_streamfree_event(self):
        """Verifies clean_streamfree_event correctly extracts teams, titles, and preserves sources."""
        raw_event = {
            "id": "streamfree-real-madrid-vs-barcelona",
            "title": "Real Madrid vs Barcelona",
            "category": "football",
            "date": 1731182400000,
            "sources": [{"source": "streamfree", "id": "real-madrid-vs-barcelona"}],
            "poster": None,
        }

        cleaned = clean_streamfree_event(raw_event)
        self.assertEqual(cleaned["home"], "Real Madrid")
        self.assertEqual(cleaned["away"], "Barcelona")
        self.assertEqual(cleaned["clean_home"], "real madrid")
        self.assertEqual(cleaned["clean_away"], "barcelona")
        self.assertEqual(cleaned["clean_title"], "Real Madrid vs Barcelona")
        self.assertEqual(cleaned["_provider"], "streamfree")
        self.assertIsNone(cleaned.get("poster"))

    def test_24_7_canonical_channel_lookup(self):
        """Verifies that 24/7 canonical channels are mapped cleanly."""
        f1_streams = streamfree_service.get_channel_streams("sky sport f1")
        self.assertEqual(len(f1_streams), 1)
        self.assertEqual(f1_streams[0]["id"], "skyf1")
        self.assertEqual(f1_streams[0]["source"], "streamfree")
        self.assertIn("Sky Sports F1", f1_streams[0]["name"])

        tennis_streams = streamfree_service.get_channel_streams("sky sport tennis")
        self.assertEqual(len(tennis_streams), 1)
        self.assertEqual(tennis_streams[0]["id"], "skysportstennis")

        redzone_streams = streamfree_service.get_channel_streams("redzone")
        self.assertEqual(len(redzone_streams), 1)
        self.assertEqual(redzone_streams[0]["id"], "redzone")

        unknown = streamfree_service.get_channel_streams("canale 5")
        self.assertEqual(len(unknown), 0)

    async def test_resolve_stream_urls_with_tokens(self):
        """Verifies HLS URL resolution with embed extraction and signed token parameters."""
        sources_resp = MagicMock()
        sources_resp.status_code = 200
        sources_resp.json.return_value = {
            "stream_key": "sample-stream",
            "sources": [
                {
                    "quality": "FHD",
                    "url": "https://strmfree.st/embed/soccer/sample-stream1080p",
                    "live": True,
                }
            ],
        }

        srv_resp = MagicMock()
        srv_resp.status_code = 200
        srv_resp.json.return_value = {
            "stream_key": "sample-stream",
            "server_name": "externalcdn",
        }

        embed_html = (
            '<html><script>'
            'const _0x = {"1080p": {"_e": 1799999999, "_n": "abcd1234", "_t": "testtoken123"}};'
            '</script></html>'
        )
        embed_resp = MagicMock()
        embed_resp.status_code = 200
        embed_resp.text = embed_html

        def side_effect(url, **kwargs):
            if "/api/v1/sources/" in url:
                return sources_resp
            if "/get-stream-key/" in url:
                return srv_resp
            if "/embed/" in url:
                return embed_resp
            return MagicMock(status_code=404)

        streamfree_service._active_mirror = "strmfree.st"
        streamfree_service._last_mirror_check = time.time()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=side_effect):
            resolved = await streamfree_service.resolve_stream_urls("sample-stream", category="soccer")
            self.assertEqual(len(resolved), 1)
            st = resolved[0]
            self.assertEqual(st["quality"], "1080p FHD")
            self.assertIn("live-externalcdn/sample-stream1080p/index.m3u8", st["m3u8_url"])
            self.assertIn("_t=testtoken123", st["m3u8_url"])
            self.assertIn("_e=1799999999", st["m3u8_url"])
            self.assertIn("_n=abcd1234", st["m3u8_url"])
            self.assertEqual(st["referer"], "https://strmfree.st/embed/soccer/sample-stream1080p")
            self.assertEqual(st["origin"], "https://strmfree.st")

    async def test_catalog_top_down_linking_with_streamfree(self):
        """Verifies that Top-Down linking links official Diretta matches to StreamFree candidates."""
        now_ts = int(time.time() * 1000)
        official_schedule = [
            {
                "id": "diretta:12345",
                "title": "Arsenal vs Chelsea",
                "category": "football",
                "_silo": "football",
                "date": now_ts,
                "home": "Arsenal",
                "away": "Chelsea",
                "sources": [],
            }
        ]

        sf_candidate = clean_streamfree_event({
            "id": "streamfree-arsenal-vs-chelsea",
            "title": "Arsenal vs Chelsea",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "streamfree",
                    "id": "arsenal-vs-chelsea",
                    "channel": "Premier League",
                    "quality": "1080p FHD",
                }
            ],
        })

        providers_map = {
            "streamfree": [sf_candidate]
        }

        linked = catalog_service.link_official_schedule_to_providers(
            official_schedule, providers_map, time_window_minutes=60.0
        )
        self.assertEqual(len(linked), 1)
        linked_event = linked[0]
        self.assertGreaterEqual(len(linked_event["sources"]), 1)
        self.assertEqual(linked_event["sources"][0]["source"], "streamfree")
        self.assertEqual(linked_event["sources"][0]["id"], "arsenal-vs-chelsea")

    async def test_stream_service_generates_easyproxy_streamfree_urls(self):
        """Verifies that StreamService produces EasyProxy manifest URLs for StreamFree streams."""
        now_ts = int(time.time() * 1000)
        match = {
            "id": "test:sf:match",
            "title": "Liverpool vs Man City",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "streamfree",
                    "id": "liverpool-vs-man-city",
                    "channel": "Premier League",
                    "category": "soccer",
                }
            ],
            "_provider": "diretta",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(match)

        mock_resolved = [
            {
                "quality": "1080p FHD",
                "m3u8_url": "https://strmfree.st/live-externalcdn/liverpool-vs-man-city1080p/index.m3u8?_t=token&_e=123&_n=456",
                "referer": "https://strmfree.st/embed/soccer/liverpool-vs-man-city1080p",
                "origin": "https://strmfree.st",
            }
        ]

        with patch.object(streamfree_service, "resolve_stream_urls", new_callable=AsyncMock) as mock_res:
            mock_res.return_value = mock_resolved
            streams = await stream_service.get_streams_for_event(
                "test:sf:match",
                ep_url="https://ep.testproxy.org",
                ep_pass="secret123",
            )
            self.assertEqual(len(streams), 1)
            st = streams[0]
            self.assertIn("[StreamFree] Premier League [1080p FHD]", st["name"])
            self.assertIn("🇬🇧", st["name"])
            self.assertIn("https://ep.testproxy.org/proxy/manifest.m3u8", st["url"])
            self.assertIn("h_referer=", st["url"])
            self.assertIn("h_origin=", st["url"])
            self.assertIn("api_password=secret123", st["url"])


if __name__ == "__main__":
    unittest.main()
