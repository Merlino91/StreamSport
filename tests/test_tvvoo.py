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
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        # Ping response
        mock_ping_resp = MagicMock()
        mock_ping_resp.status_code = 200
        mock_ping_resp.json.return_value = {"addonSig": "test_sig_123"}

        def make_cat_resp(items):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"items": items, "nextCursor": None}
            return r

        it_resp = make_cat_resp([
            {"name": "SKY SPORT CALCIO .c", "url": "https://vavoo.to/play/calcio_c"},
            {"name": "SKY SPORT CALCIO .s", "url": "https://vavoo.to/play/calcio_s"},
            {"name": "DAZN 1 .c", "url": "https://vavoo.to/play/dazn_c"},
        ])
        es_resp = make_cat_resp([
            {"name": "MOVISTAR LALIGA .c", "url": "https://vavoo.to/play/movistar_c"},
        ])
        de_resp = make_cat_resp([
            {"name": "SKY SPORT BUNDESLIGA .c", "url": "https://vavoo.to/play/bundesliga_c"},
            {"name": "MAGENTA SPORT 1 .c", "url": "https://vavoo.to/play/magenta_c"},
        ])
        fr_resp = make_cat_resp([
            {"name": "CANAL+ SPORT .c", "url": "https://vavoo.to/play/canal_sport_c"},
        ])
        uk_resp = make_cat_resp([
            {"name": "SKY SPORTS MAIN EVENT .c", "url": "https://vavoo.to/play/sky_me_c"},
            {"name": "TNT SPORTS 1 .c", "url": "https://vavoo.to/play/tnt1_c"},
        ])

        mock_client.post.side_effect = [mock_ping_resp, it_resp, es_resp, de_resp, fr_resp, uk_resp]

        res = await self.service.sync_channels(force=True)
        self.assertIn("sky sport calcio", res)
        self.assertIn("dazn 1", res)
        self.assertIn("movistar laliga", res)
        self.assertIn("sky sport bundesliga", res)
        self.assertIn("canal plus sport", res)
        self.assertIn("sky sports main event", res)

        calcio_streams = self.service.get_channel_streams("Sky Sport Calcio")
        self.assertEqual(len(calcio_streams), 2)
        self.assertEqual(calcio_streams[0]["tag"], "c")
        self.assertEqual(calcio_streams[1]["tag"], "s")

        movistar_streams = self.service.get_channel_streams("Movistar LaLiga")
        self.assertEqual(len(movistar_streams), 1)
        self.assertEqual(movistar_streams[0]["display_name"], "🇪🇸 Movistar LaLiga")

        self.assertTrue(self.service.has_channel("Sky Sport Calcio"))
        self.assertTrue(self.service.has_channel("Movistar LaLiga"))
        self.assertTrue(self.service.has_channel("Sky Sports Main Event"))
        self.assertFalse(self.service.has_channel("SportInesistente"))

    def test_enrich_matches_with_tvvoo(self):
        # Manually populate channels
        self.service._channels_by_canonical = {
            "movistar laliga": [{
                "canonical": "movistar laliga",
                "display_name": "🇪🇸 Movistar LaLiga",
                "url": "https://vavoo.to/play/movistar_c",
                "tag": "c",
                "country": "Spain",
            }],
            "sky sport uno": [{
                "canonical": "sky sport uno",
                "display_name": "Sky Sport Uno",
                "url": "https://vavoo.to/play/sky1_c",
                "tag": "c",
                "country": "Italy",
            }],
        }

        matches = [
            {
                "id": "match_dl_1",
                "title": "Real Madrid vs Barcelona",
                "sources": [
                    {"source": "dlhd", "id": "101", "name": "Movistar LaLiga"},
                ]
            },
            {
                "id": "match_dl_2",
                "title": "Unrelated Event",
                "sources": [
                    {"source": "dlhd", "id": "999", "name": "Unknown Channel"},
                ]
            }
        ]

        count = self.service.enrich_matches_with_tvvoo(matches)
        self.assertEqual(count, 1)

        # First match should have TvVoo stream inserted at index 0
        srcs = matches[0]["sources"]
        self.assertEqual(len(srcs), 2)
        self.assertEqual(srcs[0]["source"], "tvvoo")
        self.assertEqual(srcs[0]["name"], "🇪🇸 Movistar LaLiga")
        self.assertEqual(srcs[0]["url"], "https://vavoo.to/play/movistar_c")
        self.assertEqual(srcs[1]["source"], "dlhd")

        # Second match remains untouched
        self.assertEqual(len(matches[1]["sources"]), 1)
        self.assertEqual(matches[1]["sources"][0]["source"], "dlhd")


if __name__ == "__main__":
    unittest.main()
