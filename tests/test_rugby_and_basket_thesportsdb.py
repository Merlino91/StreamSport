import unittest
import time
from app.services.canonical_competition_service import (
    is_tier_match,
    detect_canonical_competition,
)
from app.services.genre_classifier import genre_classifier
from app.services.team_matching import match_single_team, calculate_match_score
from app.services.thesportsdb_service import thesportsdb_service


class TestRugbyAndBasketTheSportsDB(unittest.TestCase):

    def test_italian_rugby_tier_match_and_classification(self):
        """Verify Italian Rugby (Serie A Elite, Coppa Italia) is admitted and routed to 'Rugby e AFL'."""
        m_serie_a_elite = {
            "title": "Petrarca Padova vs Rovigo",
            "_silo": "rugby",
            "category": "rugby",
            "country": "italia",
            "league": "serie a elite",
            "sport": "rugby",
        }
        admitted, slug = is_tier_match(m_serie_a_elite)
        self.assertTrue(admitted)
        self.assertEqual(slug, "rugby_serie_a_elite")

        m_serie_a_elite["canonical_competition"] = slug
        cat, genre = genre_classifier.classify(m_serie_a_elite)
        self.assertEqual(cat, "altri_sport")
        self.assertEqual(genre, "Rugby e AFL")

        # Coppa Italia Rugby
        m_coppa = {
            "title": "Colorno vs Fiamme Oro",
            "_silo": "rugby",
            "category": "rugby",
            "country": "italy",
            "league": "coppa italia",
            "sport": "rugby",
        }
        admitted_c, slug_c = is_tier_match(m_coppa)
        self.assertTrue(admitted_c)
        self.assertEqual(slug_c, "rugby_serie_a_elite")

    def test_international_rugby_tier_match(self):
        """Verify international rugby competitions (URC, Sei Nazioni, Top 14, Champions Cup)."""
        # URC (Benetton / Zebre)
        m_urc = {
            "title": "Benetton Treviso vs Leinster",
            "_silo": "rugby",
            "category": "rugby",
            "country": "europa",
            "league": "united rugby championship",
            "sport": "rugby",
        }
        admitted, slug = is_tier_match(m_urc)
        self.assertTrue(admitted)
        self.assertEqual(slug, "rugby_top_14")

        # Six Nations
        m_6n = {
            "title": "Italia vs Inghilterra",
            "_silo": "rugby",
            "category": "rugby",
            "country": "europa",
            "league": "sei nazioni",
            "sport": "rugby",
        }
        admitted_6n, slug_6n = is_tier_match(m_6n)
        self.assertTrue(admitted_6n)
        self.assertEqual(slug_6n, "rugby_six_nations")

        # Uncertified amateur rugby excluded
        m_noise = {
            "title": "Amatori Catania vs Benevento",
            "_silo": "rugby",
            "category": "rugby",
            "country": "italia",
            "league": "serie b rugby",
            "sport": "rugby",
        }
        admitted_noise, _ = is_tier_match(m_noise)
        self.assertFalse(admitted_noise)

    def test_basket_tenerife_canarias_and_thesportsdb_reconciliation(self):
        """
        Verify that Tenerife vs Canarias and other basketball cross-namings
        match accurately in TheSportsDB phase 3.
        """
        now_ms = int(time.time() * 1000)

        # 1. Tenerife vs Venezia (Diretta naming) vs Canarias vs Reyer Venezia Mestre (TheSportsDB naming)
        match_diretta = {
            "title": "Tenerife vs Venezia",
            "teams": {"home": "Tenerife", "away": "Venezia"},
            "category": "basketball",
            "_silo": "basketball",
            "date": now_ms,
        }
        cal_tsdb = {
            "title": "Canarias vs Reyer Venezia Mestre",
            "home": "Canarias",
            "away": "Reyer Venezia Mestre",
            "category": "basketball",
            "_silo": "basketball",
            "date_ms": now_ms,
        }

        res = calculate_match_score(match_diretta, cal_tsdb, phase="phase3_tsdb")
        self.assertTrue(res["matched"], f"Tenerife vs Canarias failed: {res}")
        self.assertGreaterEqual(res["score"], 0.85)

        # 2. Tortona vs Derthona
        self.assertGreaterEqual(match_single_team("Tortona", "Bertram Derthona Tortona"), 0.80)
        self.assertGreaterEqual(match_single_team("Derthona", "Bertram Derthona Tortona"), 0.80)

        # 3. Badalona vs Joventut
        self.assertGreaterEqual(match_single_team("Badalona", "Joventut"), 0.95)
        self.assertGreaterEqual(match_single_team("Joventut Badalona", "Joventut"), 0.95)

    def test_multi_sport_without_hardcoded_sponsors(self):
        """
        Verifies that team matching succeeds naturally for teams with corporate title sponsors
        without requiring hardcoded sponsor aliases in the codebase.
        """
        # Basketball
        self.assertGreaterEqual(match_single_team("EA7 Emporio Armani Milano", "Olimpia Milano"), 0.80)
        self.assertGreaterEqual(match_single_team("Virtus Segafredo Bologna", "Virtus Bologna"), 0.80)
        self.assertGreaterEqual(match_single_team("Germani Brescia", "Brescia"), 0.80)
        self.assertGreaterEqual(match_single_team("Banco di Sardegna Sassari", "Dinamo Sassari"), 0.80)
        self.assertGreaterEqual(match_single_team("Dolomiti Energia Trento", "Trento"), 0.80)
        self.assertGreaterEqual(match_single_team("Dreamland Gran Canaria", "Gran Canaria"), 0.80)
        self.assertGreaterEqual(match_single_team("UCAM Murcia", "Murcia"), 0.80)

        # Volleyball
        self.assertGreaterEqual(match_single_team("Sir Susa Vim Perugia", "Perugia"), 0.80)
        self.assertGreaterEqual(match_single_team("Itas Trentino", "Trentino"), 0.80)
        self.assertGreaterEqual(match_single_team("Cucine Lube Civitanova", "Lube Civitanova"), 0.80)
        self.assertGreaterEqual(match_single_team("Valsa Group Modena", "Modena"), 0.80)
        self.assertGreaterEqual(match_single_team("Savino Del Bene Scandicci", "Scandicci"), 0.80)
        self.assertGreaterEqual(match_single_team("Prosecco Doc Imoco Conegliano", "Imoco Conegliano"), 0.80)
        self.assertGreaterEqual(match_single_team("Gas Sales Bluenergy Piacenza", "Piacenza"), 0.80)
        self.assertGreaterEqual(match_single_team("Rana Verona", "Verona"), 0.80)
        self.assertGreaterEqual(match_single_team("Sonepar Padova", "Padova"), 0.80)

    def test_tsdb_aliases_dead_code_removed(self):
        """Verify the bloated TSDB_ALIASES dictionary has been completely removed from TheSportsDBService."""
        self.assertFalse(hasattr(thesportsdb_service, "TSDB_ALIASES"))
