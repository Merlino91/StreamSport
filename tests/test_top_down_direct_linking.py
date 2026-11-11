import asyncio
from contextlib import ExitStack
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.provider_normalizers import (
    clean_daddylive_event,
    clean_spon_event,
    clean_fctv33_event,
    clean_mandrakodi_event,
    clean_streamed_event,
    normalize_ascii_text,
)
from app.services.catalog_service import CatalogService, catalog_service


def test_normalize_ascii_special_characters():
    assert normalize_ascii_text("København") == "Kobenhavn"
    assert normalize_ascii_text("Brøndby") == "Brondby"
    assert normalize_ascii_text("Bandırmaspor") == "Bandirmaspor"
    assert normalize_ascii_text("Fatih Karagümrük") == "Fatih Karagumruk"
    assert normalize_ascii_text("Mil-Muğan") == "Mil-Mugan"
    assert normalize_ascii_text("CF Montréal") == "CF Montreal"


def test_clean_daddylive_event():
    raw_dl = {
        "id": "dlhd-1",
        "title": "Bristol City 🏴󠁧󠁢󠁥󠁮󠁧󠁿 vs Norwich City 🏴󠁧󠁢󠁥󠁮󠁧󠁿 (England - Championship) | HD",
        "category": "football",
        "date": 1700000000000,
        "sources": [{"source": "dlhd", "id": "101", "name": "Sky Sport 1"}],
    }
    cleaned = clean_daddylive_event(raw_dl)
    assert cleaned["home"] == "Bristol City"
    assert cleaned["away"] == "Norwich City"
    assert cleaned["clean_title"] == "Bristol City vs Norwich City"
    assert cleaned["league_note"] == "England - Championship"
    assert cleaned["teams"]["home"]["name"] == "Bristol City"
    assert cleaned["teams"]["away"]["name"] == "Norwich City"
    assert cleaned["raw_title"] == raw_dl["title"]


def test_clean_daddylive_women_demographic():
    raw_dl = {
        "id": "dlhd-2",
        "title": "Central Coast Mariners 🇦🇺 vs Newcastle Jets 🇦🇺 (Australia - A-League Women)",
        "category": "football",
        "date": 1700000000000,
        "sources": [{"source": "dlhd", "id": "102", "name": "Fox"}],
    }
    cleaned = clean_daddylive_event(raw_dl)
    assert cleaned["is_women"] is True
    assert cleaned["home"] == "Central Coast Mariners"
    assert cleaned["away"] == "Newcastle Jets"


def test_clean_spon_event_fixes_flat_strings():
    raw_spon = {
        "id": "spon:hd1_1700000000",
        "title": "Football: Arsenal vs Chelsea",
        "category": "football",
        "date": 1700000000000,
        "teams": {"home": "Arsenal", "away": "Chelsea"},  # Flat string bug
        "sources": [{"source": "sportsonline", "id": "hd1", "name": "HD1"}],
    }
    cleaned = clean_spon_event(raw_spon)
    assert cleaned["home"] == "Arsenal"
    assert cleaned["away"] == "Chelsea"
    assert cleaned["clean_title"] == "Arsenal vs Chelsea"
    assert cleaned["teams"]["home"] == {"name": "Arsenal"}
    assert cleaned["teams"]["away"] == {"name": "Chelsea"}


