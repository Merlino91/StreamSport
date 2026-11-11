import unittest

from app.services.canonical_competition_service import (
    CANONICAL_LEAGUES,
    detect_canonical_competition,
    get_canonical_display_name,
    is_competition_conflict,
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)
from app.services.catalog_service import CatalogService
from app.services.genre_classifier import genre_classifier


class TestCanonicalDeduplication(unittest.TestCase):

    def setUp(self):
        self.cs = CatalogService()

    def test_canonical_competition_detection(self):
        # Soccer
        self.assertEqual(detect_canonical_competition("Italy - Serie A : Inter vs Juventus", "football"), "football_serie_a")
        self.assertEqual(detect_canonical_competition("Spain - La Liga : Real Madrid vs Barcelona", "football"), "football_la_liga")
        self.assertEqual(detect_canonical_competition("sabah-fk-vs-slavia-prague-uefa-champions-league-1791909900", "football"), "football_uefa_cl")
        self.assertEqual(detect_canonical_competition("England - Premier League : Arsenal vs Chelsea", "football"), "football_premier_league")

        # Basketball
        self.assertEqual(detect_canonical_competition("NBA: Los Angeles Lakers vs Golden State Warriors", "basketball"), "basketball_nba")
        self.assertEqual(detect_canonical_competition("live_nba-preseason_trail-blazers-london-lions", "basketball"), "basketball_nba")

        # Tennis
        self.assertEqual(detect_canonical_competition("ATP - Singles: Shanghai Sinner vs Alcaraz", "tennis"), "tennis_atp")
        self.assertEqual(detect_canonical_competition("WTA - Singles: Wuhan Swiatek vs Sabalenka", "tennis"), "tennis_wta")

        # Motori
        self.assertEqual(detect_canonical_competition("Formula 1 Pirelli Gran Premio d'Italia", "motor-sports"), "motorsport_f1")
        self.assertEqual(detect_canonical_competition("Grand Prix of Japan (MotoGP)", "motor-sports"), "motorsport_motogp")

    def test_redzone_handling(self):
        # Detection
        self.assertTrue(is_redzone_event("NFL RedZone"))
        self.assertTrue(is_redzone_event("NFL: Red Zone Live"))
        self.assertFalse(is_redzone_event("Miami Dolphins vs Buffalo Bills"))

        # Split title should never treat RedZone as a 'vs' match
        clean_title, comp = self.cs.split_title_and_competition("NFL: RedZone Live")
        self.assertEqual(clean_title, "NFL RedZone")
        self.assertEqual(comp, "NFL")

        cat, genre = genre_classifier.classify({"title": "NFL RedZone", "_silo": "american-football"})
        self.assertEqual(cat, "football_americano")
        self.assertEqual(genre, "NFL")

    def test_simulcast_handling(self):
        # 1. Detection
        self.assertTrue(is_simulcast_event("The Golazo Show"))
        self.assertTrue(is_simulcast_event("Simulcast (UEFA Champions League)"))
        self.assertTrue(is_simulcast_event("Diretta Gol Serie A"))
        self.assertTrue(is_simulcast_event("Zona Serie A"))
        self.assertTrue(is_simulcast_event("Bundesliga Konferenz"))
        self.assertTrue(is_simulcast_event("Diretta Basket"))
        self.assertFalse(is_simulcast_event("Inter vs Milan"))
        self.assertFalse(is_simulcast_event("Real Madrid vs Chelsea"))

        # 2. Split title & competition (no fake 'vs' teams)
        t1, c1 = self.cs.split_title_and_competition("The Golazo Show")
        self.assertEqual(t1, "The Golazo Show")
        self.assertEqual(c1, "UEFA Champions League")

        t2, c2 = self.cs.split_title_and_competition("Diretta Gol Serie A")
        self.assertEqual(t2, "Diretta Gol Serie A")
        self.assertEqual(c2, "Serie A")

        t3, c3 = self.cs.split_title_and_competition("Bundesliga Konferenz")
        self.assertEqual(t3, "Bundesliga Konferenz")
        self.assertEqual(c3, "Bundesliga")

        # 3. Genre classification
        cat_g, genre_g = genre_classifier.classify({"title": "The Golazo Show", "_silo": "football"})
        self.assertEqual(cat_g, "calcio_estero")
        self.assertEqual(genre_g, "Champions League")

        cat_d, genre_d = genre_classifier.classify({"title": "Diretta Gol Serie A", "_silo": "football"})
        self.assertEqual(cat_d, "calcio_italiano")
        self.assertEqual(genre_d, "Serie A")

        cat_b, genre_b = genre_classifier.classify({"title": "Bundesliga Konferenz", "_silo": "football"})
        self.assertEqual(cat_b, "calcio_estero")
        self.assertEqual(genre_b, "Bundesliga e Ligue 1")

        # 4. Top-Down isolation: A simulcast show and a 1v1 match MUST NEVER link
        now_ms = 1792000000000
        sim_match = {
            "id": "dlhd-the-golazo-show-179200",
            "title": "The Golazo Show",
            "date": now_ms,
            "_silo": "football",
            "sources": [{"id": "s1", "name": "Paramount+"}]
        }
        single_match = {
            "id": "dlhd-inter-vs-barcelona-179200",
            "title": "Inter vs Barcelona",
            "date": now_ms,
            "_silo": "football",
            "sources": [{"id": "s2", "name": "Sky Sport Uno"}]
        }
        linked_isolated = self.cs.link_official_schedule_to_providers([single_match], {"prov": [sim_match]})
        self.assertEqual(len(linked_isolated[0]["sources"]), 1, "Simulcast show must NEVER link onto a 1v1 match!")

        # 5. Canonical metadata lookup for simulcast shows
        from app.services.canonical_competition_service import get_simulcast_info
        sim_info = get_simulcast_info("The Golazo Show")
        self.assertIsNotNone(sim_info)
        self.assertEqual(sim_info["canonical"], "football_uefa_cl")

    def test_competition_conflict(self):
        # Direct conflicts in same silo
        self.assertTrue(is_competition_conflict("football_serie_a", "football_serie_b"))
        self.assertTrue(is_competition_conflict("tennis_atp", "tennis_wta"))
        self.assertTrue(is_competition_conflict("basketball_nba", "basketball_euroleague"))

        # Matching is NOT a conflict
        self.assertFalse(is_competition_conflict("football_serie_a", "football_serie_a"))

        # Neutral if either is None
        self.assertFalse(is_competition_conflict("football_serie_a", None))
        self.assertFalse(is_competition_conflict(None, "basketball_nba"))

    def test_weighted_deduplication_merge(self):
        # 1. Matching competition links cleanly
        primary = [{
            "id": "diretta-inter-juve",
            "title": "FC Internazionale vs Juventus",
            "home": "FC Internazionale",
            "away": "Juventus",
            "date": 1791838800000,
            "_silo": "football",
            "category": "football",
            "canonical_competition": "football_serie_a",
            "sources": [{"source": "streamed", "id": "100"}],
        }]
        secondary = [{
            "id": "dlhd-inter-juve",
            "title": "Inter vs Juve",
            "date": 1791838800000,
            "_silo": "football",
            "category": "football",
            "canonical_competition": "football_serie_a",
            "sources": [{"source": "dlhd", "id": "200"}],
        }]
        linked = self.cs.link_official_schedule_to_providers(primary, {"dlhd": secondary})
        self.assertEqual(len(linked), 1)
        self.assertEqual(len(linked[0]["sources"]), 2)

        # 2. Conflicting youth/competition trait prevents false link on senior match
        sec_conflicting = [{
            "id": "dlhd-inter-juve-primavera",
            "title": "Inter Primavera vs Juventus Primavera",
            "date": 1791838800000 + 20 * 60 * 1000,
            "_silo": "football",
            "category": "football",
            "canonical_competition": "football_primavera",
            "sources": [{"source": "dlhd", "id": "300"}],
        }]
        linked_conflict = self.cs.link_official_schedule_to_providers(primary, {"dlhd": sec_conflicting})
        self.assertEqual(len(linked_conflict[0]["sources"]), 1, "Conflicting youth leagues must not link onto senior match!")

        # 3. Time window: within 45 min links, > 45 min does not
        sec_time_ok = [{
            "id": "dlhd-inter-juve-time-ok",
            "title": "FC Internazionale vs Juventus",
            "date": 1791838800000 + 20 * 60 * 1000,
            "_silo": "football",
            "category": "football",
            "sources": [{"source": "dlhd", "id": "400"}],
        }]
        linked_time_ok = self.cs.link_official_schedule_to_providers(primary, {"dlhd": sec_time_ok}, time_window_minutes=45.0)
        self.assertEqual(len(linked_time_ok[0]["sources"]), 2, "20-min offset with matching teams should still link!")

        sec_time_fail = [{
            "id": "dlhd-inter-juve-time-fail",
            "title": "FC Internazionale vs Juventus",
            "date": 1791838800000 + 50 * 60 * 1000,
            "_silo": "football",
            "category": "football",
            "sources": [{"source": "dlhd", "id": "500"}],
        }]
        linked_time_fail = self.cs.link_official_schedule_to_providers(primary, {"dlhd": sec_time_fail}, time_window_minutes=45.0)
        self.assertEqual(len(linked_time_fail[0]["sources"]), 1, "Outside time window must never link!")

    def test_latin_american_serie_leagues_classification(self):
        # 1. Brazil Serie B detection & classification
        canon_brazil_b = detect_canonical_competition("⚽ Brazil - Serie B : Athletic Club 🇧🇷 vs Londrina 🇧🇷", "football")
        self.assertEqual(canon_brazil_b, "football_brazil_serie_b")
        cat_id, genre = genre_classifier.classify({
            "title": "Athletic Club 🇧🇷 vs Londrina 🇧🇷",
            "category": "football",
            "canonical_competition": canon_brazil_b,
        })
        self.assertEqual(cat_id, "calcio_estero")
        self.assertEqual(genre, "Americhe e Leghe Extra-UE")

        # 2. Brazil Serie A detection & classification
        canon_brazil_a = detect_canonical_competition("Brazil - Serie A : Flamengo vs Palmeiras", "football")
        self.assertEqual(canon_brazil_a, "football_brasileirao")
        cat_id, genre = genre_classifier.classify({
            "title": "Flamengo vs Palmeiras",
            "category": "football",
            "canonical_competition": canon_brazil_a,
        })
        self.assertEqual(cat_id, "calcio_estero")
        self.assertEqual(genre, "Americhe e Leghe Extra-UE")

        # 3. Italian Serie B detection & classification
        canon_ita_b = detect_canonical_competition("Italy - Serie B : Palermo vs Spezia", "football")
        self.assertEqual(canon_ita_b, "football_serie_b")
        cat_id, genre = genre_classifier.classify({
            "title": "Palermo vs Spezia",
            "category": "football",
            "canonical_competition": canon_ita_b,
        })
        self.assertEqual(cat_id, "calcio_italiano")
        self.assertEqual(genre, "Serie B")

        # 4. Fallback classification with Brazil flag without canonical competition
        cat_id, genre = genre_classifier.classify({
            "title": "Athletic Club 🇧🇷 vs Londrina 🇧🇷",
            "category": "football",
        })
        self.assertEqual(cat_id, "calcio_estero")
        self.assertEqual(genre, "Americhe e Leghe Extra-UE")

    def test_basket_category_classification(self):
        # Category 'basket' should not fall through to Calcio
        cat_id, genre = genre_classifier.classify({
            "title": "BC Roma SPQR vs BC Balkan Botevgrad",
            "category": "basket",
        })
        self.assertEqual(cat_id, "basket")


if __name__ == "__main__":
    unittest.main()
