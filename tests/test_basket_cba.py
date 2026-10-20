import unittest
from app.services.genre_classifier import genre_classifier


class TestBasketClassification(unittest.TestCase):
    def test_cba_matches_not_in_nba(self):
        cases = [
            ({"title": "Ningbo Rockets vs Shanghai Sharks", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Ningbo Rockets vs Shanxi Zhongyu", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Ningbo Rockets vs Shanxi Fenjiu", "category": "basketball", "competition": "Chinese Basketball Association", "country": "China"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Xinjiang Yili vs Tianjin Pioneers", "category": "basketball", "competition": "CBA", "country": "China"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Zhejiang Golden Bulls vs Jilin", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Nanjing Monkey Kings vs Shandong", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "CBA: Liaoning vs Guangdong", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Beijing Ducks vs Fujian Sturgeons", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Sichuan Blue Whales vs Guangzhou", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_nbl_and_foreign_matches(self):
        cases = [
            ({"title": "Sydney Kings vs Melbourne United", "category": "basketball", "competition": "NBL", "country": "Australia"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Sydney Kings vs Illawarra Hawks", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Tasmania JackJumpers vs Brisbane Bullets", "category": "basketball"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "Real Madrid vs Barcelona", "category": "basketball", "competition": "Liga ACB", "country": "Spain"}, "basket", "Campionati Esteri ed NBL"),
            ({"title": "ALBA Berlin vs Bayern Munich", "category": "basketball", "competition": "BBL", "country": "Germany"}, "basket", "Campionati Esteri ed NBL"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_nba_matches_in_nba(self):
        cases = [
            ({"title": "Detroit Pistons vs Boston Celtics", "category": "basketball", "competition": "NBA", "country": "USA"}, "basket", "NBA"),
            ({"title": "Los Angeles Lakers vs Boston Celtics", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Lakers vs Celtics", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Chicago Bulls vs Houston Rockets", "category": "basketball"}, "basket", "NBA"),
            ({"title": "NBA: Warriors vs Kings", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Golden State Warriors vs Sacramento Kings", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Denver Nuggets vs Minnesota Timberwolves", "category": "basketball"}, "basket", "NBA"),
            ({"title": "San Antonio Spurs vs Oklahoma City Thunder", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Miami Heat vs Orlando Magic", "category": "basketball"}, "basket", "NBA"),
            ({"title": "Dallas Mavericks vs Phoenix Suns", "category": "basketball"}, "basket", "NBA"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_lba_and_european_cups(self):
        cases = [
            ({"title": "Olimpia Milano vs Virtus Bologna", "category": "basketball", "competition": "LBA Serie A", "country": "Italy"}, "basket", "LBA Serie A"),
            ({"title": "Varese vs Brescia", "category": "basketball", "competition": "LBA"}, "basket", "LBA Serie A"),
            ({"title": "Panathinaikos vs Olympiacos", "category": "basketball", "competition": "EuroLeague"}, "basket", "Eurolega ed Eurocup"),
            ({"title": "Virtus Bologna vs Real Madrid", "category": "basketball", "competition": "EuroLeague"}, "basket", "Eurolega ed Eurocup"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_college_women_fiba(self):
        cases = [
            ({"title": "Duke vs North Carolina", "category": "basketball", "competition": "NCAA Basketball"}, "basket", "NCAA e College Basket"),
            ({"title": "Las Vegas Aces vs New York Liberty", "category": "basketball", "competition": "WNBA"}, "basket", "WNBA e Femminile"),
            ({"title": "USA vs Spain", "category": "basketball", "competition": "FIBA World Cup"}, "basket", "FIBA e Tornei Nazionali"),
        ]
        for m, exp_cat, exp_genre in cases:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual((cat, genre), (exp_cat, exp_genre), f"Failed for {m['title']}")

    def test_fctv33_basket_junk_filtering(self):
        from app.services.fctv33_service import FCTV33Service

        # Junk events that MUST be discarded upstream
        junk_events = [
            {"category": "basketball", "_silo": "basketball", "title": "Fujian Xunxing vs Zhejiang", "competition": "Chinese Basketball Association", "country": "China"},
            {"category": "basketball", "_silo": "basketball", "title": "Seoul SK Knights vs Anyang KGC", "competition": "Korean Basketball League", "country": "South Korea"},
            {"category": "basketball", "_silo": "basketball", "title": "San Miguel Beermen vs NLEX Road Warriors", "competition": "Philippine basketball governors cup", "country": "Philippines"},
            {"category": "basketball", "_silo": "basketball", "title": "Adelaide Link Lightning vs Townsville Fire", "competition": "Women's National Basketball League", "country": "Australia"},
            {"category": "basketball", "_silo": "basketball", "title": "Denso Iris Women vs Indian Railways Women", "competition": "Women's Basketball League Asia", "country": "Asian"},
            {"category": "basketball", "_silo": "basketball", "title": "Spintex Knights vs Nabaya Sofas", "competition": "8Africa Champions Clubs ROAD TO B.A.L. 2027", "country": ""},
            {"category": "basketball", "_silo": "basketball", "title": "France U18 vs Spain U18", "competition": "FIBA U18 Championship", "country": "Europe"},
            {"category": "basketball", "_silo": "basketball", "title": "Valdosta State vs West Florida", "competition": "NCAA Division II", "country": "USA"},
        ]
        for ev in junk_events:
            self.assertTrue(FCTV33Service.is_junk_event(ev), f"Expected junk for {ev['title']} ({ev['competition']})")

        # Legitimate events that MUST be kept upstream
        legit_events = [
            {"category": "basketball", "_silo": "basketball", "title": "Detroit Pistons vs Boston Celtics", "competition": "National Basketball Association", "country": "USA"},
            {"category": "basketball", "_silo": "basketball", "title": "Golden State Valkyries vs Atlanta Dream", "competition": "Women's National Basketball Association", "country": "USA"},
            {"category": "basketball", "_silo": "basketball", "title": "Balkan Botevgrad vs Lietkabelis", "competition": "EuroCup Basketball", "country": "Europe"},
            {"category": "basketball", "_silo": "basketball", "title": "Galatasaray vs Strasbourg", "competition": "Basketball Champions League", "country": "Europe"},
            {"category": "basketball", "_silo": "basketball", "title": "Nancy vs Nitra Blue Wings", "competition": "FIBA Europe Cup", "country": "Europe"},
            {"category": "basketball", "_silo": "basketball", "title": "Virtus Bologna vs Olimpia Milano", "competition": "LBA Serie A", "country": "Italy"},
            {"category": "basketball", "_silo": "basketball", "title": "Familia Schio vs Virtus Bologna Femminile", "competition": "Serie A1 Femminile", "country": "Italy"},
            {"category": "basketball", "_silo": "basketball", "title": "Tasmania JackJumpers vs Brisbane Bullets", "competition": "National Basketball League", "country": "Australia"},
            {"category": "basketball", "_silo": "basketball", "title": "Duke vs North Carolina", "competition": "NCAA Basketball", "country": "USA"},
        ]
        for ev in legit_events:
            self.assertFalse(FCTV33Service.is_junk_event(ev), f"Expected legitimate for {ev['title']} ({ev['competition']})")


if __name__ == "__main__":
    unittest.main()
