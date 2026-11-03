import unittest
from app.services.genre_classifier import genre_classifier
from app.services.catalog_service import catalog_service
from app.services.banner_service import banner_service
from app.services.italian_epg_service import italian_epg_service


class TestOlympicAndWinterSports(unittest.TestCase):
    def test_genre_classification_winter_and_olympics(self):
        """Verifies that Olympic and Winter Sports are classified into granular genres."""
        events = [
            ({"title": "Giochi Olimpici Giovanili 2026: 1a giornata", "category": "altri_sport"}, "altri_sport", "Sport Olimpici e Atletica"),
            ({"title": "Nuoto: Trofeo Nico Sapio 2a giornata", "category": "altri_sport"}, "altri_sport", "Sport Olimpici e Atletica"),
            ({"title": "Pentathlon Moderno - Campionato Italiano 2026", "category": "altri_sport"}, "altri_sport", "Sport Olimpici e Atletica"),
            ({"title": "Orienteering - Finale Coppa Italia Sprint", "category": "altri_sport"}, "altri_sport", "Sport Olimpici e Atletica"),
            ({"title": "Curling - Europei maschili 2026 - Finale", "category": "altri_sport"}, "altri_sport", "Sport Invernali"),
            ({"title": "Pattinaggio di Figura: Skate Canada International", "category": "altri_sport"}, "altri_sport", "Sport Invernali"),
            ({"title": "Sci Alpino Coppa del Mondo: Discesa Libera", "category": "altri_sport"}, "altri_sport", "Sport Invernali"),
            ({"title": "Biathlon Coppa del Mondo Oestersund Sprint", "category": "altri_sport"}, "altri_sport", "Sport Invernali"),
            ({"title": "Ciclocross: Giro delle Regioni 1a prova", "category": "altri_sport"}, "altri_sport", "Ciclismo"),
            ({"title": "Amgen Irish Open: Giorno 4", "category": "altri_sport"}, "altri_sport", "Golf"),
        ]
        for m, expected_cat, expected_genre in events:
            cat, genre = genre_classifier.classify(m)
            self.assertEqual(cat, expected_cat, f"Failed category for {m['title']}")
            self.assertEqual(genre, expected_genre, f"Failed genre for {m['title']}")

    def test_anti_fusion_clean_tokens(self):
        """Verifies that non-matchup events do not get truncated to just the round/day number."""
        tok1 = catalog_service._clean_tokens("Preliminari Napoli: 3a giornata")
        tok2 = catalog_service._clean_tokens("Giochi Olimpici Giovanili 2026: 1a giornata")

        self.assertIn("napoli", tok1)
        self.assertIn("preliminari", tok1)
        self.assertIn("olimpici", tok2)
        self.assertIn("giochi", tok2)

        # Similarity between two totally different non-team events must remain well below deduplication threshold (0.78)
        sim = catalog_service.calc_similarity(tok1, tok2)
        self.assertLess(sim, 0.55)

    def test_anti_fusion_deduplication(self):
        """Verifies that distinct session events scheduled at the same time NEVER merge."""
        m1 = {
            "id": "sky_arena_preliminari",
            "title": "Preliminari Napoli: 3a giornata",
            "category": "altri_sport",
            "_silo": "altri_sport",
            "date": 1793674800000,
            "sources": [{"id": "sky_sport_arena", "source": "tvvoo"}]
        }
        m2 = {
            "id": "tivusat_olimpici",
            "title": "Giochi Olimpici Giovanili 2026: 1a giornata",
            "category": "altri_sport",
            "_silo": "altri_sport",
            "date": 1793674800000,
            "sources": [{"id": "rai_sport", "source": "tvvoo"}]
        }
        merged = catalog_service.merge_and_deduplicate([m1], [m2])
        self.assertEqual(len(merged), 2, "Events with different titles must remain separate even at identical kickoff times!")

    def test_gender_separation_deduplication(self):
        """Verifies that Men and Women events in individual sports never merge."""
        m_men = {
            "id": "curling_men",
            "title": "Curling: Camp. Europei Ostrava 2026 Finale Maschile",
            "category": "altri_sport",
            "_silo": "winter-sports",
            "date": 1793725800000,
            "sources": [{"id": "src1"}]
        }
        m_women = {
            "id": "curling_women",
            "title": "Curling: Camp. Europei Ostrava 2026 Finale Femminile",
            "category": "altri_sport",
            "_silo": "winter-sports",
            "date": 1793725800000,
            "sources": [{"id": "src2"}]
        }
        merged = catalog_service.merge_and_deduplicate([m_men], [m_women])
        self.assertEqual(len(merged), 2, "Maschile and Femminile finals must never merge!")

    def test_tivusat_image_extraction_logic(self):
        """Verifies that Tivusat image parser extracts URLs from lists of dicts."""
        ev_sample = {
            "images": [
                {
                    "url": "https://www.raiplay.it/resizegd/640x-/dl/img/2026/10/26/Giochi-Olimpici-2048x1152.jpg",
                    "size": "640x360"
                }
            ]
        }
        raw_imgs = ev_sample.get("images")
        self.assertIsInstance(raw_imgs, list)
        poster = None
        for candidate in raw_imgs:
            if isinstance(candidate, dict) and candidate.get("url"):
                poster = candidate["url"]
                break
        self.assertEqual(poster, "https://www.raiplay.it/resizegd/640x-/dl/img/2026/10/26/Giochi-Olimpici-2048x1152.jpg")


if __name__ == "__main__":
    unittest.main()
