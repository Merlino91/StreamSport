import unittest
from unittest.mock import patch, AsyncMock
from app.services.stream_service import StreamService


class TestStreamServiceTvVoo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = StreamService()

    @patch("app.services.mandrakodi_service.mandrakodi_service.resolve_live_match_streams", new_callable=AsyncMock)
    @patch("app.services.stream_service.daddylive_api")
    async def test_tvvoo_stream_priority_and_format(self, mock_dl, mock_mandra):
        mock_dl.get_active_domain = AsyncMock(return_value="dlive.sx")
        mock_mandra.return_value = []

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

        # 1st and 2nd streams are exclusive TvVoo No-Freeze streams!
        self.assertIn("No-Freeze", streams[0]["name"])
        self.assertIn("live/manifest.m3u8", streams[0]["url"])
        self.assertIn("No-Freeze", streams[1]["name"])
        self.assertIn("live/manifest.m3u8", streams[1]["url"])

        # DaddyLive streams follow cleanly without duplicate TvVoo entries
        dl_streams = [s for s in streams if "DaddyLive" in s.get("title", "")]
        self.assertEqual(len(dl_streams), 2)
        self.assertIn("🇮🇹", dl_streams[0]["name"])
        self.assertIn("🇬🇧", dl_streams[1]["name"])

    @patch("app.services.stream_service.daddylive_api")
    async def test_tvvoo_no_freeze_without_easyproxy(self, mock_dl):
        """Verifies that TvVoo No-Freeze streams work seamlessly even when EasyProxy is not configured."""
        match = {
            "id": "test_match_2",
            "title": "Inter vs Roma",
            "date": 0,
            "sources": [
                {
                    "source": "tvvoo",
                    "id": "sky_sport_calcio",
                    "name": "Sky Sport Calcio",
                    "url": "https://vavoo.to/play/calcio_1",
                },
            ],
        }
        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches = [match]

        streams = await self.service.get_streams_for_event(
            "streamsport:test_match_2",
            ep_url=None,
            ep_pass=None,
            user_tz="Europe/Rome",
            base_url="https://addon.example.com",
        )
        self.assertEqual(len(streams), 1)
        self.assertIn("No-Freeze", streams[0]["name"])
        self.assertIn("live/manifest.m3u8", streams[0]["url"])

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
