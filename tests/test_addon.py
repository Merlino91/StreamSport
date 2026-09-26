import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import base64
import time
from fastapi.testclient import TestClient
from app.main import app, decode_config, get_base_url, extract_extra_params
from app.config import CATALOG_DEFINITIONS, CATALOG_ID, CATALOG_TYPE, SPORT_GENRES
from app.services.genre_classifier import genre_classifier
from app.services.italian_resolver import italian_resolver

client = TestClient(app)

class StreamSportTestCase(unittest.TestCase):

    def test_decode_config_pipe(self):
        raw = "https://ep.example.com|mypass|Europe/Rome"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        ep_url, ep_pass, tz = decode_config(encoded)
        self.assertEqual(ep_url, "https://ep.example.com")
        self.assertEqual(ep_pass, "mypass")
        self.assertEqual(tz, "Europe/Rome")

    def test_decode_config_empty(self):
        ep_url, ep_pass, tz = decode_config(None)
        self.assertIsNone(ep_url)
        self.assertIsNone(ep_pass)
        self.assertEqual(tz, "Europe/Rome")

    def test_unconfigured_manifest(self):
        response = client.get("/manifest.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "com.streamsport.addon")
        self.assertTrue(data["behaviorHints"]["configurationRequired"])
        self.assertEqual(len(data["catalogs"]), len(CATALOG_DEFINITIONS))
        
        catalog_ids = [c["id"] for c in data["catalogs"]]
        self.assertIn("calcio_italiano", catalog_ids)
        self.assertIn("calcio_estero", catalog_ids)
        self.assertIn("tennis", catalog_ids)
        self.assertIn("motori", catalog_ids)
        self.assertIn("basket", catalog_ids)
        self.assertIn("volley", catalog_ids)
        self.assertIn("football_americano", catalog_ids)
        self.assertIn("baseball", catalog_ids)
        self.assertIn("hockey", catalog_ids)
        self.assertIn("combattimento", catalog_ids)
        self.assertIn("altri_sport", catalog_ids)
        
        # Verify Tennis catalog genres
        tennis_cat = next((c for c in data["catalogs"] if c["id"] == "tennis"), None)
        self.assertIsNotNone(tennis_cat)
        tennis_genres = next((e["options"] for e in tennis_cat["extra"] if e["name"] == "genre"), [])
        self.assertIn("ATP", tennis_genres)
        self.assertIn("WTA", tennis_genres)
        self.assertIn("Coppa Davis e BJK Cup", tennis_genres)

        # Verify Basket catalog genres order: LBA Serie A right below NBA
        basket_cat = next((c for c in data["catalogs"] if c["id"] == "basket"), None)
        self.assertIsNotNone(basket_cat)
        basket_genres = next((e["options"] for e in basket_cat["extra"] if e["name"] == "genre"), [])
        self.assertEqual(basket_genres[0], "NBA")
        self.assertEqual(basket_genres[1], "LBA Serie A")

        # Verify Baseball catalog has NCAA College Baseball
        baseball_cat = next((c for c in data["catalogs"] if c["id"] == "baseball"), None)
        self.assertIsNotNone(baseball_cat)
        baseball_genres = next((e["options"] for e in baseball_cat["extra"] if e["name"] == "genre"), [])
        self.assertIn("NCAA College Baseball", baseball_genres)

    def test_configured_manifest(self):
        raw = "https://ep.example.com|pass|Europe/Rome"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        response = client.get(f"/{encoded}/manifest.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["behaviorHints"]["configurationRequired"])

    def test_genre_classifier_accuracy(self):
        test_cases = [
            ({"title": "Inter vs Milan", "category": "football", "teams": {"home": {"name": "Inter"}, "away": {"name": "Milan"}}}, ("calcio_italiano", "Serie A")),
            ({"title": "Palermo vs Bari", "category": "football", "teams": {"home": {"name": "Palermo"}, "away": {"name": "Bari"}}}, ("calcio_italiano", "Serie B")),
            ({"title": "Real Madrid vs Barcelona", "category": "football", "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}}}, ("calcio_estero", "La Liga")),
            ({"title": "Arsenal vs Liverpool", "category": "football", "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Liverpool"}}}, ("calcio_estero", "Premier League")),
            ({"title": "Formula 1 - Monza GP Race", "category": "motor-sports"}, ("motori", "Formula 1")),
            ({"title": "MotoGP Mugello", "category": "motor-sports"}, ("motori", "MotoGP e Superbike")),
            ({"title": "UFC 310 Main Card", "category": "fight"}, ("combattimento", "UFC")),
            ({"title": "WWE Monday Night Raw", "category": "fight"}, ("combattimento", "Wrestling e WWE")),
            ({"title": "Boston Celtics vs LA Lakers", "category": "basketball", "teams": {"home": {"name": "Celtics"}, "away": {"name": "Lakers"}}}, ("basket", "NBA")),
            ({"title": "Kansas City Chiefs vs San Francisco 49ers", "category": "american-football", "teams": {"home": {"name": "Chiefs"}, "away": {"name": "49ers"}}}, ("football_americano", "NFL")),
            ({"title": "Saskatchewan Roughriders at BC Lions", "category": "american-football"}, ("football_americano", "CFL e Altre Leghe")),
            ({"title": "Oregon Ducks - USC Trojans", "category": "american-football"}, ("football_americano", "NCAA College Football")),
            # Edge cases & other sports
            ({"title": "Brazil - Serie B: Criciuma vs Operaio PR", "category": "football"}, ("calcio_estero", "Americhe e Leghe Extra-UE")),
            ({"title": "Denmark - 1. division: HB Koge vs Hobro", "category": "football"}, ("calcio_estero", "Altri Campionati Europei")),
            ({"title": "UEFA Nations League: Italy vs France", "category": "football"}, ("calcio_estero", "Nazionali e Amichevoli")),
            ({"title": "Manchester City vs Inter", "category": "football", "teams": {"home": {"name": "Manchester City"}, "away": {"name": "Inter"}}}, ("calcio_estero", "Champions League")),
            ({"title": "UEFA Champions League: Real Madrid vs Stuttgart", "category": "football"}, ("calcio_estero", "Champions League")),
            ({"title": "Milan vs Barcelona - Club Friendly", "category": "football"}, ("calcio_estero", "Nazionali e Amichevoli")),
            ({"title": "Italy - Serie B: Palermo vs Bari", "category": "football"}, ("calcio_italiano", "Serie B")),
            ({"title": "Juventus vs Salernitana - Coppa Italia", "category": "football"}, ("calcio_italiano", "Coppa Italia e Supercoppa")),
            ({"title": "Petr Bar Biryukov vs Andre Ilagan (ATP - Singles)", "category": "tennis"}, ("tennis", "ATP")),
            ({"title": "Elena Rybakina vs Aryna Sabalenka (WTA - Singles)", "category": "tennis"}, ("tennis", "WTA")),
            ({"title": "Italy vs Netherlands - Billie Jean King Cup", "category": "tennis"}, ("tennis", "Coppa Davis e BJK Cup")),
            ({"title": "Itas Trentino vs Sir Susa Vim Perugia", "category": "volleyball"}, ("volley", "Superlega e Serie A1")),
            ({"title": "New York Yankees vs Boston Red Sox", "category": "baseball"}, ("baseball", "MLB")),
            ({"title": "Texas Longhorns vs LSU Tigers Baseball", "category": "baseball"}, ("baseball", "NCAA College Baseball")),
            ({"title": "Edmonton Oilers vs Florida Panthers", "category": "hockey"}, ("hockey", "NHL")),
            ({"title": "HC Elbflorenz Dresden vs Handball Club Hamburg (Handball Germany DHB Pokal)", "category": "other"}, ("altri_sport", "Pallamano")),
            ({"title": "Sidney University HC vs One Veszprem (IHF Club World Championship)", "category": "other"}, ("altri_sport", "Pallamano")),
            ({"title": "UCI Road World Championship U23 Montreal", "category": "other"}, ("altri_sport", "Ciclismo")),
            ({"title": "Shanghai vs RSSB Tigers (Intercontinental Cup)", "category": "basketball"}, ("basket", "FIBA e Tornei Nazionali")),
            ({"title": "WNBA: New York Liberty vs Atlanta Dream", "category": "basketball"}, ("basket", "WNBA e Femminile")),
            ({"title": "NBL: Brisbane Bullets vs New Zealand Breakers", "category": "basketball"}, ("basket", "Campionati Esteri ed NBL")),
            ({"title": "Panathinaikos vs Real Madrid (Basketball Euroleague)", "category": "basketball"}, ("basket", "Eurolega ed Eurocup")),
            ({"title": "England vs Sri Lanka (One Day International)", "category": "cricket"}, ("altri_sport", "Cricket")),
            ({"title": "England vs Sri Lanka (One Day International)", "category": "other"}, ("altri_sport", "Cricket")),
            ({"title": "South Africa vs Australia (T20 International)", "category": "other"}, ("altri_sport", "Cricket")),
        ]
        for match_dict, expected in test_cases:
            res = genre_classifier.classify(match_dict)
            self.assertEqual(res, expected, f"Expected {expected} for {match_dict['title']}, got {res}")

    def test_italian_resolver_channels(self):
        streams = italian_resolver.get_italian_streams_for_genre("Serie A", ep_url="https://ep.example.com", ep_pass="secret")
        self.assertTrue(len(streams) > 0)
        self.assertIn("Sky Sport Calcio", streams[0]["name"])
        self.assertIn("extractor/video.m3u8", streams[0]["url"])

    def test_catalog_root_route(self):
        response = client.get(f"/catalog/{CATALOG_TYPE}/calcio_italiano.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metas", data)
        self.assertIsInstance(data["metas"], list)

    def test_catalog_with_genre_filter(self):
        response = client.get(f"/catalog/{CATALOG_TYPE}/baseball.json?genre=MLB")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        metas = data.get("metas", [])
        for m in metas:
            self.assertIn("MLB", m.get("genres", []))

    def test_catalog_genre_ampersand_resilience(self):
        # Test path segment with raw &
        response = client.get(f"/catalog/{CATALOG_TYPE}/calcio_estero/genre=Americhe & Altre Leghe.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metas", data)
        # Even with raw ampersand in old URL, it must not crash or be truncated to 0 matches
        self.assertTrue(len(data["metas"]) > 0)

    def test_genre_isolation_nazionali_vs_u21(self):
        """Verifies that senior Nations League / Nazionali matches NEVER bleed into Under 21 and vice-versa."""
        import asyncio
        from app.services.catalog_service import catalog_service

        # Mock matches: 1 senior Nations League and 1 U21 match
        mock_matches = [
            {
                "id": "nl-ice-est",
                "title": "Iceland vs Estonia",
                "date": int(time.time() * 1000) + 3600000,
                "_catalog": "calcio_estero",
                "_genre": "Nazionali e Amichevoli",
                "sources": [{"id": "s1"}],
            },
            {
                "id": "u21-ita-fra",
                "title": "Italy U21 vs France U21",
                "date": int(time.time() * 1000) + 3600000,
                "_catalog": "calcio_estero",
                "_genre": "Europei Under 21 e Nazionali Giovanili",
                "sources": [{"id": "s2"}],
            },
        ]
        catalog_service._cached_matches = mock_matches

        # Query Nazionali e Amichevoli
        res_senior = asyncio.run(catalog_service.get_catalog(catalog_id="calcio_estero", genre_filter="Nazionali e Amichevoli"))
        senior_ids = [m["id"] for m in res_senior]
        self.assertIn("streamsport:nl-ice-est", senior_ids)
        self.assertNotIn("streamsport:u21-ita-fra", senior_ids)

        # Query Europei Under 21 e Nazionali Giovanili
        res_u21 = asyncio.run(catalog_service.get_catalog(catalog_id="calcio_estero", genre_filter="Europei Under 21 e Nazionali Giovanili"))
        u21_ids = [m["id"] for m in res_u21]
        self.assertIn("streamsport:u21-ita-fra", u21_ids)
        self.assertNotIn("streamsport:nl-ice-est", u21_ids)

    def test_spartak_kostroma_and_ncaa_classification(self):
        """Verifies that Kostroma does not collide with Roma, and NCAA small colleges never enter NFL."""
        from app.services.genre_classifier import genre_classifier

        # 1. Spartak Kostroma must NOT be classified as Italian Serie A
        cat_sk, genre_sk = genre_classifier.classify({"title": "Spartak Kostroma vs Ural", "category": "football"})
        self.assertNotEqual(cat_sk, "calcio_italiano")
        self.assertNotEqual(genre_sk, "Serie A")

        # 2. NCAA small college football matches must NOT be classified as NFL
        small_colleges = [
            "Duquesne vs Rio Grande",
            "Marist vs Presbyterian",
            "Sacred Heart vs New Hampshire",
            "Oberlin Yeomen vs Washington Bears",
        ]
        for title in small_colleges:
            cat_af, genre_af = genre_classifier.classify({"title": title, "category": "american-football"})
            self.assertEqual(cat_af, "football_americano")
            self.assertEqual(genre_af, "NCAA College Football")

        # 3. Real NFL must be recognized
        cat_nfl, genre_nfl = genre_classifier.classify({"title": "Kansas City Chiefs vs Baltimore Ravens", "category": "american-football"})
        self.assertEqual(cat_nfl, "football_americano")
        self.assertEqual(genre_nfl, "NFL")

    def test_deduplication(self):
        from app.services.catalog_service import catalog_service
        list1 = [
            {"id": "m1", "title": "Inter vs Juventus", "date": 1780000000000, "poster": "http://img/1.png", "sources": [{"id": "s1"}]},
            {"id": "m2", "title": "Real Madrid vs Barcelona", "date": 1780000000000, "poster": "http://img/2.png", "sources": [{"id": "s2"}]},
        ]
        list2 = [
            # Same match with slightly different prefix and title formatting from another source
            {"id": "dl1", "title": "Italy - Serie A : Inter vs Juventus", "date": 1780000000000, "poster": None, "sources": [{"id": "dl_s1"}]},
        ]
        merged = catalog_service.merge_and_deduplicate(list1, list2)
        self.assertEqual(len(merged), 2)
        inter_match = next((m for m in merged if "Inter" in m["title"]), None)
        self.assertIsNotNone(inter_match)
        self.assertEqual(len(inter_match["sources"]), 2)
        self.assertEqual(inter_match["poster"], "http://img/1.png")

        # Test Melbourne Phoenix vs Melbourne United partial name match
        nbl_streamed = [{"id": "nbl1", "title": "South East Melbourne Phoenix vs Melbourne United", "date": 1790000000000, "poster": "http://img/nbl.png", "sources": [{"id": "s_nbl"}]}]
        nbl_dlhd = [{"id": "dl_nbl1", "title": "South East Melbourne vs Melbourne United (NBL)", "date": 1790000000000, "poster": None, "sources": [{"id": "dl_nbl"}]}]
        nbl_merged = catalog_service.merge_and_deduplicate(nbl_streamed, nbl_dlhd)
        self.assertEqual(len(nbl_merged), 1)
        self.assertEqual(len(nbl_merged[0]["sources"]), 2)
        self.assertEqual(nbl_merged[0]["poster"], "http://img/nbl.png")

    def test_silo_deduplication_isolation(self):
        """Verifies that events with identical tokens from different sport silos NEVER merge together."""
        from app.services.catalog_service import catalog_service

        m_foot = {"id": "f1", "title": "Bears vs Lions", "date": 1780000000000, "category": "football", "_silo": "football", "sources": [{"id": "s_f"}]}
        m_af = {"id": "af1", "title": "Bears vs Lions", "date": 1780000000000, "category": "american-football", "_silo": "american-football", "sources": [{"id": "s_af"}]}

        # Merging across different silos must preserve both distinct events!
        merged = catalog_service.merge_and_deduplicate_by_silos([m_foot], [m_af])
        self.assertEqual(len(merged), 2)
        silos = [m.get("_silo") for m in merged]
        self.assertIn("football", silos)
        self.assertIn("american-football", silos)

    def test_title_and_description_formatting(self):
        from app.services.catalog_service import catalog_service
        now_ms = int(time.time() * 1000)

        # 1. Match with country flags and colon
        m1 = {
            "id": "test_m1",
            "title": "🇮🇹 Italy - Serie A: 🇮🇹 Inter vs 🇮🇹 Milan",
            "date": now_ms - (30 * 60 * 1000),  # Started 30 mins ago -> LIVE
        }
        item1 = catalog_service.build_meta_item(m1, "Serie A")
        # Name should be competitors only, flag emojis next to teams preserved
        self.assertEqual(item1["name"], "🇮🇹 Inter vs 🇮🇹 Milan")
        # Description: Status first, then competition without emoji, then competitors
        self.assertTrue(item1["description"].startswith("🔴 LIVE ORA • Italy - Serie A • 🇮🇹 Inter vs 🇮🇹 Milan"))
        # Redundant date not in description
        self.assertNotIn("2026", item1["description"])
        # releaseInfo preserves formatted date
        self.assertIn("2026", item1["releaseInfo"])

        # 2. Match with parenthesized competition (e.g. Handball)
        m2 = {
            "id": "test_m2",
            "title": "Sidney University HC vs One Veszprem (IHF Club World Championship)",
            "date": now_ms + (15 * 60 * 1000),  # Starts in 15 mins
        }
        item2 = catalog_service.build_meta_item(m2, "Pallamano")
        self.assertEqual(item2["name"], "Sidney University HC vs One Veszprem")
        self.assertTrue(item2["description"].startswith("⏳ Inizio tra"))
        self.assertIn("IHF Club World Championship", item2["description"])
        self.assertIn("Sidney University HC vs One Veszprem", item2["description"])

        # 3. Concluded match (> 4 hours ago)
        m3 = {
            "id": "test_m3",
            "title": "UEFA Champions League: Arsenal vs Chelsea",
            "date": now_ms - (5 * 3600 * 1000),
        }
        item3 = catalog_service.build_meta_item(m3, "Champions League")
        self.assertEqual(item3["name"], "Arsenal vs Chelsea")
        self.assertTrue(item3["description"].startswith("🏁 Conclusa • Replay e Sintesi • UEFA Champions League • Arsenal vs Chelsea"))

    def test_espn_reconciliation(self):
        from app.services.espn_service import espn_service
        from app.services.catalog_service import catalog_service

        # Simulated official registry from ESPN
        official_espn = [
            {
                "id": "espn-12345",
                "title": "Dubai Basketball vs Real Madrid Baloncesto",
                "category": "basketball",
                "date": 1790000000000,
                "teams": {
                    "home": {"name": "Dubai Basketball"},
                    "away": {"name": "Real Madrid Baloncesto"},
                },
                "_catalog": "basket",
                "_genre": "Eurolega ed Eurocup",
                "_espn": True,
            },
            {
                "id": "espn-67890",
                "title": "Fiorentina vs Empoli",
                "category": "football",
                "date": 1790003600000,
                "teams": {
                    "home": {"name": "Fiorentina"},
                    "away": {"name": "Empoli"},
                },
                "_catalog": "calcio_italiano",
                "_genre": "Serie A",
                "_espn": True,
            },
        ]

        # Raw streamed matches with informal / slightly modified titles
        raw_streamed = [
            {
                "id": "strem-1",
                "title": "Dubai vs Real Madrid",
                "category": "basketball",
                "date": 1790000000000,
                "teams": {"home": {"name": "Dubai"}, "away": {"name": "Real Madrid"}},
            },
            {
                "id": "dl-2",
                "title": "Italy - Serie A : Fiorentina vs Empoli [HD]",
                "category": "football",
                "date": 1790003600000,
            },
        ]

        espn_service.reconcile_matches(
            raw_streamed,
            official_espn,
            clean_tokens_fn=catalog_service._clean_tokens,
            get_teams_key_fn=catalog_service._get_teams_key,
        )

        # Match 1 reconciled
        self.assertTrue(raw_streamed[0].get("_espn_matched"))
        self.assertEqual(raw_streamed[0]["title"], "Dubai Basketball vs Real Madrid Baloncesto")
        self.assertEqual(raw_streamed[0]["teams"]["home"]["name"], "Dubai Basketball")
        self.assertEqual(raw_streamed[0]["teams"]["away"]["name"], "Real Madrid Baloncesto")
        self.assertEqual(raw_streamed[0]["_catalog"], "basket")
        self.assertEqual(raw_streamed[0]["_genre"], "Eurolega ed Eurocup")

        # Match 2 reconciled
        self.assertTrue(raw_streamed[1].get("_espn_matched"))
        self.assertEqual(raw_streamed[1]["title"], "Fiorentina vs Empoli")
        self.assertEqual(raw_streamed[1]["_catalog"], "calcio_italiano")
        self.assertEqual(raw_streamed[1]["_genre"], "Serie A")

    def test_is_replay_eligible_whitelist(self):
        from app.services.catalog_service import catalog_service

        # Eligible events
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "calcio_italiano", "_genre": "Serie A", "title": "Inter vs Milan"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Champions League", "title": "Real Madrid vs Manchester City"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Premier League", "title": "Arsenal vs Chelsea"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "motori", "_genre": "Formula 1", "title": "F1 Gran Premio d'Italia"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "motori", "_genre": "MotoGP e Moto2/3", "title": "MotoGP Mugello"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "tennis", "_genre": "ATP", "title": "Jannik Sinner vs Carlos Alcaraz"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "tennis", "_genre": "Grandi Slam", "title": "Wimbledon Final"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "basket", "_genre": "NBA", "title": "Boston Celtics vs LA Lakers"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "basket", "_genre": "Eurolega ed Eurocup", "title": "Olimpia Milano vs Virtus Bologna"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "combattimento", "_genre": "UFC", "title": "UFC 305: Main Card"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "football_americano", "_genre": "NFL", "title": "Chiefs vs 49ers"}))
        # National teams and friendlies: ONLY eligible if Italy is playing
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Nazionali e Amichevoli", "title": "Italy vs France (UEFA Nations League)"}))
        self.assertTrue(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Nazionali e Amichevoli", "title": "Germania vs Italia (Amichevole)"}))

    def test_is_replay_ineligible_blacklist(self):
        from app.services.catalog_service import catalog_service

        # Ineligible events (College / NCAA, minor sports, MLB, NHL)
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "basket", "_genre": "NCAA e College Basket", "title": "The Citadel vs UNC Greensboro"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Americhe e Leghe Extra-UE", "title": "Women's College Soccer: Citadel vs UNC"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "baseball", "_genre": "MLB", "title": "Yankees vs Red Sox"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "hockey", "_genre": "NHL", "title": "Bruins vs Rangers"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "altri_sport", "_genre": "Darts", "title": "PDC Darts Championship"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "tennis", "_genre": "Challenger e Altri", "title": "Challenger Biella"}))
        # National teams and friendlies without Italy are NOT eligible
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Nazionali e Amichevoli", "title": "Portugal vs Wales (UEFA Nations League)"}))
        self.assertFalse(catalog_service.is_replay_eligible({"_catalog": "calcio_estero", "_genre": "Nazionali e Amichevoli", "title": "Brazil vs Argentina (Amichevole)"}))

    def test_cache_control_headers(self):
        # 1. Catalog list: 3 minutes (180s)
        resp_cat = client.get("/catalog/Live Sports/calcio_italiano.json")
        self.assertEqual(resp_cat.status_code, 200)
        self.assertEqual(resp_cat.headers.get("cache-control"), "public, max-age=180")

        # 2. Configured Catalog list: 3 minutes (180s)
        dummy_cfg = base64.b64encode(b"https://ep.test|pass|Europe/Rome").decode("utf-8")
        resp_cfg_cat = client.get(f"/{dummy_cfg}/catalog/Live Sports/calcio_estero.json")
        self.assertEqual(resp_cfg_cat.status_code, 200)
        self.assertEqual(resp_cfg_cat.headers.get("cache-control"), "public, max-age=180")

        # 3. Meta detail: no-cache
        # Test with existing match if available, or verify header on 200 response
        from app.services.catalog_service import catalog_service
        if catalog_service._cached_matches:
            m_id = catalog_service._cached_matches[0]["id"]
            resp_meta = client.get(f"/meta/Live Sports/{m_id}.json")
            if resp_meta.status_code == 200:
                self.assertEqual(resp_meta.headers.get("cache-control"), "no-cache, no-store, must-revalidate")

    def test_extra_eu_confederations_and_youth_classification(self):
        # 1. COSAFA Youth tournament must be Europei Under 21 e Nazionali Giovanili (NOT Champions League)
        cat1, genre1 = genre_classifier.classify({"title": "Namibia U20 vs Mozambique U20 (COSAFA Champions League)", "category": "football"})
        self.assertEqual(cat1, "calcio_estero")
        self.assertEqual(genre1, "Europei Under 21 e Nazionali Giovanili")

        # 2. African CAF Champions League must be Americhe e Leghe Extra-UE (NOT UEFA Champions League)
        cat2, genre2 = genre_classifier.classify({"title": "Al Ahly vs Esperance (CAF Champions League)", "category": "football"})
        self.assertEqual(cat2, "calcio_estero")
        self.assertEqual(genre2, "Americhe e Leghe Extra-UE")

        # 3. Asian AFC Champions League must be Americhe e Leghe Extra-UE
        cat3, genre3 = genre_classifier.classify({"title": "Yokohama F. Marinos vs Al Ain (AFC Champions League)", "category": "football"})
        self.assertEqual(cat3, "calcio_estero")
        self.assertEqual(genre3, "Americhe e Leghe Extra-UE")

        # 4. UEFA Champions League remains Champions League
        cat4, genre4 = genre_classifier.classify({"title": "Real Madrid vs Manchester City (UEFA Champions League)", "category": "football"})
        self.assertEqual(cat4, "calcio_estero")
        self.assertEqual(genre4, "Champions League")

    def test_dynamic_live_window_and_replay_toggle(self):
        import asyncio
        import time
        from app.services.catalog_service import CatalogService, catalog_service
        from app.services.stream_service import stream_service

        # 1. Sport-specific dynamic live windows
        # Tennis: 300 minutes (5 hours)
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "tennis", "title": "Sinner vs Alcaraz"}), 300)
        # Football regular: 150 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "football", "title": "Inter vs Milan"}), 150)
        # Football cup: 170 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "football", "title": "Real Madrid vs Man City (Champions League)"}), 170)
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "football", "title": "Juventus vs Lazio (Coppa Italia)"}), 170)
        # Basket: 150 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "basketball", "title": "Olimpia Milano vs Virtus Bologna"}), 150)
        # Motori GP: 160 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "motor-sports", "title": "Gran Premio d'Italia Gara"}), 160)
        # Motori Practice / Qualifying: 80 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "motor-sports", "title": "Gran Premio d'Italia Qualifiche"}), 80)
        # NFL: 230 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "american-football", "title": "Kansas City Chiefs vs 49ers"}), 230)
        # Baseball: 200 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "baseball", "title": "New York Yankees vs Boston Red Sox"}), 200)
        # Hockey: 170 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "hockey", "title": "New York Rangers vs Boston Bruins"}), 170)
        # UFC: 240 minutes
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "fight", "title": "UFC 310: Jones vs Miocic"}), 240)
        # Unrecognized / uncategorized fallback: exactly 240 minutes (4 hours)
        self.assertEqual(CatalogService.get_live_window_minutes({"category": "unknown_sport_xyz", "title": "Team X vs Team Y"}), 240)

        # 2. Concluded streams behavior when ENABLE_REPLAYS=False
        past_date_ms = (time.time() - (4 * 3600)) * 1000  # 4 hours ago
        concluded_match = {
            "id": "test_concluded_match",
            "title": "Inter vs Milan",
            "category": "football",
            "date": past_date_ms,
            "sources": []
        }
        catalog_service._cached_matches.append(concluded_match)
        streams = asyncio.run(stream_service.get_streams_for_event(concluded_match["id"], "https://ep.example.com", user_tz="Europe/Rome"))
        # When ENABLE_REPLAYS is False, stream service immediately returns Evento Concluso without calling highlights
        self.assertTrue(len(streams) >= 1)
        self.assertIn("Concluso", streams[0].get("name", ""))

if __name__ == "__main__":
    unittest.main()
