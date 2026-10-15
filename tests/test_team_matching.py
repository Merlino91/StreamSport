import unittest
from app.services.team_matching import (
    clean_team_name,
    compare_teams_fuzzy,
    calculate_match_score,
    match_single_team,
)

class TestTeamMatching(unittest.TestCase):
    def test_alias_normalization(self):
        self.assertEqual(clean_team_name("Stella Rossa"), "crvena zvezda")
        self.assertEqual(clean_team_name("Red Star Belgrade"), "crvena zvezda")
        self.assertEqual(clean_team_name("Stade Rennais FC"), "rennes")
        self.assertEqual(clean_team_name("OFI Crete"), "ofi")
        self.assertEqual(clean_team_name("Los Angeles FC"), "lafc")
        self.assertEqual(clean_team_name("Paris Saint-Germain"), "psg")
        self.assertEqual(clean_team_name("FC Internazionale Milano"), "inter")
        self.assertEqual(clean_team_name("Manchester United"), "man utd")
        self.assertEqual(clean_team_name("Sir Susa Vim Perugia"), "perugia")

    def test_single_team_similarity(self):
        # Exact alias match
        self.assertGreaterEqual(match_single_team("Stella Rossa", "Crvena Zvezda"), 0.95)
        self.assertGreaterEqual(match_single_team("Stade Rennais", "Rennes"), 0.95)
        self.assertGreaterEqual(match_single_team("OFI Crete", "OFI"), 0.95)
        self.assertGreaterEqual(match_single_team("LAFC", "Los Angeles FC"), 0.95)
        # Typo / transliteration
        self.assertGreaterEqual(match_single_team("Bayern Munich", "Bayern Munchen"), 0.80)
        # Non-matching different clubs sharing generic 'real'
        self.assertLess(match_single_team("Real Madrid", "Real Sociedad"), 0.65)

    def test_phase1_diretta_matching(self):
        # Match with good team similarity and time within 90m
        match = {
            "title": "Lugano vs Stella Rossa",
            "teams": {"home": {"name": "Lugano"}, "away": {"name": "Stella Rossa"}},
            "date": 1729018800000,
            "_silo": "football",
        }
        cand = {
            "home": "Lugano",
            "away": "Crvena Zvezda",
            "date": 1729018800000,
            "competition": "Conference League",
        }
        res = calculate_match_score(match, cand, phase="phase1_diretta")
        self.assertTrue(res["matched"])
        self.assertGreaterEqual(res["score"], 0.80)

    def test_phase1_diretta_no_false_positive_on_generic_words(self):
        # Real Valladolid vs Sporting Gijon should NOT match Real Betis vs Sporting Lisbon
        match = {
            "title": "Real Valladolid vs Sporting Gijon",
            "teams": {"home": {"name": "Real Valladolid"}, "away": {"name": "Sporting Gijon"}},
            "date": 1729018800000,
            "_silo": "football",
        }
        cand = {
            "home": "Real Betis",
            "away": "Sporting",
            "date": 1729018800000,
            "competition": "La Liga",
        }
        res = calculate_match_score(match, cand, phase="phase1_diretta")
        self.assertFalse(res["matched"])

    def test_phase1_youth_women_conflict_veto(self):
        # Men vs U19 conflict
        match = {
            "title": "Milan vs Inter",
            "teams": {"home": {"name": "Milan"}, "away": {"name": "Inter"}},
            "date": 1729018800000,
            "_silo": "football",
        }
        cand = {
            "home": "Milan U19",
            "away": "Inter U19",
            "date": 1729018800000,
            "competition": "Primavera 1",
        }
        res = calculate_match_score(match, cand, phase="phase1_diretta")
        self.assertFalse(res["matched"])
        self.assertEqual(res["reason"], "conflict_youth")

    def test_phase2_epg_asymmetric_window(self):
        match = {
            "title": "Juventus vs Napoli",
            "teams": {"home": {"name": "Juventus"}, "away": {"name": "Napoli"}},
            "date": 1729018800000,
            "_silo": "football",
        }
        # Pre-match broadcast 60m before kickoff -> ALLOWED
        cand_ok = {
            "title": "Serie A: Juventus - Napoli",
            "teams": {"home": {"name": "Juventus"}, "away": {"name": "Napoli"}},
            "date": 1729018800000 - (60 * 60 * 1000),
        }
        res_ok = calculate_match_score(match, cand_ok, phase="phase2_epg")
        self.assertTrue(res_ok["matched"])

        # Broadcast starting 40m AFTER kickoff -> VETOED (post_max is 20m)
        cand_late = {
            "title": "Serie A: Juventus - Napoli",
            "teams": {"home": {"name": "Juventus"}, "away": {"name": "Napoli"}},
            "date": 1729018800000 + (40 * 60 * 1000),
        }
        res_late = calculate_match_score(match, cand_late, phase="phase2_epg")
        self.assertFalse(res_late["matched"])

    def test_phase3_tsdb_dual_test(self):
        # Match where raw team is 'Rennes' but Diretta normalized is 'Stade Rennais'
        match = {
            "title": "Rennes vs OFI",
            "teams": {"home": {"name": "Rennes"}, "away": {"name": "OFI"}},
            "_diretta_home": "Rennes",
            "_diretta_away": "OFI Crete",
            "date": 1729018800000,
        }
        cand_tsdb = {
            "home": "Stade Rennais",
            "away": "OFI",
            "date_ms": 1729018800000,
            "competition": "Club Friendlies",
        }
        res = calculate_match_score(match, cand_tsdb, phase="phase3_tsdb")
        self.assertTrue(res["matched"])
        self.assertGreaterEqual(res["score"], 0.80)

if __name__ == "__main__":
    unittest.main()
