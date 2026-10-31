import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from app.config import TIMEZONE
from app.main import decode_config
from app.services.catalog_service import get_safe_timezone, catalog_service
from app.services.italian_epg_service import italian_epg_service
from app.services.stream_service import stream_service


class TestTimezoneAndFctv33Linking(unittest.TestCase):
    def test_timezone_resolution_and_defaults(self):
        """Verifies TIMEZONE setting and fallback behavior."""
        # Clean resolution of valid string
        tz_ny = get_safe_timezone("America/New_York")
        self.assertEqual(str(tz_ny), "America/New_York")

        # None / Empty string resolves to server default TIMEZONE
        tz_default = get_safe_timezone(None)
        self.assertEqual(str(tz_default), TIMEZONE)

        # "nuvio" or "stremio" passed as timezone safely falls back to TIMEZONE
        tz_nuvio = get_safe_timezone("nuvio")
        self.assertEqual(str(tz_nuvio), TIMEZONE)

        # Invalid string falls back to TIMEZONE
        tz_inv = get_safe_timezone("NonExistent/Mars_Time")
        self.assertEqual(str(tz_inv), TIMEZONE)

    def test_decode_config_variations(self):
        """Verifies that decode_config handles all past and present config string formats safely."""
        import base64

        # 1. 2-part format: 'https://ep.example.com|mypass'
        b64_2 = base64.b64encode(b"https://ep.example.com|mypass").decode("utf-8")
        ep_url, ep_pass, tz, client = decode_config(b64_2)
        self.assertEqual(ep_url, "https://ep.example.com")
        self.assertEqual(ep_pass, "mypass")
        self.assertEqual(tz, TIMEZONE)
        self.assertEqual(client, "stremio")

        # 2. 3-part format with client: 'https://ep.example.com|mypass|nuvio'
        b64_3 = base64.b64encode(b"https://ep.example.com|mypass|nuvio").decode("utf-8")
        ep_url, ep_pass, tz, client = decode_config(b64_3)
        self.assertEqual(ep_url, "https://ep.example.com")
        self.assertEqual(ep_pass, "mypass")
        self.assertEqual(tz, TIMEZONE)
        self.assertEqual(client, "nuvio")

        # 3. Legacy 4-part format: 'https://ep.example.com|mypass|Europe/London|nuvio'
        b64_4 = base64.b64encode(b"https://ep.example.com|mypass|Europe/London|nuvio").decode("utf-8")
        ep_url, ep_pass, tz, client = decode_config(b64_4)
        self.assertEqual(ep_url, "https://ep.example.com")
        self.assertEqual(ep_pass, "mypass")
        self.assertEqual(tz, TIMEZONE)
        self.assertEqual(client, "nuvio")

    def test_fctv33_id_inheritance_in_topdown(self):
        """Verifies that official Diretta match inherits _fctv33_id and _sport_type from FCTV33 candidate."""
        official_schedule = [
            {
                "id": "diretta-abc1234",
                "title": "Mihalikova T./Nicholls O. vs Errani S./Paolini J.",
                "category": "tennis",
                "_silo": "tennis",
                "date": 1793439000000,
                "teams": {
                    "home": {"name": "Mihalikova T./Nicholls O."},
                    "away": {"name": "Errani S./Paolini J."},
                },
            }
        ]
        provider_clusters = [
            {
                "id": "fctv33:2257586",
                "title": "Mihalikova T / Nicholls O vs Sara Errani/Jasmine Paolini",
                "category": "tennis",
                "_silo": "tennis",
                "date": 1793438100000,
                "_fctv33_id": "2257586",
                "_sport_type": 3,
                "sources": [
                    {
                        "source": "fctv33",
                        "id": "2257586",
                        "match_id": "2257586",
                        "sport_type": 3,
                        "name": "FCTV33 Live",
                    }
                ],
            }
        ]

        linked = catalog_service.link_official_schedule_to_provider_clusters(official_schedule, provider_clusters)
        self.assertEqual(len(linked), 1)
        m = linked[0]
        self.assertEqual(m["_matched_cluster_id"], "fctv33:2257586")
        self.assertEqual(m["_fctv33_id"], "2257586")
        self.assertEqual(m["_sport_type"], 3)
        self.assertEqual(len(m["sources"]), 1)

    def test_sky_epg_synopsis_extraction_for_tennis_sessions(self):
        """Verifies that generic url_comp falls through to eventSynopsis to extract WTA 500 Tokyo."""
        synopsis = "... 250 Guangzhou. - WTA 500 TOKYO Center Court - 2.a semifinale doppio"
        comp = italian_epg_service._extract_competition_from_synopsis(synopsis)
        self.assertEqual(comp, "WTA 500 Tokyo")


if __name__ == "__main__":
    unittest.main()
