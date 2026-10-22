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

    async def test_stream_ordering_tvvoo_mandrakodi_acestream(self):
        """Verifies that TvVoo streams appear first, followed by Mandrakodi, and AceStreams at the end."""
        now_ts = int(time.time() * 1000)
        multi_stream_match = {
            "id": "epg:test:multi",
            "title": "Milan vs Inter",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": [
                {
                    "source": "acestream",
                    "provider": "mandrakodi",
                    "id": "16519c3e10c7f1dddea05eb2dae6182cbdd34e4f",
                    "name": "Zona Dazn",
                    "stream_data": {"ace_id": "16519c3e10c7f1dddea05eb2dae6182cbdd34e4f"},
                    "country": "Italia",
                },
                {
                    "source": "direct",
                    "provider": "mandrakodi",
                    "id": "https://stream.cdn/live.m3u8",
                    "name": "Canale 5",
                    "stream_data": {"stream_url": "https://stream.cdn/live.m3u8"},
                    "country": "Italia",
                },
                {
                    "source": "amstaff",
                    "provider": "mandrakodi",
                    "id": "https://live.mediaset/20.mpd",
                    "name": "20 Mediaset",
                    "stream_data": {"mpd_url": "https://live.mediaset/20.mpd", "kid": "kid1", "key": "key1"},
                    "country": "Italia",
                },
                {
                    "source": "tvvoo",
                    "id": "canale 5",
                    "name": "🇮🇹 Canale 5 HD",
                    "url": "https://vavoo.to/play/123/index.m3u8",
                    "country": "Italy",
                }
            ],
            "_provider": "italian_epg",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(multi_stream_match)

        streams = await stream_service.get_streams_for_event(
            "epg:test:multi",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )
        self.assertEqual(len(streams), 4)

        # 1. TvVoo stream comes first
        self.assertIn("🇮🇹 Canale 5 HD", streams[0]["name"])

        # 2. Mandrakodi streams (Direct & Amstaff) come second
        self.assertIn("Canale 5 [Diretto]", streams[1]["name"])
        self.assertEqual(streams[1]["url"], "https://stream.cdn/live.m3u8")

        self.assertIn("20 Mediaset [1080p 50fps]", streams[2]["name"])
        self.assertIn("/proxy/mpd/manifest.m3u8", streams[2]["url"])

        # 3. AceStream comes at the very end
        self.assertIn("[AceStream] Zona Dazn", streams[3]["name"])
        self.assertEqual(streams[3]["externalUrl"], "acestream://16519c3e10c7f1dddea05eb2dae6182cbdd34e4f")
        self.assertTrue(streams[3]["behaviorHints"]["notWebReady"])

    async def test_daddylive_streams_capped_at_six_and_italian_first(self):
        """Verifies that DaddyLive exposes at most 6 streams, with Italian channels prioritised at the top."""
        now_ts = int(time.time() * 1000)
        # Create 10 DaddyLive sources: 8 foreign and 2 Italian
        dl_sources = [
            {"source": "dlhd", "id": f"src_{i}", "name": f"Foreign Channel {i} UK"}
            for i in range(1, 9)
        ]
        # Insert 2 Italian sources at the end
        dl_sources.append({"source": "dlhd", "id": "src_it1", "name": "Sky Sport Calcio IT"})
        dl_sources.append({"source": "dlhd", "id": "src_it2", "name": "DAZN 1 Italia"})

        fake_match = {
            "id": "dlhd:test:capped",
            "title": "Arsenal vs Chelsea",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "sources": dl_sources,
            "_provider": "dlhd",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_match)

        streams = await stream_service.get_streams_for_event(
            "dlhd:test:capped",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )

        # Must be capped at exactly 6 streams
        self.assertEqual(len(streams), 6)
        # Italian streams must be at the very top (indices 0 and 1)
        self.assertIn("🇮🇹", streams[0]["name"])
        self.assertIn("🇮🇹", streams[1]["name"])

    async def test_fctv33_dict_teams_resolution_avoids_500(self):
        """Verifies that FCTV33 matches with dict teams structure do not crash with 500 in JIT resolution."""
        now_ts = int(time.time() * 1000)
        fctv_match = {
            "id": "streamsport:fctv33:4521897",
            "title": "Boca Juniors vs River Plate",
            "category": "football",
            "_silo": "football",
            "date": now_ts,
            "_sport_type": 1,
            "teams": {
                "home": {"name": "Boca Juniors"},
                "away": {"name": "River Plate"},
            },
            "sources": [
                {
                    "source": "fctv33",
                    "id": "4521897",
                    "match_id": "4521897",
                    "stream_id": "stream_1",
                    "name": "Canale 1",
                }
            ],
            "_provider": "fctv33",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fctv_match)

        # Ensure Mandrakodi has a cached match or does not crash
        mandrakodi_service._cached_matches = []
        mandrakodi_service._last_fetch_time = time.time()

        streams = await stream_service.get_streams_for_event(
            "streamsport:fctv33:4521897",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP"
        )
        self.assertIsInstance(streams, list)
        self.assertGreaterEqual(len(streams), 1)
        self.assertIn("FCTV33", streams[0]["name"])

    async def test_non_mediaset_clear_mpd_keeps_mpd_url(self):
        """Verifies that non-Mediaset clear MPD streams (like MSVDN) do NOT generate a broken .m3u8 URL."""
        msvdn_payload = "https://421b2081187a40368f24e4fb496bfc26.msvdn.net/channel1/manifest.mpd|0000"
        src_type, meta = mandrakodi_service._parse_amstaff_payload(msvdn_payload)
        self.assertEqual(src_type, "direct")
        self.assertTrue(meta.get("is_clear"))
        self.assertEqual(meta.get("stream_url"), "https://421b2081187a40368f24e4fb496bfc26.msvdn.net/channel1/manifest.mpd")
        self.assertNotIn(".m3u8", meta.get("stream_url"))

    async def test_enrichment_idempotency_prevents_duplicates(self):
        """Verifies that running enrichment multiple times does not produce duplicate streams."""
        now_ts = int(time.time() * 1000)
        match = {
            "id": "tennis:test:dupe",
            "title": "Kawa vs Bencic",
            "category": "tennis",
            "_silo": "tennis",
            "date": now_ts,
            "sources": [
                {"source": "fctv33", "id": "2249457", "name": "FCTV33 Live"},
                {"source": "tvvoo", "id": "supertennis", "name": "SuperTennis", "url": "https://vavoo.to/play/st1"}
            ]
        }
        # Pre-populate registry with a direct stream
        mandrakodi_service._channels_by_canonical["supertennis"] = [
            {
                "source": "direct",
                "provider": "mandrakodi",
                "id": "https://live-embed.supertennix.hiway.media/manifest.m3u8",
                "name": "SuperTennis (ITA - M3U8)",
                "stream_data": {"stream_url": "https://live-embed.supertennix.hiway.media/manifest.m3u8"},
                "country": "Italia",
                "language": "ita",
                "quality": "1080p",
            }
        ]

        # Pass 1
        c1 = mandrakodi_service.enrich_matches_with_mandrakodi([match])
        self.assertEqual(c1, 1)
        self.assertEqual(len(match["sources"]), 3)

        # Pass 2
        c2 = mandrakodi_service.enrich_matches_with_mandrakodi([match])
        self.assertEqual(c2, 0)
        self.assertEqual(len(match["sources"]), 3)


if __name__ == "__main__":
    unittest.main()
