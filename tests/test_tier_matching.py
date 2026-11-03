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

        # Diretta Lega A
        ok, slug = is_tier_match({"_silo": "basketball", "country": "Italia", "league": "Lega A"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_lba_serie_a")

        # Serie A2 Maschile
        ok, slug = is_tier_match({"_silo": "basketball", "country": "Italia", "league": "Serie A2"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_serie_a2")

        # Serie A1 Femminile Basket
        ok, slug = is_tier_match({"_silo": "basketball", "country": "Italia", "league": "Serie A1 Femminile"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_serie_a1_femm")

        # Serie A2 Femminile Basket
        ok, slug = is_tier_match({"_silo": "basketball", "country": "Italia", "league": "Serie A2 Femminile - Gr. A"})
        self.assertTrue(ok)
        self.assertEqual(slug, "basketball_serie_a2_femm")

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

from app.services.genre_classifier import genre_classifier

class TestBasketGenreClassification(unittest.TestCase):
    def test_basket_routing(self):
        # 1. Lega A
        cat, sub = genre_classifier.classify({
            "silo": "basketball",
            "country": "Italia",
            "league": "Lega A",
            "title": "Virtus Bologna - Milano"
        })
        self.assertEqual(cat, "basket")
        self.assertEqual(sub, "LBA Serie A")

        # 2. Serie A2 Maschile
        cat, sub = genre_classifier.classify({
            "silo": "basketball",
            "country": "Italia",
            "league": "Serie A2",
            "title": "Fortitudo Bologna - Cantù"
        })
        self.assertEqual(cat, "basket")
        self.assertEqual(sub, "LBA Serie A")

        # 3. Serie A1 Femminile
        cat, sub = genre_classifier.classify({
            "silo": "basketball",
            "country": "Italia",
            "league": "Serie A1 Femminile",
            "title": "Schio D - Venezia D"
        })
        self.assertEqual(cat, "basket")
        self.assertEqual(sub, "WNBA e Femminile")

        # 4. Serie A2 Femminile
        cat, sub = genre_classifier.classify({
            "silo": "basketball",
            "country": "Italia",
            "league": "Serie A2 Femminile - Gr. A",
            "title": "Sanga Milano D - Broni D"
        })
        self.assertEqual(cat, "basket")
        self.assertEqual(sub, "WNBA e Femminile")


class TestArchitectureAndSimulcastFixes(unittest.TestCase):
    def test_basket_simulcast_routing(self):
        from app.services.canonical_competition_service import get_simulcast_info

        # NBA Simulcast
        nba_sim = get_simulcast_info("NBA Simulcast", "basketball")
        self.assertIsNotNone(nba_sim)
        self.assertEqual(nba_sim["canonical"], "basketball_nba")
        self.assertEqual(nba_sim["genre"], "NBA")
        self.assertEqual(nba_sim["catalog"], "basket")

        # NCAA Basket Simulcast
        ncaa_sim = get_simulcast_info("NCAA Basket Simulcast", "basketball")
        self.assertIsNotNone(ncaa_sim)
        self.assertEqual(ncaa_sim["canonical"], "basketball_ncaa")
        self.assertEqual(ncaa_sim["genre"], "NCAA e College Basket")
        self.assertEqual(ncaa_sim["catalog"], "basket")

        # Euroleague Simulcast
        el_sim = get_simulcast_info("Diretta Eurolega", "basketball")
        self.assertIsNotNone(el_sim)
        self.assertEqual(el_sim["canonical"], "basketball_euroleague")
        self.assertEqual(el_sim["genre"], "Eurolega ed Eurocup")
        self.assertEqual(el_sim["catalog"], "basket")

        # LBA Diretta Basket
        lba_sim = get_simulcast_info("Diretta Basket", "basketball")
        self.assertIsNotNone(lba_sim)
        self.assertEqual(lba_sim["canonical"], "basketball_lba_serie_a")
        self.assertEqual(lba_sim["genre"], "LBA Serie A")
        self.assertEqual(lba_sim["catalog"], "basket")

    def test_centralized_canonical_catalog_and_genre(self):
        from app.services.canonical_competition_service import get_canonical_catalog_and_genre

        # Serie A
        cat, genre = get_canonical_catalog_and_genre("football_serie_a")
        self.assertEqual(cat, "calcio_italiano")
        self.assertEqual(genre, "Serie A")

        # NBA
        cat, genre = get_canonical_catalog_and_genre("basketball_nba")
        self.assertEqual(cat, "basket")
        self.assertEqual(genre, "NBA")

        # Formula 1
        cat, genre = get_canonical_catalog_and_genre("motorsport_f1")
        self.assertEqual(cat, "motori")
        self.assertEqual(genre, "Formula 1")

    def test_u23_serie_c_pro_second_teams(self):
        # 1. Juventus Next Gen in Serie C (even without explicit Serie C in competition)
        cat, genre = genre_classifier.classify({
            "title": "Juventus Next Gen vs SPAL",
            "country": "Italia",
            "league": "Lega Pro",
        })
        self.assertEqual(cat, "calcio_italiano")
        self.assertEqual(genre, "Serie C")

        # 2. Milan Futuro
        cat, genre = genre_classifier.classify({
            "title": "Milan Futuro vs Carrarese",
            "country": "Italia",
            "league": "",
        })
        self.assertEqual(cat, "calcio_italiano")
        self.assertEqual(genre, "Serie C")

        # 3. Atalanta U23
        cat, genre = genre_classifier.classify({
            "title": "Atalanta U23 vs Triestina",
            "country": "Italia",
            "league": "",
        })
        self.assertEqual(cat, "calcio_italiano")
        self.assertEqual(genre, "Serie C")

        # 4. Explicit Primavera match must still be Primavera e Giovanili
        cat, genre = genre_classifier.classify({
            "title": "Juventus Primavera vs Milan Primavera",
            "country": "Italia",
            "league": "Campionato Primavera 1",
        })
        self.assertEqual(cat, "calcio_italiano")
        self.assertEqual(genre, "Primavera e Giovanili")
