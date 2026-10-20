import unittest
from unittest.mock import AsyncMock, patch
from app.services.streamed_api import streamed_api
from app.services.catalog_service import catalog_service


class TestIdMatchingPrecision(unittest.IsolatedAsyncioTestCase):
    @patch.object(streamed_api, "get_all_matches")
    async def test_streamed_api_exact_and_delimited_matching(self, mock_get_matches):
        mock_get_matches.return_value = [
            {"id": "football-arsenal-vs-chelsea-105", "title": "Arsenal vs Chelsea", "slug": "arsenal-vs-chelsea"},
            {"id": "tennis-sinner-vs-alcaraz-5", "title": "Sinner vs Alcaraz", "slug": "sinner-vs-alcaraz"},
            {"id": "f1-monza-gp-20349", "title": "Monza GP", "slug": "monza-gp"},
        ]

        # 1. Exact ID
        m = await streamed_api.find_match_by_slug_and_id("tennis-sinner-vs-alcaraz-5")
        self.assertIsNotNone(m)
        self.assertEqual(m["title"], "Sinner vs Alcaraz")

        # 2. Delimited ID suffix "-5" matches Sinner vs Alcaraz
        m2 = await streamed_api.find_match_by_slug_and_id("5")
        self.assertIsNotNone(m2)
        self.assertEqual(m2["title"], "Sinner vs Alcaraz")

        # 3. Delimited ID suffix "-105" matches Arsenal vs Chelsea
        m3 = await streamed_api.find_match_by_slug_and_id("105")
        self.assertIsNotNone(m3)
        self.assertEqual(m3["title"], "Arsenal vs Chelsea")

        # 4. Short digit "2" present in "20349" must NOT match (previously caused false positive)
        m4 = await streamed_api.find_match_by_slug_and_id("2")
        self.assertIsNone(m4)

        # 5. Non-delimited substring "34" or "20" must NOT match
        self.assertIsNone(await streamed_api.find_match_by_slug_and_id("34"))
        self.assertIsNone(await streamed_api.find_match_by_slug_and_id("20"))

        # 6. Exact slug match
        m_slug = await streamed_api.find_match_by_slug_and_id("monza-gp")
        self.assertIsNotNone(m_slug)
        self.assertEqual(m_slug["title"], "Monza GP")

    def test_catalog_service_get_cached_match_precision(self):
        catalog_service._cached_matches = [
            {"id": "streamsport:dlhd-series-a-inter-vs-milan-502", "title": "Inter vs Milan"},
            {"id": "streamsport:basket-lakers-vs-celtics-2", "title": "Lakers vs Celtics"},
        ]
        # Rebuild indexed dictionary
        catalog_service._matches_by_id = {
            m["id"]: m for m in catalog_service._cached_matches
        }
        for m in list(catalog_service._cached_matches):
            catalog_service._matches_by_id[m["id"].replace("streamsport:", "")] = m

        # 1. Exact match with prefix
        self.assertEqual(catalog_service.get_cached_match("streamsport:basket-lakers-vs-celtics-2")["title"], "Lakers vs Celtics")

        # 2. Delimited suffix "-2" matches Lakers vs Celtics
        self.assertEqual(catalog_service.get_cached_match("2")["title"], "Lakers vs Celtics")

        # 3. Searching for "5" or "50" must NOT match Inter vs Milan (id ends with 502)
        self.assertIsNone(catalog_service.get_cached_match("5"))
        self.assertIsNone(catalog_service.get_cached_match("50"))

        # 4. Searching for "02" must NOT match (non-delimited)
        self.assertIsNone(catalog_service.get_cached_match("02"))
