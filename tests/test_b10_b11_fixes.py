import time
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.sportvideo_service import sportvideo_service
from app.services.daddylive_api import daddylive_api


class TestB10B11Fixes(unittest.IsolatedAsyncioTestCase):
    def test_extract_info_hash_malformed_torrent_no_infinite_loop(self):
        """Verifies that malformed/truncated torrent bytes never trigger an infinite loop."""
        # 1. Truncated integer marker 'i' without 'e'
        truncated_bytes = b"d4:infodi123456"  # Missing 'e' after 'i'
        t0 = time.time()
        info_hash = sportvideo_service.extract_info_hash(truncated_bytes)
        t_elapsed = time.time() - t0
        self.assertEqual(info_hash, "")
        self.assertLess(t_elapsed, 0.1)  # Must finish instantly, not hang

        # 2. Corrupted string without colon
        corrupt_str = b"d4:infod5abcdef"  # '5' without ':'
        info_hash2 = sportvideo_service.extract_info_hash(corrupt_str)
        self.assertEqual(info_hash2, "")

        # 3. Valid info dictionary computes correct SHA1
        valid_torrent = b"d8:announce10:http://test4:infod4:name4:test12:piece lengthi16384e6:pieces0:ee"
        valid_hash = sportvideo_service.extract_info_hash(valid_torrent)
        self.assertEqual(len(valid_hash), 40)

    @patch("httpx.AsyncClient.get")
    async def test_daddylive_active_domain_fallback_updates_timestamp(self, mock_get):
        """Verifies that DaddyLive fallback updates _last_domain_check to prevent request spam."""
        daddylive_api._last_domain_check = 0.0

        # Simulate check_status returning HTTP 500
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        domain = await daddylive_api.get_active_domain()
        self.assertEqual(domain, "dlive.sx")
        self.assertGreater(daddylive_api._last_domain_check, 0.0)

        # Immediate second call must hit the 6-hour cache without calling HTTP again
        mock_get.reset_mock()
        domain2 = await daddylive_api.get_active_domain()
        self.assertEqual(domain2, "dlive.sx")
        mock_get.assert_not_called()
