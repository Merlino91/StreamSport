import unittest
import asyncio
import time
from unittest.mock import patch, MagicMock
from app.services.mandrakodi_service import mandrakodi_service, xor_decrypt

class TestMandrakodiService(unittest.IsolatedAsyncioTestCase):

    def test_xor_decrypt(self):
        key = b"my_secret_key"
        plain = b"hello world"
        cipher = bytearray([b ^ key[i % len(key)] for i, b in enumerate(plain)])
        import base64
        b64 = base64.b64encode(cipher).decode()
        dec = xor_decrypt(b64)
        self.assertEqual(dec, "hello world")

    async def test_channels_registry_and_enrichment(self):
        # Test that sync_channels indexes Sky and Eurosport channels
        await mandrakodi_service.sync_channels(force=True)
        uno_streams = mandrakodi_service.get_channel_streams("sky sport uno")
        self.assertGreater(len(uno_streams), 0)
        self.assertEqual(uno_streams[0]["provider"], "mandrakodi")
        self.assertEqual(uno_streams[0]["sky_channel_id"], "skysportuno")

        # Test enrichment of a match listing Sky Sport Uno
        mock_matches = [
            {
                "title": "Inter vs Milan",
                "sources": [
                    {"name": "Sky Sport Uno", "source": "daddylive", "id": "dl_1"}
                ]
            }
        ]
        enriched = mandrakodi_service.enrich_matches_with_mandrakodi(mock_matches)
        self.assertGreater(enriched, 0)
        sources = mock_matches[0]["sources"]
        mandra_srcs = [s for s in sources if s.get("provider") == "mandrakodi"]
        self.assertGreater(len(mandra_srcs), 0)
        self.assertEqual(mandra_srcs[0].get("sky_channel_id"), "skysportuno")

    async def test_get_matches_structure(self):
        # Dynamic matches list (purged if >6h old)
        matches = await mandrakodi_service.get_matches(force=True)
        self.assertIsInstance(matches, list)
        for m in matches:
            self.assertIn("id", m)
            self.assertIn("title", m)
            self.assertIn("category", m)
            self.assertIn("_silo", m)
            self.assertIn("sources", m)
            self.assertEqual(m.get("_provider"), "mandrakodi")

    async def test_resolve_sky_channel(self):
        # Test resolving Sky Sport Uno
        res = await mandrakodi_service.resolve_sky_channel("skysportuno")
        self.assertIsNotNone(res)
        self.assertIn("manifest", res)
        self.assertIn("kid", res)
        self.assertIn("key", res)
        self.assertTrue(res["manifest"].startswith("http"))
        self.assertGreater(len(res["kid"]), 10)
        self.assertGreater(len(res["key"]), 10)

    def test_dynamic_sync_interval(self):
        from app.services.catalog_service import catalog_service
        # Test interval calculation in catalog_service and mandrakodi_service
        with patch("datetime.datetime") as mock_dt:
            # 15:00 Rome time -> peak hours (12 to 22) -> 900s
            mock_dt.now.return_value = MagicMock(hour=15)
            self.assertEqual(catalog_service.get_current_sync_interval(), 900)
            self.assertEqual(mandrakodi_service.get_sync_interval(), 900)

            # 22:59 Rome time -> still peak hours -> 900s
            mock_dt.now.return_value = MagicMock(hour=22)
            self.assertEqual(catalog_service.get_current_sync_interval(), 900)
            self.assertEqual(mandrakodi_service.get_sync_interval(), 900)

            # 23:00 Rome time -> off-peak hours -> 3600s
            mock_dt.now.return_value = MagicMock(hour=23)
            self.assertEqual(catalog_service.get_current_sync_interval(), 3600)
            self.assertEqual(mandrakodi_service.get_sync_interval(), 3600)

            # 08:00 Rome time -> off-peak hours -> 3600s
            mock_dt.now.return_value = MagicMock(hour=8)
            self.assertEqual(catalog_service.get_current_sync_interval(), 3600)
            self.assertEqual(mandrakodi_service.get_sync_interval(), 3600)

    async def test_resolve_live_match_streams_and_reconcile(self):
        from app.services.catalog_service import catalog_service
        # Prepare mock cached matches in Mandrakodi
        mandrakodi_service._cached_matches = [
            {
                "id": "mandrakodi:live_123",
                "title": "Juventus vs Milan",
                "teams": {"home": "Juventus", "away": "Milan"},
                "sources": [
                    {
                        "source": "amstaff",
                        "provider": "mandrakodi",
                        "id": "mandra_juv_mil",
                        "name": "DAZN 1",
                        "stream_data": {"mpd_url": "https://test.mpd", "kid": "k1", "key": "k2"}
                    }
                ]
            }
        ]
        mandrakodi_service._last_fetch_time = time.time()  # Recent within 60s

        # Test resolving live match by title/teams
        target_match = {
            "title": "Juventus vs Milan",
            "teams": {"home": "Juventus", "away": "Milan"},
            "sources": []
        }
        streams = await mandrakodi_service.resolve_live_match_streams(target_match)
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["id"], "mandra_juv_mil")

        # Test resolving with dict teams structure (e.g. FCTV33: {"home": {"name": "Juventus"}})
        fctv_match = {
            "id": "streamsport:fctv33:4521897",
            "title": "Juventus vs Milan",
            "teams": {
                "home": {"name": "Juventus"},
                "away": {"name": "Milan"},
            },
            "sources": [{"source": "fctv33", "id": "4521897"}]
        }
        fctv_streams = await mandrakodi_service.resolve_live_match_streams(fctv_match)
        self.assertEqual(len(fctv_streams), 1)
        self.assertEqual(fctv_streams[0]["id"], "mandra_juv_mil")

        # Test catalog reconciliation and timer reset
        old_sync_time = catalog_service._last_sync_time
        catalog_service._cached_matches = [
            {
                "id": "dl:12345",
                "title": "Juventus vs Milan",
                "teams": {"home": "Juventus", "away": "Milan"},
                "sources": [{"source": "daddylive", "id": "dl_src_1", "name": "Stream 1"}]
            }
        ]
        catalog_service.reconcile_mandrakodi_live_matches(mandrakodi_service._cached_matches)
        self.assertGreater(catalog_service._last_sync_time, old_sync_time)
        # Verify source was merged
        cat_match = catalog_service._cached_matches[0]
        src_ids = [s["id"] for s in cat_match["sources"]]
        self.assertIn("mandra_juv_mil", src_ids)
        self.assertIn("dl_src_1", src_ids)

        # Cleanup mock matches
        mandrakodi_service._cached_matches = []
        catalog_service._cached_matches = []

    async def test_italian_channels_sync(self):
        """Verifies ingestion of Zona DAZN (AceStream), Mediaset 20 (ClearKey), and Rai 4K (Direct)."""
        await mandrakodi_service.sync_channels(force=True)

        # 1. Zona DAZN -> canonical "dazn 1" with acestream
        dazn_streams = mandrakodi_service.get_channel_streams("dazn 1")
        self.assertGreater(len(dazn_streams), 0)
        ace_streams = [s for s in dazn_streams if s.get("source") == "acestream"]
        self.assertGreater(len(ace_streams), 0)
        self.assertTrue(ace_streams[0]["id"].startswith("16519c3e10c7f1dddea05eb2dae6182cbdd34e4f") or len(ace_streams[0]["id"]) == 40)
        self.assertEqual(ace_streams[0]["country"], "Italia")

        # 2. Mediaset 20 -> canonical "20 mediaset" with amstaff ClearKey
        m20_streams = mandrakodi_service.get_channel_streams("20 mediaset")
        self.assertGreater(len(m20_streams), 0)
        self.assertEqual(m20_streams[0]["source"], "amstaff")
        self.assertIn("kid", m20_streams[0]["stream_data"])
        self.assertIn("key", m20_streams[0]["stream_data"])

        # 3. Rai 4K -> canonical "rai 4k" with direct HLS
        r4k_streams = mandrakodi_service.get_channel_streams("rai 4k")
        self.assertGreater(len(r4k_streams), 0)
        self.assertEqual(r4k_streams[0]["source"], "direct")
        self.assertIn(".m3u8", r4k_streams[0]["stream_data"]["stream_url"])

        # 4. Canale 5 -> canonical "canale 5" with direct HLS
        c5_streams = mandrakodi_service.get_channel_streams("canale 5")
        self.assertGreater(len(c5_streams), 0)
        self.assertEqual(c5_streams[0]["source"], "direct")

if __name__ == "__main__":
    unittest.main()
