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


if __name__ == "__main__":
    unittest.main()
