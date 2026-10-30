import base64
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.config import CATALOG_DEFINITIONS, NUVIO_CATALOG_DEFINITIONS, NUVIO_CATALOG_LOOKUP
from app.main import app, decode_config, build_manifest


client = TestClient(app)


class TestNuvioAndStremioCatalogs(unittest.TestCase):
    def test_nuvio_catalogs_definitions_integrity(self):
        """Verifies that all 56 Nuvio catalogs have unique IDs, proper parents, and no genre filters."""
        self.assertEqual(len(NUVIO_CATALOG_DEFINITIONS), 56)
        self.assertEqual(len(NUVIO_CATALOG_LOOKUP), 56)

        self.assertIn("nuvio_calcio_estero_champions_league_femminile", NUVIO_CATALOG_LOOKUP)
        parent, genre = NUVIO_CATALOG_LOOKUP["nuvio_calcio_estero_champions_league_femminile"]
        self.assertEqual(parent, "calcio_estero")
        self.assertEqual(genre, "Champions League Femminile")

        all_ids = [c["id"] for c in NUVIO_CATALOG_DEFINITIONS]
        self.assertEqual(len(all_ids), len(set(all_ids)), "Nuvio catalog IDs must be 100% unique!")

        for cat in NUVIO_CATALOG_DEFINITIONS:
            self.assertTrue(cat["id"].startswith("nuvio_"))
            self.assertIn(cat["id"], NUVIO_CATALOG_LOOKUP)
            parent, genre = NUVIO_CATALOG_LOOKUP[cat["id"]]
            self.assertTrue(len(parent) > 0)
            self.assertTrue(len(genre) > 0)

    def test_decode_config_backwards_compatibility(self):
        """Verifies that 3-part config defaults to stremio, while 4-part can specify nuvio."""
        # 1. Legacy 3-part
        raw3 = "https://ep.example.com|pass123|Europe/Rome"
        b64_3 = base64.b64encode(raw3.encode("utf-8")).decode("utf-8")
        ep_url, ep_pass, tz, client_target = decode_config(b64_3)
        self.assertEqual(ep_url, "https://ep.example.com")
        self.assertEqual(ep_pass, "pass123")
        self.assertEqual(tz, "Europe/Rome")
        self.assertEqual(client_target, "stremio")

        # 2. 4-part Nuvio
        raw4 = "https://ep.example.com|pass123|Europe/Rome|nuvio"
        b64_4 = base64.b64encode(raw4.encode("utf-8")).decode("utf-8")
        ep_url4, ep_pass4, tz4, client_target4 = decode_config(b64_4)
        self.assertEqual(client_target4, "nuvio")

    def test_stremio_manifest_structure(self):
        """Verifies Stremio manifest contains 11 thematic catalogs with 'genre' extra parameter."""
        manifest = build_manifest(configured=True, client="stremio")
        self.assertEqual(manifest["id"], "com.streamsport.addon")
        self.assertEqual(manifest["name"], "StreamSport")
        self.assertEqual(len(manifest["catalogs"]), 11)

        for cat in manifest["catalogs"]:
            genre_extra = next((e for e in cat["extra"] if e["name"] == "genre"), None)
            self.assertIsNotNone(genre_extra, f"Catalog {cat['id']} missing genre extra!")
            self.assertTrue(len(genre_extra["options"]) > 0)

    def test_nuvio_manifest_structure(self):
        """Verifies Nuvio manifest contains 56 flat catalogs with unique IDs and NO 'genre' extra parameter."""
        manifest = build_manifest(configured=True, client="nuvio")
        self.assertEqual(manifest["id"], "com.streamsport.addon.nuvio")
        self.assertEqual(manifest["name"], "StreamSport (Nuvio)")
        self.assertEqual(len(manifest["catalogs"]), 56)

        for cat in manifest["catalogs"]:
            self.assertTrue(cat["id"].startswith("nuvio_"))
            genre_extra = next((e for e in cat["extra"] if e["name"] == "genre"), None)
            self.assertIsNone(genre_extra, f"Nuvio catalog {cat['id']} should not have a genre extra!")

    def test_manifest_endpoints_stremio_vs_nuvio(self):
        """Tests HTTP endpoints for unconfigured and configured manifests for both clients."""
        # 1. Unconfigured Stremio
        res_stremio = client.get("/manifest.json")
        self.assertEqual(res_stremio.status_code, 200)
        self.assertEqual(len(res_stremio.json()["catalogs"]), 11)

        # 2. Unconfigured Nuvio
        res_nuvio = client.get("/nuvio/manifest.json")
        self.assertEqual(res_nuvio.status_code, 200)
        self.assertEqual(len(res_nuvio.json()["catalogs"]), 56)

        # 3. Configured Nuvio via base64
        raw_nuvio = "https://ep.example.com|pass|Europe/Rome|nuvio"
        b64_nuvio = base64.b64encode(raw_nuvio.encode("utf-8")).decode("utf-8")
        res_cfg_nuvio = client.get(f"/{b64_nuvio}/manifest.json")
        self.assertEqual(res_cfg_nuvio.status_code, 200)
        self.assertEqual(len(res_cfg_nuvio.json()["catalogs"]), 56)

        # 4. Configured Nuvio via dedicated path
        raw_stremio = "https://ep.example.com|pass|Europe/Rome"
        b64_stremio = base64.b64encode(raw_stremio.encode("utf-8")).decode("utf-8")
        res_cfg_nuvio_path = client.get(f"/{b64_stremio}/nuvio/manifest.json")
        self.assertEqual(res_cfg_nuvio_path.status_code, 200)
        self.assertEqual(len(res_cfg_nuvio_path.json()["catalogs"]), 56)

    def test_configure_page_generates_distinct_urls(self):
        """Verifies /configure generates visibly distinct URLs for Stremio vs Nuvio."""
        # 1. Stremio
        res_stremio = client.get("/configure?save=1&epUrl=https://ep.example.com&clientTarget=stremio")
        self.assertEqual(res_stremio.status_code, 200)
        html_stremio = res_stremio.text
        self.assertIn("/manifest.json", html_stremio)
        self.assertNotIn("/nuvio/manifest.json", html_stremio)

        # 2. Nuvio
        res_nuvio = client.get("/configure?save=1&epUrl=https://ep.example.com&clientTarget=nuvio")
        self.assertEqual(res_nuvio.status_code, 200)
        html_nuvio = res_nuvio.text
        self.assertIn("/nuvio/manifest.json", html_nuvio)

    def test_nuvio_subpath_routing_for_catalogs(self):
        """Verifies that /{config}/nuvio/catalog/... routes resolve cleanly."""
        raw_nuvio = "https://ep.example.com|pass|Europe/Rome|nuvio"
        b64_nuvio = base64.b64encode(raw_nuvio.encode("utf-8")).decode("utf-8")
        res = client.get(f"/{b64_nuvio}/nuvio/catalog/other/nuvio_calcio_italiano_serie_a.json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("metas", res.json())

    def test_nuvio_catalog_lookup_and_filtering(self):
        """Verifies that requesting a flat Nuvio catalog resolves correctly through catalog_service."""
        sample_nuvio_id = "nuvio_calcio_italiano_serie_a"
        res = client.get(f"/catalog/other/{sample_nuvio_id}.json")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("metas", data)
        self.assertIsInstance(data["metas"], list)

    def test_pagination_full_limit_stremio_and_nuvio(self):
        """Verifies both Stremio and Nuvio return full limit without 4-item cap on skip=0."""
        import asyncio
        from app.services.catalog_service import catalog_service

        fake_matches = [
            {
                "id": f"serie_a_match_{i}",
                "title": f"Team {i} vs Team {i+1}",
                "category": "football",
                "_catalog": "calcio_italiano",
                "_genre": "Serie A",
                "date": 2000000000000 + i * 1000,
                "sources": [{"id": f"stream_{i}", "name": f"Sky Sport {i}"}],
            }
            for i in range(10)
        ]

        async def run_check():
            with patch.object(catalog_service, "_cached_matches", fake_matches):
                # 1. Stremio: gets all 10 items (not capped at 4)
                stremio_page = await catalog_service.get_catalog(
                    catalog_id="calcio_italiano",
                    genre_filter="Serie A",
                    skip=0,
                    limit=50,
                    is_nuvio=False,
                )
                self.assertEqual(len(stremio_page), 10)

                # 2. Nuvio: also gets all 10 items without 4-item cap
                nuvio_page = await catalog_service.get_catalog(
                    catalog_id="nuvio_calcio_italiano_serie_a",
                    skip=0,
                    limit=50,
                    is_nuvio=True,
                )
                self.assertEqual(len(nuvio_page), 10)

        asyncio.run(run_check())


if __name__ == "__main__":
    unittest.main()
