import unittest
from unittest.mock import AsyncMock, patch
from app.services.fctv33_service import fctv33_service, FCTV33_SPORT_TYPE_MAP
from app.services.stream_service import stream_service
from app.services.catalog_service import catalog_service


class TestFCTV33Integration(unittest.IsolatedAsyncioTestCase):

    def test_silo_mapping(self):
        """Verifies that all FCTV33 sport types map to recognized canonical StreamSport silos."""
        for st_id, info in FCTV33_SPORT_TYPE_MAP.items():
            self.assertIn("silo", info)
            self.assertIn("name", info)
            # Check canonical silo compatibility
            canon = catalog_service.get_canonical_silo({"_silo": info["silo"], "category": info["silo"]})
            self.assertTrue(len(canon) > 0)

    async def test_stream_service_formatting(self):
        """Verifies that stream_service correctly formats FCTV33 streams with EasyProxy parameters."""
        mock_match = {
            "id": "fctv33:4441287",
            "title": "Monza vs Cagliari",
            "category": "football",
            "date": 0,  # Live now
            "sources": [
                {
                    "source": "fctv33",
                    "id": "4441287",
                    "match_id": "4441287",
                    "stream_id": "999888",
                    "name": "Sky Sport Calcio",
                    "sport_type": 1,
                }
            ],
            "_silo": "football",
            "_fctv33_id": "4441287",
        }

        with patch.object(catalog_service, "_cached_matches", [mock_match]):
            streams = await stream_service.get_streams_for_event(
                item_id="fctv33:4441287",
                ep_url="https://ep.bluccoj.com",
                ep_pass="Z3r0EP",
            )

            self.assertTrue(len(streams) > 0)
            fctv_stream = next((s for s in streams if "FCTV33" in s["name"]), None)
            self.assertIsNotNone(fctv_stream)
            self.assertIn("host=fctv33", fctv_stream["url"])
            self.assertIn("d=4441287%3A999888", fctv_stream["url"])
            self.assertIn("redirect_stream=true", fctv_stream["url"])
            self.assertIn("api_password=Z3r0EP", fctv_stream["url"])

    def test_deduplication_preserves_fctv33_source(self):
        """Verifies that when FCTV33 match merges into an existing match, FCTV33 source and ID are preserved."""
        primary = [
            {
                "id": "diretta:dUzp8Jka",
                "title": "Monza - Cagliari",
                "category": "football",
                "date": 1792400000000,
                "sources": [
                    {"source": "tvvoo", "id": "sky_sport_calcio", "name": "Sky Sport Calcio"}
                ],
                "_silo": "football",
                "teams": {
                    "home": {"name": "Monza"},
                    "away": {"name": "Cagliari"}
                }
            }
        ]
        fctv = [
            {
                "id": "fctv33:4441287",
                "title": "Monza vs Cagliari",
                "category": "football",
                "date": 1792400000000,
                "sources": [
                    {
                        "source": "fctv33",
                        "id": "4441287",
                        "match_id": "4441287",
                        "name": "FCTV33 Live",
                    }
                ],
                "_silo": "football",
                "_fctv33_id": "4441287",
                "_sport_type": 1,
                "teams": {
                    "home": {"name": "Monza"},
                    "away": {"name": "Cagliari"}
                }
            }
        ]

        merged = catalog_service.merge_and_deduplicate_by_silos(primary, fctv)
        self.assertEqual(len(merged), 1)
        res = merged[0]
        # Check that both sources are present
        source_types = [s.get("source") for s in res["sources"]]
        self.assertIn("tvvoo", source_types)
        self.assertIn("fctv33", source_types)
        # Check that _fctv33_id is enriched
        self.assertEqual(res.get("_fctv33_id"), "4441287")


if __name__ == "__main__":
    unittest.main()
