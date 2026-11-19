import unittest
import time
from app.services.canonical_competition_service import (
    is_tier_match,
    detect_canonical_competition,
)
from app.services.genre_classifier import genre_classifier
from app.services.team_matching import match_single_team
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

        # 1. World senior official tournament admitted
        m_nations_champ = {
            "title": "Inghilterra vs Nuova Zelanda",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Mondo",
            "league": "Nations Championship",
            "alt_country": "World",
            "alt_league": "Nations Championship",
            "sport": "rugby",
        }
        admitted_nc, slug_nc = is_tier_match(m_nations_champ)
        self.assertTrue(admitted_nc)
        self.assertEqual(slug_nc, "rugby_six_nations")

        m_wrnc = {
            "title": "Georgia vs USA",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Mondo",
            "league": "World Rugby Nations Cup",
            "alt_country": "World",
            "alt_league": "World Rugby Nations Cup",
            "sport": "rugby",
        }
        admitted_wrnc, slug_wrnc = is_tier_match(m_wrnc)
        self.assertTrue(admitted_wrnc)
        self.assertEqual(slug_wrnc, "rugby_six_nations")

        # 2. Friendlies and test matches excluded
        m_friendly = {
            "title": "Perù vs Isole Cayman",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Mondo",
            "league": "Amichevoli Internazionali",
            "alt_country": "World",
            "alt_league": "Friendly International",
            "sport": "rugby",
        }
        admitted_f, _ = is_tier_match(m_friendly)
        self.assertFalse(admitted_f)

        # 3. Women competitions strictly excluded
        m_women_feed = {
            "title": "Francia D vs Argentina D",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Mondo",
            "league": "Amichevoli Internazionali - Donne",
            "alt_country": "World",
            "alt_league": "Friendly International Women",
            "sport": "rugby",
        }
        admitted_wf, _ = is_tier_match(m_women_feed)
        self.assertFalse(admitted_wf)

        m_six_nations_women = {
            "title": "Inghilterra D vs Francia D",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Europa",
            "league": "Sei Nazioni Femminile",
            "alt_country": "Europe",
            "alt_league": "Women's Six Nations",
            "sport": "rugby",
        }
        admitted_snw, _ = is_tier_match(m_six_nations_women)
        self.assertFalse(admitted_snw)

        # 4. Youth competitions strictly excluded
        m_six_nations_u20 = {
            "title": "Italia U20 vs Galles U20",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Europa",
            "league": "Sei Nazioni Under 20",
            "alt_country": "Europe",
            "alt_league": "Six Nations Under 20",
            "sport": "rugby",
        }
        admitted_u20, _ = is_tier_match(m_six_nations_u20)
        self.assertFalse(admitted_u20)

        # 5. Domestic foreign leagues excluded (Inghilterra, Galles, Francia, etc.)
        m_prem_cup = {
            "title": "Bristol vs Bath",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Inghilterra",
            "league": "Premiership Rugby Cup",
            "alt_country": "England",
            "alt_league": "Premiership Rugby Cup",
            "sport": "rugby",
        }
        admitted_pc, _ = is_tier_match(m_prem_cup)
        self.assertFalse(admitted_pc)

        m_wales_prem = {
            "title": "Bargoed vs Llangennech",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Galles",
            "league": "Premiership",
            "alt_country": "Wales",
            "alt_league": "Premiership",
            "sport": "rugby",
        }
        admitted_wp, _ = is_tier_match(m_wales_prem)
        self.assertFalse(admitted_wp)

        m_france_prod2 = {
            "title": "Grenoble vs Pays d'Aix",
            "_silo": "rugby",
            "category": "rugby",
            "country": "Francia",
            "league": "Pro D2",
            "alt_country": "France",
            "alt_league": "Pro D2",
            "sport": "rugby",
        }
        admitted_pd2, _ = is_tier_match(m_france_prod2)
        self.assertFalse(admitted_pd2)

    def test_basket_tenerife_canarias_and_thesportsdb_reconciliation(self):
        """
        Verify that Tenerife vs Canarias and other basketball cross-namings
        match accurately in TheSportsDB team matching.
        """
        # 1. Tenerife vs Canarias (TheSportsDB) and Venezia vs Reyer Venezia Mestre (TheSportsDB)
        self.assertTrue(thesportsdb_service._team_matches("Tenerife", "Canarias"))
        self.assertTrue(thesportsdb_service._team_matches("Venezia", "Reyer Venezia Mestre"))
        self.assertGreaterEqual(match_single_team("Tenerife", "Canarias"), 0.85)
        self.assertGreaterEqual(match_single_team("Venezia", "Reyer Venezia Mestre"), 0.85)

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
