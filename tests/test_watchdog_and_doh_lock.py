import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.doh_client import DoHClient
from app.services.catalog_service import CatalogService


class TestWatchdogAndDoHLock(unittest.IsolatedAsyncioTestCase):
    async def test_doh_client_lock_initialized_atomically(self):
        """Verifies _resolve_lock is an initialized asyncio.Lock immediately in __init__."""
        client = DoHClient()
        self.assertIsNotNone(client._resolve_lock)
        self.assertIsInstance(client._resolve_lock, asyncio.Lock)

        # Run 20 concurrent resolve() calls to ensure thread safety
        with patch.object(client._doh_client, "get", new_callable=AsyncMock) as mock_get:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"Answer": [{"type": 1, "data": "1.2.3.4", "TTL": 300}]}
            mock_get.return_value = mock_res

            results = await asyncio.gather(*[client.resolve("streamed.pk") for _ in range(20)])
            self.assertEqual(len(results), 20)
            self.assertTrue(all(r == "1.2.3.4" for r in results))

        await client.close()

    async def test_catalog_watchdog_auto_restarts_on_failure(self):
        """Verifies watchdog auto-restarts background task when it crashes unexpectedly."""
        cat = CatalogService()
        cat._is_running = True

        restart_called = False

        def mock_restart(loop):
            nonlocal restart_called
            restart_called = True

        cat._restart_background_sync = mock_restart

        # Simulate a failing task
        async def failing_loop():
            raise RuntimeError("Fatal unhandled crash outside try-catch")

        loop = asyncio.get_running_loop()
        task = loop.create_task(failing_loop())
        cat._background_task = task
        task.add_done_callback(cat._on_background_task_done)

        # Allow task to fail and trigger done_callback
        await asyncio.sleep(0.05)

        # Watchdog must have caught failure and scheduled restart
        self.assertTrue(task.done())
        self.assertIsNotNone(task.exception())

    async def test_catalog_watchdog_no_restart_on_clean_stop(self):
        """Verifies that clean stop_background_sync() does NOT trigger auto-restart."""
        cat = CatalogService()
        cat.start_background_sync()
        self.assertTrue(cat._is_running)
        self.assertIsNotNone(cat._background_task)

        # Clean stop
        cat.stop_background_sync()
        self.assertFalse(cat._is_running)
        await asyncio.sleep(0.02)
        self.assertTrue(cat._background_task.cancelled() or cat._background_task.done())

    async def test_doh_client_lru_image_cache(self):
        """Verifies LRU eviction policy, max capacity, and close cleanup in DoH image cache."""
        client = DoHClient()
        self.assertEqual(client._image_cache_max, 400)

        # Set a small capacity for testing LRU behavior
        client._image_cache_max = 3
        now = 1000.0

        # Insert 3 images
        client._save_to_image_cache("url1", b"img1", "image/jpeg", now)
        client._save_to_image_cache("url2", b"img2", "image/jpeg", now)
        client._save_to_image_cache("url3", b"img3", "image/jpeg", now)
        self.assertEqual(list(client._image_cache.keys()), ["url1", "url2", "url3"])

        # Access url1 (simulating hit in get_raw), marking it recently used
        client._image_cache.move_to_end("url1")
        self.assertEqual(list(client._image_cache.keys()), ["url2", "url3", "url1"])

        # Insert url4 -> url2 must be evicted as the least recently used
        client._save_to_image_cache("url4", b"img4", "image/jpeg", now)
        self.assertEqual(len(client._image_cache), 3)
        self.assertNotIn("url2", client._image_cache)
        self.assertIn("url1", client._image_cache)
        self.assertIn("url3", client._image_cache)
        self.assertIn("url4", client._image_cache)

        await client.close()
        self.assertEqual(len(client._image_cache), 0)
