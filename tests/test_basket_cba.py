import unittest
from app.services.genre_classifier import genre_classifier


class TestBasketCBAClassification(unittest.TestCase):
    def test_cba_matches_not_in_nba(self):
        cases = [
            ({"title": "Ningbo Rockets vs Shanghai Sharks", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Zhejiang Golden Bulls vs Jilin", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Nanjing Monkey Kings vs Shandong", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "CBA: Liaoning vs Guangdong", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Beijing Ducks vs Fujian Sturgeons", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Sichuan Blue Whales vs Guangzhou", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_nba_matches_in_nba(self):
        cases = [
            ({"title": "Los Angeles Lakers vs Boston Celtics", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Lakers vs Celtics", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Chicago Bulls vs Houston Rockets", "category": "basketball"}, "basket", "NBA"),
            ({"title": "NBA: Warriors vs Kings", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Golden State Warriors vs Sacramento Kings", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Denver Nuggets vs Minnesota Timberwolves", "category": "basketball"}, "basket", "NBA"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")


if __name__ == "__main__":
    unittest.main()
