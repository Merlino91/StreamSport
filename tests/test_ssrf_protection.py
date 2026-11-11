import asyncio
import ssl
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.doh_client import DoHAsyncHTTPTransport, doh_client, is_ip_private_or_reserved, parse_ip_safe


class TestSSRFProtection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_parse_ip_variants(self):
        # Standard IPv4 and IPv6
        self.assertEqual(str(parse_ip_safe("127.0.0.1")), "127.0.0.1")
        self.assertEqual(str(parse_ip_safe("::1")), "::1")
        self.assertEqual(str(parse_ip_safe("169.254.169.254")), "169.254.169.254")

        # Decimal notation (127.0.0.1 -> 2130706433)
        self.assertEqual(str(parse_ip_safe("2130706433")), "127.0.0.1")

        # Octal / hex notation
        self.assertEqual(str(parse_ip_safe("0177.0.0.1")), "127.0.0.1")
        self.assertEqual(str(parse_ip_safe("0x7f000001")), "127.0.0.1")

        # Non-IP hostnames
        self.assertIsNone(parse_ip_safe("www.thesportsdb.com"))
        self.assertIsNone(parse_ip_safe("streamed.pk"))

    def test_is_ip_private_or_reserved(self):
        # Loopback
        self.assertTrue(is_ip_private_or_reserved("127.0.0.1"))
        self.assertTrue(is_ip_private_or_reserved("127.0.0.2"))
        self.assertTrue(is_ip_private_or_reserved("::1"))

        # Cloud metadata / link-local
        self.assertTrue(is_ip_private_or_reserved("169.254.169.254"))
        self.assertTrue(is_ip_private_or_reserved("fe80::1"))

        # RFC1918 Private ranges
        self.assertTrue(is_ip_private_or_reserved("10.0.0.1"))
        self.assertTrue(is_ip_private_or_reserved("172.17.0.2"))
        self.assertTrue(is_ip_private_or_reserved("192.168.1.100"))

        # Unspecified / 0.0.0.0
        self.assertTrue(is_ip_private_or_reserved("0.0.0.0"))

        # Public IPs
        self.assertFalse(is_ip_private_or_reserved("8.8.8.8"))
        self.assertFalse(is_ip_private_or_reserved("1.1.1.1"))
        self.assertFalse(is_ip_private_or_reserved("104.21.5.12"))

    def test_is_safe_image_target_schemes(self):
        async def check():
            for url in (
                "file:///etc/passwd",
                "gopher://127.0.0.1:70",
                "ftp://127.0.0.1/test",
            ):
                safe, _ = await doh_client.is_safe_image_target_async(url)
                self.assertFalse(safe)

        asyncio.run(check())

    def test_is_safe_image_target_private_hosts(self):
        async def check():
            for url in (
                "http://localhost/admin",
                "http://127.0.0.1:8000/api",
                "http://169.254.169.254/latest/meta-data/",
                "http://172.17.0.2:9000/",
                "http://2130706433/test",
                "http://server.local/",
                "http://app.internal/",
            ):
                safe, _ = await doh_client.is_safe_image_target_async(url)
                self.assertFalse(safe)

            safe_pub, _ = await doh_client.is_safe_image_target_async("https://images.unsplash.com/photo-123.jpg")
            self.assertTrue(safe_pub)

        asyncio.run(check())

    def test_api_endpoint_blocks_ssrf(self):
        # 1. Loopback attack
        res = self.client.get("/image-proxy?url=http://127.0.0.1:8000/metrics")
        self.assertEqual(res.status_code, 403)
        self.assertIn("Access denied", res.json()["detail"])

        # 2. Localhost attack
        res = self.client.get("/image-proxy?url=http://localhost:5000/")
        self.assertEqual(res.status_code, 403)

        # 3. Cloud metadata attack
        res = self.client.get("/image-proxy?url=http://169.254.169.254/latest/meta-data/")
        self.assertEqual(res.status_code, 403)

        # 4. Docker internal network attack
        res = self.client.get("/image-proxy?url=http://172.17.0.5:8080/")
        self.assertEqual(res.status_code, 403)

        # 5. File protocol attack
        res = self.client.get("/image-proxy?url=file:///etc/shadow")
        self.assertEqual(res.status_code, 403)

        # 6. Missing url parameter
        res = self.client.get("/image-proxy?url=")
        self.assertEqual(res.status_code, 400)

    @patch.object(doh_client, "get_raw")
    def test_non_image_content_type_rejected(self, mock_get_raw):
        import asyncio

        # If upstream returns HTML or JSON instead of image, proxy_image must reject it
        mock_get_raw.return_value = (b'{"admin_token": "secret123"}', "application/json")
        content, ctype = asyncio.run(doh_client.proxy_image("https://example.com/api/data"))
        self.assertIsNone(content)
        self.assertIsNone(ctype)

    def test_doh_client_ssl_verification_isolation(self):
        # 1. Verify both DoH resolver client and default upstream/image client enforce strict TLS verification
        self.assertIsNotNone(doh_client._doh_client)
        doh_ssl_ctx = doh_client._doh_client._transport._pool._ssl_context
        self.assertIsNotNone(doh_ssl_ctx)
        self.assertEqual(doh_ssl_ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(doh_ssl_ctx.check_hostname)

        self.assertIsInstance(doh_client._client._transport, DoHAsyncHTTPTransport)
        upstream_ssl_ctx = doh_client._client._transport._pool._ssl_context
        self.assertIsNotNone(upstream_ssl_ctx)
        self.assertEqual(upstream_ssl_ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(upstream_ssl_ctx.check_hostname)

        # 2. Test live DoH resolution through verified TLS endpoint
        resolved = asyncio.run(doh_client.resolve("google.com"))
        self.assertIsNotNone(resolved)
        self.assertTrue(len(resolved.split(".")) == 4)

    def test_cors_headers_wildcard_no_credentials(self):
        """Verifies compliant CORS headers: wildcard origin without conflicting credentials."""
        res = self.client.get("/health", headers={"Origin": "https://web.stremio.com"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("access-control-allow-origin"), "*")
        self.assertNotIn("access-control-allow-credentials", res.headers)


if __name__ == "__main__":
    unittest.main()
