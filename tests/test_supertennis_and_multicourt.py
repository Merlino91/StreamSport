import unittest
import asyncio
from unittest.mock import patch, MagicMock

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
                and not m.get("_certified_by_sky")
                and m.get("_provider") != "italian_epg"
            )
        ]
        self.assertEqual(len(filtered), 2)
        remaining_ids = {m["id"] for m in filtered}
        self.assertIn("dlhd-kawa", remaining_ids)
        self.assertIn("sky-ningbo-session", remaining_ids)
        self.assertNotIn("dlhd-garbled", remaining_ids)


if __name__ == "__main__":
    unittest.main()
