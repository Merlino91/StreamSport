import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.doh_client import doh_client, is_ip_private_or_reserved, parse_ip_safe


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
        safe, _ = doh_client.is_safe_image_target("file:///etc/passwd")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("gopher://127.0.0.1:70")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("ftp://127.0.0.1/test")
        self.assertFalse(safe)

    def test_is_safe_image_target_private_hosts(self):
        safe, _ = doh_client.is_safe_image_target("http://localhost/admin")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://127.0.0.1:8000/api")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://172.17.0.2:9000/")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://2130706433/test")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://server.local/")
        self.assertFalse(safe)

        safe, _ = doh_client.is_safe_image_target("http://app.internal/")
        self.assertFalse(safe)

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


if __name__ == "__main__":
    unittest.main()
