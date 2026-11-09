import asyncio
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.tennis_poster_service import tennis_poster_service, ATHLETES_DIR
from app.services.db_service import db_service


class TennisOfficialProvidersTestCase(unittest.IsolatedAsyncioTestCase):

    async def test_espn_search_cutout(self):
        """Tests Level 1 ESPN Search API for athlete cutouts."""
        res = await tennis_poster_service._search_espn("Jannik Sinner")
        self.assertIsNotNone(res)
        img, img_url = res
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.mode, "RGBA")
        self.assertIn("espncdn.com", img_url)

    async def test_atp_search_cutout(self):
        """Tests Level 2 ATP Tour directory via curl_cffi for gladiator/headshot cutout."""
        # Use a player whose official cutout is on atptour.com
        res = await tennis_poster_service._search_atp("Valentin Vacherot")
        self.assertIsNotNone(res)
        img, img_url = res
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.mode, "RGBA")
        self.assertIn("atptour.com", img_url)
        self.assertIn("va25", img_url)

    async def test_wta_search_cutout(self):
        """Tests Level 2 WTA Tour directory & photoresources CloudFront for torso cutout."""
        # Use an active Italian WTA player
        res = await tennis_poster_service._search_wta("Lucia Bronzetti")
        self.assertIsNotNone(res)
        img, img_url = res
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.mode, "RGBA")
        self.assertIn("photoresources.wtatennis.com", img_url)
        self.assertIn("322280", img_url)

    async def test_active_validation_and_auto_invalidation(self):
        """
        Tests that broken/expired URLs in database cache are actively detected
        and purged immediately (auto-invalidation), triggering live re-query.
        """
        slug = "test_broken_athlete"
        # Seed cache with a non-existent URL (dead port/invalid server)
        broken_url = "http://127.0.0.1:54321/expired_cutout.png"
        db_service.save_tennis_athlete(slug, "espn", broken_url, "male")

        # Verify it was saved in SQLite
        cached_before = db_service.get_tennis_athlete(slug)
        self.assertIsNotNone(cached_before)
        self.assertEqual(cached_before["image_url"], broken_url)

        # Call get_player_cutout: active validation should detect the dead URL and delete it
        img = await tennis_poster_service.get_player_cutout("Test Broken Athlete")

        # Verify the broken URL was purged from SQLite by auto-invalidation
        cached_after = db_service.get_tennis_athlete(slug)
        self.assertIsNone(cached_after)

    async def test_no_permanent_png_saved_to_disk(self):
        """
        Tests that athlete cutouts are kept strictly in volatile RAM
        and NO permanent .png file is written to ATHLETES_DIR.
        """
        slug = "jannik_sinner"
        cached_disk_file = ATHLETES_DIR / f"{slug}.png"
        if cached_disk_file.exists():
            cached_disk_file.unlink()

        img = await tennis_poster_service.get_player_cutout("Jannik Sinner")
        self.assertIsNotNone(img)

        # Crucial check: No PNG saved to disk!
        self.assertFalse(cached_disk_file.exists())

        # Check URL was saved to SQLite instead
        db_record = db_service.get_tennis_athlete(slug)
        self.assertIsNotNone(db_record)
        self.assertTrue(db_record["image_url"].startswith("http"))

    async def test_gender_isolation_no_wta_banner_for_male(self):
        """
        Tests strict gender isolation: ATP male matches never query WTA
        and never receive WTA Home-Share banners.
        """
        # Leo Borg should be found via ATP gladiator headshot, never WTA
        leo_img = await tennis_poster_service.get_player_cutout("Leo Borg", is_female=False)
        self.assertIsNotNone(leo_img)
        leo_record = db_service.get_tennis_athlete("leo_borg")
        self.assertIsNotNone(leo_record)
        self.assertEqual(leo_record["provider"], "atp")
        self.assertNotIn("home-share", leo_record["image_url"].lower())

        # An unknown male player must return None, never cross-fallback to WTA
        fake_slug = "test_nonexistent_male_athlete"
        tennis_poster_service._missing_athletes.discard(fake_slug)
        fake_img = await tennis_poster_service.get_player_cutout("Test Nonexistent Male Athlete", is_female=False)
        self.assertIsNone(fake_img)
        fake_record = db_service.get_tennis_athlete(fake_slug)
        self.assertIsNone(fake_record)

    async def test_auto_purge_contaminated_banner_url(self):
        """Tests that any historical contaminated Home-Share banner in SQLite is purged on read."""
        slug = "test_contaminated_player"
        banner_url = "https://photoresources.wtatennis.com/photo-resources/2025/05/06/837e6f32-a9b9-42e2-b3a8-057f37846755/Home-Share_1200x630.png"
        db_service.save_tennis_athlete(slug, "wta", banner_url, "female")

        # Verify saved
        self.assertIsNotNone(db_service.get_tennis_athlete(slug))

        # get_player_cutout should purge the contaminated banner
        await tennis_poster_service.get_player_cutout("Test Contaminated Player", is_female=True)
        self.assertIsNone(db_service.get_tennis_athlete(slug))

    def test_player_match_verification(self):
        """Tests that _is_player_match prevents false-positive surname collisions while supporting abbreviations."""
        is_match = tennis_poster_service._is_player_match
        self.assertTrue(is_match("Leo Borg", "Leo Borg"))
        self.assertTrue(is_match("Bolelli S.", "Simone Bolelli"))
        self.assertTrue(is_match("S. Bolelli", "Simone Bolelli"))
        self.assertTrue(is_match("Alex De Minaur", "Alex De Minaur"))
        self.assertTrue(is_match("De Minaur A.", "Alex De Minaur"))
        self.assertTrue(is_match("Struff J.", "Jan-Lennard Struff"))
        self.assertFalse(is_match("Test Fake Male", "Alastair Male"))
        self.assertFalse(is_match("Toby Samuel", "Blessing Samuel"))
        self.assertFalse(is_match("Jannik Sinner", "Carlos Alcaraz"))


if __name__ == "__main__":
    unittest.main()
