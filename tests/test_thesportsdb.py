import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

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

    def test_space_insensitive_and_guardrail_reconciliation(self):
        # 1. Test space-insensitive matching for Ostiamare vs Forli
        thesportsdb_service._calendar_cache = [
            {
                "title": "Ostiamare vs Forlì",
                "home": "Ostiamare",
                "away": "Forlì",
                "sport": "Soccer",
                "_silo": "football",
                "competition": "Serie D",
                "date_ms": 1789900000000,
                "thumb": "https://r2.thesportsdb.com/images/media/event/thumb/ostiamare_forli.jpg/medium",
            }
        ]
        thesportsdb_service._calendar_by_silo = {
            "football": thesportsdb_service._calendar_cache
        }

        # Virgilio-style title with 'Ostia Mare Lidocalcio'
        virgilio_match = {
            "id": "match_ostiamare_forli",
            "title": "Ostia Mare Lidocalcio vs Forlì",
            "_silo": "football",
            "category": "football",
            "date": 1789900000000,
            "poster": None,
            "competition": None,
        }

        count = thesportsdb_service.enrich_matches([virgilio_match])
        self.assertEqual(count, 1)
        self.assertEqual(virgilio_match["poster"], "https://r2.thesportsdb.com/images/media/event/thumb/ostiamare_forli.jpg/medium")

        # 2. Test guardrails: generic prefixes (Real, Virtus, Atletico) MUST NOT false match
        self.assertFalse(thesportsdb_service._team_matches("real madrid", "real sociedad"))
        self.assertFalse(thesportsdb_service._team_matches("virtus bologna", "virtus entella"))
        self.assertFalse(thesportsdb_service._team_matches("atletico madrid", "atletico bilbao"))

    async def test_semantic_html_search_fallback(self):
        sample_browse_html = """
        <html>
        <div id="browse_events">
        <a href='/event/2556445-ostiamare-vs-forl%c3%ac'>
            <img src='https://r2.thesportsdb.com/images/media/event/thumb/oadmnp1787395021.jpg/small' height='70PX'>
            <br>Ostiamare vs Forlì
        </a>
        </div>
        </html>
        """
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_browse_html

        with patch.object(thesportsdb_service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_get_client.return_value = mock_client

            thumb = await thesportsdb_service.search_event_thumb_html("Ostia Mare Lidocalcio", "Forlì")
            self.assertEqual(thumb, "https://r2.thesportsdb.com/images/media/event/thumb/oadmnp1787395021.jpg/medium")

    def test_is_tsdb_sport_compatible(self):
        self.assertTrue(thesportsdb_service.is_tsdb_sport_compatible("Soccer", "football"))
        self.assertFalse(thesportsdb_service.is_tsdb_sport_compatible("American-Football", "football"))
        self.assertTrue(thesportsdb_service.is_tsdb_sport_compatible("American-Football", "american-football"))
        self.assertFalse(thesportsdb_service.is_tsdb_sport_compatible("Baseball", "football"))
        self.assertTrue(thesportsdb_service.is_tsdb_sport_compatible("Basketball", "basketball"))

    async def test_semantic_html_search_filters_date_and_sport(self):
        # Sample HTML containing NFL match (wrong sport + wrong date) and MLS match (correct)
        sample_browse_html = """
        <html>
        <div class='col-sm-3'>
        <a href='/event/2475479-seattle-seahawks-vs-kansas-city-chiefs'>
            <img src='https://r2.thesportsdb.com/images/media/event/thumb/nfl_seahawks_chiefs.jpg/small'>
            <img src='/images/icons/svg/sports/American-Football.svg'/> Seattle Seahawks vs Kansas City Chiefs
        </a> (2026-10-26)
        <a href='/event/2407065-seattle-sounders-vs-sporting-kansas-city'>
            <img src='https://r2.thesportsdb.com/images/media/event/thumb/mls_sounders_kc.jpg/small'>
            <img src='/images/icons/svg/sports/Soccer.svg'/> Seattle Sounders vs Sporting Kansas City
        </a> (2026-10-02)
        </div>
        </html>
        """
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_browse_html

        with patch.object(thesportsdb_service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_get_client.return_value = mock_client

            # Match is on 2026-10-02 (timestamp 1790904600000), silo is 'football' (calcio)
            thumb = await thesportsdb_service.search_event_thumb_html(
                "Seattle Sounders FC",
                "Sporting Kansas City",
                match_date_ms=1790904600000,
                expected_silo="football",
            )
            # Must strictly skip NFL match and pick the MLS match!
            self.assertEqual(thumb, "https://r2.thesportsdb.com/images/media/event/thumb/mls_sounders_kc.jpg/medium")

    def test_extract_teams_motorsport_and_stage_guards(self):
        # 1. Motorsport silo must return empty teams (no home/away extraction)
        motor_match = {
            "title": "Formula 1: Azerbaijan Grand Prix - Gara (Baku City Circuit)",
            "_silo": "motori",
            "category": "motori",
        }
        h, a = thesportsdb_service._extract_teams(motor_match)
        self.assertEqual(h, "")
        self.assertEqual(a, "")

        # 2. Hyphen separation with stage/tournament tokens must not extract fake teams
        tennis_match = {
            "title": "ATP Tokyo - Finals",
            "_silo": "tennis",
            "category": "tennis",
        }
        h2, a2 = thesportsdb_service._extract_teams(tennis_match)
        self.assertEqual(h2, "")
        self.assertEqual(a2, "")

        # 3. Legitimate versus matches should extract normally
        real_match = {
            "title": "Jannik Sinner vs Carlos Alcaraz",
            "_silo": "tennis",
            "category": "tennis",
        }
        h3, a3 = thesportsdb_service._extract_teams(real_match)
        self.assertEqual(h3, "jannik sinner")
        self.assertEqual(a3, "carlos alcaraz")

    def test_motorsport_in_ram_reconciliation_and_cascade_fallback(self):
        # Mock cached motorsport calendar in memory
        base_time = 1790905500000  # Friday ~01:45 UTC
        mock_ms_calendar = [
            {
                "title": "Japan Free Practice 1",
                "competition": "MotoGP",
                "sport": "Motorsport",
                "_silo": "motor-sports",
                "date_ms": base_time,
                "thumb": None,
            },
            {
                "title": "Japan Qualifying 2",
                "competition": "MotoGP",
                "sport": "Motorsport",
                "_silo": "motor-sports",
                "date_ms": base_time + (24 * 3600 * 1000),  # Saturday
                "thumb": None,
            },
            {
                "title": "Japan Sprint Race",
                "competition": "MotoGP",
                "sport": "Motorsport",
                "_silo": "motor-sports",
                "date_ms": base_time + (28 * 3600 * 1000),  # Saturday Sprint
                "thumb": "https://r2.thesportsdb.com/images/media/event/thumb/japan_sprint.jpg/medium",
            },
            {
                "title": "Japan GP",
                "competition": "MotoGP",
                "sport": "Motorsport",
                "_silo": "motor-sports",
                "date_ms": base_time + (51 * 3600 * 1000),  # Sunday GP Main Race
                "thumb": "https://r2.thesportsdb.com/images/media/event/thumb/japan_gp_main.jpg/medium",
            },
            {
                "title": "Bahrain in Malaysia Grand Prix",
                "competition": "Formula 1",
                "sport": "Motorsport",
                "_silo": "motor-sports",
                "date_ms": base_time + (53 * 3600 * 1000),
                "thumb": "https://r2.thesportsdb.com/images/media/event/thumb/f1_malaysia_gp.jpg/medium",
            },
        ]

        old_cache = thesportsdb_service._calendar_cache
        old_silo = thesportsdb_service._calendar_by_silo
        try:
            thesportsdb_service._calendar_cache = mock_ms_calendar
            thesportsdb_service._calendar_by_silo = {"motor-sports": mock_ms_calendar}

            test_matches = [
                # Case 1: Minor session with no thumb (FP1) -> Falls back to Sunday Japan GP main poster!
                {
                    "id": "match_motogp_fp1",
                    "title": "MotoGP: GP Giappone - Prove Libere 1",
                    "_silo": "motor-sports",
                    "date": base_time,
                    "poster": None,
                },
                # Case 2: Qualifying 2 with no thumb -> Falls back to Sunday Japan GP main poster!
                {
                    "id": "match_motogp_q2",
                    "title": "MotoGP: Grand Prix of Japan - Qualifying 2",
                    "_silo": "motor-sports",
                    "date": base_time + (24 * 3600 * 1000),
                    "poster": None,
                },
                # Case 3: Session that has its own official thumb (Sprint Race) -> Takes session thumb!
                {
                    "id": "match_motogp_sprint",
                    "title": "MotoGP: Grand Prix of Japan - Sprint Race",
                    "_silo": "motor-sports",
                    "date": base_time + (28 * 3600 * 1000),
                    "poster": None,
                },
                # Case 4: Formula 1 event -> Matches F1 GP poster, not MotoGP!
                {
                    "id": "match_f1_fp1",
                    "title": "Formula 1: Malaysia Grand Prix - FP1",
                    "_silo": "motor-sports",
                    "date": base_time + (3 * 3600 * 1000),
                    "poster": None,
                },
            ]

            reconciled = thesportsdb_service.reconcile_motorsport_matches(test_matches)
            self.assertEqual(reconciled, 4)

            # Verification of artwork assignments
            self.assertEqual(
                test_matches[0]["poster"],
                "https://r2.thesportsdb.com/images/media/event/thumb/japan_gp_main.jpg/medium"
            )
            self.assertEqual(
                test_matches[1]["poster"],
                "https://r2.thesportsdb.com/images/media/event/thumb/japan_gp_main.jpg/medium"
            )
            self.assertEqual(
                test_matches[2]["poster"],
                "https://r2.thesportsdb.com/images/media/event/thumb/japan_sprint.jpg/medium"
            )
            self.assertEqual(
                test_matches[3]["poster"],
                "https://r2.thesportsdb.com/images/media/event/thumb/f1_malaysia_gp.jpg/medium"
            )
            for m in test_matches:
                self.assertTrue(m.get("_tsdb_matched"))
        finally:
            thesportsdb_service._calendar_cache = old_cache
            thesportsdb_service._calendar_by_silo = old_silo

    @patch("app.services.thesportsdb_service.thesportsdb_service._get_client")
    @patch("app.services.tvvoo_service.tvvoo_service.ensure_synced")
    async def test_fetch_browse_tv_matches(self, mock_ensure_synced, mock_get_client):
        sample_html = """
        <table>
            <tr>
                <td class="tv-event-card">
                    <a href='/event/1001-spain-vs-croatia'>
                        <img src='https://r2.thesportsdb.com/images/media/event/thumb/spain_croatia.jpg/small' alt='thumb'/>
                        <br><img src='/images/icons/calendar.png'/> Spain vs Croatia<br>
                    </a>
                    <img src='/images/icons/time.png'/>18:00 UTC<br>
                    <img src='/images/icons/svg/flags/italy.svg'/> <a href='/channel/10-sky-sport-arena'>Sky Sport Arena</a><br>
                    <img src='/images/icons/svg/flags/spain.svg'/> <a href='/channel/20-movistar-laliga'>Movistar LaLiga</a><br>
                </td>
                <td class="tv-event-card">
                    <a href='/event/1002-canada-local-match'>
                        <img src='https://r2.thesportsdb.com/images/media/event/thumb/canada.jpg/small' alt='thumb'/>
                        <br><img src='/images/icons/calendar.png'/> Canada Event<br>
                    </a>
                    <img src='/images/icons/time.png'/>20:00 UTC<br>
                    <img src='/images/icons/svg/flags/canada.svg'/> <a href='/channel/30-onesoccer'>OneSoccer CA</a><br>
                </td>
            </tr>
        </table>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        from app.services.tvvoo_service import tvvoo_service
        old_channels = tvvoo_service._channels_by_canonical
        try:
            tvvoo_service._channels_by_canonical = {
                "sky sport arena": [{
                    "canonical": "sky sport arena",
                    "display_name": "Sky Sport Arena",
                    "url": "https://vavoo.to/play/sky_arena_c",
                    "tag": "c",
                    "country": "Italy",
                }],
                "movistar laliga": [{
                    "canonical": "movistar laliga",
                    "display_name": "🇪🇸 Movistar LaLiga",
                    "url": "https://vavoo.to/play/movistar_c",
                    "tag": "c",
                    "country": "Spain",
                }],
            }

            matches = await thesportsdb_service.fetch_browse_tv_matches(force=True)

            # Canada event filtered out; Spain vs Croatia retained
            self.assertEqual(len(matches), 1)
            m = matches[0]
            self.assertEqual(m["title"], "Spain vs Croatia")
            self.assertEqual(m["category"], "football")
            self.assertEqual(m["poster"], "https://r2.thesportsdb.com/images/media/event/thumb/spain_croatia.jpg/medium")
            self.assertEqual(m["teams"]["home"]["name"], "Spain")
            self.assertEqual(m["teams"]["away"]["name"], "Croatia")

            # TvVoo streams attached and Italian stream prioritized at index 0
            sources = m["sources"]
            self.assertEqual(len(sources), 2)
            self.assertEqual(sources[0]["name"], "Sky Sport Arena")
            self.assertEqual(sources[0]["country"], "Italy")
            self.assertEqual(sources[1]["name"], "🇪🇸 Movistar LaLiga")
            self.assertEqual(sources[1]["country"], "Spain")
        finally:
            tvvoo_service._channels_by_canonical = old_channels


if __name__ == "__main__":
    unittest.main()


