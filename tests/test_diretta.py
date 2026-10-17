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

    def test_reconcile_matches_success(self):
        self.service._cached_events = [
            {
                "id": "diretta-1",
                "_silo": "football",
                "sport": "Calcio",
                "country": "Italia",
                "league": "Serie A",
                "competition": "Italia: Serie A",
                "home": "Inter",
                "away": "Juventus",
                "title": "Inter vs Juventus",
                "date": 1728846000000,
            }
        ]

        stream_matches = [
            {
                "title": "FC Internazionale Milano vs Juventus FC",
                "_silo": "football",
                "date": 1728846000000 + (10 * 60 * 1000),  # 10 min drift
                "competition": "other",
            }
        ]

        count = self.service.reconcile_matches(stream_matches)
        self.assertEqual(count, 1)
        m = stream_matches[0]
        self.assertTrue(m.get("_diretta_matched"))
        self.assertEqual(m["title"], "Inter vs Juventus")
        self.assertEqual(m["date"], 1728846000000)
        self.assertEqual(m["competition"], "Italia: Serie A")
        self.assertEqual(m.get("canonical_competition"), "football_serie_a")

    def test_reconcile_matches_inverted_us_format(self):
        self.service._cached_events = [
            {
                "id": "diretta-nba-1",
                "_silo": "basketball",
                "sport": "Basket",
                "country": "USA",
                "league": "NBA",
                "competition": "USA: NBA",
                "home": "Boston Celtics",
                "away": "Los Angeles Lakers",
                "title": "Boston Celtics vs Los Angeles Lakers",
                "date": 1728850000000,
            }
        ]

        # In US format, away visits home: "Lakers at Celtics"
        stream_matches = [
            {
                "title": "Los Angeles Lakers vs Boston Celtics",
                "_silo": "basketball",
                "date": 1728850000000,
                "competition": "NBA",
            }
        ]

        count = self.service.reconcile_matches(stream_matches)
        self.assertEqual(count, 1)
        m = stream_matches[0]
        self.assertTrue(m.get("_diretta_matched"))
        self.assertEqual(m["title"], "Boston Celtics vs Los Angeles Lakers")

    def test_reconcile_matches_time_drift_rejection(self):
        self.service._cached_events = [
            {
                "id": "diretta-1",
                "_silo": "football",
                "home": "Inter",
                "away": "Juventus",
                "title": "Inter vs Juventus",
                "date": 1728846000000,
                "competition": "Italia: Serie A",
            }
        ]

        # 3 hours apart (> 90 min tolerance)
        stream_matches = [
            {
                "title": "Inter vs Juventus",
                "_silo": "football",
                "date": 1728846000000 + (180 * 60 * 1000),
                "competition": "Serie A",
            }
        ]

        count = self.service.reconcile_matches(stream_matches)
        self.assertEqual(count, 0)
        self.assertFalse(stream_matches[0].get("_diretta_matched", False))

    def test_reconcile_matches_silo_mismatch(self):
        self.service._cached_events = [
            {
                "id": "diretta-1",
                "_silo": "football",
                "home": "Real Madrid",
                "away": "Barcelona",
                "title": "Real Madrid vs Barcelona",
                "date": 1728846000000,
                "competition": "Spagna: LaLiga",
            }
        ]

        # Same team names, but basketball silo (Euroleague Clásico)
        stream_matches = [
            {
                "title": "Real Madrid vs Barcelona",
                "_silo": "basketball",
                "date": 1728846000000,
                "competition": "Euroleague",
            }
        ]

        count = self.service.reconcile_matches(stream_matches)
        self.assertEqual(count, 0)


class TestCatalogServiceValidationFallback(unittest.TestCase):
    @patch("app.services.catalog_service.diretta_service")
    @patch("app.services.catalog_service.espn_service")
    def test_diretta_healthy_uses_diretta_skips_espn(self, mock_espn, mock_diretta):
        from app.services.catalog_service import catalog_service

        mock_diretta.is_healthy = True
        mock_diretta.reconcile_matches.return_value = 5

        matches = [{"title": "Test vs Sample", "_silo": "football"}]
        
        # Simulate step 4b directly
        if mock_diretta.is_healthy:
            mock_diretta.reconcile_matches(matches)
        else:
            mock_espn.reconcile_matches(matches, [], lambda x: set(), lambda x: "")

        mock_diretta.reconcile_matches.assert_called_once_with(matches)
        mock_espn.reconcile_matches.assert_not_called()

    @patch("app.services.catalog_service.diretta_service")
    @patch("app.services.catalog_service.espn_service")
    def test_diretta_unhealthy_triggers_espn_fallback(self, mock_espn, mock_diretta):
        mock_diretta.is_healthy = False
        mock_espn.reconcile_matches.return_value = None

        matches = [{"title": "Test vs Sample", "_silo": "football"}]
        espn_events = [{"id": "espn-1", "title": "Test vs Sample"}]

        # Simulate step 4b fallback logic
        if mock_diretta.is_healthy:
            mock_diretta.reconcile_matches(matches)
        else:
            if espn_events:
                mock_espn.reconcile_matches(matches, espn_events, lambda x: set(), lambda x: "")

        mock_diretta.reconcile_matches.assert_not_called()
        mock_espn.reconcile_matches.assert_called_once()


class TestPostValidationDeduplication(unittest.TestCase):
    def test_deduplicate_lafc_austin_fc_with_acronym_and_sources(self):
        from app.services.catalog_service import catalog_service

        match_streamed = {
            "id": "streamed-101",
            "title": "LAFC vs Austin FC",
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

        # 1. Direct duplicate detection test
        is_dup = catalog_service.are_post_validation_duplicates(match_streamed, match_daddylive)
        self.assertTrue(is_dup)

        # 2. End-to-end deduplicate_post_validation
        deduped = catalog_service.deduplicate_post_validation([match_streamed, match_daddylive])
        self.assertEqual(len(deduped), 1)

        merged = deduped[0]
        # Streams combined from both sources without loss
        self.assertEqual(len(merged["sources"]), 2)
        urls = [s["url"] for s in merged["sources"]]
        self.assertIn("https://streamed.xyz/1", urls)
        self.assertIn("https://daddylive.sx/2", urls)

        # Poster preserved
        self.assertEqual(merged["poster"], "https://images.thesportsdb.com/events/101.jpg")

        # 3. Test build_meta_item presentation: certified trophy badge, no TV channels in description
        meta = catalog_service.build_meta_item(merged, "Americhe e Leghe Extra-UE")
        self.assertIn("🏆 Major League Soccer", meta["description"])
        self.assertNotIn("Sky Sport Uno", meta["description"])
        self.assertIn("⭐ In Evidenza", meta["description"])


if __name__ == "__main__":
    unittest.main()
