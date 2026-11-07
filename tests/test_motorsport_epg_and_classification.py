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


if __name__ == "__main__":
    unittest.main()
