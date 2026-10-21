import unittest
import asyncio
from unittest.mock import patch, MagicMock
from app.services.mandrakodi_service import mandrakodi_service, xor_decrypt

class TestMandrakodiService(unittest.IsolatedAsyncioTestCase):

    def test_xor_decrypt(self):
        # Encrypt "hello world" with key "my_secret_key"
        key = b"my_secret_key"
        plain = b"hello world"
        cipher = bytearray([b ^ key[i % len(key)] for i, b in enumerate(plain)])
        import base64
        b64 = base64.b64encode(cipher).decode()
        dec = xor_decrypt(b64)
        self.assertEqual(dec, "hello world")

    async def test_get_matches_structure(self):
        # Test that get_matches returns properly structured matches
        matches = await mandrakodi_service.get_matches(force=True)
        self.assertIsInstance(matches, list)
        self.assertGreater(len(matches), 0)

        for m in matches:
            self.assertIn("id", m)
            self.assertIn("title", m)
            self.assertIn("category", m)
            self.assertIn("_silo", m)
            self.assertIn("sources", m)
            self.assertEqual(m.get("_provider"), "mandrakodi")
            self.assertGreater(len(m["sources"]), 0)
            src = m["sources"][0]
            self.assertEqual(src.get("provider"), "mandrakodi")
            self.assertIn(src.get("source"), ("amstaff", "acestream", "socceron", "direct"))

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

if __name__ == "__main__":
    unittest.main()
