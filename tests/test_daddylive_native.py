import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.daddylive_api import daddylive_api, DaddyLiveAPI
from app.services.stream_service import stream_service
from app.services.italian_epg_service import italian_epg_service


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<body>
<section>
    <div class="schedule__dayTitle">Monday 02nd Nov 2026 - Schedule Time UK GMT</div>
    <!-- 1. Excluded Category: TV Shows -->
    <div class="schedule__category">
        <div class="card__meta">TV Shows 📺</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="14:00">14:00</span>
            <span class="schedule__eventTitle">Big Brother Live 24/7</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=999">Channel 999</a>
            </div>
        </div>
    </div>
    <!-- 2. Excluded Category: Upcoming Events -->
    <div class="schedule__category">
        <div class="card__meta">Upcoming Events</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="15:00">15:00</span>
            <span class="schedule__eventTitle">Duplicate Upcoming Event</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=998">Channel 998</a>
            </div>
        </div>
    </div>
    <!-- 3. Preserved Sport: Tennis WTA with is_women=True -->
    <div class="schedule__category">
        <div class="card__meta">Tennis 🎾 WTA - Singles: Hong Kong (Hong Kong)</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="12:30">12:30</span>
            <span class="schedule__eventTitle">Tennis 🎾 WTA - Singles: 🇮🇹 Jasmine Paolini vs 🇺🇸 Coco Gauff</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=576">Sky Sport Tennis Italy</a>
                <a href="/stream/stream-1050.php">Court 1 HD</a>
            </div>
        </div>
    </div>
    <!-- 4. Preserved Sport: College Sports (Men's College Basketball) -->
    <div class="schedule__category">
        <div class="card__meta">Men's College Basketball 🏀</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="20:00">20:00</span>
            <span class="schedule__eventTitle">Duke Blue Devils vs North Carolina Tar Heels</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=301">ESPN USA</a>
            </div>
        </div>
    </div>
    <!-- 5. Preserved Sport: Women's College Soccer with is_women=True -->
    <div class="schedule__category">
        <div class="card__meta">Women's College Soccer ⚽</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="21:15">21:15</span>
            <span class="schedule__eventTitle">Florida State vs UCLA</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=302">ACC Network</a>
            </div>
        </div>
    </div>
    <!-- 6. Preserved Sport: Italian Serie A Football -->
    <div class="schedule__category">
        <div class="card__meta">All Soccer Events ⚽</div>
        <div class="schedule__event">
            <span class="schedule__time" data-time="19:45">19:45</span>
            <span class="schedule__eventTitle">Serie A : Inter vs Juventus</span>
            <div class="schedule__channels">
                <a href="/watch.php?id=870">Sky Sport Calcio Italy</a>
                <a href="/watch.php?id=877">DAZN ZONA Italy</a>
            </div>
        </div>
    </div>
</section>
</body>
</html>
"""


class TestDaddyLiveNative(unittest.IsolatedAsyncioTestCase):

    def test_parse_html_schedule(self):
        """Verifies native HTML schedule parser accurately filters noise, captures college and tennis, and sets is_women."""
        matches = daddylive_api.parse_html_schedule(SAMPLE_HTML)

        # 4 valid matches (TV Shows and Upcoming Events must be discarded)
        self.assertEqual(len(matches), 4)

        titles = [m["title"] for m in matches]
        # 1. TV show and upcoming must be absent
        self.assertNotIn("Big Brother Live 24/7", titles)
        self.assertNotIn("Duplicate Upcoming Event", titles)

        # 2. Tennis WTA match: check teams, silo, and is_women flag
        wta_match = next(m for m in matches if "Paolini" in m["title"])
        self.assertEqual(wta_match["_silo"], "tennis")
        self.assertTrue(wta_match.get("is_women"))
        self.assertEqual(wta_match["teams"]["home"]["name"], "Jasmine Paolini")
        self.assertEqual(wta_match["teams"]["away"]["name"], "Coco Gauff")
        self.assertEqual(len(wta_match["sources"]), 2)
        self.assertEqual(wta_match["sources"][0]["id"], "576")
        self.assertEqual(wta_match["sources"][1]["id"], "1050")

        # 3. Men's College Basketball: kept in memory
        cbb_match = next(m for m in matches if "Duke" in m["title"])
        self.assertEqual(cbb_match["_silo"], "basketball")
        self.assertFalse(cbb_match.get("is_women", False))
        self.assertEqual(cbb_match["sources"][0]["id"], "301")

        # 4. Women's College Soccer: kept in memory with is_women=True
        wcs_match = next(m for m in matches if "Florida State" in m["title"])
        self.assertEqual(wcs_match["_silo"], "football")
        self.assertTrue(wcs_match.get("is_women"))

        # 5. Serie A football: Inter vs Juventus
        serie_a = next(m for m in matches if "Inter vs Juventus" in m["title"])
        self.assertEqual(serie_a["_silo"], "football")
        self.assertEqual(len(serie_a["sources"]), 2)
        ch_ids = {s["id"] for s in serie_a["sources"]}
        self.assertIn("870", ch_ids)
        self.assertIn("877", ch_ids)

    def test_canonical_247_channels_mapping(self):
        """Verifies DaddyLive 24/7 channels mapping for Italian sports networks."""
        # Sky Sport Uno
        uno = daddylive_api.get_channel_streams("sky sport uno")
        self.assertEqual(len(uno), 3)  # Player 1, Player 2, Player 5
        self.assertEqual(uno[0]["id"], "461")
        self.assertEqual(uno[0]["player"], 1)
        self.assertEqual(uno[1]["player"], 2)
        self.assertEqual(uno[2]["player"], 5)

        # DAZN 1
        dazn = daddylive_api.get_channel_streams("dazn 1")
        self.assertEqual(len(dazn), 3)
        self.assertEqual(dazn[0]["id"], "877")

        # Sky Calcio 4 (LCN 254)
        c4 = daddylive_api.get_channel_streams("sky sport 254")
        self.assertEqual(len(c4), 3)
        self.assertEqual(c4[0]["id"], "874")

        # Rai Sport
        raisport = daddylive_api.get_channel_streams("rai sport")
        self.assertEqual(len(raisport), 3)
        self.assertEqual(raisport[0]["id"], "882")

        # Italia 1
        it1 = daddylive_api.get_channel_streams("italia 1")
        self.assertEqual(len(it1), 3)
        self.assertEqual(it1[0]["id"], "854")

    async def test_stream_service_multi_player_routing(self):
        """Verifies StreamService constructs separate player URLs for Player 1, 2, and 5 via EasyProxy."""
        fake_match = {
            "id": "dlhd:test:multi",
            "title": "Inter vs Milan",
            "category": "football",
            "_silo": "football",
            "date": int(time.time() * 1000),
            "sources": [
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 1]", "player": 1},
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 2]", "player": 2},
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 5]", "player": 5},
            ],
            "_provider": "dlhd",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_match)

        streams = await stream_service.get_streams_for_event(
            "dlhd:test:multi",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP",
        )

        self.assertEqual(len(streams), 3)

        # Player 1: watch.php
        p1 = streams[0]
        self.assertIn("watch.php%3Fid%3D877", p1["url"])
        self.assertIn("Fonte: DaddyLive", p1["title"])

        # Player 2: /cast/stream-877.php
        p2 = streams[1]
        self.assertIn("cast%2Fstream-877.php", p2["url"])
        self.assertIn("Player 2 - Backup", p2["title"])

        # Player 5: /casting/stream-877.php
        p5 = streams[2]
        self.assertIn("casting%2Fstream-877.php", p5["url"])
        self.assertIn("Player 5 - Backup", p5["title"])


if __name__ == "__main__":
    unittest.main()
