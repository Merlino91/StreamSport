import sys
sys.path.insert(0, ".")
import pytest
from app.services.canonical_competition_service import is_tier_match
from app.services.diretta_service import diretta_service
from app.services.thesportsdb_service import TheSportsDBService
from app.services.team_matching import calculate_match_score

def test_tier_matching_motorsports():
    # Formula 1
    admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "FORMULA 1: GP Messico (Messico) - Prove Libere 2"})
    assert admitted is True
    assert slug == "motorsport_f1"

    # MotoGP, Moto2, Moto3
    admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTOGP: Sepang (Malesia) - Qualifiche 1"})
    assert admitted is True
    assert slug == "motorsport_motogp"

    admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTO2: Sepang (Malesia) - Prove Libere 3"})
    assert admitted is True
    assert slug == "motorsport_motogp"

    admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "MOTO3: Sepang (Malesia) - Gara"})
    assert admitted is True
    assert slug == "motorsport_motogp"

    # NASCAR
    admitted, slug = is_tier_match({"_silo": "motor-sports", "league": "NASCAR CUP SERIES: Xfinity 500 (USA) - Qualifiche"})
    assert admitted is True
    assert slug == "motorsport_nascar"

def test_tier_matching_rugby():
    # URC United Rugby Championship
    admitted, slug = is_tier_match({"_silo": "rugby", "league": "United Rugby Championship"})
    assert admitted is True
    assert slug == "rugby_top_14"

def test_team_matching_motorsport_session():
    # Top-Down match between Diretta session card and DaddyLive stream
    match = {
        "title": "FORMULA 1: GP Messico (Messico) - Prove Libere 3",
        "competition": "GP Messico",
        "_silo": "motor-sports",
        "date": 1730419200000,
    }
    candidate = {
        "title": "Mexico City GP - Practice 3 (Formula 1)",
        "category": "motor-sports",
        "date": 1730419200000,
    }
    res = calculate_match_score(match, candidate, phase="phase1_diretta")
    assert res["matched"] is True
    assert res["score"] >= 0.75

def test_team_matching_motorsport_session_conflict():
    # Qualifying must not match Practice
    match = {
        "title": "FORMULA 1: GP Messico (Messico) - Qualifiche 1",
        "competition": "GP Messico",
        "_silo": "motor-sports",
        "date": 1730419200000,
    }
    candidate = {
        "title": "Mexico City GP - Practice 1 (Formula 1)",
        "category": "motor-sports",
        "date": 1730419200000,
    }
    res = calculate_match_score(match, candidate, phase="phase1_diretta")
    assert res["matched"] is False

def test_parse_flashscore_feed_motorsports():
    feed_sample = (
        "~ZA÷FORMULA 1: GP Messico (Messico) - Prove Libere 2¬ZAE÷Prove Libere 2¬ZY÷MESSICO¬"
        "~AA÷test_f1_session¬AD÷1730419200¬AE÷Leclerc C.¬"
        "~AA÷driver_2¬AD÷1730419200¬AE÷Verstappen M.¬"
    )
    matches = diretta_service.parse_flashscore_feed(feed_sample, "Formula 1", "motor-sports")
    # Must emit exactly 1 session card, not 2
    assert len(matches) == 1
    m = matches[0]
    assert m["id"] == "diretta-test_f1_session"
    assert m["title"] == "FORMULA 1: GP Messico (Messico) - Prove Libere 2"
    assert m["session"] == "Prove Libere 2"
    assert m["_silo"] == "motor-sports"
    assert m["date"] == 1730419200000

def test_epg_poster_isolation_to_motorsports():
    import asyncio
    from app.services.italian_epg_service import italian_epg_service

    # 1. 1v1 match (e.g. Football) MUST NOT inherit Sky EPG poster
    match_1v1 = {
        "id": "test_1v1",
        "title": "Inter vs Juventus",
        "_silo": "football",
        "category": "football",
        "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
        "date": 1730419200000,
        "poster": "https://thesportsdb.com/images/inter_juve.jpg",
    }
    epg_fake_1v1 = {
        "title": "Serie A: Inter vs Juventus",
        "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
        "_silo": "football",
        "category": "football",
        "date": 1730419200000,
        "poster": "https://guidatv.sky.it/uuid/sky_inter_juve_poster.jpg",
        "sources": [{"name": "Sky Sport Uno", "id": "sky sport uno"}],
    }

    # 2. Motorsport session MUST inherit Sky EPG poster
    match_motor = {
        "id": "test_motor",
        "title": "FORMULA 1: GP Messico - Prove Libere 3",
        "competition": "GP Messico",
        "_silo": "motor-sports",
        "category": "motor-sports",
        "date": 1730419200000,
        "poster": "https://thesportsdb.com/images/default_fallback.jpg",
    }
    epg_fake_motor = {
        "title": "GP Messico: Prove libere 3",
        "_silo": "motor-sports",
        "category": "motori",
        "date": 1730419200000,
        "poster": "https://guidatv.sky.it/uuid/sky_f1_mexico_poster.jpg",
        "sources": [{"name": "Sky Sport F1", "id": "sky sport f1"}],
    }

    async def _run():
        # Mock get_matches on italian_epg_service
        async def mock_get_matches(*args, **kwargs):
            return [epg_fake_1v1, epg_fake_motor]

        orig = italian_epg_service.get_matches
        italian_epg_service.get_matches = mock_get_matches
        try:
            await italian_epg_service.enrich_matches_with_epg_channels([match_1v1, match_motor])

            # Verification 1: 1v1 football retained its TheSportsDB poster
            assert match_1v1["poster"] == "https://thesportsdb.com/images/inter_juve.jpg"
            assert match_1v1.get("_poster_source") != "sky"

            # Verification 2: Motorsport received the Sky EPG poster
            assert match_motor["poster"] == "https://guidatv.sky.it/uuid/sky_f1_mexico_poster.jpg"
            assert match_motor.get("_poster_source") == "sky"
        finally:
            italian_epg_service.get_matches = orig

    asyncio.run(_run())

