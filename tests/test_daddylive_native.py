import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
        self.assertEqual(len(uno), 1)
        self.assertEqual(uno[0]["id"], "461")
        self.assertEqual(uno[0]["name"], "Sky Sport UNO Italy")

        # DAZN 1
        dazn = daddylive_api.get_channel_streams("dazn 1")
        self.assertEqual(len(dazn), 1)
        self.assertEqual(dazn[0]["id"], "877")

        # Sky Calcio 4 (LCN 254)
        c4 = daddylive_api.get_channel_streams("sky sport 254")
        self.assertEqual(len(c4), 1)
        self.assertEqual(c4[0]["id"], "874")

        # Rai Sport
        raisport = daddylive_api.get_channel_streams("rai sport")
        self.assertEqual(len(raisport), 1)
        self.assertEqual(raisport[0]["id"], "882")

        # Italia 1
        it1 = daddylive_api.get_channel_streams("italia 1")
        self.assertEqual(len(it1), 1)
        self.assertEqual(it1[0]["id"], "854")

    async def test_stream_service_auto_fallback_stream(self):
        """Verifies StreamService constructs 1 clean auto-fallback stream instead of exposing multiple player entries."""
        fake_match = {
            "id": "dlhd:test_dlhd_multi_p6",
            "title": "FakeAlphaFC vs FakeBetaFC",
            "category": "football",
            "_silo": "football",
            "date": int(time.time() * 1000),
            "sources": [
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 1]"},
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 2]"},
                {"source": "dlhd", "id": "877", "name": "DAZN 1 [Player 5]"},
            ],
            "_provider": "dlhd",
        }

        from app.services.catalog_service import catalog_service
        catalog_service._cached_matches.append(fake_match)

        # 1. With Player 6 active -> returns Direct stream + Auto-Fallback stream
        with patch.object(daddylive_api, "check_player6_direct_active", return_value=True):
            streams = await stream_service.get_streams_for_event(
                "dlhd:test_dlhd_multi_p6",
                ep_url="https://ep.bluccoj.com",
                ep_pass="Z3r0EP",
                base_url="https://streamsport.example.com",
                config="myconfig",
            )

            self.assertEqual(len(streams), 2)
            # Direct stream first
            s_direct = streams[0]
            self.assertEqual(s_direct["name"], "DAZN 1")
            self.assertIn("(Direct)", s_direct["title"])
            self.assertEqual(s_direct["url"], "https://streamsport.example.com/myconfig/daddylive/direct/877.m3u8")

            # EasyProxy Auto-Fallback stream second
            s_fallback = streams[1]
            self.assertEqual(s_fallback["name"], "DAZN 1")
            self.assertEqual(s_fallback["title"], "📡 Fonte: DaddyLive • Qualità: Live TV")
            self.assertEqual(s_fallback["url"], "https://streamsport.example.com/myconfig/daddylive/stream/877.m3u8")

        # 2. With Player 6 inactive -> returns only 1 clean Auto-Fallback stream
        with patch.object(daddylive_api, "check_player6_direct_active", return_value=False):
            streams_no_p6 = await stream_service.get_streams_for_event(
                "dlhd:test_dlhd_multi_p6",
                ep_url="https://ep.bluccoj.com",
                ep_pass="Z3r0EP",
                base_url="https://streamsport.example.com",
                config="myconfig",
            )
            self.assertEqual(len(streams_no_p6), 1)
            self.assertEqual(streams_no_p6[0]["title"], "📡 Fonte: DaddyLive • Qualità: Live TV")
            self.assertEqual(streams_no_p6[0]["url"], "https://streamsport.example.com/myconfig/daddylive/stream/877.m3u8")

    async def test_daddylive_cascade_resolution(self):
        """Verifies resolve_active_stream_url cascades across players on failure."""
        mock_client = AsyncMock()
        # Simulate Player 1 failing (502), Player 1 Alt failing (404), Player 2 succeeding (200)
        resp_502 = MagicMock(status_code=502)
        resp_404 = MagicMock(status_code=404)
        resp_200 = MagicMock(status_code=200)
        mock_client.get.side_effect = [resp_502, resp_404, resp_200]

        daddylive_api._active_player_cache.clear()
        res_url = await daddylive_api.resolve_active_stream_url(
            "877",
            ep_url="https://ep.bluccoj.com",
            ep_pass="Z3r0EP",
            client=mock_client,
        )

        self.assertIsNotNone(res_url)
        # Should have selected Player 2 (cast/stream-877.php)
        self.assertIn("cast%2Fstream-877.php", res_url)
        self.assertIn("api_password=Z3r0EP", res_url)

    def test_fastapi_daddylive_stream_endpoint(self):
        """Verifies /{config}/daddylive/stream/{channel_id}.m3u8 endpoint returns 307 redirect."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch.object(daddylive_api, "resolve_active_stream_url", return_value="https://ep.bluccoj.com/resolved.m3u8"):
            client = TestClient(app)
            # config: base64 of 'https://ep.bluccoj.com|Z3r0EP|nuvio'
            cfg = "aHR0cHM6Ly9lcC5ibHVjY29qLmNvbXxaM3IwRVB8bnV2aW8="
            resp = client.get(f"/{cfg}/daddylive/stream/877.m3u8", follow_redirects=False)
            self.assertEqual(resp.status_code, 307)
            self.assertEqual(resp.headers["location"], "https://ep.bluccoj.com/resolved.m3u8")

    def test_fastapi_daddylive_direct_endpoint(self):
        """Verifies /{config}/daddylive/direct/{channel_id}.m3u8 returns 307 redirect to wideiptv CDN."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch.object(daddylive_api, "resolve_player6_direct", return_value="https://cdn.wideiptv.top/ZonaDAZN/index.m3u8?token=xyz"):
            client = TestClient(app)
            cfg = "aHR0cHM6Ly9lcC5ibHVjY29qLmNvbXxaM3IwRVB8bnV2aW8="
            resp = client.get(f"/{cfg}/daddylive/direct/877.m3u8", follow_redirects=False)
            self.assertEqual(resp.status_code, 307)
            self.assertEqual(resp.headers["location"], "https://cdn.wideiptv.top/ZonaDAZN/index.m3u8?token=xyz")

        # When direct resolution fails -> must return 502 without fallback to EasyProxy
        with patch.object(daddylive_api, "resolve_player6_direct", return_value=None):
            client = TestClient(app)
            cfg = "aHR0cHM6Ly9lcC5ibHVjY29qLmNvbXxaM3IwRVB8bnV2aW8="
            resp = client.get(f"/{cfg}/daddylive/direct/877.m3u8", follow_redirects=False)
            self.assertEqual(resp.status_code, 502)

    def test_verify_wideiptv_stream_active_ts(self):
        """Verifies _verify_wideiptv_stream returns True for valid HLS stream with MPEG-TS sync byte 0x47."""
        import httpx
        from unittest.mock import AsyncMock

        master_content = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000\nindex-0.m3u8?token=xyz\n"
        media_content = "#EXTM3U\n#EXTINF:4.000,\nseg-0.ts\n"
        # 188-byte valid MPEG-TS packet starting with 0x47
        ts_packet = b"\x47" + (b"\x10" * 187)

        async def _run():
            mock_client = AsyncMock(spec=httpx.AsyncClient)
            
            async def _fake_get(url, **kwargs):
                resp = MagicMock()
                if "index.m3u8" in url:
                    resp.status_code = 200
                    resp.text = master_content
                elif "index-0.m3u8" in url:
                    resp.status_code = 200
                    resp.text = media_content
                elif "seg-0.ts" in url:
                    resp.status_code = 206
                    resp.content = ts_packet
                else:
                    resp.status_code = 404
                    resp.content = b""
                return resp

            mock_client.get = AsyncMock(side_effect=_fake_get)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            with patch("httpx.AsyncClient", return_value=mock_client):
                res = await daddylive_api._verify_wideiptv_stream("https://cdn.wideiptv.top/channel/index.m3u8")
                self.assertTrue(res)

        asyncio.run(_run())

    def test_verify_wideiptv_stream_filters_404_and_cartel(self):
        """Verifies _verify_wideiptv_stream returns False for 404 or non-TS cartel streams."""
        import httpx
        from unittest.mock import AsyncMock

        async def _run():
            # Case A: Master playlist 404
            mock_client_404 = AsyncMock(spec=httpx.AsyncClient)
            resp_404 = MagicMock()
            resp_404.status_code = 404
            mock_client_404.get = AsyncMock(return_value=resp_404)
            mock_client_404.__aenter__.return_value = mock_client_404
            mock_client_404.__aexit__.return_value = None

            with patch("httpx.AsyncClient", return_value=mock_client_404):
                res_404 = await daddylive_api._verify_wideiptv_stream("https://cdn.wideiptv.top/dead/index.m3u8")
                self.assertFalse(res_404)

            # Case B: Segment returns HTML error / cartel without 0x47 sync byte
            mock_client_cartel = AsyncMock(spec=httpx.AsyncClient)
            
            async def _fake_get_cartel(url, **kwargs):
                resp = MagicMock()
                if "index.m3u8" in url:
                    resp.status_code = 200
                    resp.text = "#EXTM3U\nseg.ts\n"
                elif "seg.ts" in url:
                    resp.status_code = 200
                    resp.content = b"<html>Cartel / Offline</html>"
                return resp

            mock_client_cartel.get = AsyncMock(side_effect=_fake_get_cartel)
            mock_client_cartel.__aenter__.return_value = mock_client_cartel
            mock_client_cartel.__aexit__.return_value = None

            with patch("httpx.AsyncClient", return_value=mock_client_cartel):
                res_cartel = await daddylive_api._verify_wideiptv_stream("https://cdn.wideiptv.top/cartel/index.m3u8")
                self.assertFalse(res_cartel)

        asyncio.run(_run())

    def test_resolve_player6_direct_drops_unverified_stream(self):
        """Verifies resolve_player6_direct returns None when the deep probe fails."""
        async def _run():
            daddylive_api._player6_cache.clear()
            p1_html = b'<iframe src="https://wideiptv.top/player/SkySportMaxIT"></iframe>'
            p6_html = b'streamUrl: "https:\\/\\/cdn.wideiptv.top\\/SkySportMaxIT\\/index.m3u8?token=xyz"'

            with patch.object(daddylive_api, "get_active_domain", return_value="thedaddy.pk"), \
                 patch("app.services.doh_client.doh_client.get_raw", side_effect=[(p1_html, "text/html"), (p6_html, "text/html")]), \
                 patch.object(daddylive_api, "_verify_wideiptv_stream", return_value=False):
                res = await daddylive_api.resolve_player6_direct("460")
                self.assertIsNone(res)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
