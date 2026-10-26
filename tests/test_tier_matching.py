import unittest
from app.services.canonical_competition_service import is_tier_match

class TestTierMatching(unittest.TestCase):
    def test_calcio_tier_filtering(self):
        # Admitted Tier 1 & 2
        ok, slug = is_tier_match({"_silo": "football", "country": "Italia", "league": "Serie A"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_serie_a")

        ok, slug = is_tier_match({"_silo": "football", "country": "Italia", "league": "Serie B"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_serie_b")

        ok, slug = is_tier_match({"_silo": "football", "country": "Italia", "league": "Serie C - Girone A"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_serie_c")

        ok, slug = is_tier_match({"_silo": "football", "country": "Italia", "league": "Coppa Italia"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_coppa_italia")

        # Excluded Calcio: Serie D, Eccellenza, Primavera
        ok, _ = is_tier_match({"_silo": "football", "country": "Italia", "league": "Serie D - Girone H"})
        self.assertFalse(ok)

        ok, _ = is_tier_match({"_silo": "football", "country": "Italia", "league": "Eccellenza Campania"})
        self.assertFalse(ok)

        ok, _ = is_tier_match({"_silo": "football", "country": "Italia", "league": "Campionato Primavera 1"})
        self.assertFalse(ok)

        # International Tier 1 & 2
        ok, slug = is_tier_match({"_silo": "football", "country": "Inghilterra", "league": "Premier League"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_premier_league")

        ok, slug = is_tier_match({"_silo": "football", "country": "Spagna", "league": "LaLiga"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_la_liga")

        ok, slug = is_tier_match({"_silo": "football", "country": "Germania", "league": "Bundesliga"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_bundesliga")

        ok, slug = is_tier_match({"_silo": "football", "country": "Europa", "league": "Champions League"})
        self.assertTrue(ok)
        self.assertEqual(slug, "football_uefa_cl")

        # Excluded reserve leagues
        ok, _ = is_tier_match({"_silo": "football", "country": "USA", "league": "MLS Next Pro - Play Off"})
        self.assertFalse(ok)

    def test_tennis_tier_filtering(self):
        # Admitted
        ok, slug = is_tier_match({"_silo": "tennis", "league": "ATP - SINGOLARE: Vienna (Austria), cemento"})
        self.assertTrue(ok)
        self.assertEqual(slug, "tennis_atp")

        ok, slug = is_tier_match({"_silo": "tennis", "league": "WTA - SINGOLARE: Guangzhou (Cina), cemento"})
        self.assertTrue(ok)
        self.assertEqual(slug, "tennis_wta")

        ok, slug = is_tier_match({"_silo": "tennis", "league": "Wimbledon - Singolare Maschile"})
        self.assertTrue(ok)
        self.assertEqual(slug, "tennis_grand_slam")

        # Excluded Challenger & ITF
        ok, _ = is_tier_match({"_silo": "tennis", "league": "CHALLENGER UOMINI - SINGOLARE: Ningbo (Cina)"})
        self.assertFalse(ok)

        ok, _ = is_tier_match({"_silo": "tennis", "league": "ITF DONNE - SINGOLARE: W35 Bakersfield, CA (USA)"})
        self.assertFalse(ok)

    def test_basket_volley_american_football_filtering(self):
        # Basket
        ok, slug = is_tier_match({"_silo": "basketball", "country": "USA", "league": "NBA"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_nba")

        ok, slug = is_tier_match({"_silo": "basketball", "country": "Europa", "league": "Euroleague"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_euroleague")

        ok, slug = is_tier_match({"_silo": "basketball", "country": "Italia", "league": "Serie A (LBA)"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_lba_serie_a")

        # Volley
        ok, slug = is_tier_match({"_silo": "volley", "country": "Italia", "league": "SuperLega"})
        self.assertTrue(ok)
        self.assertEqual(slug, "volleyball_superlega")

        ok, slug = is_tier_match({"_silo": "volley", "country": "Italia", "league": "Serie A1 Femminile"})
        self.assertTrue(ok)
        self.assertEqual(slug, "volleyball_serie_a1_femm")

        # American Football
        ok, slug = is_tier_match({"_silo": "american-football", "country": "USA", "league": "NFL"})
        self.assertTrue(ok)
        self.assertEqual(slug, "am_football_nfl")

        ok, slug = is_tier_match({"_silo": "american-football", "country": "USA", "league": "NCAA Football"})
        self.assertTrue(ok)
        self.assertEqual(slug, "am_football_ncaa")

        # College FCS excluded
        ok, _ = is_tier_match({"_silo": "american-football", "country": "USA", "league": "NCAA FCS Championship"})
        self.assertFalse(ok)

if __name__ == "__main__":
    unittest.main()
