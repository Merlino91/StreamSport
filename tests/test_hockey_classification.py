import unittest
from app.services.canonical_competition_service import detect_canonical_competition, get_canonical_display_name
from app.services.genre_classifier import genre_classifier
from app.services.catalog_service import catalog_service
from app.services.diretta_service import diretta_service


class TestHockeyEuropeanLeagues(unittest.TestCase):

    def test_canonical_competition_detection(self):
        # SHL variations
        c_shl1 = detect_canonical_competition("Sweden: SHL : Timra vs HV 71", silo_hint="hockey")
        self.assertEqual(c_shl1, "hockey_shl")
        self.assertEqual(get_canonical_display_name(c_shl1), "SHL")

        c_shl2 = detect_canonical_competition("Timra vs HV 71 (SHL)", silo_hint="hockey")
        self.assertEqual(c_shl2, "hockey_shl")

        c_shl3 = detect_canonical_competition("Swedish Hockey League : Farjestad vs Lulea", silo_hint="hockey")
        self.assertEqual(c_shl3, "hockey_shl")

        # Czech Extraliga variations
        c_cz1 = detect_canonical_competition("Czech Republic: Extraliga : Litvinov vs Mountfield", silo_hint="hockey")
        self.assertEqual(c_cz1, "hockey_extraliga")
        self.assertEqual(get_canonical_display_name(c_cz1), "Czech Extraliga")

        c_cz2 = detect_canonical_competition("Litvinov vs Mountfield HK (Tipsport Extraliga)", silo_hint="hockey")
        self.assertEqual(c_cz2, "hockey_extraliga")

    def test_hockey_genre_classification(self):
        # European leagues must be classified into 'KHL e Leghe Europee'
        m_shl = {
            "title": "Timra vs HV 71",
            "category": "hockey",
            "_silo": "hockey",
            "canonical_competition": "hockey_shl",
            "competition": "Sweden: SHL"
        }
        cat, genre = genre_classifier.classify(m_shl)
        self.assertEqual(cat, "hockey")
        self.assertEqual(genre, "KHL e Leghe Europee")

        # Czech Extraliga (by competition string)
        m_cz = {
            "title": "Litvinov vs Mountfield HK",
            "category": "hockey",
            "_silo": "hockey",
            "competition": "Czech Extraliga"
        }
        cat, genre = genre_classifier.classify(m_cz)
        self.assertEqual(cat, "hockey")
        self.assertEqual(genre, "KHL e Leghe Europee")

        # True NHL match
        m_nhl = {
            "title": "Florida Panthers vs Vancouver Canucks",
            "category": "hockey",
            "_silo": "hockey"
        }
        cat, genre = genre_classifier.classify(m_nhl)
        self.assertEqual(cat, "hockey")
        self.assertEqual(genre, "NHL")

    def test_meta_item_badge(self):
        m_shl = {
            "title": "Timra vs HV 71",
            "category": "hockey",
            "_silo": "hockey",
            "canonical_competition": "hockey_shl",
            "competition": "Sweden: SHL"
        }
        meta = catalog_service.build_meta_item(m_shl, "KHL e Leghe Europee")
        self.assertIn("🏆 SHL", meta.get("description", ""))

    def test_livesport_competition_dominance(self):
        # A raw match with rough provider competition
        m = {
            "id": "dlhd-test-1",
            "title": "Timra vs HV 71 (SHL)",
            "category": "hockey",
            "_silo": "hockey",
            "competition": "Leghe Europee Hockey",
            "canonical_competition": "hockey_euro",
            "date": 1792083600000,
            "sources": []
        }

        # Mock Pass 1 Flashscore EN candidate
        en_cand = {
            "id": "diretta-en-123",
            "flashscore_id": "fs-123",
            "_silo": "hockey",
            "home": "Timra",
            "away": "HV 71",
            "title": "Timra vs HV 71",
            "competition": "Sweden: SHL",
            "date": 1792083600000,
            "tv_channels": []
        }

        diretta_service._en_events_by_silo = {"hockey": [en_cand]}
        diretta_service._cached_events = [en_cand]

        n = diretta_service.reconcile_matches([m])
        self.assertEqual(n, 1)
        # Dominant English competition from Livesport inherited
        self.assertEqual(m["competition"], "Sweden: SHL")
        self.assertEqual(m["canonical_competition"], "hockey_shl")


if __name__ == "__main__":
    unittest.main()
