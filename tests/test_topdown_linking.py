import unittest
from app.services.catalog_service import CatalogService
from app.services.team_matching import calculate_match_score

class TestCatalogServiceLinking(unittest.TestCase):
    def setUp(self):
        self.cs = CatalogService()

    def test_link_official_schedule(self):
        official = [
            {
                "id": "diretta-12345",
                "title": "Inter vs Milan",
                "home": "Inter",
                "away": "Milan",
                "teams": {"home": {"name": "Inter"}, "away": {"name": "Milan"}},
                "category": "football",
                "_silo": "football",
                "competition": "Serie A",
                "date": 1729970000000,
                "sources": [],
            },
            {
                "id": "diretta-67890",
                "title": "Jannik Sinner vs Carlos Alcaraz",
                "home": "Jannik Sinner",
                "away": "Carlos Alcaraz",
                "teams": {"home": {"name": "Jannik Sinner"}, "away": {"name": "Carlos Alcaraz"}},
                "category": "tennis",
                "_silo": "tennis",
                "competition": "ATP Finals",
                "date": 1729980000000,
                "sources": [],
            }
        ]

        clusters = [
            # Football cluster with 2 sources
            {
                "id": "streamed-100",
                "title": "Inter Milano vs AC Milan",
                "teams": {"home": "Inter Milano", "away": "AC Milan"},
                "category": "football",
                "_silo": "football",
                "date": 1729970000000 + 10 * 60 * 1000, # +10m
                "sources": [{"source": "hotel", "id": "1"}, {"source": "dlhd", "id": "2"}],
            },
            # Tennis cluster with 1 source
            {
                "id": "dlhd-200",
                "title": "Sinner J. vs Alcaraz C.",
                "home": "Sinner J.",
                "away": "Alcaraz C.",
                "category": "tennis",
                "_silo": "tennis",
                "date": 1729980000000,
                "sources": [{"source": "dlhd", "id": "3"}],
            },
            # Irrelevant cluster from different time
            {
                "id": "fctv-300",
                "title": "Inter vs Milan",
                "category": "football",
                "_silo": "football",
                "date": 1729970000000 + 5 * 3600 * 1000, # +5 hours (different match!)
                "sources": [{"source": "fctv33", "id": "4"}],
            }
        ]

        res = self.cs.link_official_schedule_to_provider_clusters(official, clusters)
        
        # Verify Inter vs Milan linked the 2 sources
        inter = next(m for m in res if m["id"] == "diretta-12345")
        self.assertEqual(len(inter["sources"]), 2)
        self.assertEqual(inter["sources"][0]["source"], "hotel")
        
        # Verify Sinner vs Alcaraz linked the 1 source
        sinner = next(m for m in res if m["id"] == "diretta-67890")
        self.assertEqual(len(sinner["sources"]), 1)
        self.assertEqual(sinner["sources"][0]["source"], "dlhd")

if __name__ == "__main__":
    unittest.main()
