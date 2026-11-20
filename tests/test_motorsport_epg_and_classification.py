import asyncio
import unittest
from app.services.canonical_competition_service import detect_canonical_competition, is_tier_match
from app.services.diretta_service import diretta_service
from app.services.genre_classifier import genre_classifier
from app.services.italian_epg_service import italian_epg_service
from app.services.team_matching import calculate_match_score


class TestMotorsportsIntegration(unittest.TestCase):

    def test_italian_epg_motorsport_resolution(self):
        meta = {"name": "Sky Sport Uno", "default_silo": None, "default_cat": None}

        # Moto 3 and MotoGP
        cat1, silo1 = italian_epg_service._resolve_category_and_silo(meta, "GP Qatar: Moto 3")
        self.assertEqual(cat1, "motori")
        self.assertEqual(silo1, "motor-sports")

        cat2, silo2 = italian_epg_service._resolve_category_and_silo(meta, "GP Spagna: Moto 2")
        self.assertEqual(cat2, "motori")
        self.assertEqual(silo2, "motor-sports")

        cat3, silo3 = italian_epg_service._resolve_category_and_silo(meta, "Motociclismo: GP Italia - Gara")
        self.assertEqual(cat3, "motori")
        self.assertEqual(silo3, "motor-sports")

        # WEC and Endurance
        cat4, silo4 = italian_epg_service._resolve_category_and_silo(
            meta,
            "Automobilismo: World Championship Endurance 2026 6 ore di monza - Sessione mattutina"
        )
        self.assertEqual(cat4, "motori")
        self.assertEqual(silo4, "motor-sports")

        cat5, silo5 = italian_epg_service._resolve_category_and_silo(meta, "Automobilismo: 24 Ore di Le Mans")
        self.assertEqual(cat5, "motori")
        self.assertEqual(silo5, "motor-sports")

        cat6, silo6 = italian_epg_service._resolve_category_and_silo(meta, "WEC: 6 Ore di Spa")
        self.assertEqual(cat6, "motori")
        self.assertEqual(silo6, "motor-sports")

        # Formula 1
        cat7, silo7 = italian_epg_service._resolve_category_and_silo(meta, "GP Bahrain: Formula 1")
        self.assertEqual(cat7, "motori")
        self.assertEqual(silo7, "motor-sports")

    def test_genre_classification_motorsport_events(self):
        cases = [
            ("GP Qatar: Moto 3", "motori", "MotoGP e Superbike"),
            ("GP Spagna: Moto 2", "motori", "MotoGP e Superbike"),
            ("Motociclismo: GP Italia - Gara", "motori", "MotoGP e Superbike"),
            ("Automobilismo: World Championship Endurance 2026 6 ore di monza - Sessione mattutina", "motori", "NASCAR e IndyCar"),
            ("Automobilismo: 24 Ore di Le Mans", "motori", "NASCAR e IndyCar"),
            ("WEC: 6 Ore di Spa", "motori", "NASCAR e IndyCar"),
            ("GP Bahrain: Formula 1", "motori", "Formula 1"),
            ("Rally di Montecarlo: WRC - Prova Speciale", "motori", "Rally e WRC"),
        ]

        for title, exp_cat, exp_genre in cases:
            # Test directly with motor-sports silo
            m = {"title": title, "category": exp_cat, "_silo": "motor-sports"}
            c_cat, c_genre = genre_classifier.classify(m)
            self.assertEqual(c_cat, exp_cat, f"Expected {exp_cat} for '{title}', got {c_cat}")
            self.assertEqual(c_genre, exp_genre, f"Expected {exp_genre} for '{title}', got {c_genre}")

            # Test recovery even if category was generic altri_sport
            m_generic = {"title": title, "category": "altri_sport", "_silo": "altri_sport"}
            c_cat2, c_genre2 = genre_classifier.classify(m_generic)
            self.assertEqual(c_cat2, exp_cat, f"Generic fallback expected {exp_cat} for '{title}', got {c_cat2}")
            self.assertEqual(c_genre2, exp_genre, f"Generic fallback expected {exp_genre} for '{title}', got {c_genre2}")

    def test_canonical_competition_motorsport_matching(self):
        slug1 = detect_canonical_competition("GP Qatar: Moto 3", silo_hint="motor-sports")
        self.assertEqual(slug1, "motorsport_motogp")

        slug2 = detect_canonical_competition("World Championship Endurance 6 Ore di Monza", silo_hint="motor-sports")
        self.assertEqual(slug2, "motorsport_wec")

        slug3 = detect_canonical_competition("Formula 1 GP Bahrain", silo_hint="motor-sports")
        self.assertEqual(slug3, "motorsport_f1")

    def test_tier_matching_motorsports(self):
        # Formula 1
        admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "FORMULA 1: GP Messico (Messico) - Prove Libere 2"})
        self.assertTrue(admitted)
        self.assertEqual(slug, "motorsport_f1")

        # MotoGP, Moto2, Moto3
        admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTOGP: Sepang (Malesia) - Qualifiche 1"})
        self.assertTrue(admitted)
        self.assertEqual(slug, "motorsport_motogp")

        admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTO2: Sepang (Malesia) - Prove Libere 3"})
        self.assertTrue(admitted)
        self.assertEqual(slug, "motorsport_motogp")

        admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTO3: Sepang (Malesia) - Gara"})
        self.assertTrue(admitted)
        self.assertEqual(slug, "motorsport_motogp")

        # NASCAR
        admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "NASCAR CUP SERIES: Xfinity 500 (USA) - Qualifiche"})
        self.assertTrue(admitted)
        self.assertEqual(slug, "motorsport_nascar")

    def test_team_matching_motorsport_session(self):
        match = {
            "title": "FORMULA 1: GP Messico (Messico) - Prove Libere 3",
            "competition": "GP Messico",
            "_silo": "motor-sports",
            "date": 1730419200000,
        }
        candidate = {
            "title": "Mexico City GP - Practice 3 (Formula 1)",
            "category": "motor-sports",
            "date": 1730419200000,
        }
        res = calculate_match_score(match, candidate, phase="phase1_diretta")
        self.assertTrue(res["matched"])
        self.assertGreaterEqual(res["score"], 0.75)

    def test_team_matching_motorsport_session_conflict(self):
        # Qualifying must not match Practice
        match = {
            "title": "FORMULA 1: GP Messico (Messico) - Qualifiche 1",
            "competition": "GP Messico",
            "_silo": "motor-sports",
            "date": 1730419200000,
        }
        candidate = {
            "title": "Mexico City GP - Practice 1 (Formula 1)",
            "category": "motor-sports",
            "date": 1730419200000,
        }
        res = calculate_match_score(match, candidate, phase="phase1_diretta")
        self.assertFalse(res["matched"])

    def test_parse_flashscore_feed_motorsports(self):
        feed_sample = (
            "~ZA÷FORMULA 1: GP Messico (Messico) - Prove Libere 2¬ZAE÷Prove Libere 2¬ZY÷MESSICO¬"
            "~AA÷test_f1_session¬AD÷1730419200¬AE÷Leclerc C.¬"
            "~AA÷driver_2¬AD÷1730419200¬AE÷Verstappen M.¬"
        )
        matches = diretta_service.parse_flashscore_feed(feed_sample, "Formula 1", "motor-sports")
        self.assertEqual(len(matches), 1)
        m = matches[0]
        self.assertEqual(m["id"], "diretta-test_f1_session")
        self.assertEqual(m["title"], "FORMULA 1: GP Messico (Messico) - Prove Libere 2")
        self.assertEqual(m["session"], "Prove Libere 2")
        self.assertEqual(m["_silo"], "motor-sports")
        self.assertEqual(m["date"], 1730419200000)

    def test_epg_poster_isolation_to_motorsports(self):
        match_1v1 = {
            "id": "test_1v1",
            "title": "Inter vs Juventus",
            "_silo": "football",
            "category": "football",
            "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
            "date": 1730419200000,
            "poster": "https://thesportsdb.com/images/inter_juve.jpg",
        }
        epg_fake_1v1 = {
            "title": "Serie A: Inter vs Juventus",
            "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
            "_silo": "football",
            "category": "football",
            "date": 1730419200000,
            "poster": "https://guidatv.sky.it/uuid/sky_inter_juve_poster.jpg",
            "sources": [{"name": "Sky Sport Uno", "id": "sky sport uno"}],
        }

        match_motor = {
            "id": "test_motor",
            "title": "FORMULA 1: GP Messico - Prove Libere 3",
            "competition": "GP Messico",
            "_silo": "motor-sports",
            "category": "motor-sports",
            "date": 1730419200000,
            "poster": "https://thesportsdb.com/images/default_fallback.jpg",
        }
        epg_fake_motor = {
            "title": "GP Messico: Prove libere 3",
            "_silo": "motor-sports",
            "category": "motori",
            "date": 1730419200000,
            "poster": "https://guidatv.sky.it/uuid/sky_f1_mexico_poster.jpg",
            "sources": [{"name": "Sky Sport F1", "id": "sky sport f1"}],
        }

        async def _run():
            async def mock_get_matches(*args, **kwargs):
                return [epg_fake_1v1, epg_fake_motor]

            orig = italian_epg_service.get_matches
            italian_epg_service.get_matches = mock_get_matches
            try:
                await italian_epg_service.enrich_matches_with_epg_channels([match_1v1, match_motor])
                self.assertEqual(match_1v1["poster"], "https://thesportsdb.com/images/inter_juve.jpg")
                self.assertNotEqual(match_1v1.get("_poster_source"), "sky")
                self.assertEqual(match_motor["poster"], "https://guidatv.sky.it/uuid/sky_f1_mexico_poster.jpg")
                self.assertEqual(match_motor.get("_poster_source"), "sky")
            finally:
                italian_epg_service.get_matches = orig

        asyncio.run(_run())

    def test_epg_synopsis_and_channel_canonical_detection(self):
        # Sky Sport Uno with F1 in synopsis
        c1 = detect_canonical_competition(
            "Sky Sport Uno : GP Las Vegas : GP Las Vegas: Prove libere 1 : ... 1 F1. - Inizia il weekend in Nevada",
            silo_hint="motor-sports"
        )
        self.assertEqual(c1, "motorsport_f1")

        # Sky Sport Uno with Moto3 in synopsis
        c2 = detect_canonical_competition(
            "Sky Sport Uno : GP Portogallo : GP Portogallo: Prove libere 1 : ... libere 1 Moto3. - Ventunesimo appuntamento",
            silo_hint="motor-sports"
        )
        self.assertEqual(c2, "motorsport_motogp")

        # Sky Sport MotoGP with Pre Qualifiche
        c3 = detect_canonical_competition(
            "Sky Sport MotoGP : GP Portogallo : GP Portogallo: Pre Qualifiche : ... MotoGP.",
            silo_hint="motor-sports"
        )
        self.assertEqual(c3, "motorsport_motogp")

    def test_figure_skating_not_hijacked_by_motorsport_gp(self):
        # Event with GP in title should not be hijacked into motor-sports
        m = {
            "title": "Pattinaggio di Figura - GP Skate America 2026: Programma libero Uomini",
            "category": "altri_sport",
            "_silo": "winter-sports",
        }
        c_cat, c_genre = genre_classifier.classify(m)
        self.assertEqual(c_cat, "altri_sport")
        self.assertEqual(c_genre, "Sport Invernali")

    def test_epg_motorsport_session_merge_with_5min_diff(self):
        from app.services.catalog_service import catalog_service
        off_vegas = {
            "id": "off_vegas_fp1",
            "title": "Formula 1: Las Vegas Grand Prix - FP1",
            "competition": "Formula 1",
            "canonical_competition": "motorsport_f1",
            "date": 1795134600000,  # 00:30 UTC
            "_silo": "motor-sports",
            "category": "motor-sports",
            "sources": [{"id": "dlhd_1", "name": "Sky Sports F1 UK"}]
        }
        ep_vegas = {
            "id": "sky_9097_gp-las-vegas-prove-libere-1_1795134300",
            "title": "GP Las Vegas: Prove libere 1",
            "competition": "GP Las Vegas",
            "canonical_competition": "motorsport_f1",
            "date": 1795134300000,  # 00:25 UTC (5 min earlier)
            "_silo": "motor-sports",
            "category": "motori",
            "eventSynopsis": "... 1 F1. - Inizia il weekend in Nevada...",
            "sources": [{"id": "tvvoo_sky_uno", "name": "Sky Sport Uno"}]
        }

        schedule = [off_vegas]
        catalog_service._merge_epg_certified_sessions(schedule, [ep_vegas])

        # Must merge into single card and inherit Sky source
        self.assertEqual(len(schedule), 1)
        src_names = [s.get("name") for s in schedule[0].get("sources", [])]
        self.assertIn("Sky Sport Uno", src_names)
        self.assertIn("Sky Sports F1 UK", src_names)

    def test_distinct_motorsport_sessions_not_flattened_by_ocblacktop(self):
        from app.services.ocblacktop_service import ocblacktop_service
        matches = [
            {"id": "d_q1", "title": "FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 1", "competition": "GP Stati Uniti - Las Vegas", "date": 1795230000000, "_silo": "motor-sports"},
            {"id": "d_q2", "title": "FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 2", "competition": "GP Stati Uniti - Las Vegas", "date": 1795231500000, "_silo": "motor-sports"},
            {"id": "d_q3", "title": "FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 3", "competition": "GP Stati Uniti - Las Vegas", "date": 1795233000000, "_silo": "motor-sports"},
            {"id": "d_q", "title": "FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche", "competition": "GP Stati Uniti - Las Vegas", "date": 1795230000000, "_silo": "motor-sports"},
        ]
        ocb_sessions = [
            {
                "series": "formula1",
                "event_name": "Las Vegas Grand Prix",
                "session_name": "Qualifying",
                "circuit": "Las Vegas Strip Circuit",
                "city": "Las Vegas",
                "country": "USA",
                "date_ms": 1795230000000,
                "catalog": "motori",
                "genre": "Formula 1",
            }
        ]
        matched = ocblacktop_service.reconcile_matches(matches, ocb_sessions)
        self.assertGreaterEqual(matched, 1)

        # Titles must remain strictly distinct and must NOT be overwritten into identical titles
        titles = [m["title"] for m in matches]
        self.assertIn("FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 1", titles)
        self.assertIn("FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 2", titles)
        self.assertIn("FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche 3", titles)
        self.assertIn("FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche", titles)
        self.assertEqual(len(set(titles)), 4)

        # Dates must NOT be collapsed to the same timestamp if already distinct
        self.assertEqual(matches[0]["date"], 1795230000000)
        self.assertEqual(matches[1]["date"], 1795231500000)
        self.assertEqual(matches[2]["date"], 1795233000000)

        # Metadata should still be enriched
        for m in matches:
            self.assertTrue(m.get("_ocb_matched"))
            self.assertEqual(m.get("_genre"), "Formula 1")

    def test_moto3_and_f1_strict_isolation(self):
        # 1. Team matching: Moto 3 Algarve vs F1 Las Vegas Practice 2
        m_moto3 = {
            "title": "Moto 3: Algarve (Portogallo) - Prove libere 1",
            "competition": "GP Portogallo",
            "_silo": "motor-sports",
            "date": 1795179300000,
        }
        c_f1 = {
            "title": "GP Las Vegas: Prove libere 2 (Formula 1)",
            "competition": "GP Las Vegas",
            "category": "motor-sports",
            "date": 1795179300000,
        }
        res = calculate_match_score(m_moto3, c_f1, phase="phase1_diretta")
        self.assertFalse(res["matched"])

        # 2. Italian EPG bridge: Moto 3 Algarve must never match Sky F1 Las Vegas
        m_card = {
            "id": "diretta_moto3_algarve",
            "title": "Moto 3: Algarve (Portogallo) - Prove libere 1",
            "competition": "GP Portogallo",
            "_silo": "motor-sports",
            "date": 1795179300000,
            "poster": "https://default.com/poster.jpg",
        }
        epg_f1 = {
            "id": "sky_9096_vegas_fp2",
            "title": "GP Las Vegas: Prove libere 2",
            "competition": "GP Las Vegas",
            "_silo": "motor-sports",
            "date": 1795179300000,
            "poster": "https://guidatv.sky.it/f1_vegas.jpg",
            "sources": [{"name": "Sky Sport F1", "id": "sky_f1"}],
        }

        async def _test():
            async def mock_get():
                return [epg_f1]
            orig = italian_epg_service.get_matches
            italian_epg_service.get_matches = mock_get
            try:
                await italian_epg_service.enrich_matches_with_epg_channels([m_card])
                # Poster and matched ID must NOT be assigned to Moto3 card
                self.assertIsNone(m_card.get("_matched_epg_id"))
                self.assertEqual(m_card["poster"], "https://default.com/poster.jpg")
                self.assertNotEqual(m_card.get("_poster_source"), "sky")
            finally:
                italian_epg_service.get_matches = orig

        asyncio.run(_test())

    def test_sky_uno_f1_session_classification_and_merge(self):
        from app.services.catalog_service import catalog_service

        ep_sky_uno_qual = {
            "id": "sky_9097_gp-las-vegas-qualifiche_1795233300",
            "title": "GP Las Vegas: Qualifiche",
            "competition": "GP Las Vegas",
            "eventSynopsis": "E' sfida per la pole e per scattare piu' avanti possibile in griglia di partenza. Q1, Q2 e Q3 con l'adrenalina che sale. Telecronaca di Carlo Vanzini, Marc Gene' e Roberto Chinchero.",
            "sources": [{"name": "Sky Sport Uno", "id": "sky_uno"}],
            "_silo": "motor-sports",
            "category": "motori",
            "date": 1795233300000,
        }

        # 1. Canonical competition must detect Formula 1 from synopsis/channel
        canon = detect_canonical_competition(
            f"{ep_sky_uno_qual['sources'][0]['name']} : {ep_sky_uno_qual['competition']} : {ep_sky_uno_qual['title']} : {ep_sky_uno_qual['eventSynopsis']}",
            silo_hint="motor-sports"
        )
        self.assertEqual(canon, "motorsport_f1")

        # 2. Genre classification must be Formula 1, NOT NASCAR e IndyCar
        ep_sky_uno_qual["canonical_competition"] = canon
        cat, genre = genre_classifier.classify(ep_sky_uno_qual)
        self.assertEqual(cat, "motori")
        self.assertEqual(genre, "Formula 1")

        # 3. Even without canonical_competition set, genre classifier must still classify into Formula 1
        ep_sky_uno_qual_no_canon = dict(ep_sky_uno_qual)
        ep_sky_uno_qual_no_canon["canonical_competition"] = None
        cat2, genre2 = genre_classifier.classify(ep_sky_uno_qual_no_canon)
        self.assertEqual(cat2, "motori")
        self.assertEqual(genre2, "Formula 1")

        # 4. Merging with official schedule: must merge and not orphan-duplicate
        off_f1_qual = {
            "id": "diretta_f1_vegas_qual",
            "title": "FORMULA 1: GP Stati Uniti - Las Vegas (USA) - Qualifiche",
            "competition": "GP Stati Uniti - Las Vegas",
            "canonical_competition": "motorsport_f1",
            "date": 1795233300000,
            "_silo": "motor-sports",
            "category": "motor-sports",
            "sources": [{"id": "stream_1", "name": "Stream 1"}],
        }
        schedule = [off_f1_qual]
        catalog_service._merge_epg_certified_sessions(schedule, [ep_sky_uno_qual])
        self.assertEqual(len(schedule), 1)
        src_ids = [s.get("id") for s in schedule[0].get("sources", [])]
        self.assertIn("sky_uno", src_ids)


if __name__ == "__main__":
    unittest.main()
