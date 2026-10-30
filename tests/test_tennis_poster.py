import os
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.tennis_poster_service import (
    tennis_poster_service,
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    POSTERS_DIR,
    ATHLETES_DIR,
    ASSETS_DIR,
    STATIC_ASSETS_DIR,
)


class TennisPosterTestCase(unittest.IsolatedAsyncioTestCase):

    def test_parse_players_single(self):
        title = "🇨🇳 Zhizhen Zhang vs 🇭🇰 Coleman Wong (ATP - Singles)"
        p1, p2 = tennis_poster_service.parse_players(title)
        self.assertEqual(p1, "Zhizhen Zhang")
        self.assertEqual(p2, "Coleman Wong")

    def test_parse_players_flags_and_brackets(self):
        title = "🇬🇷 Maria Sakkari vs 🇯🇵 Nao Hibino (WTA - Singles)"
        p1, p2 = tennis_poster_service.parse_players(title)
        self.assertEqual(p1, "Maria Sakkari")
        self.assertEqual(p2, "Nao Hibino")

    def test_parse_players_doubles(self):
        title = "🇳🇿 Erin Routliffe / 🇮🇩 Aldila Sutjiadi vs 🇬🇧 Joanna Garland / 🇹🇼 Su-Wei Hsieh (WTA - Doubles)"
        p1, p2 = tennis_poster_service.parse_players(title)
        self.assertEqual(p1, "Erin Routliffe / Aldila Sutjiadi")
        self.assertEqual(p2, "Joanna Garland / Su-Wei Hsieh")

    def test_format_short_display_name(self):
        self.assertEqual(tennis_poster_service.format_short_display_name("Jannik Sinner"), "J. SINNER")
        self.assertEqual(tennis_poster_service.format_short_display_name("Carlos Alcaraz"), "C. ALCARAZ")
        self.assertEqual(tennis_poster_service.format_short_display_name("Nao Hibino"), "N. HIBINO")
        self.assertEqual(
            tennis_poster_service.format_short_display_name("Erin Routliffe / Aldila Sutjiadi"),
            "ROUTLIFFE / SUTJIADI",
        )
        # Doubles with trailing initials (e.g. from Diretta) must show full surnames, not initials!
        self.assertEqual(
            tennis_poster_service.format_short_display_name("Bolelli S./Vavassori A."),
            "BOLELLI / VAVASSORI",
        )
        self.assertEqual(
            tennis_poster_service.format_short_display_name("Frantzen C./Haase R."),
            "FRANTZEN / HAASE",
        )
        self.assertEqual(
            tennis_poster_service.format_short_display_name("Errani S./Paolini J."),
            "ERRANI / PAOLINI",
        )
        self.assertEqual(
            tennis_poster_service.format_short_display_name("Cerundolo F./Etcheverry T. M."),
            "CERUNDOLO / ETCHEVERRY",
        )

    def test_theme_palette(self):
        atp = tennis_poster_service.get_theme_palette("ATP", "Sinner vs Alcaraz")
        self.assertEqual(atp["badge_text"], "ATP TOUR")

        wta = tennis_poster_service.get_theme_palette("WTA", "Sakkari vs Hibino (WTA)")
        self.assertEqual(wta["badge_text"], "WTA TOUR")

        wim = tennis_poster_service.get_theme_palette("Grandi Slam", "Djokovic vs Alcaraz (Wimbledon Final)")
        self.assertEqual(wim["badge_text"], "WIMBLEDON")

        davis = tennis_poster_service.get_theme_palette("Coppa Davis e BJK Cup", "Italy vs China (Billie Jean King Cup)")
        self.assertEqual(davis["badge_text"], "DAVIS CUP")

    async def test_generate_poster_creation(self):
        match_id = "test_tennis_match_unit"
        title = "Jannik Sinner vs Carlos Alcaraz"
        genre = "ATP"

        rel_path = await tennis_poster_service.generate_poster(match_id, title, genre)
        self.assertIsNotNone(rel_path)
        self.assertTrue(rel_path.startswith("/posters/"))

        # Verify physical file
        filename = rel_path.replace("/posters/", "")
        file_path = POSTERS_DIR / filename
        self.assertTrue(file_path.exists())

        # Verify image properties
        with Image.open(file_path) as img:
            self.assertEqual(img.size, (CANVAS_WIDTH, CANVAS_HEIGHT))
            self.assertEqual(img.format, "JPEG")
            self.assertGreater(file_path.stat().st_size, 10000)  # > 10 KB

    def test_stage_placeholder_detection(self):
        title = "(Couples) 1/4 Final 1 (WTA 500 Singapore)"
        stage_info = tennis_poster_service.is_stage_placeholder(title)
        self.assertIsNotNone(stage_info)
        self.assertIn("QUARTER FINAL", stage_info["stage"])

        title_normal = "Jannik Sinner vs Carlos Alcaraz"
        self.assertIsNone(tennis_poster_service.is_stage_placeholder(title_normal))

        # ATP & WTA broadcast feeds should be detected as stage placeholder cards, not athletes
        atp_wta_info = tennis_poster_service.is_stage_placeholder("ATP & WTA")
        self.assertIsNotNone(atp_wta_info)
        self.assertEqual(atp_wta_info["stage"], "LIVE BROADCAST")

    def test_parse_team_players_excludes_circuits(self):
        """Tests that tournament circuits (ATP, WTA, ITF) are never parsed as athletes in doubles."""
        self.assertEqual(tennis_poster_service.parse_team_players("ATP & WTA"), [])
        self.assertEqual(tennis_poster_service.parse_team_players("Simone Bolelli / Andrea Vavassori"), ["Simone Bolelli", "Andrea Vavassori"])

    def test_country_match_detection(self):
        self.assertTrue(tennis_poster_service.is_country_match("Italy vs China", "Italy", "China", "Coppa Davis e BJK Cup"))
        self.assertTrue(tennis_poster_service.is_country_match("Ukraine vs Belgium (Billie Jean King Cup)", "Ukraine", "Belgium", "WTA"))
        self.assertFalse(tennis_poster_service.is_country_match("Sinner vs Alcaraz", "Sinner", "Alcaraz", "ATP"))

    async def test_generate_stage_poster(self):
        match_id = "test_stage_card_unit"
        title = "(Couples) 1/4 Final 1 (WTA 500 Singapore)"
        genre = "WTA"
        rel_path = await tennis_poster_service.generate_poster(match_id, title, genre)
        self.assertIsNotNone(rel_path)
        self.assertTrue((POSTERS_DIR / rel_path.replace("/posters/", "")).exists())

    async def test_generate_country_poster(self):
        match_id = "test_country_tie_unit"
        title = "Italy vs China (Billie Jean King Cup)"
        genre = "Coppa Davis e BJK Cup"
        rel_path = await tennis_poster_service.generate_poster(match_id, title, genre)
        self.assertIsNotNone(rel_path)
        self.assertTrue((POSTERS_DIR / rel_path.replace("/posters/", "")).exists())

    def test_parse_players_hyphen_stage_guard(self):
        # Hyphen split should NOT occur if tournament or stage tokens are present
        p1, p2 = tennis_poster_service.parse_players("ATP Tokyo - Finals")
        self.assertEqual(p2, "")
        self.assertIn("ATP Tokyo - Finals", p1)

        p1_r, p2_r = tennis_poster_service.parse_players("WTA Beijing - Round 1")
        self.assertEqual(p2_r, "")

    def test_parse_team_players(self):
        team = tennis_poster_service.parse_team_players("Simone Bolelli / Andrea Vavassori")
        self.assertEqual(team, ["Simone Bolelli", "Andrea Vavassori"])

        single = tennis_poster_service.parse_team_players("Jannik Sinner")
        self.assertEqual(single, ["Jannik Sinner"])

    def test_unknown_cutout_loaded(self):
        cutout = tennis_poster_service._get_unknown_cutout()
        self.assertIsNotNone(cutout)
        self.assertEqual(cutout.mode, "RGBA")

    async def test_generate_doubles_poster_with_unknown(self):
        match_id = "test_doubles_match_unit"
        title = "Simone Bolelli / Andrea Vavassori vs Unknown Partner A / Unknown Partner B (ATP - Doubles)"
        genre = "ATP"
        rel_path = await tennis_poster_service.generate_poster(match_id, title, genre)
        self.assertIsNotNone(rel_path)
        file_path = POSTERS_DIR / rel_path.replace("/posters/", "")
        self.assertTrue(file_path.exists())

    def test_cleanup_finished_events(self):
        # Create a mock athlete file that is not part of any active match
        mock_file = ATHLETES_DIR / "obsolete_player_12345.png"
        mock_file.touch(exist_ok=True)
        self.assertTrue(mock_file.exists())

        # Run cleanup with empty active matches
        tennis_poster_service.cleanup_finished_events([])

        # Verify obsolete player was deleted and Unknown.png still exists
        self.assertFalse(mock_file.exists())
        self.assertTrue((ASSETS_DIR / "Unknown.png").exists() or (STATIC_ASSETS_DIR / "Unknown.png").exists())

    def test_is_female_competition(self):
        """Verifies female competition detection for WTA, women's tournaments, and BJK cup."""
        self.assertTrue(tennis_poster_service.is_female_competition("WTA 1000 Wuhan", "tennis"))
        self.assertTrue(tennis_poster_service.is_female_competition("Ann Li vs Janice Tjen (WTA - Singles)", "WTA"))
        self.assertTrue(tennis_poster_service.is_female_competition("Lanlana Tararudee vs Laura Samson (Challenger Women - Singles)", "Challenger"))
        self.assertTrue(tennis_poster_service.is_female_competition("Italy vs China", "Billie Jean King Cup"))
        self.assertFalse(tennis_poster_service.is_female_competition("ATP Masters 1000 Shanghai", "tennis"))
        self.assertFalse(tennis_poster_service.is_female_competition("Zverev vs De Minaur", "ATP"))
        self.assertFalse(tennis_poster_service.is_female_competition("Challenger Men - Singles: Olbia", "Challenger"))

    def test_gender_unknown_cutouts(self):
        """Verifies distinct male and female cutout assets are returned."""
        male_cutout = tennis_poster_service._get_unknown_cutout(is_female=False)
        female_cutout = tennis_poster_service._get_unknown_cutout(is_female=True)
        self.assertIsNotNone(male_cutout)
        self.assertIsNotNone(female_cutout)

    def test_multicourt_session_detection_and_poster(self):
        """Verifies that multi-court / broadcast sessions are properly routed to stage cards without fake players."""
        multicourt_titles = [
            "Tennis: ATP 250 Almaty & WTA 500 Ningbo & WTA 250 Osaka",
            "Tennis: ATP 250 Brussels & ATP 250 Almaty & WTA 500 Ningbo",
            "WTA 500 Ningbo & WTA 250 Osaka (Tennis)",
            "WTA 250 Osaka",
            "ATP 250 Almaty",
            "Brussels-ATP, Lyon-ATP, Almaty-ATP, Osaka-WTA & Ningbo-WTA Early Rounds",
        ]
        for t in multicourt_titles:
            info = tennis_poster_service.is_stage_placeholder(t)
            self.assertIsNotNone(info, f"Expected stage/session card for: {t}")
            self.assertIn("tournament", info)
            self.assertIn("stage", info)

        real_matches = [
            "Terence Atmane vs Rinky Hijikata (ATP - Singles)",
            "Jasmine Paolini vs Anhelina Kalinina",
            "Simone Bolelli / Andrea Vavassori vs Jean-Julien Rojer / Tyler Winegar",
        ]
        for t in real_matches:
            info = tennis_poster_service.is_stage_placeholder(t)
            self.assertIsNone(info, f"Expected regular match (None) for: {t}")


if __name__ == "__main__":
    unittest.main()
