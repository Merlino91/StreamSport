import unittest
from app.services.genre_classifier import genre_classifier
from app.services.fctv33_service import fctv33_service


class TestSoccerClassificationAndFallback(unittest.TestCase):
    """
    Tests the inversion of the soccer fallback to Extra-EU,
    strict gating of 'Altri Campionati Europei', routing of youth/college,
    and upstream FCTV33 anti-junk discarding.
    """

    def test_european_peripheral_leagues(self):
        """San Marino, Cyprus, and Malta leagues must route to 'Altri Campionati Europei'."""
        cases = [
            {"title": "Omonia Nicosia vs APOEL", "competition": "Cypriot Cup", "country": "Cyprus"},
            {"title": "Tre Fiori vs La Fiorita", "competition": "Campionato Sammarinese", "country": "San Marino"},
            {"title": "Hamrun Spartans vs Floriana", "competition": "Maltese Premier League", "country": "Malta"},
            {"title": "FC Porto vs Benfica", "competition": "Primeira Liga", "country": "Portugal"},
            {"title": "Ajax vs Feyenoord", "competition": "Eredivisie", "country": "Netherlands"},
            {"title": "Galatasaray vs Fenerbahce", "competition": "Süper Lig", "country": "Turkey"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Altri Campionati Europei", f"Failed for {c['title']}")

    def test_extra_eu_fallback_and_lower_divisions(self):
        """Non-European leagues (J2, Betplay) and unknown leagues must fall back to 'Americhe e Leghe Extra-UE'."""
        cases = [
            {"title": "Yamagata vs Sapporo", "competition": "Japanese J2 League", "country": "Japan"},
            {"title": "Real Cartagena vs Bogota", "competition": "Torneo BetPlay Dimayor", "country": "Colombia"},
            {"title": "Seoul E-Land vs Busan", "competition": "K League 2", "country": "South Korea"},
            {"title": "Random FC vs Unknown Club", "competition": "Unknown Minor League", "country": ""},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Americhe e Leghe Extra-UE", f"Failed for {c['title']}")

    def test_youth_and_college_routing(self):
        """Youth tournaments (U17, U19, U20, U21) and College Soccer must route to 'Europei Under 21 e Nazionali Giovanili'."""
        cases = [
            {"title": "Grand Canyon vs Abilene Christian", "competition": "College Soccer", "country": "USA"},
            {"title": "Corea del Nord U17 D vs Polonia U17 D", "competition": "World Cup Women U17", "country": "International"},
            {"title": "Spagna U19 vs Francia U19", "competition": "Euro U19", "country": "Europe"},
            {"title": "Brasile U20 vs Argentina U20", "competition": "Sub-20", "country": "South America"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Europei Under 21 e Nazionali Giovanili", f"Failed for {c['title']}")

    def test_two_gate_premier_league_and_extra_eu_homonyms(self):
        """
        Homonym 'Premier League' competitions from outside the UK must never enter 'Premier League'.
        Non-European ones fall back to Extra-EU, Maltese to European, and genuine English to Premier League.
        """
        extra_eu_cases = [
            {"title": "Al-Quwa Al-Jawiya vs Al-Shorta (Iraqi Premier League)", "category": "football"},
            {"title": "Al Ahed vs Nejmeh (Lebanese Premier League)", "category": "football"},
            {"title": "Al Ahly vs Zamalek", "competition": "Egyptian Premier League", "country": "Egypt", "category": "football"},
            {"title": "Forge FC vs Cavalry FC", "competition": "Canadian Premier League", "country": "Canada", "category": "football"},
            {"title": "Al Kuwait vs Al Qadsia", "competition": "Kuwait Premier League", "country": "Kuwait", "category": "football"},
        ]
        for c in extra_eu_cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Americhe e Leghe Extra-UE", f"Failed for {c['title']}")

        epl_cases = [
            {"title": "Arsenal vs Chelsea (Premier League)", "category": "football"},
            {"title": "Aston Villa vs Wolves", "competition": "Premier League", "country": "England", "category": "football"},
            {"title": "Manchester United vs Liverpool", "category": "football"},
            {"title": "Manchester City vs Newcastle", "competition": "FA Cup", "country": "UK", "category": "football"},
            {"title": "Tottenham vs Brentford", "competition": "Carabao Cup", "country": "Inghilterra", "category": "football"},
        ]
        for c in epl_cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Premier League", f"Failed for {c['title']}")

    def test_lower_division_european_rerouting(self):
        """
        Discards from English, Spanish, German, and French two-gate filters
        must be cleanly rerouted to 'Altri Campionati Europei', never to Extra-EU.
        """
        cases = [
            {"title": "Leeds United vs Sunderland", "competition": "Championship", "country": "England", "category": "football"},
            {"title": "Bolton Wanderers vs Reading", "competition": "League One", "country": "England", "category": "football"},
            {"title": "Bradford City vs Notts County", "competition": "League Two", "country": "England", "category": "football"},
            {"title": "Sporting Gijon vs Real Oviedo", "competition": "Segunda Division", "country": "Spain", "category": "football"},
            {"title": "Levante vs Real Zaragoza", "competition": "LaLiga Hypermotion", "country": "Spain", "category": "football"},
            {"title": "Hamburg vs Schalke 04", "competition": "2. Bundesliga", "country": "Germany", "category": "football"},
            {"title": "Bordeaux vs Saint-Etienne", "competition": "Ligue 2", "country": "France", "category": "football"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero")
            self.assertEqual(genre, "Altri Campionati Europei", f"Failed for {c['title']}")

    def test_fctv33_upstream_anti_junk(self):
        """College soccer, NCAA, J2/J3, K League 2, Torneo Betplay, and foreign U17 must be dropped at source by FCTV33."""
        junk_events = [
            {"title": "Abilene Christian vs Grand Canyon", "_silo": "football", "competition": "College Soccer NCAA", "country": "USA"},
            {"title": "Yamagata vs Sapporo", "_silo": "football", "competition": "Japanese J2 League", "country": "Japan"},
            {"title": "Seoul E-Land vs Busan", "_silo": "football", "competition": "K League 2", "country": "South Korea"},
            {"title": "Real Cartagena vs Bogota", "_silo": "football", "competition": "Torneo BetPlay Dimayor", "country": "Colombia"},
            {"title": "USA U17 Women vs Samoa U17 Women", "_silo": "football", "competition": "U17 Women's World Cup", "country": "International"},
        ]
        for ev in junk_events:
            self.assertTrue(fctv33_service.is_junk_event(ev), f"Expected junk for {ev['title']}")

    def test_foreign_youth_routing_isolation(self):
        """Foreign youth matches (e.g. English U21) must route to Europei Under 21 and NEVER hijack into Italian Primavera."""
        cases = [
            {"title": "Bolton U21 vs QPR U21", "competition": "Premier League Cup U21", "country": "England", "category": "football"},
            {"title": "Bournemouth U21 vs Cardiff U21", "competition": "Professional Development League", "country": "England", "category": "football"},
            {"title": "Chelsea U21 vs Arsenal U21", "canonical_competition": "football_premier_league", "competition": "Premier League 2", "country": "Inghilterra", "category": "football"},
            {"title": "Leeds U21 vs Watford U21", "canonical_competition": "football_championship", "competition": "U21 League", "country": "England", "category": "football"},
            {"title": "Valencia U19 vs Villarreal U19", "competition": "Division de Honor Juvenil", "country": "Spain", "category": "football"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_estero", f"Expected calcio_estero for {c['title']}, got {cat}")
            self.assertEqual(genre, "Europei Under 21 e Nazionali Giovanili", f"Failed for {c['title']}")

    def test_serie_c_reserve_teams_not_hijacked_to_youth(self):
        """Serie C matches with reserve teams (Atalanta U23, Juventus Next Gen, Milan Futuro) must remain in Serie C."""
        cases = [
            {"title": "Atalanta U23 vs Renate", "canonical_competition": "football_serie_c", "competition": "Serie C - Girone A", "country": "Italia", "category": "football"},
            {"title": "Juventus Next Gen vs Pro Vercelli", "canonical_competition": "football_serie_c", "competition": "Serie C - Girone A", "country": "Italy", "category": "football"},
            {"title": "Milan Futuro vs Lucchese", "canonical_competition": "football_serie_c", "competition": "Serie C - Girone B", "country": "Italia", "category": "football"},
            # Level 2 fallback (without canonical competition)
            {"title": "Atalanta U23 vs Renate", "competition": "Serie C", "country": "Italia", "category": "football"},
            {"title": "Juventus Next Gen vs Novara", "competition": "Serie C - Girone A", "country": "Italy", "category": "football"},
            {"title": "Milan Futuro vs Carpi", "competition": "Serie C - Girone B", "country": "Italia", "category": "football"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_italiano", f"Expected calcio_italiano for {c['title']}, got {cat}")
            self.assertEqual(genre, "Serie C", f"Failed for {c['title']}")

    def test_italian_youth_primavera_routing(self):
        """Italian youth matches must route to Primavera e Giovanili."""
        cases = [
            {"title": "Inter Primavera vs Milan Primavera", "canonical_competition": "football_primavera", "competition": "Primavera 1", "country": "Italia", "category": "football"},
            {"title": "Roma U19 vs Fiorentina U19", "competition": "Campionato Primavera 1", "country": "Italia", "category": "football"},
            {"title": "Juventus Primavera vs Lazio Primavera", "competition": "Primavera 1", "country": "Italia", "category": "football"},
        ]
        for c in cases:
            cat, genre = genre_classifier.classify(c)
            self.assertEqual(cat, "calcio_italiano", f"Expected calcio_italiano for {c['title']}, got {cat}")
            self.assertEqual(genre, "Primavera e Giovanili", f"Failed for {c['title']}")

    def test_canonical_competition_additions(self):
        """Tests the 6 canonical competitions completed in canon_map."""
        cases = [
            ({"title": "Athletic Bilbao vs Barcelona", "canonical_competition": "football_copa_del_rey", "category": "football"}, ("calcio_estero", "La Liga")),
            ({"title": "Bayern Munich vs Dortmund", "canonical_competition": "football_dfb_pokal", "category": "football"}, ("calcio_estero", "Bundesliga e Ligue 1")),
            ({"title": "Arsenal vs Chelsea", "canonical_competition": "football_fa_cup", "category": "football"}, ("calcio_estero", "Premier League")),
            ({"title": "Winnipeg Blue Bombers vs BC Lions", "canonical_competition": "am_football_cfl", "category": "american-football"}, ("football_americano", "CFL e Altre Leghe")),
            ({"title": "Birmingham Stallions vs Michigan Panthers", "canonical_competition": "am_football_ufl", "category": "american-football"}, ("football_americano", "CFL e Altre Leghe")),
            ({"title": "6 Hours of Spa-Francorchamps", "canonical_competition": "motorsport_wec", "category": "motor-sports"}, ("motori", "NASCAR e IndyCar")),
        ]
        for ev, (expected_cat, expected_genre) in cases:
            cat, genre = genre_classifier.classify(ev)
            self.assertEqual(cat, expected_cat, f"Catalog mismatch for {ev['canonical_competition']}")
            self.assertEqual(genre, expected_genre, f"Genre mismatch for {ev['canonical_competition']}")


if __name__ == "__main__":
    unittest.main()
