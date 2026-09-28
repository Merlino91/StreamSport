import unittest
from unittest.mock import patch, AsyncMock
from app.services.stream_service import StreamService


class TestStreamServiceTvVoo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = StreamService()

    @patch("app.services.stream_service.daddylive_api")
    async def test_tvvoo_stream_priority_and_format(self, mock_dl):
        mock_dl.get_active_domain = AsyncMock(return_value="dlive.sx")

        match = {
            "id": "test_match_1",
            "title": "Juventus vs Milan",
            "date": 0,  # Live
            "sources": [
                {
                    "source": "dlhd",
                    "id": "123",
                    "name": "Sky Sport Calcio IT",
                },
                {
                    "source": "dlhd",
                    "id": "456",
                    "name": "TNT Sports 1 UK",
                },
                {
                    "source": "tvvoo",
                    "id": "sky_sport_calcio",
                    "name": "Sky Sport Calcio",
                    "url": "https://vavoo.to/play/calcio_1",
                },
                {
                    "source": "tvvoo",
                    "id": "dazn_1",
                    "name": "DAZN 1",
                    "url": "https://vavoo.to/play/dazn_1",
                },
            ],
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches = [match]

        streams = await self.service.get_streams_for_event(
            "streamsport:test_match_1",
            ep_url="https://ep.example.com",
            ep_pass="secret",
            user_tz="Europe/Rome",
            base_url="https://addon.example.com",
        )


        self.assertEqual(len(streams), 4)

        # 1st and 2nd streams must be TvVoo!
        self.assertEqual(streams[0]["name"], "🇮🇹 Sky Sport Calcio")
        self.assertEqual(streams[0]["title"], "⚡ Fonte: TvVoo • Qualità FHD 1080p")
        self.assertIn("host=Vavoo", streams[0]["url"])

        self.assertEqual(streams[1]["name"], "🇮🇹 DAZN 1")
        self.assertEqual(streams[1]["title"], "⚡ Fonte: TvVoo • Qualità FHD 1080p")
        self.assertIn("host=Vavoo", streams[1]["url"])

        # 3rd stream must be Italian DaddyLive!
        self.assertIn("🇮🇹", streams[2]["name"])
        self.assertIn("Fonte: DaddyLive", streams[2]["title"])

        # 4th stream must be UK DaddyLive!
        self.assertIn("🇬🇧", streams[3]["name"])
        self.assertIn("Fonte: DaddyLive", streams[3]["title"])

    def test_resolve_streamed_language(self):
        cases = [
            ("English", ("🇬🇧", "Inglese")),
            ("English - DAZN", ("🇬🇧", "Inglese - DAZN")),
            ("English - Fubo Sports", ("🇬🇧", "Inglese - Fubo Sports")),
            ("English - Sky Sports+", ("🇬🇧", "Inglese - Sky Sports+")),
            ("Main", ("🇬🇧", "Inglese")),
            ("Willow", ("🇬🇧", "Inglese - Willow")),
            ("Channel 1", ("🇬🇧", "Inglese - Channel 1")),
            ("Spanish", ("🇪🇸", "Spagnolo")),
            ("Spanish - Movistar", ("🇪🇸", "Spagnolo - Movistar")),
            ("Italian", ("🇮🇹", "Italiano")),
            ("French - Canal+", ("🇫🇷", "Francese - Canal+")),
            (None, ("🇬🇧", "Inglese")),
        ]
        for raw, expected in cases:
            flag, label = self.service.resolve_streamed_language(raw)
            self.assertEqual(flag, expected[0], f"Failed flag for {raw}")
            self.assertEqual(label, expected[1], f"Failed label for {raw}")


if __name__ == "__main__":
    unittest.main()
