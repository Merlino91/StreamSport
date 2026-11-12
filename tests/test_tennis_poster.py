from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.canonical_competition_service import detect_canonical_competition, is_tier_match
from app.services.diretta_service import DirettaService
from app.services.genre_classifier import genre_classifier
from app.services.tennis_poster_service import (
    tennis_poster_service,
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    POSTERS_DIR,
    ATHLETES_DIR,
    FLAGS_DIR,
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
        self.assertEqual(atp["bg_file"], "bg_atp.jpg")

        wta = tennis_poster_service.get_theme_palette("WTA", "Sakkari vs Hibino (WTA)")
        self.assertEqual(wta["badge_text"], "WTA TOUR")
        self.assertEqual(wta["bg_file"], "bg_wta.jpg")

        # Explicit circuit in title must override any inherited/noisy genre
        atp_title_override = tennis_poster_service.get_theme_palette("WTA", "ATP 1000 Parigi Bercy 2025: Jannik Sinner vs Felix Auger-Aliassime")
        self.assertEqual(atp_title_override["badge_text"], "ATP TOUR")
        self.assertEqual(atp_title_override["bg_file"], "bg_atp.jpg")

        wta_title_override = tennis_poster_service.get_theme_palette("ATP", "WTA 500 Tokyo 2026")
        self.assertEqual(wta_title_override["badge_text"], "WTA TOUR")
        self.assertEqual(wta_title_override["bg_file"], "bg_wta.jpg")

        wim = tennis_poster_service.get_theme_palette("Grandi Slam", "Djokovic vs Alcaraz (Wimbledon Final)")
        self.assertEqual(wim["badge_text"], "WIMBLEDON")

        bjk = tennis_poster_service.get_theme_palette("Coppa Davis e BJK Cup", "Italy vs China (Billie Jean King Cup)")
        self.assertEqual(bjk["badge_text"], "BJK CUP")
        self.assertEqual(bjk["bg_file"], "bg_bjk_cup.jpg")

        davis = tennis_poster_service.get_theme_palette("Coppa Davis e BJK Cup", "Emirati arabi uniti vs Yemen (Davis Cup)")
        self.assertEqual(davis["badge_text"], "DAVIS CUP")
        self.assertEqual(davis["bg_file"], "bg_davis_cup.jpg")

        # "United Arab Emirates" must NOT false-trigger bg_united_cup.jpg
        davis_uae = tennis_poster_service.get_theme_palette(
            "Coppa Davis e BJK Cup",
            "United Arab Emirates vs Yemen",
            match={"competition": "SQUADRE - UOMINI: Davis Cup - Group V"},
        )
        self.assertEqual(davis_uae["badge_text"], "DAVIS CUP")
        self.assertEqual(davis_uae["bg_file"], "bg_davis_cup.jpg")

        ucup = tennis_poster_service.get_theme_palette("Coppa Davis e BJK Cup", "Italy vs Spain (United Cup)")
        self.assertEqual(ucup["badge_text"], "UNITED CUP")
        self.assertEqual(ucup["bg_file"], "bg_united_cup.jpg")

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
        self.addCleanup(file_path.unlink, missing_ok=True)
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
        file_path = POSTERS_DIR / rel_path.replace("/posters/", "")
        self.addCleanup(file_path.unlink, missing_ok=True)
        self.assertTrue(file_path.exists())

    async def test_generate_country_poster(self):
        match_id = "test_country_tie_unit"
        title = "Italy vs China (Billie Jean King Cup)"
        genre = "Coppa Davis e BJK Cup"
        rel_path = await tennis_poster_service.generate_poster(match_id, title, genre)
        self.assertIsNotNone(rel_path)
        file_path = POSTERS_DIR / rel_path.replace("/posters/", "")
        self.addCleanup(file_path.unlink, missing_ok=True)
        self.assertTrue(file_path.exists())

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
        self.addCleanup(file_path.unlink, missing_ok=True)
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

    def test_diretta_reconstruct_skips_team_ties(self):
        """Ensures national-team tennis ties keep clean Italianized titles without English slug prepending."""
        ds = DirettaService()
        raw_feed = (
            "~ZA÷SQUADRE - UOMINI: Davis Cup - Group V (Mondo)¬"
            "~AA÷tie123¬AD÷1794535200¬AE÷Emirati arabi uniti¬WU÷united-arab-emirates"
            "¬AF÷Tagikistan¬WV÷tajikistan¬AB÷1¬"
            "~ZA÷WTA - SINGOLARE: Finals¬"
            "~AA÷plr456¬AD÷1794535200¬AE÷Paolini J.¬WU÷paolini-jasmine"
            "¬AF÷Gauff C.¬WV÷gauff-coco¬AB÷1¬"
        )
        events = ds.parse_flashscore_feed(raw_feed, "Tennis", "tennis")
        by_id = {e["id"]: e for e in events}
        self.assertEqual(by_id["diretta-tie123"]["title"], "Emirati arabi uniti vs Tagikistan")
        self.assertEqual(by_id["diretta-plr456"]["title"], "Jasmine Paolini vs Coco Gauff")

    def test_genre_classifier_and_canonical_team_cups(self):
        """Ensures BJK Cup and United Cup stay in 'Coppa Davis e BJK Cup' and match canonical patterns."""
        self.assertEqual(detect_canonical_competition("United Cup Perth", silo_hint="tennis"), "tennis_davis_bjk")
        self.assertEqual(detect_canonical_competition("Hopman Cup Nice", silo_hint="tennis"), "tennis_davis_bjk")
        self.assertEqual(is_tier_match({"_silo": "tennis", "competition": "United Cup"})[1], "tennis_davis_bjk")

        cat_bjk, gen_bjk = genre_classifier.classify(
            {"title": "Laos vs Bhutan", "competition": "SQUADRE - DONNE: Billie Jean King Cup", "sport": "Tennis", "is_women": True}
        )
        self.assertEqual((cat_bjk, gen_bjk), ("tennis", "Coppa Davis e BJK Cup"))

        cat_uc, gen_uc = genre_classifier.classify(
            {"title": "Italia vs Spagna", "competition": "United Cup", "sport": "Tennis", "is_women": True}
        )
        self.assertEqual((cat_uc, gen_uc), ("tennis", "Coppa Davis e BJK Cup"))

    def test_country_tie_vs_individual_rubber_detection(self):
        """Individual Davis/BJK rubbers must still render player cutouts; nation ties must render flags."""
        self.assertTrue(tennis_poster_service.is_country_match("Laos vs Bhutan", "Laos", "Bhutan", "WTA"))
        self.assertTrue(tennis_poster_service.is_country_match("Repubblica Ceca vs Gran Bretagna", "Repubblica Ceca", "Gran Bretagna", "Coppa Davis e BJK Cup"))
        self.assertTrue(tennis_poster_service.is_country_match("Pacific Oceania vs Guam", "Pacific Oceania", "Guam", "Coppa Davis e BJK Cup"))
        # Legacy doubled EN+IT title recovery
        resolved = tennis_poster_service.resolve_country("United Arab Emirates Emirati arabi uniti")
        self.assertTrue(resolved["is_team"])
        self.assertEqual(resolved["abbr"], "UAE")
        self.assertEqual(resolved["display"], "Emirati Arabi Uniti")
        # Singles rubber inside Davis Cup must NOT trigger country boxes
        self.assertFalse(
            tennis_poster_service.is_country_match(
                "Jannik Sinner vs Carlos Alcaraz",
                "Jannik Sinner",
                "Carlos Alcaraz",
                "Coppa Davis e BJK Cup",
                match={"competition": "SQUADRE - UOMINI: Davis Cup"},
            )
        )

    async def test_country_poster_skips_athlete_searches_and_uses_flags(self):
        """Nation ties must render standalone flags (or clean Italian fallback) without any player cutout searches."""
        sample_flag = Image.new("RGBA", (180, 120), (0, 140, 60, 255))

        async def fake_fetch_flag(info):
            if info.get("is_special"):
                return None
            return sample_flag.copy()

        with (
            patch.object(tennis_poster_service, "_ensure_espn_country_index", new=AsyncMock()),
            patch.object(tennis_poster_service, "_fetch_country_flag", side_effect=fake_fetch_flag),
            patch.object(tennis_poster_service, "_search_espn", new=AsyncMock()) as m_espn,
            patch.object(tennis_poster_service, "_search_atp", new=AsyncMock()) as m_atp,
            patch.object(tennis_poster_service, "_search_wta", new=AsyncMock()) as m_wta,
        ):
            rel = await tennis_poster_service.generate_poster(
                "test_davis_pac_gum_flags",
                "Pacific Oceania vs Guam",
                "WTA",
                match={"competition": "SQUADRE - UOMINI: Davis Cup - Group V"},
            )
            self.assertIsNotNone(rel)
            file_path = POSTERS_DIR / rel.replace("/posters/", "")
            self.addCleanup(file_path.unlink, missing_ok=True)
            self.assertTrue(file_path.exists())
            m_espn.assert_not_awaited()
            m_atp.assert_not_awaited()
            m_wta.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
