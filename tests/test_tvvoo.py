import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.tvvoo_service import TvVooService


class TestTvVooService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = TvVooService()

    def test_cleanup_channel_name(self):
        self.assertEqual(self.service.cleanup_channel_name("SKY SPORT CALCIO .c"), "sky sport calcio")
        self.assertEqual(self.service.cleanup_channel_name("EUROSPORT 1 .s"), "eurosport 1")
        self.assertEqual(self.service.cleanup_channel_name("RAI SPORT [LIVE DURING EVENTS ONLY] .s"), "rai sport")
        self.assertEqual(self.service.cleanup_channel_name("DAZN 1 FHD"), "dazn 1")

    def test_canonical_key_mapping(self):
        self.assertEqual(self.service.get_canonical_key("Sky Sport 1"), "sky sport uno")
        self.assertEqual(self.service.get_canonical_key("Sky Sport Uno"), "sky sport uno")
        self.assertEqual(self.service.get_canonical_key("Sky Sport Calcio HD"), "sky sport calcio")
        self.assertEqual(self.service.get_canonical_key("DAZN"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("DAZN 1"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("Eurosport 1"), "eurosport 1")
        self.assertEqual(self.service.get_canonical_key("Rai Sport + HD"), "rai sport")
        self.assertEqual(self.service.get_canonical_key("SuperTennis HD"), "supertennis")
        self.assertIsNone(self.service.get_canonical_key("CanaleInesistenteXYZ"))

    @patch("app.services.tvvoo_service.httpx.AsyncClient")
    async def test_sync_channels_mock(self, mock_client_cls):
        # Mock ping response
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        # Ping response
        mock_ping_resp = MagicMock()
        mock_ping_resp.status_code = 200
        mock_ping_resp.json.return_value = {"addonSig": "test_sig_123"}

        # Catalog response
        mock_cat_resp = MagicMock()
        mock_cat_resp.status_code = 200
        mock_cat_resp.json.return_value = {
            "items": [
                {"name": "SKY SPORT CALCIO .c", "url": "https://vavoo.to/play/calcio_c"},
                {"name": "SKY SPORT CALCIO .s", "url": "https://vavoo.to/play/calcio_s"},
                {"name": "DAZN 1 .c", "url": "https://vavoo.to/play/dazn_c"},
            ],
            "nextCursor": None,
        }

        mock_client.post.side_effect = [mock_ping_resp, mock_cat_resp]

        res = await self.service.sync_channels(force=True)
        self.assertIn("sky sport calcio", res)
        self.assertIn("dazn 1", res)

        calcio_streams = self.service.get_channel_streams("Sky Sport Calcio")
        self.assertEqual(len(calcio_streams), 2)
        self.assertEqual(calcio_streams[0]["tag"], "c")
        self.assertEqual(calcio_streams[1]["tag"], "s")

        self.assertTrue(self.service.has_channel("Sky Sport Calcio"))
        self.assertTrue(self.service.has_channel("DAZN 1"))
        self.assertFalse(self.service.has_channel("SportInesistente"))


if __name__ == "__main__":
    unittest.main()
