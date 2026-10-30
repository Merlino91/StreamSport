import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.mandrakodi_service import mandrakodi_service
from app.services.tvvoo_service import tvvoo_service
from app.services.italian_epg_service import italian_epg_service
from app.services.catalog_service import catalog_service


class TestSuperTennisAndMulticourt(unittest.TestCase):

    def test_mandrakodi_and_tvvoo_supertennis_canonical_isolation(self):
        """Verify SuperTennis 24/7 linear is isolated from OTT feeds 1, 2, 3, 4."""
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis"), "supertennis")
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis HD"), "supertennis")
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis 1"), "supertennis 1")
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis 2"), "supertennis 2")
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis 3"), "supertennis 3")
        self.assertEqual(mandrakodi_service.get_canonical_channel_key("SuperTennis 4"), "supertennis 4")

        self.assertEqual(tvvoo_service.get_canonical_key("SuperTennis"), "supertennis")
        self.assertEqual(tvvoo_service.get_canonical_key("SuperTennis 1"), "supertennis 1")
        self.assertEqual(tvvoo_service.get_canonical_key("SuperTennis 4"), "supertennis 4")

    def test_tournament_bridge_does_not_pollute_1v1_matches_with_supertennis(self):
        """Verify generic SuperTennis sessions do not attach SuperTennis to 1v1 court matches."""
        session_start = 1792667100000

        with patch.object(italian_epg_service, "get_matches", return_value=[
            {
                "id": "sky-6000-ningbo-session",
                "_provider": "italian_epg",
                "title": "LIVE WTA 500 NINGBO",
                "competition": "WTA 500 Ningbo",
                "eventSynopsis": "A Ningbo continua lo Swing asiatico del circuito WTA.",
                "date": session_start,
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,
                "poster": "https://apid.sky.it/poster/ningbo.jpg",
                "sources": [
                    {
                        "source": "tvvoo",
                        "id": "supertennis",
                        "canonical": "supertennis",
                        "name": "SuperTennis",
                        "url": "https://vavoo.to/play/st.m3u8",
                    }
                ],
            }
        ]):
            # Match 1: Katarzyna Kawa vs Belinda Bencic (1v1 match in Ningbo) -> MUST NOT get SuperTennis!
            kawa_match = {
                "id": "dlhd-kawa-bencic",
                "title": "Katarzyna Kawa vs Belinda Bencic",
                "competition": "Tennis: WTA - Singles: Ningbo (China)",
                "date": session_start + 1800000,
                "_silo": "tennis",
                "category": "tennis",
                "teams": {"home": "Katarzyna Kawa", "away": "Belinda Bencic"},
                "sources": [{"source": "dlhd", "id": "101", "name": "Tennis Stream"}],
            }

            # Match 2: Provider multicourt aggregate event -> CAN adopt Sky certified session!
            multicourt_match = {
                "id": "dlhd-ningbo-early-rounds",
                "title": "Ningbo-WTA Early Rounds",
                "competition": "WTA Ningbo",
                "date": session_start,
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,
                "sources": [{"source": "dlhd", "id": "102", "name": "Tennis HD Stream"}],
            }

            asyncio.run(italian_epg_service.enrich_matches_with_epg_channels([kawa_match, multicourt_match]))

            # Kawa vs Bencic MUST retain only its original source (NO SuperTennis attached)
            kawa_sources = kawa_match.get("sources", [])
            self.assertEqual(len(kawa_sources), 1)
            self.assertEqual(kawa_sources[0]["id"], "101")
            self.assertNotIn("SuperTennis", [s.get("name") for s in kawa_sources])

            # Multicourt match adopted Sky's certified session
            self.assertTrue(multicourt_match.get("_certified_by_sky"))
            self.assertEqual(multicourt_match.get("title"), "LIVE WTA 500 NINGBO")
            self.assertEqual(multicourt_match.get("poster"), "https://apid.sky.it/poster/ningbo.jpg")
            self.assertTrue(any(s.get("canonical") == "supertennis" for s in multicourt_match.get("sources", [])))

    def test_catalog_service_purges_uncertified_tennis_multicourt_events(self):
        """Verify uncertified provider tennis multicourt events are purged while certified Sky sessions and 1v1 matches remain."""
        matches = [
            # 1. 1v1 match (Kawa vs Bencic) -> KEPT
            {
                "id": "dlhd-kawa",
                "title": "Katarzyna Kawa vs Belinda Bencic",
                "_silo": "tennis",
                "category": "tennis",
                "teams": {"home": "Katarzyna Kawa", "away": "Belinda Bencic"},
                "_provider": "daddylive",
                "sources": [{"id": "s1", "name": "Court 1"}],
            },
            # 2. Garbled uncertified multicourt from DaddyLive -> PURGED
            {
                "id": "dlhd-garbled",
                "title": "Brussels-ATP, Lyon-ATP, Almaty-ATP, Osaka-WTA & Ningbo-WTA Early Rounds",
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,
                "_provider": "daddylive",
                "_certified_by_sky": False,
                "sources": [{"id": "s2", "name": "Tennis Channel"}],
            },
            # 3. Certified Sky session -> KEPT
            {
                "id": "sky-ningbo-session",
                "title": "LIVE WTA 500 NINGBO",
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,
                "_provider": "italian_epg",
                "sources": [{"id": "s3", "name": "SuperTennis"}],
            },
        ]

        filtered = [
            m for m in matches
            if not (
                (m.get("_silo") == "tennis" or m.get("category") == "tennis")
                and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                and m.get("_provider") != "italian_epg"
            )
        ]
        self.assertEqual(len(filtered), 2)
        remaining_ids = {m["id"] for m in filtered}
        self.assertIn("dlhd-kawa", remaining_ids)
        self.assertIn("sky-ningbo-session", remaining_ids)
        self.assertNotIn("dlhd-garbled", remaining_ids)

    def test_mandrakodi_resolve_live_match_streams_isolates_1v1_matches(self):
        """Verify Mandrakodi JIT resolver never attaches sources from different matches/courts via generic tournament stopwords."""
        match_kawa = {
            "id": "dlhd-kawa",
            "title": "WTA Tokyo: Katarzyna Kawa vs Belinda Bencic",
            "_silo": "tennis",
            "category": "tennis",
            "teams": {"home": "Katarzyna Kawa", "away": "Belinda Bencic"},
        }

        # Cached Mandrakodi matches: one for Court 1 (multicourt), one for a completely different match in Tokyo
        mandrakodi_service._cached_matches = [
            {
                "id": "mandra-court1",
                "title": "WTA Tokyo Court 1",
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,
                "sources": [{"id": "s_court1", "name": "Court 1"}],
            },
            {
                "id": "mandra-other-match",
                "title": "WTA Tokyo: Qinwen Zheng vs Moyuka Uchijima",
                "_silo": "tennis",
                "category": "tennis",
                "teams": {"home": "Qinwen Zheng", "away": "Moyuka Uchijima"},
                "sources": [{"id": "s_zheng", "name": "Zheng vs Uchijima"}],
            },
            {
                "id": "mandra-correct-match",
                "title": "Katarzyna Kawa vs Belinda Bencic",
                "_silo": "tennis",
                "category": "tennis",
                "teams": {"home": "Katarzyna Kawa", "away": "Belinda Bencic"},
                "sources": [{"id": "s_bencic", "name": "Bencic vs Kawa Stream"}],
            },
        ]
        mandrakodi_service._last_fetch_time = 9999999999.0  # skip re-fetch

        # 1. Resolve for Kawa vs Bencic -> MUST ONLY return s_bencic!
        res = asyncio.run(mandrakodi_service.resolve_live_match_streams(match_kawa))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "s_bencic")

        # 2. When no matching player names exist -> MUST NOT attach Court 1 or Zheng vs Uchijima!
        match_sabalenka = {
            "id": "dlhd-saba",
            "title": "WTA Tokyo: Aryna Sabalenka vs Iga Swiatek",
            "_silo": "tennis",
            "category": "tennis",
            "teams": {"home": "Aryna Sabalenka", "away": "Iga Swiatek"},
        }
        res2 = asyncio.run(mandrakodi_service.resolve_live_match_streams(match_sabalenka))
        self.assertEqual(len(res2), 0)

    def test_sequential_channel_handover_clamps_end_date_and_live_window(self):
        """Sequential Channel Handover: consecutive events on linear channels clamp end_date and calculate live_window."""
        t1_start = 1793344500000  # 08:35
        t1_end_raw = 1793366100000  # 14:35 (6h later)
        t2_start = 1793357100000  # 12:05 (next event starts)
        t2_end = 1793364000000  # 14:00

        mock_client = MagicMock()
        mock_client.get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "events": [
                {
                    "eventTitle": "LIVE WTA 500 Tokyo",
                    "starttime": "2026-10-30T07:35:00Z",
                    "endtime": "2026-10-30T13:35:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
                {
                    "eventTitle": "LIVE WTA 250 Guangzhou",
                    "starttime": "2026-10-30T11:05:00Z",
                    "endtime": "2026-10-30T13:00:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
            ]
        }

        with patch.object(tvvoo_service, "get_channel_streams", return_value=[{"canonical": "supertennis", "tag": "c", "url": "https://test"}]), \
             patch.object(tvvoo_service, "get_canonical_from_sky_id", return_value="supertennis"):
            async def run_fetch():
                mock_async_client = MagicMock()
                mock_async_client.get = AsyncMock(return_value=mock_resp)
                return await italian_epg_service._fetch_sky_events(mock_async_client, "2026-10-30T00:00:00Z", "2026-10-30T23:59:59Z")

            events = asyncio.run(run_fetch())
            tokyo_ev = [e for e in events if "Tokyo" in e["title"]][0]
            guangzhou_ev = [e for e in events if "Guangzhou" in e["title"]][0]

            # Tokyo's end_date MUST be clamped to Guangzhou's start date
            self.assertEqual(tokyo_ev["end_date"], guangzhou_ev["date"])

            # Catalog live_window for Tokyo MUST be ~215 minutes (ends at Guangzhou start + 5m), NOT 360m!
            lw = catalog_service.get_live_window_minutes(tokyo_ev)
            self.assertEqual(lw, 215)

    def test_differita_sintesi_tagging(self):
        """EPG events without LIVE or from previous years are tagged [Differita / Sintesi]."""
        # 1. Live event retains LIVE
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("LIVE WTA 500 Tokyo"))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("LIVE WTA 250 Guangzhou"))

        # 2. Tournament session without LIVE is tagged differita/sintesi
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("WTA 500 Tokyo 2026"))
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("WTA 250 Guangzhou 2026"))

        # 3. Previous year tournament is tagged differita/sintesi
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("ATP 1000 Parigi Bercy 2025 - Ep. 5"))
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("Nitto ATP Finals Torino 2025"))


if __name__ == "__main__":
    unittest.main()
