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
