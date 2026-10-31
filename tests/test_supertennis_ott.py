import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.services.mandrakodi_service import MandrakodiService
from app.services.stream_service import StreamService


class TestSuperTennisOTT(unittest.IsolatedAsyncioTestCase):

    async def test_mandrakodi_supertennis_ott_probing_and_cache(self):
        service = MandrakodiService()
        service._st_ott_cache = []
        service._st_ott_cache_time = 0.0

        # Mock client and responses for channels 1..4:
        # Channel 1: 1.2 MB (Active Live)
        # Channel 2: 70 KB (Cartel / Inactive)
        # Channel 3: Error / Timeout
        # Channel 4: 900 KB (Active Live)

        async def mock_get(url, *args, **kwargs):
            headers = kwargs.get("headers", {})
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.url = httpx.URL(url)

            if "manifest.mpd" in url:
                resp.text = """<?xml version="1.0" encoding="UTF-8" ?>
                <MPD>
                    <Period>
                        <AdaptationSet mimeType="video/mp4">
                            <SegmentTemplate media="$RepresentationID$_Segment-$Time$.m4v" initialization="$RepresentationID$_init.m4i">
                                <SegmentTimeline>
                                    <S t="100000" d="20000000" r="10" />
                                </SegmentTimeline>
                            </SegmentTemplate>
                            <Representation width="1280" height="720" frameRate="50" id="video_rep" bandwidth="5000000" />
                        </AdaptationSet>
                    </Period>
                </MPD>"""
                return resp

            # Segment request
            if "channel1" in url:
                resp.headers = {"content-range": "bytes 0-250000/1250000"}
                resp.content = b"x" * 250000
                return resp
            elif "channel2" in url:
                resp.headers = {"content-range": "bytes 0-70000/70000"}
                resp.content = b"x" * 70000
                return resp
            elif "channel3" in url:
                raise httpx.ConnectTimeout("Timeout connecting to CDN")
            elif "channel4" in url:
                resp.headers = {"content-range": "bytes 0-250000/900000"}
                resp.content = b"x" * 250000
                return resp

            resp.status_code = 404
            return resp

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        with patch.object(service, "_get_client", AsyncMock(return_value=mock_client)):
            active_feeds = await service.get_active_supertennis_ott_streams()

            # Channel 1 and 4 must be active, Channel 2 (cartel) and Channel 3 (timeout) must be filtered out!
            self.assertEqual(len(active_feeds), 2)
            self.assertEqual(active_feeds[0]["channel_num"], 1)
            self.assertEqual(active_feeds[0]["name"], "SuperTennis OTT 1")
            self.assertEqual(active_feeds[1]["channel_num"], 4)
            self.assertEqual(active_feeds[1]["name"], "SuperTennis OTT 4")

            # Verification of cache: second call must NOT call mock_client.get again!
            mock_client.get.reset_mock()
            cached_feeds = await service.get_active_supertennis_ott_streams()
            self.assertEqual(len(cached_feeds), 2)
            mock_client.get.assert_not_called()

    async def test_stream_service_supertennis_ott_enrichment(self):
        stream_svc = StreamService()

        fake_ott_streams = [
            {
                "provider": "mandrakodi",
                "source": "supertennis_ott",
                "id": "mandrakodi:supertennis_ott_1",
                "name": "SuperTennis OTT 1",
                "channel_num": 1,
                "country": "Italia",
                "language": "ita",
                "quality": "720p 50fps",
                "stream_data": {
                    "stream_url": "https://cdn.example.com/channel1/manifest.mpd",
                    "mpd_url": "https://cdn.example.com/channel1/manifest.mpd",
                    "channel_num": 1,
                    "is_clear": True,
                },
            }
        ]

        # Match in live window (-5 minutes to start) with Diretta SuperTennis
        now_ms = time.time() * 1000
        tennis_match = {
            "id": "tennis:test1234",
            "title": "Errani/Paolini vs Mihalikova/Nicholls",
            "_silo": "tennis",
            "category": "tennis",
            "competition": "WTA 500 Tokyo",
            "date": now_ms - 300000,  # 5 min ago (live)
            "_diretta_tv_channels": ["SuperTennis"],
            "sources": [
                {
                    "provider": "tvvoo",
                    "source": "tvvoo",
                    "id": "tvvoo:supertennis",
                    "name": "SuperTennis HD",
                    "url": "https://vavoo.to/live/supertennis.m3u8",
                    "country": "Italia",
                }
            ],
        }

        with patch("app.services.mandrakodi_service.mandrakodi_service.get_active_supertennis_ott_streams", AsyncMock(return_value=fake_ott_streams)):
            with patch("app.services.catalog_service.catalog_service.get_cached_match", return_value=tennis_match):
                streams = await stream_svc.get_streams_for_event(
                    item_id="tennis:test1234",
                    ep_url="https://easyproxy.test",
                    ep_pass="secret123",
                )

                # Assert that both linear TvVoo AND SuperTennis OTT are present!
                stream_names = [s.get("name", "") for s in streams]
                self.assertTrue(any("SuperTennis HD" in name for name in stream_names))
                self.assertTrue(any("SuperTennis OTT 1" in name for name in stream_names))

                # Assert that EasyProxy transmuxed URL is provided
                ott_stream = next(s for s in streams if "SuperTennis OTT 1" in s.get("name", ""))
                self.assertIn("/proxy/mpd/manifest.m3u8", ott_stream["url"])
                self.assertIn("api_password=secret123", ott_stream["url"])

    async def test_stream_service_non_supertennis_event_does_not_attach_ott(self):
        stream_svc = StreamService()

        now_ms = time.time() * 1000
        football_match = {
            "id": "football:derby123",
            "title": "Inter vs Milan",
            "_silo": "calcio_italiano",
            "category": "football",
            "competition": "Serie A",
            "date": now_ms - 300000,
            "_diretta_tv_channels": ["DAZN 1"],
            "sources": [
                {
                    "provider": "tvvoo",
                    "source": "tvvoo",
                    "id": "tvvoo:dazn1",
                    "name": "DAZN 1 HD",
                    "url": "https://vavoo.to/live/dazn1.m3u8",
                    "country": "Italia",
                }
            ],
        }

        mock_probe = AsyncMock()
        with patch("app.services.mandrakodi_service.mandrakodi_service.get_active_supertennis_ott_streams", mock_probe):
            with patch("app.services.catalog_service.catalog_service.get_cached_match", return_value=football_match):
                streams = await stream_svc.get_streams_for_event(
                    item_id="football:derby123",
                    ep_url="https://easyproxy.test",
                    ep_pass="secret123",
                )
                mock_probe.assert_not_called()
                stream_names = [s.get("name", "") for s in streams]
                self.assertFalse(any("SuperTennis OTT" in name for name in stream_names))


if __name__ == "__main__":
    unittest.main()
