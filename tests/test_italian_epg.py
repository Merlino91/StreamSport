import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.italian_epg_service import ItalianEPGService


MOCK_SKY_EVENTS_RESP = {
    "events": [
        # Match 1: Calcio on Sky Sport Calcio (Dedicated)
        {
            "eventTitle": "Manchester United - Tottenham (Solo Repl)",
            "eventSynopsis": "Premier League 2026/2027",
            "starttime": "2026-10-13T16:00:00Z",
            "endtime": "2026-10-13T18:00:00Z",
            "content": {
                "genre": {"id": 6, "name": "Sport"},
                "subgenre": {"id": 38, "name": "Calcio"},
            },
        },
        # Match 2: Nations League with Country Translation
        {
            "eventTitle": "UEFA Nations League: Norvegia-Portogallo (Diretta)",
            "eventSynopsis": "Fase a gironi",
            "starttime": "2026-10-13T18:45:00Z",
            "endtime": "2026-10-13T20:45:00Z",
            "content": {
                "genre": {"id": 6, "name": "Sport"},
                "subgenre": {"id": 38, "name": "Calcio"},
            },
        },
        # Match 3: Motorsport (GP Gara)
        {
            "eventTitle": "GP Indonesia: Gara",
            "eventSynopsis": "Gara MotoGP Mondiale",
            "starttime": "2026-10-13T07:00:00Z",
            "endtime": "2026-10-13T08:00:00Z",
            "content": {
                "genre": {"id": 6, "name": "Sport"},
                "subgenre": {"id": 40, "name": "Motori"},
            },
        },
        # Match 4: Talk show / Magazine filler (Must be dropped)
        {
            "eventTitle": "Uefa Champions League Magazine 2026/2027",
            "eventSynopsis": "Magazine europeo",
            "starttime": "2026-10-13T10:00:00Z",
            "endtime": "2026-10-13T10:30:00Z",
            "content": {
                "genre": {"id": 6, "name": "Sport"},
            },
        },
        # Match 5: Single-word monografia (Must be dropped)
        {
            "eventTitle": "Vialli",
            "eventSynopsis": "Speciale monografico",
            "starttime": "2026-10-13T11:00:00Z",
            "endtime": "2026-10-13T12:00:00Z",
            "content": {
                "genre": {"id": 6, "name": "Sport"},
            },
        },
    ]
}

MOCK_TIVUSAT_EVENTS_RESP = {
    "events": [
        # Match 1: Pallavolo on Rai Sport HD
        {
            "date_start": "13-10-2026 20:30",
            "date_end": "13-10-2026 22:30",
            "program": {
                "title": "Pallavolo: Serie A1 - Conegliano - Busto Arsizio",
                "description": "Campionato femminile",
                "categories": [{"id": 9, "slug": "sport", "name": "Sport"}],
                "genres": [{"id": 45, "slug": "pallavolo", "name": "Pallavolo"}],
            },
        },
        # Match 2: TV8 Non-Sport show (Generalist, must be dropped)
        {
            "date_start": "13-10-2026 06:00",
            "date_end": "13-10-2026 06:30",
            "program": {
                "title": "TG24 Pagine (Diretta)",
                "description": "Rassegna stampa",
                "categories": [],
                "genres": [],
            },
        },
    ]
}


