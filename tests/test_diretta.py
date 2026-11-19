import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.diretta_service import DirettaService, diretta_service


class TestDirettaService(unittest.TestCase):
    def setUp(self):
        self.service = DirettaService()

    def test_parse_flashscore_feed_basic(self):
        sample_feed = (
            "~ZA÷Serie A¬ZY÷Italia¬"
            "~AA÷match01¬AD÷1728846000¬AE÷Inter¬AF÷Juventus¬"
            "~AA÷match02¬AD÷1728849600¬AE÷Milan¬AF÷Napoli¬"
        )
        matches = self.service.parse_flashscore_feed(sample_feed, "Calcio", "football")
        self.assertEqual(len(matches), 2)
        
        m1 = matches[0]
        self.assertEqual(m1["id"], "diretta-match01")
        self.assertEqual(m1["_silo"], "football")
        self.assertEqual(m1["sport"], "Calcio")
        self.assertEqual(m1["country"], "Italia")
        self.assertEqual(m1["league"], "Serie A")
        self.assertEqual(m1["competition"], "Serie A")
        self.assertEqual(m1["home"], "Inter")
        self.assertEqual(m1["away"], "Juventus")
        self.assertEqual(m1["title"], "Inter vs Juventus")
        self.assertEqual(m1["date"], 1728846000 * 1000)

        m2 = matches[1]
        self.assertEqual(m2["home"], "Milan")
        self.assertEqual(m2["away"], "Napoli")

    def test_enrich_with_alt_metadata(self):
        it_feed = (
            "~ZA÷Eurolega¬ZY÷Europa¬"
            "~AA÷match_euro01¬AD÷1728846000¬AE÷Real Madrid¬AF÷Olimpia Milano¬"
        )
        en_feed = (
            "~ZA÷Euroleague¬ZY÷Europe¬"
            "~AA÷match_euro01¬AD÷1728846000¬AE÷Real Madrid¬AF÷Olimpia Milano¬"
        )
        it_events = self.service.parse_flashscore_feed(it_feed, "Basket", "basketball")
        en_events = self.service.parse_flashscore_feed(en_feed, "Basketball", "basketball")

        en_by_id = {e["flashscore_id"]: e for e in en_events if e.get("flashscore_id")}

        for it_ev in it_events:
            fid = it_ev.get("flashscore_id")
            if fid and fid in en_by_id:
                en_m = en_by_id[fid]
                it_ev["alt_home"] = en_m.get("home", "")
                it_ev["alt_away"] = en_m.get("away", "")
                it_ev["alt_title"] = en_m.get("title", "")
                it_ev["alt_league"] = en_m.get("league", "")
                it_ev["alt_competition"] = en_m.get("competition", "")
                it_ev["alt_country"] = en_m.get("country", "")

        ev = it_events[0]
        self.assertEqual(ev["league"], "Eurolega")
        self.assertEqual(ev["country"], "Europa")
        self.assertEqual(ev["alt_league"], "Euroleague")
        self.assertEqual(ev["alt_competition"], "Euroleague")
        self.assertEqual(ev["alt_country"], "Europe")

    @patch.object(DirettaService, "get_events")
    def test_official_sliding_schedule_preserves_alt_fields(self, mock_get_events):
        now_ms = 1728840000 * 1000
        mock_get_events.return_value = [
            {
                "id": "diretta-euro01",
                "title": "Real Madrid vs Olimpia Milano",
                "sport": "Basket",
                "category": "basketball",
                "_silo": "basketball",
                "country": "Europa",
                "league": "Eurolega",
                "competition": "Eurolega",
                "alt_country": "Europe",
                "alt_league": "Euroleague",
                "alt_competition": "Euroleague",
                "home": "Real Madrid",
                "away": "Olimpia Milano",
                "alt_home": "Real Madrid",
                "alt_away": "Olimpia Milano",
                "alt_title": "Real Madrid vs Olimpia Milano",
                "date": now_ms + 7200 * 1000,
                "status": "scheduled",
            }
        ]

        with patch("time.time", return_value=1728840000):
            schedule = asyncio.run(self.service.get_official_sliding_schedule())

        self.assertEqual(len(schedule), 1)
        m = schedule[0]
        # UI fields remain Italian
        self.assertEqual(m["competition"], "Eurolega")
        self.assertEqual(m["country"], "Europa")
        # Internal fields carry English metadata
        self.assertEqual(m["alt_league"], "Euroleague")
        self.assertEqual(m["alt_competition"], "Euroleague")
        self.assertEqual(m["alt_country"], "Europe")
        # Canonical competition correctly detected as basketball_euroleague
        self.assertEqual(m["canonical_competition"], "basketball_euroleague")


class TestBuildMetaItemAndMerging(unittest.TestCase):
    def test_merge_provider_streams_and_build_meta(self):
        from app.services.catalog_service import catalog_service

        match_streamed = {
            "id": "diretta-101",
            "title": "LAFC vs Austin FC",
            "home": "LAFC",
            "away": "Austin FC",
            "date": 1792031400000,
            "category": "football",
            "_silo": "football",
            "competition": "Major League Soccer",
            "canonical_competition": "usa.1",
            "popular": True,
            "poster": "https://images.thesportsdb.com/events/101.jpg",
            "_tsdb_matched": True,
            "sources": [{"id": "s1", "name": "Streamed HD", "url": "https://streamed.xyz/1"}],
        }

        match_daddylive = {
            "id": "dlhd-202",
            "title": "Los Angeles FC vs Austin FC",
            "date": 1792031400000,
            "category": "football",
            "_silo": "football",
            "competition": "Major League Soccer",
            "canonical_competition": "usa.1",
            "popular": False,
            "poster": "https://images.thesportsdb.com/events/101.jpg",
            "_diretta_matched": True,
            "_diretta_tv_channels": ["Sky Sport Uno"],
            "sources": [{"id": "s2", "name": "DaddyLive", "url": "https://daddylive.sx/2"}],
        }

        # Top-down linking attaches provider streams directly to the official match
        linked = catalog_service.link_official_schedule_to_providers(
            [match_streamed],
            {"daddylive": [match_daddylive]},
        )
        self.assertEqual(len(linked), 1)

        merged = linked[0]
        self.assertEqual(len(merged["sources"]), 2)
        urls = [s["url"] for s in merged["sources"]]
        self.assertIn("https://streamed.xyz/1", urls)
        self.assertIn("https://daddylive.sx/2", urls)

        # Build meta item presentation test
        meta = catalog_service.build_meta_item(merged, "Americhe e Leghe Extra-UE")
        self.assertIn("🏆 Major League Soccer", meta["description"])
        self.assertNotIn("Sky Sport Uno", meta["description"])
        self.assertIn("⭐ In Evidenza", meta["description"])


if __name__ == "__main__":
    unittest.main()