def test_link_official_schedule_to_providers_highest_score_selection():
    now_ms = 1700000000000
    official_match = {
        "id": "diretta-12345",
        "title": "Inter vs Juventus",
        "home": "Inter",
        "away": "Juventus",
        "alt_home": "Inter",
        "alt_away": "Juventus",
        "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "canonical_competition": "football_serie_a",
        "sources": [],
    }

    # Two DaddyLive candidates: one imperfect (0.50), one exact match (0.98)
    cand_imperfect = clean_daddylive_event({
        "id": "dlhd-imperfect",
        "title": "Inter Primavera vs Roma U19",
        "category": "football",
        "date": now_ms + 10 * 60 * 1000,
        "is_youth": True,
        "sources": [{"source": "dlhd", "id": "50", "name": "Wrong Match"}],
    })
    cand_exact = clean_daddylive_event({
        "id": "dlhd-exact",
        "title": "Inter 🇮🇹 vs Juventus 🇮🇹 (Italy - Serie A)",
        "category": "football",
        "date": now_ms + 5 * 60 * 1000,
        "sources": [{"source": "dlhd", "id": "100", "name": "Sky Sport Uno"}],
    })

    # FCTV33 candidate
    cand_fctv33 = clean_fctv33_event({
        "id": "fctv33:999",
        "title": "Inter Milano vs Juventus FC",
        "category": "football",
        "date": now_ms,
        "teams": {"home": {"name": "Inter Milano"}, "away": {"name": "Juventus FC"}},
        "sources": [{"source": "fctv33", "id": "999", "name": "FCTV33 Stream"}],
    })

    providers = {
        "daddylive": [cand_imperfect, cand_exact],
        "fctv33": [cand_fctv33],
    }

    linked = catalog_service.link_official_schedule_to_providers(
        [official_match],
        providers,
        time_window_minutes=60.0,
    )

    assert len(linked) == 1
    m = linked[0]
    # Check that sources were inherited from both providers
    src_ids = [s["id"] for s in m["sources"]]
    assert "100" in src_ids  # Exact DaddyLive picked
    assert "50" not in src_ids  # Imperfect/Youth ignored
    assert "999" in src_ids  # FCTV33 picked


