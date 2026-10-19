import unittest
from app.services.freeshot_service import freeshot_service, FREESHOT_ITALIAN_CHANNELS
from app.services.stream_service import stream_service


class TestFreeshotIntegration(unittest.TestCase):
    def test_channel_resolution(self):
        """Verifies that Italian channels resolve to their correct Freeshot embed codes."""
        # By Sky ID
        uno = freeshot_service.get_channel_info(sky_id="9097")
        self.assertIsNotNone(uno)
        self.assertEqual(uno["code"], "SkySportUnoItalia")

        dazn = freeshot_service.get_channel_info(sky_id="11402")
        self.assertIsNotNone(dazn)
        self.assertEqual(dazn["code"], "ZonaDAZN")

        # By Tivùsat ID
        rai1 = freeshot_service.get_channel_info(tivusat_id="42")
        self.assertIsNotNone(rai1)
        self.assertEqual(rai1["code"], "Rai1IT")

        # By canonical key
        f1 = freeshot_service.get_channel_info(canonical_key="sky sport f1")
        self.assertIsNotNone(f1)
        self.assertEqual(f1["code"], "SkySportF1Italia")

        tennis = freeshot_service.get_channel_info(canonical_key="sky sport tennis")
        self.assertIsNotNone(tennis)
        self.assertEqual(tennis["code"], "SkySportTennisIT")

    def test_all_freeshot_channels_registered(self):
        """Verifies key sport channels are present in FREESHOT_ITALIAN_CHANNELS."""
        expected_keys = [
            "dazn 1", "sky sport uno", "sky sport calcio", "sky sport tennis",
            "sky sport f1", "sky sport motogp", "sky sport arena", "sky sport nba",
            "sky sport golf", "sky sport max", "sky sport 24", "sky sport legend",
            "sky sport mix", "eurosport 1", "eurosport 2", "supertennis", "solocalcio",
            "sportitalia", "inter tv", "milan tv", "rai sport", "canale 5", "rai 1"
        ]
        for k in expected_keys:
            self.assertIn(k, FREESHOT_ITALIAN_CHANNELS, f"Expected {k} in FREESHOT_ITALIAN_CHANNELS")

    def test_stream_service_positioning(self):
        """Verifies that Freeshot streams are positioned immediately below TvVoo streams."""
        import asyncio
        match_fixture = {
            "id": "test_match_1",
            "title": "Inter vs Juventus",
            "date": int((unittest.mock_time if hasattr(unittest, "mock_time") else 0)),
            "sources": [
                {"source": "tvvoo", "id": "sky sport uno", "name": "Sky Sport Uno", "url": "https://vavoo.to/play/123/index.m3u8", "country": "Italy"},
                {"source": "freeshot", "id": "SkySportUnoItalia", "code": "SkySportUnoItalia", "name": "Sky Sport Uno", "country": "Italy"},
                {"source": "dlhd", "id": "500", "name": "Sky Sport Uno HD", "country": "Italy"},
            ]
        }

        # Mock current time to match date so it considers it live
        import time
        from app.services.catalog_service import catalog_service
        now_ms = int(time.time() * 1000)
        match_fixture["date"] = now_ms
        catalog_service._cached_matches = [match_fixture]

        streams = asyncio.run(
            stream_service.get_streams_for_event(
                "test_match_1",
                ep_url="https://ep.example.com",
                ep_pass="secret",
                base_url="http://addon.test"
            )
        )

        # There should be TvVoo, Freeshot, then DaddyLive
        self.assertTrue(len(streams) >= 3)
        stream_names = [s.get("name", "") for s in streams]
        stream_titles = [s.get("title", "") for s in streams]

        # Check TvVoo is first
        self.assertIn("TvVoo", stream_titles[0])
        # Check Freeshot is immediately below TvVoo
        has_freeshot_below = any("Freeshot" in t for t in stream_titles[1:3])
        self.assertTrue(has_freeshot_below, f"Expected Freeshot below TvVoo, got: {stream_titles}")


if __name__ == "__main__":
    unittest.main()
