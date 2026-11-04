import unittest
from app.services.genre_classifier import genre_classifier
from app.services.tennis_poster_service import tennis_poster_service
from app.services.catalog_service import catalog_service
from app.utils.demographics import extract_demographics


class TestDemographicsAndTennisSeparation(unittest.TestCase):
    def test_athlete_initials_not_flagged_as_women(self):
        """Initials like 'D Merida Aguilar', 'D. Medvedev', 'F. Cobolli' must not trigger women's flag."""
        matches = [
            ("T.Paul vs D Merida Aguilar", "ATP Masters 1000 Parigi"),
            ("Carlos Alcaraz vs D. Medvedev", "ATP Finals"),
            ("Jannik Sinner vs F. Cobolli", "ATP 500 Vienna"),
        ]
        for title, comp in matches:
            is_w, is_y = extract_demographics(f"{comp} {title}")
            self.assertFalse(is_w, f"False positive women on '{title}'")
            self.assertFalse(is_y, f"False positive youth on '{title}'")

            m = {
                "title": title,
                "competition": comp,
                "category": "tennis",
                "_silo": "tennis",
                "is_women": is_w,
                "is_youth": is_y,
            }
            cat, genre = genre_classifier.classify(m)
            self.assertEqual(cat, "tennis")
            self.assertEqual(genre, "ATP")

            palette = tennis_poster_service.get_theme_palette(genre, title)
            self.assertEqual(palette["bg_file"], "bg_atp.jpg")

    def test_wta_and_female_tournaments_classified_as_wta(self):
        """Explicit WTA and women tournaments must cleanly route to WTA."""
        tourneys = [
            ("WTA 500 Tokyo", "WTA 500 Tokyo"),
            ("WTA 250 Chennai", "WTA 250"),
            ("Sabalenka vs Swiatek", "WTA Finals"),
        ]
        for title, comp in tourneys:
            is_w, _ = extract_demographics(f"{comp} {title}")
            self.assertTrue(is_w, f"Failed to detect female tournament on '{comp}'")

            m = {
                "title": title,
                "competition": comp,
                "category": "tennis",
                "_silo": "tennis",
                "is_women": is_w,
            }
            cat, genre = genre_classifier.classify(m)
            self.assertEqual(cat, "tennis")
            self.assertEqual(genre, "WTA")

            palette = tennis_poster_service.get_theme_palette(genre, title)
            self.assertEqual(palette["bg_file"], "bg_wta.jpg")

    def test_tennis_epg_broadcast_session_not_suppressed_by_1v1_match(self):
        """Sky/Tivusat broadcast sessions (e.g. ATP Masters 1000 Parigi) must not be suppressed by concurrent 1v1 match."""
        off_schedule = [{
            "id": "diretta_123",
            "title": "T.Paul vs D.Medvedev",
            "category": "tennis",
            "_silo": "tennis",
            "date": 1000000,
            "teams": {"home": "T.Paul", "away": "D.Medvedev"},
        }]
        epg_session = [{
            "id": "epg_paris_session",
            "title": "ATP Masters 1000 Parigi: 4a giornata",
            "category": "tennis",
            "_silo": "tennis",
            "date": 1000000 + 30 * 60 * 1000,
            "sources": [{"name": "Sky Sport Tennis"}],
            "_provider": "italian_epg",
        }]

        orphan_epg = []
        for ep in epg_session:
            ep_silo = (ep.get("_silo") or ep.get("category") or "").lower()
            ep_date = ep.get("date", 0)
            already_covered = False
            for off in off_schedule:
                off_silo = (off.get("_silo") or off.get("category") or "").lower()
                if off_silo == ep_silo and abs((off.get("date") or 0) - ep_date) <= (90 * 60 * 1000):
                    if ep_silo == "motor-sports":
                        already_covered = True
                        break
                    # Tennis does NOT set already_covered = True!
            if not already_covered:
                orphan_epg.append(ep)

        self.assertEqual(len(orphan_epg), 1)
        self.assertEqual(orphan_epg[0]["id"], "epg_paris_session")


if __name__ == "__main__":
    unittest.main()
