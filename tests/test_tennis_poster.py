import os
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.tennis_poster_service import tennis_poster_service, CANVAS_WIDTH, CANVAS_HEIGHT, POSTERS_DIR


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


if __name__ == "__main__":
    unittest.main()
