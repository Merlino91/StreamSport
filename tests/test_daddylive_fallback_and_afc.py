import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.canonical_competition_service import (
    detect_canonical_competition,
    is_tier_match,
    get_canonical_display_name,
)
from app.services.genre_classifier import genre_classifier
from app.services.stream_service import stream_service
from app.services.catalog_service import catalog_service


class TestDaddyLiveFallbackAndAFC(unittest.IsolatedAsyncioTestCase):

    def test_afc_champions_league_two_classification(self):
        """Verify AFC Champions League Two is correctly classified as football_afc_cl, not EFL League Two."""
        # 1. Detection from raw string
        slug = detect_canonical_competition("AFC Champions League Two", silo_hint="football")
        assert slug == "football_afc_cl", f"Expected football_afc_cl but got {slug}"

        slug_elite = detect_canonical_competition("AFC Champions League Elite", silo_hint="football")
        assert slug_elite == "football_afc_cl"

        slug_num = detect_canonical_competition("AFC Champions League 2", silo_hint="football")
        assert slug_num == "football_afc_cl"

        # 2. Verify EFL League Two is still detected properly
        slug_efl = detect_canonical_competition("League Two", silo_hint="football")
        assert slug_efl == "football_efl_league_two"

        slug_efl2 = detect_canonical_competition("England - League Two", silo_hint="football")
        assert slug_efl2 == "football_efl_league_two"

        # 3. Verify Tier Match admission from Diretta
        m_diretta = {
            "title": "Tampines Rovers vs Svay Rieng",
            "_silo": "football",
            "category": "football",
            "country": "asia",
            "league": "afc champions league 2",
            "teams": {"home": "Tampines Rovers", "away": "Svay Rieng"},
        }
        admitted, comp_slug = is_tier_match(m_diretta)
        assert admitted is True
        assert comp_slug == "football_afc_cl"

        # 4. Verify Genre Classifier routes to Extra-UE catalog, NOT European Champions League
        m_diretta["canonical_competition"] = comp_slug
        cat, genre = genre_classifier.classify(m_diretta)
        assert cat == "calcio_estero"
        assert genre == "Americhe e Leghe Extra-UE", f"Expected 'Americhe e Leghe Extra-UE' but got {genre}"

    def test_find_matching_provider_event(self):
        """Verify StreamService.find_matching_provider_event finds matching matches across providers."""
        now_ms = int(time.time() * 1000)
        target = {
            "id": "diretta-IsyNRh6D",
            "title": "Tampines Rovers vs Svay Rieng",
            "teams": {"home": "Tampines Rovers", "away": "Svay Rieng"},
            "category": "football",
            "_silo": "football",
            "date": now_ms,
        }

        candidates = [
            {
                "id": "dlhd-other-match",
                "title": "Arsenal vs Chelsea",
                "teams": {"home": "Arsenal", "away": "Chelsea"},
                "category": "football",
                "_silo": "football",
                "date": now_ms,
                "sources": [{"id": "99", "source": "dlhd"}],
            },
            {
                "id": "dlhd-tampines-rovers-vs-svay-rieng",
                "title": "Tampines Rovers vs Svay Rieng",
                "teams": {"home": "Tampines Rovers", "away": "Svay Rieng"},
                "category": "football",
                "_silo": "football",
                "date": now_ms + 10000,
                "sources": [
                    {"id": "51", "name": "beIN Sports MENA 3", "source": "dlhd"},
                    {"id": "52", "name": "Sportklub 3 Croatia", "source": "dlhd"},
                ],
            },
        ]

        matched = stream_service.find_matching_provider_event(target, candidates)
        assert matched is not None
        assert matched["id"] == "dlhd-tampines-rovers-vs-svay-rieng"
        assert len(matched["sources"]) == 2

    async def test_daddylive_on_demand_fallback_resolution(self):
        """
        Verify opening a match with no DaddyLive sources triggers the on-demand fallback,
        cleanly replacing DaddyLive sources and keeping existing other-provider sources.
        """
        now_ms = int(time.time() * 1000)
        match_id = "diretta-test-afc-01"
        match = {
            "id": match_id,
            "title": "Tampines Rovers vs Svay Rieng",
            "teams": {"home": "Tampines Rovers", "away": "Svay Rieng"},
            "category": "football",
            "_silo": "football",
            "canonical_competition": "football_afc_cl",
            "date": now_ms,
            "sources": [
                {
                    "id": "tvvoo-01",
                    "source": "tvvoo",
                    "name": "Sky Sport Calcio",
                    "url": "https://tvvoo.test/stream.m3u8",
                    "country": "Italia",
                }
            ],
        }

        catalog_service.update_cached_match(match)

        fake_dl_matches = [
            {
                "id": "dlhd-tampines-rovers-vs-svay-rieng",
                "title": "Tampines Rovers vs Svay Rieng",
                "teams": {"home": "Tampines Rovers", "away": "Svay Rieng"},
                "category": "football",
                "_silo": "football",
                "date": now_ms,
                "sources": [
                    {"id": "51", "name": "beIN Sports MENA 3", "source": "dlhd"},
                    {"id": "52", "name": "Sportklub 3 Croatia", "source": "dlhd"},
                ],
            }
        ]

        with patch("app.services.daddylive_api.daddylive_api.get_matches", AsyncMock(return_value=fake_dl_matches)):
            streams = await stream_service.get_streams_for_event(
                match_id,
                ep_url="https://ep.test.com",
                ep_pass="secret123",
                base_url="https://addon.test.com",
            )

        # 1. Verify streams returned both TvVoo and DaddyLive
        stream_names = [s.get("name", "") for s in streams]
        assert any("Sky Sport Calcio" in n for n in stream_names), f"Missing TvVoo stream in {stream_names}"
        assert any("beIN Sports MENA 3" in n for n in stream_names), f"Missing DaddyLive stream in {stream_names}"
        assert any("Sportklub 3 Croatia" in n for n in stream_names), f"Missing DaddyLive stream in {stream_names}"

        # 2. Verify cached match in RAM was updated with merged sources
        updated_match = catalog_service.get_cached_match(match_id)
        assert updated_match is not None
        src_providers = [s.get("source") for s in updated_match.get("sources", [])]
        assert "tvvoo" in src_providers
        assert "dlhd" in src_providers
        assert len([s for s in updated_match["sources"] if s.get("source") == "dlhd"]) == 2

    async def test_daddylive_clean_replacement_removes_dead_links(self):
        """
        Verify that when fresh DaddyLive sources are found, any stale dlhd links are replaced,
        never accumulating dead channels.
        """
        now_ms = int(time.time() * 1000)
        match_id = "diretta-test-replacement-01"
        match = {
            "id": match_id,
            "title": "Arsenal vs Chelsea",
            "teams": {"home": "Arsenal", "away": "Chelsea"},
            "category": "football",
            "_silo": "football",
            "date": now_ms,
            "sources": [
                {
                    "id": "old-dead-99",
                    "source": "dlhd",
                    "name": "Dead Channel 99",
                },
                {
                    "id": "tvvoo-persists",
                    "source": "tvvoo",
                    "name": "Sky Sport Uno",
                    "url": "https://tvvoo.test/skyuno.m3u8",
                }
            ],
        }

        catalog_service.update_cached_match(match)

        # Test on-demand fallback: remove dlhd so fallback attaches fresh
        match["sources"] = [s for s in match["sources"] if s.get("source") != "dlhd"]

        fake_dl_matches = [
            {
                "id": "dlhd-arsenal-vs-chelsea",
                "title": "Arsenal vs Chelsea",
                "teams": {"home": "Arsenal", "away": "Chelsea"},
                "category": "football",
                "_silo": "football",
                "date": now_ms,
                "sources": [
                    {"id": "new-fresh-01", "name": "Sky Sports Main Event", "source": "dlhd"},
                    {"id": "new-fresh-02", "name": "TNT Sports 1", "source": "dlhd"},
                ],
            }
        ]

        with patch("app.services.daddylive_api.daddylive_api.get_matches", AsyncMock(return_value=fake_dl_matches)):
            streams = await stream_service.get_streams_for_event(
                match_id,
                ep_url="https://ep.test.com",
                ep_pass="secret123",
                base_url="https://addon.test.com",
            )

        updated_match = catalog_service.get_cached_match(match_id)
        dl_ids = [s.get("id") for s in updated_match["sources"] if s.get("source") == "dlhd"]
        assert "old-dead-99" not in dl_ids
        assert "new-fresh-01" in dl_ids
        assert "new-fresh-02" in dl_ids
        # TvVoo channel remained untouched
        assert any(s.get("id") == "tvvoo-persists" for s in updated_match["sources"])
