import unittest
from app.services.catalog_service import catalog_service


class TestDeduplication(unittest.TestCase):
    def test_token_match_spelling_variants(self):
        # Turkey vs Türkiye
        self.assertTrue(catalog_service._is_token_match("italy turkey", "italy turkiye"))
        # Bayern Munich vs Bayern München
        self.assertTrue(catalog_service._is_token_match("bayern munich psg", "bayern munchen psg"))

    def test_token_match_inverted_order(self):
        # Inverted Home/Away order
        self.assertTrue(catalog_service._is_token_match("turkey italy", "italy turkiye"))
        self.assertTrue(catalog_service._is_token_match("arsenal chelsea", "chelsea arsenal"))

    def test_token_match_competition_subset(self):
        # One title has extra competition words
        self.assertTrue(catalog_service._is_token_match("italy turkey", "italy nations turkey"))
        self.assertTrue(catalog_service._is_token_match("milan inter", "inter milan serie"))

    def test_token_match_rival_clubs_separated(self):
        # Real Madrid vs Atletico Madrid MUST NEVER merge
        self.assertFalse(catalog_service._is_token_match("madrid real sevilla", "atletico madrid sevilla"))
        # Arsenal vs Chelsea vs Arsenal vs Liverpool MUST NEVER merge
        self.assertFalse(catalog_service._is_token_match("arsenal chelsea", "arsenal liverpool"))

    def test_teams_match_fuzzy(self):
        # Direct
        t1 = {"home": {"name": "Turkey"}, "away": {"name": "Italy"}}
        t2 = {"home": {"name": "Türkiye"}, "away": {"name": "Italy"}}
        self.assertTrue(catalog_service._teams_match_fuzzy(t1, t2))

        # Inverted
        t3 = {"home": {"name": "Italy"}, "away": {"name": "Türkiye"}}
        self.assertTrue(catalog_service._teams_match_fuzzy(t1, t3))

        # Different opponent
        t4 = {"home": {"name": "Turkey"}, "away": {"name": "France"}}
        self.assertFalse(catalog_service._teams_match_fuzzy(t1, t4))

    def test_merge_and_deduplicate_integration(self):
        # Merge Turkey vs Italy from DaddyLive and Türkiye vs Italy from Streamed
        p = [{
            "id": "dlhd-turkey-vs-italy",
            "title": "Turkey vs Italy",
            "date": 1790621100000,
            "_silo": "football",
            "sources": [{"source": "dlhd", "id": "s1"}],
        }]
        s = [{
            "id": "ppv-turkiye-vs-italy",
            "title": "Türkiye vs. Italy",
            "date": 1790621100000,
            "_silo": "football",
            "sources": [{"source": "tvvoo", "id": "t1"}],
        }]
        merged = catalog_service.merge_and_deduplicate(p, s)
        self.assertEqual(len(merged), 1)
        sources = [x["source"] for x in merged[0]["sources"]]
        self.assertIn("tvvoo", sources)
        self.assertIn("dlhd", sources)
        # TvVoo must be at index 0 (top priority)
        self.assertEqual(merged[0]["sources"][0]["source"], "tvvoo")


if __name__ == "__main__":
    unittest.main()
