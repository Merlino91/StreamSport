import unittest
from unittest.mock import MagicMock, patch
from starlette.datastructures import Headers, URL
from fastapi import Request

import app.main as main_mod
from app.main import get_base_url


class TestBaseURLDetection(unittest.TestCase):
    def _create_mock_request(self, headers_dict, url_str="http://127.0.0.1:7002/manifest.json"):
        req = MagicMock(spec=Request)
        req.headers = Headers(headers_dict)
        req.url = URL(url_str)
        return req

    def test_public_url_priority(self):
        # When PUBLIC_URL is configured, it MUST take absolute priority over any client headers
        req = self._create_mock_request({
            "x-forwarded-proto": "http",
            "x-forwarded-host": "attacker.evil.com",
            "host": "attacker.evil.com",
        })

        with patch.object(main_mod, "PUBLIC_URL", "https://streamsport.myvps.it"):
            base_url = get_base_url(req)
            self.assertEqual(base_url, "https://streamsport.myvps.it")

    def test_clean_reverse_proxy_headers(self):
        # When PUBLIC_URL is empty, valid reverse proxy headers (e.g. from Cloudflare) are respected
        req = self._create_mock_request({
            "x-forwarded-proto": "https",
            "x-forwarded-host": "addon.example.com",
            "host": "backend-internal:7002",
        })

        with patch.object(main_mod, "PUBLIC_URL", ""):
            base_url = get_base_url(req)
            self.assertEqual(base_url, "https://addon.example.com")

    def test_proto_sanitization(self):
        # Arbitrary proto headers like javascript: or gopher: must default to http
        req = self._create_mock_request({
            "x-forwarded-proto": "javascript",
            "host": "addon.example.com",
        })

        with patch.object(main_mod, "PUBLIC_URL", ""):
            base_url = get_base_url(req)
            self.assertEqual(base_url, "http://addon.example.com")

    def test_malicious_host_rejected(self):
        # Host headers with path injection, newlines, or special characters must be rejected
        req = self._create_mock_request(
            {
                "x-forwarded-proto": "https",
                "x-forwarded-host": "evil.com/malicious/path",
            },
            url_str="http://localhost:7002/manifest.json"
        )

        with patch.object(main_mod, "PUBLIC_URL", ""):
            base_url = get_base_url(req)
            # Rejects evil.com/malicious/path and falls back to request.url.netloc (localhost:7002)
            self.assertEqual(base_url, "https://localhost:7002")

    def test_multiple_comma_separated_proxy_hosts(self):
        # Multiple proxies chain: e.g. "addon.example.com, internal-lb"
        req = self._create_mock_request({
            "x-forwarded-proto": "https",
            "x-forwarded-host": "addon.example.com, internal-lb",
        })

        with patch.object(main_mod, "PUBLIC_URL", ""):
            base_url = get_base_url(req)
            self.assertEqual(base_url, "https://addon.example.com")


if __name__ == "__main__":
    unittest.main()