def test_link_official_schedule_claimed_isolation():
    now_ms = 1700000000000
    match1 = {
        "id": "diretta-1",
        "title": "Real Madrid vs Barcelona",
        "home": "Real Madrid",
        "away": "Barcelona",
        "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "sources": [],
    }
    match2 = {
        "id": "diretta-2",
        "title": "Real Madrid B vs Cordoba",
        "home": "Real Madrid B",
        "away": "Cordoba",
        "teams": {"home": {"name": "Real Madrid B"}, "away": {"name": "Cordoba"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "sources": [],
    }

    cand_elclasico = clean_daddylive_event({
        "id": "dlhd-elclasico",
        "title": "Real Madrid vs Barcelona",
        "category": "football",
        "date": now_ms,
        "sources": [{"source": "dlhd", "id": "classic", "name": "Movistar"}],
    })

    linked = catalog_service.link_official_schedule_to_providers(
        [match1, match2],
        {"daddylive": [cand_elclasico]},
        time_window_minutes=60.0,
    )

    assert len(linked) == 2
    # Match 1 gets the classic stream
    assert len(linked[0]["sources"]) == 1
    assert linked[0]["sources"][0]["id"] == "classic"
    # Match 2 must NOT claim the same stream
    assert len(linked[1]["sources"]) == 0


def test_link_official_schedule_time_window_cutoff():
    now_ms = 1700000000000
    match = {
        "id": "diretta-3",
        "title": "Milan vs Roma",
        "home": "Milan",
        "away": "Roma",
        "teams": {"home": {"name": "Milan"}, "away": {"name": "Roma"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "sources": [],
    }

    cand_too_late = clean_daddylive_event({
        "id": "dlhd-late",
        "title": "Milan vs Roma",
        "category": "football",
        # 75 minutes later: exceeds 60m window
        "date": now_ms + (75 * 60 * 1000),
        "sources": [{"source": "dlhd", "id": "late", "name": "DAZN"}],
    })

    linked = catalog_service.link_official_schedule_to_providers(
        [match],
        {"daddylive": [cand_too_late]},
        time_window_minutes=60.0,
    )

    assert len(linked) == 1
    assert len(linked[0]["sources"]) == 0  # Cut off by 60m window


async def _run_sync_during_diretta_outage(ram_matches, db_matches, dl_matches, epg_matches=None):
    cat = CatalogService()
    cat._cached_matches = list(ram_matches)

    with ExitStack() as stack:
        stack.enter_context(patch("app.services.catalog_service.diretta_service.get_official_sliding_schedule", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.streamed_api.get_all_matches", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.daddylive_api.get_matches", new=AsyncMock(return_value=list(dl_matches))))
        stack.enter_context(patch("app.services.catalog_service.ocblacktop_service.get_official_sessions", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.thesportsdb_service.fetch_multi_day_calendar", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.italian_epg_service.get_matches", new=AsyncMock(return_value=list(epg_matches or []))))
        stack.enter_context(patch("app.services.catalog_service.fctv33_service.get_matches", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.mandrakodi_service.get_matches", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.spon_service.get_matches", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.streamfree_service.get_matches", new=AsyncMock(return_value=[])))
        stack.enter_context(patch("app.services.catalog_service.db_service.get_active_matches_async", new=AsyncMock(return_value=list(db_matches))))
        stack.enter_context(patch("app.services.catalog_service.db_service.purge_expired_matches_async", new=AsyncMock(return_value=0)))
        stack.enter_context(patch("app.services.catalog_service.db_service.save_matches_async", new=AsyncMock()))
        stack.enter_context(patch("app.services.catalog_service.db_service.delete_matches_by_ids_async", new=AsyncMock()))
        stack.enter_context(patch("app.services.catalog_service.tvvoo_service.enrich_matches_with_tvvoo", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.mandrakodi_service.enrich_matches_with_mandrakodi", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.italian_epg_service.enrich_matches_with_epg_channels", new=AsyncMock()))
        stack.enter_context(patch("app.services.catalog_service.italian_epg_service.filter_conflicting_italian_channels", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.thesportsdb_service.reconcile_matches", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.thesportsdb_service.reconcile_motorsport_matches", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.banner_service.purge_stale_banners", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.tennis_poster_service.cleanup_finished_events", new=MagicMock()))
        stack.enter_context(patch("app.services.catalog_service.tennis_poster_service.start_background_enrichment", new=MagicMock()))
        await cat.sync_matches(force=True)

    return cat._cached_matches


def test_sync_matches_diretta_outage_prefers_ram_over_sqlite_and_keeps_epg():
    now_ms = int(time.time() * 1000)
    ram_match = {
        "id": "diretta-ram-1",
        "title": "Inter vs Juventus",
        "home": "Inter",
        "away": "Juventus",
        "teams": {"home": {"name": "Inter"}, "away": {"name": "Juventus"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "canonical_competition": "football_serie_a",
        "sources": [],
    }
    db_match = {
        "id": "diretta-db-stale",
        "title": "Milan vs Roma",
        "home": "Milan",
        "away": "Roma",
        "teams": {"home": {"name": "Milan"}, "away": {"name": "Roma"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "sources": [],
    }
    dl_fresh = {
        "id": "dlhd-fresh-1",
        "title": "Inter vs Juventus",
        "category": "football",
        "date": now_ms,
        "sources": [{"source": "dlhd", "id": "881", "name": "Sky Sport Calcio"}],
    }
    epg_live = {
        "id": "epg-atp-session",
        "title": "ATP Finals Live Session",
        "competition": "ATP",
        "category": "tennis",
        "_silo": "tennis",
        "_provider": "italian_epg",
        "date": now_ms,
        "sources": [{"source": "italian_epg", "id": "sky201", "name": "Sky Sport Tennis"}],
    }

    cached = asyncio.run(_run_sync_during_diretta_outage([ram_match], [db_match], [dl_fresh], [epg_live]))
    ids = {m["id"] for m in cached}
    assert "diretta-ram-1" in ids
    assert "epg-atp-session" in ids
    assert "diretta-db-stale" not in ids
    ram_out = next(m for m in cached if m["id"] == "diretta-ram-1")
    assert any(s.get("id") == "881" for s in ram_out.get("sources", []))


def test_sync_matches_diretta_outage_cold_ram_uses_sqlite_and_relinks():
    now_ms = int(time.time() * 1000)
    db_match = {
        "id": "diretta-db-1",
        "title": "Napoli vs Lazio",
        "home": "Napoli",
        "away": "Lazio",
        "teams": {"home": {"name": "Napoli"}, "away": {"name": "Lazio"}},
        "category": "football",
        "_silo": "football",
        "date": now_ms,
        "canonical_competition": "football_serie_a",
        "sources": [],
    }
    dl_fresh = {
        "id": "dlhd-fresh-2",
        "title": "Napoli vs Lazio",
        "category": "football",
        "date": now_ms,
        "sources": [{"source": "dlhd", "id": "882", "name": "DAZN 1"}],
    }

    cached = asyncio.run(_run_sync_during_diretta_outage([], [db_match], [dl_fresh]))
    ids = {m["id"] for m in cached}
    assert "diretta-db-1" in ids
    db_out = next(m for m in cached if m["id"] == "diretta-db-1")
    assert any(s.get("id") == "882" for s in db_out.get("sources", []))
