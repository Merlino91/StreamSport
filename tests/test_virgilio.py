import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.virgilio_service import VirgilioService


SAMPLE_VIRGILIO_HTML = """
<table>
    <tr>
        <td>12:30</td>
        <td>Calcio , Serie C: Casertana-Catania</td>
        <td>Sky Sport Calcio</td>
    </tr>
    <tr>
        <td>13:00</td>
        <td>Calcio femminile , Serie A: Inter-Fiorentina</td>
        <td>Rai Sport</td>
    </tr>
    <tr>
        <td>16:00</td>
        <td>Basket , Serie A: Olimpia Milano-Trieste</td>
        <td>Sky Sport Basket, Cielo, Sky Sport 1</td>
    </tr>
    <tr>
        <td>20:45</td>
        <td>Calcio , UEFA Nations League: Norvegia-Portogallo</td>
        <td>TV8</td>
    </tr>
    <tr>
        <td>15:00</td>
        <td>Padel , Torneo Locale: Finale</td>
        <td>PadelWebTVNonEsistente</td>
    </tr>
</table>
"""


class TestVirgilioService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = VirgilioService()

    def test_parse_event_cell(self):
        sport, comp, details = self.service._parse_event_cell("Calcio , Serie C: Casertana-Catania")
        self.assertEqual(sport, "calcio")
        self.assertEqual(comp, "Serie C")
        self.assertEqual(details, "Casertana-Catania")

        sport, comp, details = self.service._parse_event_cell("Basket , Serie A: Olimpia Milano-Trieste")
        self.assertEqual(sport, "basket")
        self.assertEqual(comp, "Serie A")
        self.assertEqual(details, "Olimpia Milano-Trieste")

    def test_normalize_title_and_teams(self):
        title, teams = self.service._normalize_title_and_teams("Serie C", "Casertana-Catania")
        self.assertEqual(title, "Serie C: Casertana vs Catania")
        self.assertIsNotNone(teams)
        self.assertEqual(teams["home"]["name"], "Casertana")
        self.assertEqual(teams["away"]["name"], "Catania")

        # Test national team translation
        title, teams = self.service._normalize_title_and_teams("UEFA Nations League", "Norvegia-Portogallo")
        self.assertEqual(title, "UEFA Nations League: Norway vs Portugal")
        self.assertEqual(teams["home"]["name"], "Norway")
        self.assertEqual(teams["away"]["name"], "Portugal")

    @patch("app.services.virgilio_service.tvvoo_service")
    @patch("app.services.virgilio_service.httpx.AsyncClient")
    async def test_get_matches_mock(self, mock_client_cls, mock_tvvoo):
        # Mock HTTP response
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_VIRGILIO_HTML
        mock_client.get.return_value = mock_resp

        # Mock tvvoo_service streams
        def mock_get_streams(channel_name):
            ch_lower = channel_name.lower()
            if "calcio" in ch_lower:
                return [{"canonical": "sky sport calcio", "display_name": "Sky Sport Calcio", "url": "https://vavoo.to/play/calcio", "tag": "c"}]
            if "rai sport" in ch_lower:
                return [{"canonical": "rai sport", "display_name": "Rai Sport", "url": "https://vavoo.to/play/raisport", "tag": "c"}]
            if "cielo" in ch_lower:
                return [{"canonical": "cielo", "display_name": "Cielo", "url": "https://vavoo.to/play/cielo", "tag": "c"}]
            if "tv8" in ch_lower:
                return [{"canonical": "tv8", "display_name": "TV8", "url": "https://vavoo.to/play/tv8", "tag": "c"}]
            return []

        mock_tvvoo.get_channel_streams.side_effect = mock_get_streams
        mock_tvvoo.ensure_synced = AsyncMock()

        matches = await self.service.get_matches(force=True)

        # 4 events have streams, 1 (Padel on non-existent channel) must be dropped
        self.assertEqual(len(matches), 4)

        # Check Casertana vs Catania
        casertana = next(m for m in matches if "Casertana" in m["title"])
        self.assertEqual(casertana["_silo"], "football")
        self.assertEqual(casertana["category"], "football")
        self.assertEqual(len(casertana["sources"]), 1)
        self.assertEqual(casertana["sources"][0]["source"], "tvvoo")
        self.assertEqual(casertana["sources"][0]["name"], "Sky Sport Calcio")

        # Check Norway vs Portugal (translated)
        norway = next(m for m in matches if "Norway" in m["title"])
        self.assertEqual(norway["teams"]["home"]["name"], "Norway")
        self.assertEqual(norway["teams"]["away"]["name"], "Portugal")

    @patch("app.services.virgilio_service.tvvoo_service")
    @patch("httpx.AsyncClient")
    async def test_parse_oggi_section_only(self, mock_client_cls, mock_tvvoo):
        """Verifies that future days (Domani, 01 ottobre, etc.) are strictly excluded from parsing."""
        multi_day_html = """
        <h2><span>Oggi </span>– 28 settembre</h2>
        <table>
            <tr><td>20:45</td><td>Calcio , UEFA Nations League: Turchia-Italia</td><td>TV8</td></tr>
        </table>
        <h2><span>Domani </span>– 29 settembre</h2>
        <table>
            <tr><td>20:45</td><td>Calcio , UEFA Nations League: Francia-Italia</td><td>TV8</td></tr>
        </table>
        <h2>– 01 ottobre</h2>
        <table>
            <tr><td>20:45</td><td>Calcio , UEFA Nations League: Germania-Serbia</td><td>TV8</td></tr>
        </table>
        """
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = multi_day_html
        mock_client.get.return_value = mock_resp
        mock_tvvoo.get_channel_streams.return_value = [{"canonical": "tv8", "display_name": "TV8", "url": "https://vavoo.to/play/tv8", "tag": "c"}]
        mock_tvvoo.ensure_synced = AsyncMock()

        matches = await self.service.get_matches(force=True)
        self.assertEqual(len(matches), 1)
        self.assertIn("Turkey vs Italy", matches[0]["title"])
        # Ensure future matches are NOT present
        self.assertFalse(any("Francia" in m["title"] or "France" in m["title"] for m in matches))
        self.assertFalse(any("Germania" in m["title"] or "Germany" in m["title"] for m in matches))


if __name__ == "__main__":
    unittest.main()
