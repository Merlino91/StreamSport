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

    def test_anti_junk_filter(self):
        """Verifies that is_junk_event correctly drops low-tier exotic/youth events while preserving motorsports, tennis, and top leagues."""
        junk_items = [
            {"title": "ps manado vs ps freeport indonesia", "_silo": "football", "competition": "piala soeratin u-13 2026 putaran nasional", "country": ""},
            {"title": "YANGON UNITED FC vs SHAN UNITED FC", "_silo": "football", "competition": "Myanmar Women's League 2026-2027", "country": ""},
            {"title": "AS Armee vs ASC Kedia", "_silo": "football", "competition": "Mauritania Ligue 1", "country": "Mauritania"},
            {"title": "KPA Women vs Dynamo", "_silo": "basketball", "competition": "FIBA WBLA Qualifiers 2026 Zone 5", "country": ""},
            {"title": "Meycauayan vs Pasay Voyagers", "_silo": "basketball", "competition": "Philippines MPBL", "country": "Philippines"},
            # American football junk: FCS, Division II, Division III, NAIA
            {"title": "North Dakota State vs South Dakota State", "_silo": "american-football", "competition": "NCAA FCS Football", "country": "USA"},
            {"title": "Valdosta State vs West Florida", "_silo": "american-football", "competition": "NCAA Division II", "country": "USA"},
            # Baseball junk: MiLB, Triple-A, College baseball
            {"title": "Toledo Mud Hens vs Louisville Bats", "_silo": "baseball", "competition": "MiLB Triple-A", "country": "USA"},
            {"title": "Vanderbilt vs Florida", "_silo": "baseball", "competition": "College Baseball NCAA", "country": "USA"},
            # Hockey junk: AHL, ECHL, junior hockey
            {"title": "Chicago Wolves vs Rockford IceHogs", "_silo": "hockey", "competition": "AHL", "country": "USA"},
            {"title": "London Knights vs Guelph Storm", "_silo": "hockey", "competition": "OHL Junior Hockey", "country": "Canada"},
            # Volleyball junk: foreign college / district volleyball / minor Asian club cups
            {"title": "Stanford vs Penn State", "_silo": "volley", "competition": "College Volleyball NCAA", "country": "USA"},
            {"title": "KAL Jumbos vs Rush & Cash", "_silo": "volley", "competition": "KOVO Cup", "country": "South Korea"},
            {"title": "PLDT High Speed Hitters vs Akari", "_silo": "volley", "competition": "Premier Volleyball League", "country": "Philippines"},
            # Tennis junk: informational summary cards, table tennis
            {"title": "20/10/2026 Tennis: ATP & WTA", "_silo": "tennis", "competition": "ZRisultati in tempo reale e gli esiti", "country": ""},
            {"title": "Kukielka vs Marchlewski", "_silo": "tennis", "competition": "TT Cup Table Tennis", "country": "Poland"},
            # Fight junk: local episodic Thai broadcasts
            {"title": "Muay Mun Sanan Mueang 2026 Ep.39", "_silo": "fight", "competition": "Muay Mun Sanan Mueang", "country": ""},
        ]
        for item in junk_items:
            self.assertTrue(fctv33_service.is_junk_event(item), f"Expected junk for {item['title']}")

        valid_items = [
            # Indonesian MotoGP should NEVER be filtered
            {"title": "Indonesian MotoGP Grand Prix", "_silo": "motor-sports", "competition": "MotoGP Mandalika Indonesia", "country": "Indonesia"},
            # Foreign ATP tennis should NEVER be filtered
            {"title": "Mannarino vs Vukic", "_silo": "tennis", "competition": "ATP Almaty, Kazakhstan", "country": "Kazakhstan"},
            # Italian Serie C should NEVER be filtered
            {"title": "Juventus U23 vs Renate", "_silo": "football", "competition": "Italian Serie C", "country": "Italy"},
            # Premier League should NEVER be filtered
            {"title": "Tottenham vs Coventry", "_silo": "football", "competition": "English Premier League", "country": "England"},
            # Official FIFA National tournament should NEVER be filtered
            {"title": "USA U17 Women vs Samoa U17 Women", "_silo": "football", "competition": "U17 Women's World Cup", "country": "International"},
            # NBA
            {"title": "Boston Celtics vs LA Lakers", "_silo": "basketball", "competition": "NBA", "country": "USA"},
            # American football: NFL and NCAA Division I FBS must pass
            {"title": "Kansas City Chiefs vs Baltimore Ravens", "_silo": "american-football", "competition": "NFL", "country": "USA"},
            {"title": "Alabama vs Georgia", "_silo": "american-football", "competition": "NCAA Football FBS", "country": "USA"},
            # Baseball: MLB, NPB must pass
            {"title": "NY Yankees vs LA Dodgers", "_silo": "baseball", "competition": "MLB", "country": "USA"},
            {"title": "Yomiuri Giants vs Hanshin Tigers", "_silo": "baseball", "competition": "NPB", "country": "Japan"},
            # Hockey: NHL, KHL, Italian leagues must pass
            {"title": "Edmonton Oilers vs Florida Panthers", "_silo": "hockey", "competition": "NHL", "country": "USA"},
            {"title": "Asiago Hockey vs Bolzano Foxes", "_silo": "hockey", "competition": "ICE Hockey League", "country": "Italy"},
            # Volleyball: SuperLega, Serie A1, CEV, VNL must pass
            {"title": "Sir Safety Perugia vs Trentino Volley", "_silo": "volley", "competition": "SuperLega Credem Banca", "country": "Italy"},
            {"title": "Conegliano vs Milano", "_silo": "volley", "competition": "Serie A1 Femminile", "country": "Italy"},
        ]
        for item in valid_items:
            self.assertFalse(fctv33_service.is_junk_event(item), f"Expected valid for {item['title']}")


if __name__ == "__main__":
    unittest.main()
