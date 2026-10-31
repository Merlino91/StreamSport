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

            # Catalog live_window for Tokyo MUST be ~220 minutes (ends at Guangzhou start + 10m buffer), NOT 360m!
            lw = catalog_service.get_live_window_minutes(tokyo_ev)
            self.assertEqual(lw, 220)

    def test_differita_sintesi_tagging(self):
        """EPG events without LIVE for active tournaments are tagged [Differita / Sintesi]; replays are discarded."""
        # 1. Live event retains LIVE (not replay, not differita)
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("LIVE WTA 500 Tokyo", is_live=True))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("LIVE WTA 250 Guangzhou", is_live=True))
        self.assertFalse(italian_epg_service._is_replay_event("LIVE WTA 500 Tokyo", is_live=True))

        # 2. Tournament session without LIVE for active tournaments is tagged differita/sintesi
        active_cities = {"tokyo", "guangzhou", "vienna", "basilea"}
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("WTA 500 Tokyo 2026", active_tennis_cities=active_cities))
        self.assertTrue(italian_epg_service._is_differita_or_sintesi("WTA 250 Guangzhou 2026", active_tennis_cities=active_cities))
        self.assertFalse(italian_epg_service._is_replay_event("WTA 500 Tokyo 2026", active_tennis_cities=active_cities))

        # 3. Previous year tournament is an archival replay, NOT differita
        self.assertTrue(italian_epg_service._is_replay_event("ATP 1000 Parigi Bercy 2025 - Ep. 5"))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("ATP 1000 Parigi Bercy 2025 - Ep. 5"))
        self.assertTrue(italian_epg_service._is_replay_event("Nitto ATP Finals Torino 2025"))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("Nitto ATP Finals Torino 2025"))

        # 4. Outdated tournament from current year (e.g. Berlino in October) is an archival replay
        self.assertTrue(italian_epg_service._is_replay_event("WTA 500 Berlino 2026", active_tennis_cities=active_cities))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("WTA 500 Berlino 2026", active_tennis_cities=active_cities))
        self.assertTrue(italian_epg_service._is_replay_event("WTA 500 Berlino 2026 - Ep. 21 - Linda Noskova vs Jessica Pegula", active_tennis_cities=active_cities))
        self.assertFalse(italian_epg_service._is_differita_or_sintesi("WTA 500 Berlino 2026 - Ep. 21 - Linda Noskova vs Jessica Pegula", active_tennis_cities=active_cities))

    def test_fetch_sky_events_discards_replays_and_tags_active_differita(self):
        """Verifies _fetch_sky_events skips archival replays (Parigi Bercy 2025, Berlino 2026) while retaining Tokyo differita and live."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "events": [
                {
                    "eventTitle": "LIVE WTA 500 Tokyo",
                    "starttime": "2026-10-31T05:00:00Z",
                    "endtime": "2026-10-31T08:00:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
                {
                    "eventTitle": "WTA 500 Tokyo 2026",
                    "starttime": "2026-10-31T14:00:00Z",
                    "endtime": "2026-10-31T17:00:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
                {
                    "eventTitle": "ATP 1000 Parigi Bercy 2025 - Ep. 2 - Alexander Zverev vs Daniil Medvedev",
                    "starttime": "2026-10-31T17:00:00Z",
                    "endtime": "2026-10-31T19:00:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
                {
                    "eventTitle": "WTA 500 Berlino 2026 - Ep. 21 - Linda Noskova vs Jessica Pegula WTA 500 Berlino Finale # 1",
                    "starttime": "2026-10-31T19:00:00Z",
                    "endtime": "2026-10-31T21:00:00Z",
                    "content": {"genre": {"name": "Sport"}, "subgenre": {"name": "Tennis"}},
                },
            ]
        }

        active_cities = {"tokyo", "guangzhou"}
        with patch.object(tvvoo_service, "get_channel_streams", return_value=[{"canonical": "supertennis", "tag": "c", "url": "https://test"}]), \
             patch.object(tvvoo_service, "get_canonical_from_sky_id", return_value="supertennis"):
            async def run_fetch():
                mock_async_client = MagicMock()
                mock_async_client.get = AsyncMock(return_value=mock_resp)
                return await italian_epg_service._fetch_sky_events(
                    mock_async_client,
                    "2026-10-31T00:00:00Z",
                    "2026-10-31T23:59:59Z",
                    active_tennis_cities=active_cities,
                )

            events = asyncio.run(run_fetch())
            titles = [e["title"] for e in events]

            # 1. LIVE WTA 500 Tokyo must be present as LIVE
            self.assertTrue(any("LIVE WTA 500 Tokyo" in t and "[Differita" not in t for t in titles))

            # 2. WTA 500 Tokyo 2026 must be present with [Differita / Sintesi]
            self.assertTrue(any("[Differita / Sintesi] WTA 500 Tokyo 2026" in t for t in titles))

            # 3. ATP 1000 Parigi Bercy 2025 MUST BE DISCARDED (not in titles)
            self.assertFalse(any("Parigi Bercy 2025" in t for t in titles))

            # 4. WTA 500 Berlino 2026 MUST BE DISCARDED (not in titles)
            self.assertFalse(any("Berlino" in t for t in titles))

    def test_end_date_sqlite_persistence_and_live_window(self):
        """Verifies end_date persists in SQLite matches table and drives catalog live window (+10 min)."""
        from app.services.db_service import db_service
        db_service._ensure_db()

        test_id = "sky_6000_end_date_persist_unit_test"
        start_ms = 1793370000000
        end_ms = start_ms + (120 * 60 * 1000)  # 2 hours

        match_data = {
            "id": test_id,
            "title": "WTA 500 Tokyo 2026",
            "category": "tennis",
            "_provider": "italian_epg",
            "date": start_ms,
            "end_date": end_ms,
            "sources": [],
        }

        db_service.save_matches([match_data])
        fetched = db_service.get_match_by_id(test_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.get("end_date"), end_ms)

        lw = catalog_service.get_live_window_minutes(fetched)
        self.assertEqual(lw, 130)  # 120m duration + 10m extra buffer

        db_service.delete_matches_by_ids([test_id])

    def test_parse_sky_semantic_url(self):
        """Verifies Sky URL hierarchy correctly maps competitions and flags studio/magazine shows."""
        # 1. Real sporting competitions
        cases_valid = [
            ("/sport/tennis-wta-500-tokyo/1a-semifinale/uuid-1", "WTA 500 Tokyo", False),
            ("/sport/tennis-atp-500-vienna/quarti-di-finale/uuid-2", "ATP 500 Vienna", False),
            ("/sport/tennis-atp-masters-1000-parigi/1o-turno/uuid-3", "ATP Masters 1000 Parigi", False),
            ("/sport/tennis-wimbledon/finale/uuid-4", "Wimbledon", False),
            ("/sport/serie-a/venezia-inter/uuid-5", "Serie A", False),
            ("/sport/premier-league/arsenal-chelsea/uuid-6", "Premier League", False),
            ("/sport/f1/gp-messico-prove-libere-2/uuid-7", "Formula 1", False),
            ("/sport/motogp/gp-indonesia-gara/uuid-8", "MotoGP", False),
            ("/sport/eurolega/barcellona-olimpia-milano/uuid-9", "Eurolega", False),
            ("/sport/padel-kuwait-major/semifinali/uuid-10", "Padel Kuwait Major", False),
        ]
        for url, expected_comp, expected_mag in cases_valid:
            comp, is_mag = italian_epg_service._parse_sky_semantic_url(url)
            self.assertEqual(comp, expected_comp, f"Failed for {url}")
            self.assertEqual(is_mag, expected_mag, f"Failed magazine check for {url}")

        # 2. Studio / Magazine TV shows that must be discarded
        cases_magazine = [
            "/sport/motogp-remix/motogp-remix-gp-thailandia/uuid-11",
            "/sport/di-canio-premier-special/di-canio-premier-special-ep-3/uuid-12",
            "/sport/f1-paddock-live-post-libere/f1-paddock-live/uuid-13",
            "/sport/bundesliga-matchplan/bundesliga-matchplan/uuid-14",
            "/sport/the-boat-show/the-boat-show-ep-10/uuid-15",
            "/sport/atp-tour-this-week/atp-tour-this-week/uuid-16",
            "/sport/premier-league-magazine/premier-league-magazine/uuid-17",
            "/sport/f1-conf-stampa-piloti/f1-conf-stampa/uuid-18",
            "/sport/motogp-talent-time/motogp-talent-time/uuid-19",
        ]
        for url in cases_magazine:
            _, is_mag = italian_epg_service._parse_sky_semantic_url(url)
            self.assertTrue(is_mag, f"Expected is_magazine=True for {url}")

    def test_extract_competition_from_synopsis(self):
        """Verifies fallback competition extraction from Guida TV event synopses."""
        s1 = "Semifinali - Dai campi dell'Ariake Coliseum di Tokyo, Giappone, il torneo di tennis WTA 500..."
        s2 = "Quarti - Dal Wiener Stadthalle di Vienna, Austria, il torneo di tennis ATP 500..."
        s3 = "Da Citta' del Messico, le prove libere del Gran Premio del Messico di F1..."
        s4 = "10a Giornata. Da San Siro, Milano, la sfida di Serie A tra Inter e Juventus..."
        s5 = "7a Giornata di Eurolega dal Palau Blaugrana di Barcellona..."
        s6 = "Regular season di Superlega di volley maschile da Perugia..."

        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s1), "WTA 500 Tokyo")
        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s2), "ATP 500 Vienna")
        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s3), "Formula 1: GP Messico")
        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s4), "Serie A")
        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s5), "Eurolega")
        self.assertEqual(italian_epg_service._extract_competition_from_synopsis(s6), "Superlega")

    def test_sparse_sky_title_enrichment_and_wta_genre_classification(self):
        """Verifies sparse Sky event '1a Semifinale' adopts WTA competition from URL and classifies into WTA genre tab."""
        from app.services.genre_classifier import genre_classifier

        mock_event = {
            "eventTitle": "1a Semifinale",
            "eventSynopsis": "Semifinali - Dai campi dell'Ariake Coliseum di Tokyo, Giappone, il torneo di tennis WTA 500.",
            "starttime": "2026-10-31T04:00:00Z",
            "endtime": "2026-10-31T06:00:00Z",
            "programHighlights": "L",
            "content": {
                "url": "/sport/tennis-wta-500-tokyo/1a-semifinale/a581452a-cf39-4458-aa03-3ca01f2f010c",
                "genre": {"name": "Sport"},
                "subgenre": {"name": "Tennis"},
            },
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"events": [mock_event]}

        active_cities = {"tokyo"}
        with patch.object(tvvoo_service, "get_channel_streams", return_value=[{"canonical": "sky_sport_tennis", "tag": "c", "url": "https://test"}]), \
             patch.object(tvvoo_service, "get_canonical_from_sky_id", return_value="sky_sport_tennis"), \
             patch.object(italian_epg_service, "SKY_SPORTS_CHANNELS", {"6000": {"name": "Sky Sport Tennis"}}), \
             patch.object(italian_epg_service, "SKY_GENERALIST_CHANNELS", {}):
            async def run_fetch():
                mock_async_client = MagicMock()
                mock_async_client.get = AsyncMock(return_value=mock_resp)
                return await italian_epg_service._fetch_sky_events(
                    mock_async_client,
                    "2026-10-31T00:00:00Z",
                    "2026-10-31T23:59:59Z",
                    active_tennis_cities=active_cities,
                )

            events = asyncio.run(run_fetch())
            self.assertEqual(len(events), 1)
            ev = events[0]

            # 1. Title must be enriched with competition
            self.assertEqual(ev.get("title"), "WTA 500 Tokyo: 1a Semifinale")
            self.assertEqual(ev.get("competition"), "WTA 500 Tokyo")

            # 2. GenreClassifier MUST classify as ('tennis', 'WTA') - NOT ATP fallback!
            catalog, genre = genre_classifier.classify(ev)
            self.assertEqual(catalog, "tennis")
            self.assertEqual(genre, "WTA")

    def test_competition_reinforced_matching_and_youth_guard(self):
        """Verifies competition data reinforces matching while youth guard prevents cross-capture."""
        session_start = 1793400000000

        with patch.object(italian_epg_service, "get_matches", return_value=[
            {
                "id": "sky-serie-a-venezia-inter",
                "_provider": "italian_epg",
                "title": "Serie A: Venezia vs Inter",
                "competition": "Serie A",
                "_competition": "Serie A",
                "date": session_start,
                "_silo": "football",
                "category": "football",
                "teams": {"home": {"name": "Venezia"}, "away": {"name": "Inter"}},
                "sources": [{"source": "tvvoo", "id": "sky_sport_calcio", "name": "Sky Sport Calcio"}],
            }
        ]):
            # Case 1: Senior match with Serie A competition -> Enriched!
            match_senior = {
                "id": "dlhd-venezia-inter",
                "title": "Venezia vs Inter",
                "competition": "Italy - Serie A",
                "date": session_start,
                "_silo": "football",
                "category": "football",
                "teams": {"home": {"name": "Venezia"}, "away": {"name": "Inter"}},
                "sources": [{"source": "dlhd", "id": "1", "name": "Stream 1"}],
            }

            # Case 2: Youth match (Primavera) -> Youth guard BLOCKS enrichment from senior Serie A
            match_youth = {
                "id": "dlhd-venezia-inter-u19",
                "title": "Venezia U19 vs Inter U19",
                "competition": "Campionato Primavera 1",
                "_age": "+",
                "date": session_start,
                "_silo": "football",
                "category": "football",
                "teams": {"home": {"name": "Venezia U19"}, "away": {"name": "Inter U19"}},
                "sources": [{"source": "dlhd", "id": "2", "name": "Stream 2"}],
            }

            enriched_count = asyncio.run(italian_epg_service.enrich_matches_with_epg_channels([match_senior, match_youth]))
            self.assertEqual(enriched_count, 1)

            # Senior match received Sky Sport Calcio
            senior_sources = [s.get("name") for s in match_senior.get("sources", [])]
            self.assertIn("Sky Sport Calcio", senior_sources)

            # Youth match did NOT receive Sky Sport Calcio
            youth_sources = [s.get("name") for s in match_youth.get("sources", [])]
            self.assertNotIn("Sky Sport Calcio", youth_sources)


if __name__ == "__main__":
    unittest.main()
