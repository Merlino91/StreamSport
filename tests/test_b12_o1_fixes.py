import time
import unittest
from unittest.mock import patch, MagicMock
from app.services.catalog_service import get_safe_timezone, catalog_service
from app.services.tvvoo_service import LiveManifestManager, LiveStreamSession, tvvoo_service


class TestB12O1Fixes(unittest.IsolatedAsyncioTestCase):
    def test_get_safe_timezone_and_format_event_date(self):
        """Verifies safe timezone resolution and graceful fallback on invalid user input."""
        # 1. Valid timezone
        tz_ny = get_safe_timezone("America/New_York")
        self.assertEqual(str(tz_ny), "America/New_York")

        # 2. None or empty string defaults to Europe/Rome
        tz_default = get_safe_timezone(None)
        self.assertEqual(str(tz_default), "Europe/Rome")
        self.assertEqual(str(get_safe_timezone("")), "Europe/Rome")

        # 3. Invalid/malicious timezone safely falls back to Europe/Rome without crash
        tz_invalid = get_safe_timezone("Invalid/NonExistent_Zone")
        self.assertEqual(str(tz_invalid), "Europe/Rome")

        # 4. format_event_date with invalid timezone does not throw and produces localized output
        epoch_ms = 1770000000000  # valid timestamp in ms
        formatted = catalog_service.format_event_date(epoch_ms, user_tz="Malicious/Tz")
        self.assertIsInstance(formatted, str)
        self.assertTrue(len(formatted) > 5)

    def test_live_manifest_manager_lru_eviction(self):
        """Verifies that LiveManifestManager enforces a strict LRU cap when sessions exceed limit."""
        manager = LiveManifestManager(tvvoo_service)
        # Set small cap for testing
        manager.MAX_SESSIONS = 5

        # Populate with 7 sessions with distinct timestamps
        base_time = time.time()
        for idx in range(7):
            key = f"https://vavoo.to/play/ch_{idx}:default"
            manager.sessions[key] = LiveStreamSession(
                play_url=f"https://vavoo.to/play/ch_{idx}",
                client_ip=None,
                stream_url="https://edge.vavoo.to/live/index.m3u8",
                base_url="https://edge.vavoo.to/live/",
                valid_until=base_time + 1000,
                last_fetched_at=base_time + (idx * 10),  # ch_0 is oldest, ch_6 is newest
            )

        # Trigger cleanup
        manager.cleanup()

        # Must have capped to MAX_SESSIONS = 5
        self.assertEqual(len(manager.sessions), 5)
        # Oldest sessions (ch_0 and ch_1) must have been evicted
        self.assertNotIn("https://vavoo.to/play/ch_0:default", manager.sessions)
        self.assertNotIn("https://vavoo.to/play/ch_1:default", manager.sessions)
        # Newest sessions (ch_2 .. ch_6) must be present
        self.assertIn("https://vavoo.to/play/ch_5:default", manager.sessions)
        self.assertIn("https://vavoo.to/play/ch_6:default", manager.sessions)
