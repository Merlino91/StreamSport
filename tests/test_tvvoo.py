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

    def test_italian_lcn_mapping(self):
        """Tests that official Sky Italia / Tivùsat LCN channel numbers map directly."""
        self.assertEqual(self.service.get_canonical_key("200"), "sky sport 24")
        self.assertEqual(self.service.get_canonical_key("Sky 200"), "sky sport 24")
        self.assertEqual(self.service.get_canonical_key("201"), "sky sport uno")
        self.assertEqual(self.service.get_canonical_key("Sky 201"), "sky sport uno")
        self.assertEqual(self.service.get_canonical_key("202"), "sky sport calcio")
        self.assertEqual(self.service.get_canonical_key("203"), "sky sport tennis")
        self.assertEqual(self.service.get_canonical_key("204"), "sky sport arena")
        self.assertEqual(self.service.get_canonical_key("Sky Sport 204"), "sky sport arena")
        self.assertEqual(self.service.get_canonical_key("Sky Arena"), "sky sport arena")
        self.assertEqual(self.service.get_canonical_key("205"), "sky sport golf")
        self.assertEqual(self.service.get_canonical_key("206"), "sky sport max")
        self.assertEqual(self.service.get_canonical_key("207"), "sky sport f1")
        self.assertEqual(self.service.get_canonical_key("208"), "sky sport motogp")
        self.assertEqual(self.service.get_canonical_key("209"), "sky sport nba")
        self.assertEqual(self.service.get_canonical_key("214"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("Sky 214"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("Zona DAZN"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("215"), "dazn 2")

    def test_sky_sport_feed_distinction(self):
        """Ensures generic Sky Sport event feed is NOT merged into Sky Sport Uno."""
        self.assertEqual(self.service.get_canonical_key("Sky Sport"), "sky sport")
        self.assertEqual(self.service.get_canonical_key("Sky Sport IT"), "sky sport")
        self.assertEqual(self.service.get_canonical_key("Sky Sport Eventi"), "sky sport")
        self.assertNotEqual(self.service.get_canonical_key("Sky Sport"), "sky sport uno")

    def test_magenta_sport_strict_numbers(self):
        """Tests that Magenta Sport channels 1..4 and receiver 301..304 match strictly."""
        self.assertEqual(self.service.get_canonical_key("Magenta Sport 1"), "magenta sport 1")
        self.assertEqual(self.service.get_canonical_key("Magenta Sport 301"), "magenta sport 1")
        self.assertEqual(self.service.get_canonical_key("301"), "magenta sport 1")

        self.assertEqual(self.service.get_canonical_key("Magenta Sport 2"), "magenta sport 2")
        self.assertEqual(self.service.get_canonical_key("Magenta Sport 302"), "magenta sport 2")
        self.assertEqual(self.service.get_canonical_key("302"), "magenta sport 2")

        self.assertEqual(self.service.get_canonical_key("Magenta Sport 3"), "magenta sport 3")
        self.assertEqual(self.service.get_canonical_key("Magenta Sport 303"), "magenta sport 3")
        self.assertEqual(self.service.get_canonical_key("303"), "magenta sport 3")

        # Generic Magenta Sport should NOT match a numbered channel
        self.assertEqual(self.service.get_canonical_key("Magenta Sport"), "magenta sport")

    def test_country_silos_isolation(self):
        """Tests that when country is specified, country-specific silos resolve correctly."""
        # DAZN in Italy vs Spain vs Germany vs France
        self.assertEqual(self.service.get_canonical_key("DAZN 1", country="Italy"), "dazn 1")
        self.assertEqual(self.service.get_canonical_key("DAZN 1", country="Spain"), "dazn 1 es")
        self.assertEqual(self.service.get_canonical_key("DAZN 1", country="Germany"), "dazn 1 de")
        self.assertEqual(self.service.get_canonical_key("DAZN 1", country="France"), "dazn 1 fr")

        # Sky Sport 1 in Germany vs Sky Sport 1 in Italy
        self.assertEqual(self.service.get_canonical_key("Sky Sport 1", country="Germany"), "sky sport 1 de")
        self.assertEqual(self.service.get_canonical_key("Sky Sport 1", country="Italy"), "sky sport uno")

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
