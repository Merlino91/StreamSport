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

    def test_asymmetric_tv_schedule_window(self):
        """Verifies TV EPG allows up to 90m pre-match studio, but rejects late joinings > 20m."""
        import asyncio
        import time
        match_kickoff = 1791986400000  # 21:00

        self.service._last_fetch_time = time.time()
        self.service._cached_matches = [
            {
                "id": "sky_juve_milan",
                "title": "Juventus vs Milan",
                "date": match_kickoff - (60 * 60 * 1000),  # 20:00
                "_silo": "football",
                "sources": [{"name": "Sky Sport Uno", "id": "sky sport uno", "country": "Italy", "canonical": "sky sport uno"}],
            }
        ]
        match_pre = {
            "id": "streamed-juve-milan",
            "title": "Juventus vs Milan",
            "date": match_kickoff,
            "_silo": "football",
            "sources": [],
        }
        enriched = asyncio.run(self.service.enrich_matches_with_epg_channels([match_pre]))
        self.assertEqual(enriched, 1)
        self.assertTrue(any(s.get("name") == "Sky Sport Uno" for s in match_pre["sources"]))

        # Case B: EPG starts 30m after kickoff (21:30) -> REJECTED
        self.service._cached_matches = [
            {
                "id": "sky_juve_milan_late",
                "title": "Juventus vs Milan",
                "date": match_kickoff + (30 * 60 * 1000),  # 21:30
                "_silo": "football",
                "sources": [{"name": "Sky Sport Uno", "id": "sky sport uno", "country": "Italy", "canonical": "sky sport uno"}],
            }
        ]
        match_late = {
            "id": "streamed-juve-milan-late",
            "title": "Juventus vs Milan",
            "date": match_kickoff,
            "_silo": "football",
            "sources": [],
        }
        enriched_late = asyncio.run(self.service.enrich_matches_with_epg_channels([match_late]))
        self.assertEqual(enriched_late, 0)
        self.assertEqual(len(match_late["sources"]), 0)

    def test_tennis_circuit_and_city_extraction(self):
        """Verifies circuit and city extraction from various tennis strings."""
        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("Tennis 🎾 ATP - Singles: Shanghai (China)")
        self.assertEqual(circ, "atp")
        self.assertEqual(city, "shanghai")

        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("Tennis 🎾 WTA - Singles: Wuhan (China)")
        self.assertEqual(circ, "wta")
        self.assertEqual(city, "wuhan")

        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("Tennis 🎾 Challenger Men - Singles: Olbia (Italy)")
        self.assertEqual(circ, "challenger")
        self.assertEqual(city, "olbia")

        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("1/4 Final 2 (ATP Masters Shanghai)")
        self.assertEqual(circ, "atp")
        self.assertEqual(city, "shanghai")

        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("Dalla Qizhong Forest Sports City Arena di Shanghai, Cina.")
        self.assertEqual(city, "shanghai")

        circ, city = ItalianEPGService._extract_tennis_circuit_and_city("... Tennis: WTA 1000 Wuhan. - Dall'Optics Valley International Tennis Center di Wuhan, Cina")
        self.assertEqual(circ, "wta")
        self.assertEqual(city, "wuhan")

    def test_tennis_session_bridge_enrichment_and_poster(self):
        """
        Verifies individual tennis matches inherit Sky Sport Tennis channel
        and official 16:9 poster from tournament session broadcast.
        """
        import asyncio
        import time
        session_start = 1792058400000  # 10:00 AM

        self.service._last_fetch_time = time.time()
        self.service._cached_matches = [
            {
                "id": "sky_tennis_shanghai_session",
                "title": "ATP Masters 1000 Shanghai - 8a giornata",
                "eventSynopsis": "Dalla Qizhong Forest Sports City Arena di Shanghai, Cina.",
                "competition": "ATP Masters 1000 Shanghai",
                "date": session_start,
                "_silo": "tennis",
                "category": "tennis",
                "teams": None,  # General tournament session broadcast, not 1v1 specific players
                "poster": "https://apid.sky.it/uuid/95ec79e3-ee6f-47fa-8c63-bb193f49afed/COVER_TITLE_WIDE",
                "sources": [
                    {
                        "source": "tvvoo",
                        "id": "sky sport tennis",
                        "name": "Sky Sport Tennis",
                        "country": "Italy",
                        "canonical": "sky sport tennis",
                    }
                ],
            }
        ]

        # Match 1: Zverev vs De Minaur in Shanghai (starts 2h into the 6h session) -> MATCHES
        match_shanghai = {
            "id": "dlhd-zverev-de-minaur",
            "title": "Zverev A. vs De Minaur A.",
            "competition": "Tennis: ATP - Singles: Shanghai (China)",
            "date": session_start + (120 * 60 * 1000),  # 12:00 PM (+2h)
            "_silo": "tennis",
            "category": "tennis",
            "poster": None,
            "sources": [{"source": "dlhd", "id": "1073", "name": "Tennis HD Stream"}],
        }

        # Match 2: WTA Wuhan match (different city and circuit) -> DOES NOT MATCH
        match_wuhan = {
            "id": "dlhd-muchova-shnaider",
            "title": "Muchova K. vs Shnaider D.",
            "competition": "Tennis: WTA - Singles: Wuhan (China)",
            "date": session_start + (60 * 60 * 1000),
            "_silo": "tennis",
            "category": "tennis",
            "poster": None,
            "sources": [{"source": "dlhd", "id": "1075", "name": "Tennis HD Stream"}],
        }

        enriched = asyncio.run(self.service.enrich_matches_with_epg_channels([match_shanghai, match_wuhan]))
        self.assertEqual(enriched, 1)

        # Shanghai match inherited Sky Sport Tennis as primary channel and Sky 16:9 poster
        self.assertEqual(match_shanghai["sources"][0]["name"], "Sky Sport Tennis")
        self.assertEqual(match_shanghai["sources"][0]["canonical"], "sky sport tennis")
        self.assertEqual(match_shanghai["poster"], "https://apid.sky.it/uuid/95ec79e3-ee6f-47fa-8c63-bb193f49afed/COVER_TITLE_WIDE")

        # Wuhan match did NOT inherit Sky Sport Tennis
        self.assertEqual(len(match_wuhan["sources"]), 1)
        self.assertEqual(match_wuhan["sources"][0]["name"], "Tennis HD Stream")
        self.assertIsNone(match_wuhan["poster"])

    def test_tennis_live_window_duration(self):
        """Verifies 160 minutes for VS matches and 360 minutes for sessions."""
        from app.services.catalog_service import catalog_service

        vs_match = {
            "title": "Zverev A. vs De Minaur A.",
            "_silo": "tennis",
            "category": "tennis",
            "teams": {"home": {"name": "Zverev A."}, "away": {"name": "De Minaur A."}},
        }
        self.assertEqual(catalog_service.get_live_window_minutes(vs_match), 160)

        session_match = {
            "title": "WTA 1000 Wuhan",
            "_silo": "tennis",
            "category": "tennis",
            "teams": None,
        }
        self.assertEqual(catalog_service.get_live_window_minutes(session_match), 360)

    @patch("app.services.italian_epg_service.tvvoo_service")
    @patch("app.services.italian_epg_service.mandrakodi_service")
    async def test_sky_channel_resolved_via_mandrakodi_when_tvvoo_missing(
        self, mock_mandra, mock_tvvoo
    ):
        """Verifies that if TvVoo has 0 streams for a Sky channel, Mandrakodi streams are used and event is not ghosted."""
        # TvVoo returns empty (e.g. offline or missing)
        mock_tvvoo.get_channel_streams.return_value = []
        mock_tvvoo.get_canonical_from_sky_id.return_value = "sky sport 251"

        # Mandrakodi has this 24/7 channel registered
        mock_mandra.get_channel_streams.return_value = [
            {
                "source": "amstaff",
                "provider": "mandrakodi",
                "id": "mandra_sky_251",
                "name": "Sky Sport 251",
                "sky_channel_id": "skysport251",
                "stream_data": {"mpd_url": "https://test.mpd", "kid": "k1", "key": "k2"}
            }
        ]

        service = ItalianEPGService()
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "events": [
                {
                    "eventTitle": "Atalanta - Lazio (Diretta)",
                    "eventSynopsis": "Serie A Enilive 2026",
                    "starttime": "2026-10-21T18:45:00Z",
                    "endtime": "2026-10-21T20:45:00Z",
                    "content": {
                        "genre": {"id": 6, "name": "Sport"},
                        "subgenre": {"id": 38, "name": "Calcio"},
                    },
                }
            ]
        }
        mock_client.get.return_value = mock_resp

        parsed = await service._fetch_sky_events(mock_client, "2026-10-21T00:00:00Z", "2026-10-21T23:59:59Z")
        # Event is kept and NOT ghosted!
        self.assertGreater(len(parsed), 0)
        matched_ev = [e for e in parsed if "Atalanta" in e["title"]][0]
        self.assertEqual(len(matched_ev["sources"]), 1)
        self.assertEqual(matched_ev["sources"][0]["provider"], "mandrakodi")
        self.assertEqual(matched_ev["sources"][0]["id"], "mandra_sky_251")


if __name__ == "__main__":
    unittest.main()


