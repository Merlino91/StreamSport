import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import AsyncMock, patch
import datetime
import time
from zoneinfo import ZoneInfo

from app.services.spon_service import SPONService
from app.services.catalog_service import CatalogService
from app.services.stream_service import StreamService


SAMPLE_PROG_TXT = """
============================================================
| LAST UPDATE: 22-10-26 |
| SPORTSONLINE.ST |
============================================================
THURSDAY
HD1 ENGLISH
HD2 ENGLISH
HD3 GERMAN
HD4 FRENCH
HD5 ENGLISH
HD6 SPANISH
HD7 ITALIAN
HD8 SERBIAN
HD9 DUTCH & SPANISH
HD10 SPANISH & DUTCH
HD11 ENGLISH & FRENCH
17:45   Juventus x Rennes | https://ww1.sportsonnline.click/channels/hd/hd7.php
17:45   OFI x Bayer Leverkusen | https://ww1.sportsonnline.click/channels/hd/hd3.php
20:00   AFC Bournemouth x Milan | https://ww1.sportsonnline.click/channels/hd/hd7.php
20:00   Ajax x Atalanta | https://ww1.sportsonnline.click/channels/hd/hd10.php
23:00   MotoGP: Australia FP | https://ww1.sportsonnline.click/channels/hd/hd2.php
FRIDAY
HD1 ENGLISH
HD7 ITALIAN
HD8 ITALIAN & SPANISH
19:45   Torino x Monza | https://ww1.sportsonnline.click/channels/hd/hd7.php
19:45   Brest x Nice | https://ww1.sportsonnline.click/channels/hd/hd8.php
20:00   Ipswich Town x Nottingham Forest | https://ww1.sportsonnline.click/channels/hd/hd1.php
"""


class SPONServiceTestCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = SPONService()

    def test_parse_prog_italian_only(self):
        """Verifies that only matches on channels with Italian commentary are ingested."""
        events = self.service.parse_prog(SAMPLE_PROG_TXT)

        # Expected Italian events:
        # Thursday: Juventus x Rennes (HD7), AFC Bournemouth x Milan (HD7)
        # Friday: Torino x Monza (HD7), Brest x Nice (HD8 - ITALIAN & SPANISH)
        self.assertEqual(len(events), 4)

        titles = [e["title"] for e in events]
        self.assertIn("Juventus vs Rennes", titles)
        self.assertIn("AFC Bournemouth vs Milan", titles)
        self.assertIn("Torino vs Monza", titles)
        self.assertIn("Brest vs Nice", titles)

        # Foreign-only events must NOT be present
        self.assertNotIn("OFI vs Bayer Leverkusen", titles)
        self.assertNotIn("Ajax vs Atalanta", titles)
        self.assertNotIn("Ipswich Town vs Nottingham Forest", titles)

        # Verify structured teams
        juve_event = next(e for e in events if "Juventus" in e["title"])
        self.assertEqual(juve_event["teams"], {"home": "Juventus", "away": "Rennes"})
        self.assertEqual(juve_event["category"], "football")
        self.assertEqual(juve_event["_provider"], "sportsonline")

        # Verify source metadata
        source = juve_event["sources"][0]
        self.assertEqual(source["source"], "sportsonline")
        self.assertEqual(source["channel"], "HD7")
        self.assertEqual(source["lang"], "Italiano")
        self.assertEqual(source["flag"], "🇮🇹")

    def test_timezone_conversion(self):
        """Verifies that 17:45 London time (BST, UTC+1 in late October) converts to 16:45 UTC."""
        events = self.service.parse_prog(SAMPLE_PROG_TXT)
        juve_event = next(e for e in events if "Juventus" in e["title"])

        # Timestamp in ms
        date_ms = juve_event["date"]
        dt_utc = datetime.datetime.fromtimestamp(date_ms / 1000.0, tz=datetime.timezone.utc)
        self.assertEqual(dt_utc.strftime("%H:%M"), "16:45")

        # In Rome time (CEST, UTC+2), it should be 18:45
        dt_rome = dt_utc.astimezone(ZoneInfo("Europe/Rome"))
        self.assertEqual(dt_rome.strftime("%H:%M"), "18:45")

    def test_silo_and_prefix_detection(self):
        """Verifies sport silo detection for tennis, motorsport, and american football."""
        silo_tennis, title_tennis, _ = self.service._parse_silo_and_teams("Tennis: ATP World Tour 250 - Brussels")
        self.assertEqual(silo_tennis, "tennis")
        self.assertEqual(title_tennis, "ATP World Tour 250 - Brussels")

        silo_moto, title_moto, _ = self.service._parse_silo_and_teams("MotoGP: Australia FP")
        self.assertEqual(silo_moto, "motor-sports")

        silo_nba, title_nba, teams_nba = self.service._parse_silo_and_teams("NBA: Cleveland Cavaliers @ Philadelphia 76ers")
        self.assertEqual(silo_nba, "basketball")
        self.assertEqual(teams_nba, {"home": "Philadelphia 76ers", "away": "Cleveland Cavaliers"})

    def test_catalog_merge_and_deduplication(self):
        """Verifies that an incoming SPON match merges into an existing canonical match without duplication."""
        catalog = CatalogService()
        events = self.service.parse_prog(SAMPLE_PROG_TXT)
        juve_spon = next(e for e in events if "Juventus" in e["title"])

        # Existing canonical match (e.g. from Diretta / Streamed)
        canonical_match = {
            "id": "diretta:12345",
            "title": "Juventus vs Rennes",
            "category": "football",
            "date": juve_spon["date"],
            "teams": {"home": "Juventus", "away": "Rennes"},
            "canonical_competition": "uel",
            "sources": [
                {"source": "tvvoo", "id": "sky_sport_calcio", "channel": "Sky Sport Calcio"}
            ]
        }

        merged = catalog.merge_and_deduplicate_by_silos([canonical_match], [juve_spon])
        self.assertEqual(len(merged), 1)

        merged_match = merged[0]
        self.assertEqual(merged_match["id"], "diretta:12345")
        # Should now contain both tvvoo and sportsonline sources
        source_types = [s.get("source") for s in merged_match.get("sources", [])]
        self.assertIn("tvvoo", source_types)
        self.assertIn("sportsonline", source_types)

    async def test_stream_service_spon_generation(self):
        """Verifies that stream_service formats EasyProxy manifest URLs for Sportzonline."""
        from app.services.catalog_service import catalog_service
        stream_svc = StreamService()
        match = {
            "id": "test_match_1",
            "title": "Juventus vs Rennes",
            "date": int(time.time() * 1000),  # live now
            "sources": [
                {
                    "source": "sportsonline",
                    "id": "https://ww1.sportsonnline.click/channels/hd/hd7.php",
                    "channel": "HD7",
                    "lang": "Italiano",
                    "flag": "🇮🇹",
                    "url": "https://ww1.sportsonnline.click/channels/hd/hd7.php"
                }
            ]
        }

        with patch.object(catalog_service, "get_cached_match", return_value=match):
            streams = await stream_svc.get_streams_for_event(
                "test_match_1",
                ep_url="https://ep.bluccoj.com",
                ep_pass="secret_password"
            )

        self.assertEqual(len(streams), 1)
        s = streams[0]
        self.assertIn("Sportzonline", s["name"])
        self.assertIn("HD7", s["name"])
        self.assertIn("🇮🇹", s["name"])
        self.assertIn("ep.bluccoj.com/proxy/manifest.m3u8", s["url"])
        self.assertIn("url=https%3A%2F%2Fww1.sportsonnline.click%2Fchannels%2Fhd%2Fhd7.php", s["url"])
        self.assertIn("api_password=secret_password", s["url"])


if __name__ == "__main__":
    unittest.main()
