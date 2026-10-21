import unittest
import asyncio
import time
from app.services.stream_service import stream_service
from app.services.mandrakodi_service import mandrakodi_service

class TestMandrakodiStreams(unittest.IsolatedAsyncioTestCase):

    async def test_amstaff_stream_generation(self):
        now_ts = int(time.time() * 1000)
        fake_match = {
            "id": "mandrakodi:test:01",
            "title": "Inter vs Milan",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "amstaff",
                    "provider": "mandrakodi",
                    "id": "https://example.com/manifest.mpd",
                    "name": "Sky Sport Uno",
                    "stream_data": {
                        "mpd_url": "https://example.com/manifest.mpd",
                        "kid": "1118a4985bf0db8ebf50f5f99074f564",
                        "key": "2bcf9a9da5234207b0fcf2974b474de4",
                    },
                    "country": "Italia",
                    "language": "ita",
                    "quality": "1080p 50fps",
                }
            ],
            "_provider": "mandrakodi",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_match)

        streams = await stream_service.get_streams_for_event(
            "mandrakodi:test:01",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )
        self.assertGreaterEqual(len(streams), 1)
        st = streams[0]
        self.assertIn("1080p 50fps", st["name"])
        self.assertIn("https://ep.bluccoj.com/proxy/mpd/manifest.m3u8", st["url"])
        self.assertIn("clearkey=1118a4985bf0db8ebf50f5f99074f564:2bcf9a9da5234207b0fcf2974b474de4", st["url"])
        self.assertIn("api_password=Z3r0EP", st["url"])

    async def test_acestream_stream_generation(self):
        now_ts = int(time.time() * 1000)
        fake_ace_match = {
            "id": "mandrakodi:test:02",
            "title": "Real Madrid vs Barcelona",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "acestream",
                    "provider": "mandrakodi",
                    "id": "a5f2161b0cabe910dc866b5abc6efe5d8f41fd7f",
                    "name": "Eleven Sports 4K",
                    "stream_data": {
                        "ace_id": "a5f2161b0cabe910dc866b5abc6efe5d8f41fd7f"
                    },
                    "country": "Spagna",
                    "quality": "4K",
                }
            ],
            "_provider": "mandrakodi",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_ace_match)

        streams = await stream_service.get_streams_for_event(
            "mandrakodi:test:02",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )
        self.assertGreaterEqual(len(streams), 1)
        st = streams[0]
        self.assertIn("AceStream", st["name"])
        self.assertEqual(st.get("externalUrl"), "acestream://a5f2161b0cabe910dc866b5abc6efe5d8f41fd7f")
        self.assertEqual(st.get("url"), "acestream://a5f2161b0cabe910dc866b5abc6efe5d8f41fd7f")
        self.assertTrue(st["behaviorHints"]["notWebReady"])

    async def test_mediaset_clear_stream_direct_generation(self):
        """Verifies that unencrypted clear streams (e.g. key == 0000 or -clr.isml) bypass EasyProxy completely."""
        # 1. Test parsing of amstaff payload with 0000
        raw_payload = "https://live03-col.msf.cdn.mediaset.net/live/ch-i1/i1-clr.isml/manifest.mpd|0000"
        src_type, meta = mandrakodi_service._parse_amstaff_payload(raw_payload)
        self.assertEqual(src_type, "direct")
        self.assertTrue(meta.get("is_clear"))
        self.assertEqual(meta.get("stream_url"), "https://live03-col.msf.cdn.mediaset.net/live/ch-i1/i1-clr.isml/manifest.m3u8")

        # 2. Test StreamService resolution delivers direct HLS URL with zero EasyProxy routing
        now_ts = int(time.time() * 1000)
        fake_clear_match = {
            "id": "mandrakodi:test:03",
            "title": "Sedicesimi di Finale",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "direct",
                    "provider": "mandrakodi",
                    "id": meta["stream_url"],
                    "name": "Italia 1",
                    "stream_data": meta,
                    "country": "Italia",
                }
            ],
            "_provider": "mandrakodi",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_clear_match)

        streams = await stream_service.get_streams_for_event(
            "mandrakodi:test:03",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )
        self.assertGreaterEqual(len(streams), 1)
        st = streams[0]
        self.assertIn("Diretto", st["name"])
        self.assertEqual(st["url"], "https://live03-col.msf.cdn.mediaset.net/live/ch-i1/i1-clr.isml/manifest.m3u8")
        self.assertNotIn("proxy", st["url"])
        self.assertNotIn("ep.bluccoj.com", st["url"])


if __name__ == "__main__":
    unittest.main()
