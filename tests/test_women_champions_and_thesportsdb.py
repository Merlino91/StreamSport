import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.team_matching import clean_team_name, match_single_team
from app.services.genre_classifier import genre_classifier
from app.services.thesportsdb_service import thesportsdb_service
from app.services.canonical_competition_service import is_tier_match, detect_canonical_competition
from app.config import NUVIO_CATALOG_LOOKUP, CATALOG_MAP


class TestWomenChampionsAndTheSportsDB(unittest.IsolatedAsyncioTestCase):
    def test_clean_team_name_across_sports(self):
        """Verifies clean_team_name strips trailing D, W, F suffixes across football, basketball, and volley."""
        # 1. Football
        self.assertEqual(clean_team_name("Juventus D"), "juventus")
        self.assertEqual(clean_team_name("Juventus (D)"), "juventus")
        self.assertEqual(clean_team_name("Chelsea W"), "chelsea")
        self.assertEqual(clean_team_name("Chelsea FC (W)"), "chelsea")
        self.assertEqual(clean_team_name("Paris SG F"), "psg")
        self.assertEqual(clean_team_name("Wolfsburg D"), "wolfsburg")
        self.assertEqual(clean_team_name("Arsenal Women"), "arsenal")

        # 2. Basketball
        self.assertEqual(clean_team_name("Famila Schio D"), "famila schio")
        self.assertEqual(clean_team_name("Virtus Bologna D"), "virtus bologna")
        self.assertEqual(clean_team_name("Valencia Basket (W)"), "valencia")
        self.assertEqual(clean_team_name("Fenerbahce D"), "fenerbahce")

        # 3. Volleyball
        self.assertEqual(clean_team_name("Conegliano D"), "conegliano")
        self.assertEqual(clean_team_name("Prosecco Doc Imoco Conegliano D"), "prosecco doc imoco conegliano")
        self.assertEqual(clean_team_name("Vero Volley Milano D"), "vero milano")
        self.assertEqual(clean_team_name("Vero Volley Milano Women"), "vero milano")
        self.assertEqual(match_single_team("Vero Volley Milano D", "Vero Volley Milano Women"), 1.0)
        self.assertEqual(clean_team_name("Scandicci (D)"), "scandicci")

        # 4. Safe non-stripping for Dinamo / D. Zagreb / etc.
        self.assertEqual(clean_team_name("D. Zagreb"), "d zagreb")
        self.assertEqual(clean_team_name("Dinamo Zagreb"), "dinamo zagreb")
        self.assertEqual(clean_team_name("Real Madrid"), "real madrid")

    def test_team_matching_diretta_d_vs_thesportsdb_women(self):
        """Verifies Diretta 'D' matches TheSportsDB 'Women' with high score (1.0 exact)."""
        score = match_single_team("Juventus D", "Juventus Women")
        self.assertEqual(score, 1.0)

        score_bask = match_single_team("Schio D", "Famila Schio Women")
        self.assertTrue(score_bask >= 0.75)

        score_chelsea = match_single_team("Chelsea W", "Chelsea FC Women")
        self.assertEqual(score_chelsea, 1.0)

    def test_uwcl_classification_separate_from_mens_champions(self):
        """Verifies Women's Champions League goes to 'Champions League Femminile', not 'Champions League'."""
        # 1. Official match with UWCL canonical slug
        uwcl_match = {
            "title": "Arsenal vs Juventus",
            "competition": "UEFA Women's Champions League",
            "country": "Europa",
            "category": "football",
            "_gender": "+",
            "canonical_competition": "football_uwcl",
        }
        cat, genre = genre_classifier.classify(uwcl_match)
        self.assertEqual(cat, "calcio_estero")
        self.assertEqual(genre, "Champions League Femminile")

        # 2. Raw match with 'uwcl' keyword
        raw_uwcl = {
            "title": "UWCL: Lyon vs Bayern",
            "competition": "UWCL",
            "category": "football",
        }
        cat, genre = genre_classifier.classify(raw_uwcl)
        self.assertEqual(cat, "calcio_estero")
        self.assertEqual(genre, "Champions League Femminile")

        # 3. Match with 'Champions League' and 'D' team suffix
        d_uwcl = {
            "title": "Juventus D vs Arsenal D",
            "competition": "Champions League",
            "country": "Europa",
            "category": "football",
        }
        cat, genre = genre_classifier.classify(d_uwcl)
        self.assertEqual(cat, "calcio_estero")
        self.assertEqual(genre, "Champions League Femminile")

        # 4. Men's Champions League remains strictly in 'Champions League'
        mens_ucl = {
            "title": "Real Madrid vs Bayern Munich",
            "competition": "UEFA Champions League",
            "country": "Europa",
            "category": "football",
            "canonical_competition": "football_uefa_cl",
        }
        cat_m, genre_m = genre_classifier.classify(mens_ucl)
        self.assertEqual(cat_m, "calcio_estero")
        self.assertEqual(genre_m, "Champions League")

    def test_canonical_is_tier_match_admits_uwcl_and_rejects_foreign_domestic_women(self):
        """Verifies is_tier_match admits UWCL but rejects WSL, Liga F, Frauen-Bundesliga."""
        # 1. UWCL admitted
        admitted, slug = is_tier_match({
            "category": "football",
            "country": "Europa",
            "league": "Champions League Donne",
        })
        self.assertTrue(admitted)
        self.assertEqual(slug, "football_uwcl")

        # 2. English Women's Super League rejected
        admitted_eng, _ = is_tier_match({
            "category": "football",
            "country": "Inghilterra",
            "league": "Women's Super League",
        })
        self.assertFalse(admitted_eng)

        # 3. Spanish Liga F rejected
        admitted_esp, _ = is_tier_match({
            "category": "football",
            "country": "Spagna",
            "league": "Liga F Femenina",
        })
        self.assertFalse(admitted_esp)

        # 4. Italian Serie A Femminile admitted to Serie A Women
        admitted_ita, slug_ita = is_tier_match({
            "category": "football",
            "country": "Italia",
            "league": "Serie A Femminile",
        })
        self.assertTrue(admitted_ita)
        self.assertEqual(slug_ita, "football_serie_a_women")

    def test_nuvio_lookup_for_champions_league_femminile(self):
        """Verifies Nuvio flat catalog lookup correctly routes the new catalog."""
        cid = "nuvio_calcio_estero_champions_league_femminile"
        self.assertIn(cid, NUVIO_CATALOG_LOOKUP)
        parent, genre = NUVIO_CATALOG_LOOKUP[cid]
        self.assertEqual(parent, "calcio_estero")
        self.assertEqual(genre, "Champions League Femminile")

        # Check Stremio catalog definition contains the new genre
        estero = CATALOG_MAP["calcio_estero"]
        self.assertIn("Champions League Femminile", estero["genres"])
        self.assertIn("Champions League", estero["genres"])

    def test_thesportsdb_level1_reconciliation_d_vs_women(self):
        """Verifies Level 1 calendar reconciliation connects Diretta 'D' matches to TheSportsDB 'Women' events."""
        thesportsdb_service._calendar_cache = [{
            "title": "Arsenal Women vs Juventus Women",
            "home": "Arsenal Women",
            "away": "Juventus Women",
            "sport": "Soccer",
            "_silo": "football",
            "competition": "UEFA Women's Champions League",
            "canonical_competition": "football_uwcl",
            "is_youth": False,
            "is_women": True,
            "date_ms": 1700000000000,
            "thumb": "https://www.thesportsdb.com/images/media/event/thumb/arsenal_juve.jpg",
        }]
        thesportsdb_service._calendar_by_silo = {"football": thesportsdb_service._calendar_cache}

        incoming_match = {
            "id": "diretta-test123",
            "title": "Arsenal D vs Juventus D",
            "home": "Arsenal D",
            "away": "Juventus D",
            "teams": {"home": {"name": "Arsenal D"}, "away": {"name": "Juventus D"}},
            "competition": "Champions League",
            "date": 1700000000000,
            "category": "football",
            "_silo": "football",
            "_gender": "+",
            "sources": [],
        }

        reconciled = thesportsdb_service.reconcile_matches([incoming_match])
        self.assertEqual(reconciled, 1)
        self.assertEqual(incoming_match.get("poster"), "https://www.thesportsdb.com/images/media/event/thumb/arsenal_juve.jpg")

    async def test_thesportsdb_search_event_thumb_html_d_vs_women(self):
        """Verifies Level 2 HTML search accepts Women's results when target uses 'D'."""
        html_response = """
        <html>
            <body>
                <a href="/event/999-arsenal-women-vs-juventus-women">
                    <img src="/sports/Soccer.svg"/>
                    <img src="https://www.thesportsdb.com/images/media/event/thumb/arsenal_juve.jpg"/>
                    Arsenal vs Juventus
                </a> (2024-11-20)
            </body>
        </html>
        """
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = html_response
        mock_client.get.return_value = mock_res

        with patch.object(thesportsdb_service, "_get_client", AsyncMock(return_value=mock_client)):
            # Target has 'D' in team names:
            thumb = await thesportsdb_service.search_event_thumb_html(
                home_raw="Arsenal D",
                away_raw="Juventus D",
                match_date_ms=1732100000000,
                expected_silo="football",
            )
            # Should NOT be rejected by is_target_women != is_result_women guard
            self.assertIsNotNone(thumb)


if __name__ == "__main__":
    unittest.main()
