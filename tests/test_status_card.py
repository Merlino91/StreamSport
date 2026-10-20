import time
import unittest
from app.services.stream_service import stream_service


class TestStatusCard(unittest.TestCase):
    def test_status_card_no_match(self):
        cards = stream_service.generate_status_card(None)
        self.assertEqual(len(cards), 1)
        self.assertIn("Nessun flusso", cards[0]["name"])
        self.assertTrue(cards[0].get("behaviorHints", {}).get("notWebReady"))

    def test_status_card_more_than_24h(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_2days",
            "title": "Inter vs Juventus",
            "date": now_ms + (48 * 3600 * 1000) + 10000,  # in >48 hours
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Inizia tra 2 giorni", cards[0]["name"])

    def test_status_card_between_1h_and_24h(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_3h",
            "title": "Inter vs Juventus",
            "date": now_ms + (3 * 3600 * 1000) + 10000,  # in >3 hours
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Inizia tra 3h", cards[0]["name"])

    def test_status_card_between_20m_and_60m(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_45m",
            "title": "Inter vs Juventus",
            "date": now_ms + (45 * 60 * 1000) + 10000,  # in >45 mins
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Inizia tra ~45 min", cards[0]["name"])

    def test_status_card_imminent_under_20m(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_10m",
            "title": "Inter vs Juventus",
            "date": now_ms + (10 * 60 * 1000),  # in 10 mins
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Inizio imminente", cards[0]["name"])

    def test_status_card_in_progress_live(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_live",
            "title": "Inter vs Juventus",
            "category": "football",
            "date": now_ms - (30 * 60 * 1000),  # started 30 mins ago
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Partita in corso (Nessun flusso)", cards[0]["name"])
        self.assertIn("Iniziata alle", cards[0]["title"])

    def test_status_card_concluded(self):
        now_ms = time.time() * 1000
        match = {
            "id": "match_ended",
            "title": "Inter vs Juventus",
            "category": "football",
            "date": now_ms - (300 * 60 * 1000),  # started 5 hours ago (> live_window)
        }
        cards = stream_service.generate_status_card(match, user_tz="Europe/Rome")
        self.assertEqual(len(cards), 1)
        self.assertIn("Evento Terminato", cards[0]["name"])


if __name__ == "__main__":
    unittest.main()
