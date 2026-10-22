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


if __name__ == "__main__":
    unittest.main()