class TestItalianEPGService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = ItalianEPGService()

    def test_clean_event_title(self):
        # Strips replays, season/episode tags, and dates
        raw1 = "Tennis Atp Masters 1000 - Stag. 2026 Ep. 196 - Shanghai 6a Giornata (Solo Repl) 12/10/2026"
        clean1 = self.service._clean_event_title(raw1)
        self.assertNotIn("Solo Repl", clean1)
        self.assertNotIn("12/10/2026", clean1)
        self.assertNotIn("Stag.", clean1)

        raw2 = "GP Indonesia: Gara (Diretta)"
        clean2 = self.service._clean_event_title(raw2)
        self.assertEqual(clean2, "GP Indonesia: Gara")

    def test_extract_competition_and_details(self):
        comp, details = self.service._extract_competition_and_details("Serie C: Casertana - Catania")
        self.assertEqual(comp, "Serie C")
        self.assertEqual(details, "Casertana - Catania")

        comp2, details2 = self.service._extract_competition_and_details("GP Indonesia: Gara")
        self.assertEqual(comp2, "GP Indonesia")
        self.assertEqual(details2, "Gara")

    def test_normalize_teams_and_title(self):
        # Direct versus pairing
        title, teams = self.service._normalize_teams_and_title("Premier League", "Manchester United - Tottenham")
        self.assertEqual(title, "Premier League: Manchester United vs Tottenham")
        self.assertIsNotNone(teams)
        self.assertEqual(teams["home"]["name"], "Manchester United")
        self.assertEqual(teams["away"]["name"], "Tottenham")

        # National team country translation
        title2, teams2 = self.service._normalize_teams_and_title("UEFA Nations League", "Norvegia - Portogallo")
        self.assertEqual(title2, "UEFA Nations League: Norway vs Portugal")
        self.assertIsNotNone(teams2)
        self.assertEqual(teams2["home"]["name"], "Norway")
        self.assertEqual(teams2["away"]["name"], "Portugal")

        # Non-versus event preserves title without teams dict
        title3, teams3 = self.service._normalize_teams_and_title("GP Indonesia", "Gara")
        self.assertIn("GP Indonesia: Gara", title3)
        self.assertIsNone(teams3)

    def test_is_real_sport_event(self):
        # Match with teams
        self.assertTrue(self.service._is_real_sport_event("Inter vs Milan", {"home": {}, "away": {}}))
        # Race/session keyword
        self.assertTrue(self.service._is_real_sport_event("GP Indonesia: Gara", None))
        self.assertTrue(self.service._is_real_sport_event("ATP Masters Shanghai 7a Giornata", None))
        # Filler magazines / monografie without teams must be False
        self.assertFalse(self.service._is_real_sport_event("Vialli", None))
        self.assertFalse(self.service._is_real_sport_event("Cressoni", None))
        self.assertFalse(self.service._is_real_sport_event("Uefa Champions League Magazine 2026/2027", None))
        self.assertFalse(self.service._is_real_sport_event("News Line", None))

    def test_resolve_category_and_silo(self):
        # Dedicated channel
        calcio_meta = {"default_silo": "football", "default_cat": "football"}
        cat, silo = self.service._resolve_category_and_silo(calcio_meta, "Qualsiasi Partita")
        self.assertEqual(cat, "football")
        self.assertEqual(silo, "football")

        # Generic channel with subgenre
        generic_meta = {"name": "Sky Sport Uno"}
        cat2, silo2 = self.service._resolve_category_and_silo(generic_meta, "Gara", subgenre_name="Motori")
        self.assertEqual(cat2, "motori")
        self.assertEqual(silo2, "motor-sports")

        cat3, silo3 = self.service._resolve_category_and_silo(generic_meta, "Conegliano vs Busto Arsizio", subgenre_name="Pallavolo")
        self.assertEqual(cat3, "volley")
        self.assertEqual(silo3, "volleyball")

    @patch("app.services.italian_epg_service.tvvoo_service")
    @patch("app.services.italian_epg_service.httpx.AsyncClient")
    async def test_get_matches_mock(self, mock_client_cls, mock_tvvoo):
        # Mock HTTP responses
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "apid.sky.it" in url:
                if "channels=9113" in url:  # Sky Sport Calcio
                    resp.json.return_value = MOCK_SKY_EVENTS_RESP
                else:
                    resp.json.return_value = {"events": []}
            elif "services.tivulaguida.it" in url:
                if "channels/88/" in url:  # Rai Sport HD
                    resp.json.return_value = MOCK_TIVUSAT_EVENTS_RESP
                else:
                    resp.json.return_value = {"events": []}
            return resp

        mock_client.get.side_effect = mock_get

        # Mock TvVoo streams
        def mock_get_streams(channel_name=None, country=None, sky_id=None, tivusat_id=None):
            if str(sky_id) == "9113":
                return [{"canonical": "sky sport calcio", "display_name": "Sky Sport Calcio", "url": "https://vavoo.to/play/calcio", "tag": "c"}]
            if str(tivusat_id) == "88":
                return [{"canonical": "rai sport", "display_name": "Rai Sport HD", "url": "https://vavoo.to/play/raisport", "tag": "c"}]
            return []

        mock_tvvoo.get_channel_streams.side_effect = mock_get_streams
        mock_tvvoo.ensure_synced = AsyncMock()

        matches = await self.service.get_matches(force=True)

        # Expected:
        # Sky: Manchester United vs Tottenham, Norway vs Portugal, GP Indonesia: Gara (3 events)
        # (Magazine and Vialli dropped by filter)
        # Tivùsat: Pallavolo Conegliano vs Busto Arsizio (1 event)
        # (TV8 non-sport dropped by filter)
        # Total = 4 events
        self.assertEqual(len(matches), 4)

        # Verify Manchester United vs Tottenham
        manu = next(m for m in matches if "Manchester United" in m["title"])
        self.assertEqual(manu["_silo"], "football")
        self.assertEqual(manu["teams"]["home"]["name"], "Manchester United")
        self.assertEqual(manu["teams"]["away"]["name"], "Tottenham")
        self.assertEqual(len(manu["sources"]), 1)
        self.assertEqual(manu["sources"][0]["source"], "tvvoo")
        self.assertEqual(manu["sources"][0]["name"], "Sky Sport Calcio")

        # Verify Norway vs Portugal (Translated)
        norway = next(m for m in matches if "Norway" in m["title"])
        self.assertEqual(norway["teams"]["home"]["name"], "Norway")
        self.assertEqual(norway["teams"]["away"]["name"], "Portugal")

        # Verify Volleyball
        volley = next(m for m in matches if "Conegliano" in m["title"])
        self.assertEqual(volley["_silo"], "volleyball")
        self.assertEqual(volley["category"], "volley")
        self.assertEqual(volley["sources"][0]["name"], "Rai Sport HD")

    def test_filter_conflicting_italian_channels_purges_palpably_wrong(self):
        # Setup EPG cache with:
        # Sky Sport Calcio: Latina vs Atalanta U23 at time T
        # Sky Sport Max: Villarreal CF vs Napoli at time T
        match_time = 1791986400000
        self.service._cached_matches = [
            {
                "id": "sky_calcio_latina",
                "title": "Latina vs Atalanta U23",
                "date": match_time,
                "teams": {"home": {"name": "Latina"}, "away": {"name": "Atalanta U23"}},
                "sources": [{"name": "Sky Sport Calcio", "id": "sky sport calcio", "country": "Italy"}],
            },
            {
                "id": "sky_max_villarreal",
                "title": "Villarreal CF vs Napoli",
                "date": match_time,
                "teams": {"home": {"name": "Villarreal CF"}, "away": {"name": "Napoli"}},
                "sources": [{"name": "Sky Sport Max", "id": "sky sport max", "country": "Italy"}],
            },
        ]

        # Target match from DaddyLive with mixed channels
        match = {
            "id": "dlhd-latina",
            "title": "Latina vs Atalanta U23",
            "date": match_time,
            "teams": {"home": {"name": "Latina"}, "away": {"name": "Atalanta U23"}},
            "sources": [
                {"source": "dlhd", "name": "Sky Sport Calcio"},
                {"source": "dlhd", "name": "Sky Sport 251"},
                {"source": "dlhd", "name": "Sky Sport Max"},
                {"source": "dlhd", "name": "Canal+ Sport"},
            ],
        }

        purged_count = self.service.filter_conflicting_italian_channels([match])
        self.assertEqual(purged_count, 1)

        source_names = [s["name"] for s in match["sources"]]
        # Sky Sport Max must be purged because EPG proves Villarreal vs Napoli is on!
        self.assertNotIn("Sky Sport Max", source_names)
        # Legitimate and uncertain channels must be preserved
        self.assertIn("Sky Sport Calcio", source_names)
        self.assertIn("Sky Sport 251", source_names)  # Ignorance / missing EPG -> KEPT
        self.assertIn("Canal+ Sport", source_names)   # Foreign channel -> KEPT

    def test_filter_conflicting_italian_channels_preserves_on_diretta_confirmation(self):
        match_time = 1791986400000
        self.service._cached_matches = [
            {
                "id": "sky_max_villarreal",
                "title": "Villarreal CF vs Napoli",
                "date": match_time,
                "sources": [{"name": "Sky Sport Max", "id": "sky sport max", "country": "Italy"}],
            },
        ]

        match = {
            "id": "dlhd-match",
            "title": "Sample Match",
            "date": match_time,
            "_diretta_tv_channels": ["Sky Sport Max"],  # Explicitly confirmed by Diretta.it!
            "sources": [
                {"source": "dlhd", "name": "Sky Sport Max"},
            ],
        }

        purged_count = self.service.filter_conflicting_italian_channels([match])
        self.assertEqual(purged_count, 0)
        self.assertEqual(len(match["sources"]), 1)
        self.assertEqual(match["sources"][0]["name"], "Sky Sport Max")

    def test_filter_conflicting_italian_channels_preserves_generic_container(self):
        match_time = 1791986400000
        self.service._cached_matches = [
            {
                "id": "sky_calcio_diretta_gol",
                "title": "Diretta Gol Serie B",
                "date": match_time,
                "sources": [{"name": "Sky Sport Calcio", "id": "sky sport calcio", "country": "Italy"}],
            },
        ]

        match = {
            "id": "dlhd-latina",
            "title": "Latina vs Atalanta U23",
            "date": match_time,
            "sources": [
                {"source": "dlhd", "name": "Sky Sport Calcio"},
            ],
        }

        purged_count = self.service.filter_conflicting_italian_channels([match])
        self.assertEqual(purged_count, 0)
        self.assertEqual(len(match["sources"]), 1)


if __name__ == "__main__":
    unittest.main()

