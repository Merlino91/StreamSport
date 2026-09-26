import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.thesportsdb_service import thesportsdb_service
from app.services.db_service import db_service


class TheSportsDBTestCase(unittest.IsolatedAsyncioTestCase):

    def test_parse_retry_delay(self):
        # Header retry-after
        self.assertEqual(thesportsdb_service.parse_retry_delay("", {"Retry-After": "480"}), 480)
        self.assertEqual(thesportsdb_service.parse_retry_delay("", {"retry-after": "120"}), 120)

        # Italian text formats
        self.assertEqual(thesportsdb_service.parse_retry_delay("Riprova tra 8m"), 480)
        self.assertEqual(thesportsdb_service.parse_retry_delay("Riprova tra 8 minuti"), 480)
        self.assertEqual(thesportsdb_service.parse_retry_delay("Riprova tra 30 secondi"), 30)

        # English text formats
        self.assertEqual(thesportsdb_service.parse_retry_delay("Retry in 5 minutes"), 300)
        self.assertEqual(thesportsdb_service.parse_retry_delay("Rate limit exceeded. Retry in 60s"), 60)

        # Default fallback
        self.assertEqual(thesportsdb_service.parse_retry_delay("Too Many Requests"), 300)

    def test_clean_event_query(self):
        # Case 1: Teams dictionary present
        teams = {"home": {"name": "Inter"}, "away": {"name": "Milan"}}
        q, h, a = thesportsdb_service.clean_event_query("24-09 20:45 Serie A: Inter vs Milan", teams)
        self.assertEqual(q, "Inter vs Milan")
        self.assertEqual(h, "Inter")
        self.assertEqual(a, "Milan")

        # Case 2: Dirty title with date, league, and suffixes
        q, h, a = thesportsdb_service.clean_event_query("24-09 20:00 Italy - Serie A : Inter vs Juventus (NBL) [HD]")
        self.assertEqual(q, "Inter vs Juventus")
        self.assertEqual(h, "Inter")
        self.assertEqual(a, "Juventus")

        # Case 3: Match with flag emoji
        q, h, a = thesportsdb_service.clean_event_query("🇮🇹 Inter vs 🇮🇹 Milan")
        self.assertEqual(q, "Inter vs Milan")

        # Case 4: England vs Sri Lanka (One Day International)
        q, h, a = thesportsdb_service.clean_event_query("England vs Sri Lanka (One Day International)")
        self.assertEqual(q, "England vs Sri Lanka")
        self.assertEqual(h, "England")
        self.assertEqual(a, "Sri Lanka")

    def test_sqlite_poster_cache(self):
        import time
        unique_suffix = str(time.time()).replace(".", "")
        test_key = f"test_team_a_vs_test_team_b_{unique_suffix}"
        test_thumb = f"https://r2.thesportsdb.com/images/media/event/thumb/test_{unique_suffix}.jpg"

        # Initially not in cache
        cached = db_service.get_poster_from_cache(test_key)
        self.assertIsNone(cached)

        # Save to cache
        db_service.save_poster_to_cache(test_key, test_thumb, "found")

        # Retrieve
        cached = db_service.get_poster_from_cache(test_key)
        self.assertIsNotNone(cached)
        status, thumb, _ = cached
        self.assertEqual(status, "found")
        self.assertEqual(thumb, test_thumb)

        # Save not_found
        nf_key = f"non_existent_event_{unique_suffix}"
        db_service.save_poster_to_cache(nf_key, None, "not_found")
        cached_nf = db_service.get_poster_from_cache(nf_key)
        self.assertIsNotNone(cached_nf)
        self.assertEqual(cached_nf[0], "not_found")
        self.assertIsNone(cached_nf[1])

    def test_format_thumb_url(self):
        self.assertEqual(
            thesportsdb_service.format_thumb_url("https://r2.thesportsdb.com/images/media/event/thumb/test.jpg"),
            "https://r2.thesportsdb.com/images/media/event/thumb/test.jpg/medium",
        )
        self.assertEqual(
            thesportsdb_service.format_thumb_url("https://r2.thesportsdb.com/images/media/event/thumb/test.jpg/medium"),
            "https://r2.thesportsdb.com/images/media/event/thumb/test.jpg/medium",
        )
        self.assertEqual(
            thesportsdb_service.format_thumb_url("https://r2.thesportsdb.com/images/media/event/thumb/test.jpg/small"),
            "https://r2.thesportsdb.com/images/media/event/thumb/test.jpg/medium",
        )
        self.assertIsNone(thesportsdb_service.format_thumb_url(None))

    async def test_search_event_thumb_success(self):
        import json
        from unittest.mock import MagicMock
        mock_response = {
            "event": [
                {
                    "strEvent": "Real Madrid Baloncesto vs Dubai Basketball",
                    "strThumb": "https://r2.thesportsdb.com/images/media/event/thumb/o4p5ix1786182990.jpg",
                    "strSquare": "https://r2.thesportsdb.com/images/media/event/square/boafxn1786186466.jpg"
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(mock_response)
        mock_resp.json.return_value = mock_response

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            thumb_url, retry_after = await thesportsdb_service.search_event_thumb("Real Madrid Baloncesto vs Dubai Basketball")
            self.assertEqual(thumb_url, "https://r2.thesportsdb.com/images/media/event/thumb/o4p5ix1786182990.jpg/medium")
            self.assertIsNone(retry_after)

    async def test_search_event_thumb_rate_limit(self):
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limit exceeded. Riprova tra 8m"
        mock_resp.headers = {"Retry-After": "480"}

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            thumb_url, retry_after = await thesportsdb_service.search_event_thumb("Some Team vs Other Team")
            self.assertIsNone(thumb_url)
            self.assertEqual(retry_after, 480)

    def test_parse_calendar_html(self):
        sample_html = """
        <table>
            <tr><th>Time</th><th>Sport</th><th>League</th><th>Event</th></tr>
            <tr>
                <td>15:00</td>
                <td>Soccer</td>
                <td>Italian Serie A</td>
                <td>Juventus vs Napoli</td>
                <td><img src="https://r2.thesportsdb.com/images/media/event/thumb/juve_napoli.jpg/tiny" /></td>
            </tr>
            <tr>
                <td>18:30</td>
                <td>Basketball</td>
                <td>Euroleague</td>
                <td>Real Madrid vs Panathinaikos</td>
                <td><img src="/images/no_thumb.png" /></td>
            </tr>
        </table>
        """
        events = thesportsdb_service.parse_calendar_html(sample_html, "2026-09-26")
        self.assertEqual(len(events), 2)

        # Event 1: Juventus vs Napoli
        ev1 = events[0]
        self.assertEqual(ev1["title"], "Juventus vs Napoli")
        self.assertEqual(ev1["home"], "Juventus")
        self.assertEqual(ev1["away"], "Napoli")
        self.assertEqual(ev1["_silo"], "football")
        self.assertEqual(ev1["competition"], "Italian Serie A")
        self.assertEqual(ev1["thumb"], "https://r2.thesportsdb.com/images/media/event/thumb/juve_napoli.jpg/medium")

        # Event 2: Real Madrid vs Panathinaikos (no_thumb)
        ev2 = events[1]
        self.assertEqual(ev2["title"], "Real Madrid vs Panathinaikos")
        self.assertEqual(ev2["_silo"], "basketball")
        self.assertEqual(ev2["competition"], "Euroleague")
        self.assertIsNone(ev2["thumb"])

    def test_enrich_matches_from_calendar(self):
        # Setup mock calendar cache
        thesportsdb_service._calendar_cache = [
            {
                "title": "Arsenal vs Leicester City",
                "home": "Arsenal",
                "away": "Leicester City",
                "sport": "Soccer",
                "_silo": "football",
                "competition": "English Premier League",
                "date_str": "2026-09-26",
                "time_str": "16:00",
                "date_ms": 1789900000000,
                "thumb": "https://r2.thesportsdb.com/images/media/event/thumb/arsenal_leicester.jpg/medium",
            }
        ]
        thesportsdb_service._calendar_by_silo = {
            "football": thesportsdb_service._calendar_cache
        }

        # Upstream match lacking poster and competition
        upstream_match = {
            "id": "match_ars_lei",
            "title": "Arsenal FC vs Leicester",
            "_silo": "football",
            "category": "football",
            "date": 1789900000000,
            "poster": None,
            "competition": None,
            "teams": {"home": {"name": "Arsenal FC"}, "away": {"name": "Leicester"}},
        }

        count = thesportsdb_service.enrich_matches([upstream_match])
        self.assertEqual(count, 1)
        self.assertEqual(upstream_match["poster"], "https://r2.thesportsdb.com/images/media/event/thumb/arsenal_leicester.jpg/medium")
        self.assertEqual(upstream_match["competition"], "English Premier League")
        self.assertTrue(upstream_match.get("_tsdb_matched"))


if __name__ == "__main__":
    unittest.main()

