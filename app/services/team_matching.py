from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

# ==============================================================================
# 1. CENTRALIZED VOCABULARY & ALIASES
# ==============================================================================

def extract_team_name(val: Any) -> str:
    """Safely extracts a team string whether val is a dict {'name': '...'} or a plain str."""
    if isinstance(val, dict):
        return str(val.get("name") or "").strip()
    if isinstance(val, str):
        return val.strip()
    return ""

# Master aliases dictionary unifying Diretta, TheSportsDB, EPG, and Catalog mappings
MASTER_CLUB_ALIASES: Dict[str, str] = {
    # Italian Top Clubs
    r"\binternazionale(?:\s*milano)?\b": "inter",
    r"\binter\s*(?:milan|milano)\b": "inter",
    r"\bfc internazionale\b": "inter",
    r"\bjuve\b": "juventus",
    r"\bmilan\b": "ac milan",
    
    # European Clubs & International Names
    r"\bstella rossa\b": "crvena zvezda",
    r"\bred star(?:\s*belgrade)?\b": "crvena zvezda",
    r"\bstade rennais\b": "rennes",
    r"\brennais\b": "rennes",
    r"\bofi crete\b": "ofi",
    r"\bathletic bilbao\b": "athletic club",
    r"\bshakhtar\b": "shakhtar donetsk",
    r"\bdynamo kiev\b": "dynamo kyiv",
    r"\bdinamo kiev\b": "dynamo kyiv",
    r"\bpartizan belgrado\b": "partizan",
    r"\bolympiakos\b": "olympiacos",
    r"\bolympiakos piraeus\b": "olympiacos",
    r"\bpanathinaikos atene\b": "panathinaikos",
    r"\bfeyenoord rotterdam\b": "feyenoord",
    r"\bpsv eindhoven\b": "psv",
    r"\baz alkmaar\b": "az",
    r"\bgalatasaray istanbul\b": "galatasaray",
    r"\bfenerbahce istanbul\b": "fenerbahce",
    r"\bbesiktas istanbul\b": "besiktas",
    r"\bbod[oøe]/glimt\b": "bodo glimt",
    r"\bmalm[oöe] ff\b": "malmo",
    
    # Premier League / English Clubs
    r"\bmanchester city\b": "man city",
    r"\bmcfc\b": "man city",
    r"\bmanchester united\b": "man utd",
    r"\bman united\b": "man utd",
    r"\bmufc\b": "man utd",
    r"\bwolverhampton\b": "wolves",
    r"\btottenham\b": "spurs",
    
    # Spanish / German / French Clubs
    r"\batletico madrid\b": "atletico madrid",
    r"\batleti\b": "atletico madrid",
    r"\bbarca\b": "barcelona",
    r"\bparis saint germain\b": "psg",
    r"\bparis sg\b": "psg",
    r"\bborussia dortmund\b": "dortmund",
    r"\bbvb\b": "dortmund",
    r"\bbayern munchen\b": "bayern",
    r"\bbayern monaco\b": "bayern",
    r"\brb leipzig\b": "leipzig",
    r"\brb salzburg\b": "salzburg",
    r"\bred bull salzburg\b": "salzburg",
    r"\bsporting lisbona?\b": "sporting",
    r"\bsporting cp\b": "sporting",

    # US Major League Soccer & American Sports
    r"\bla fc\b": "lafc",
    r"\blos angeles fc\b": "lafc",
    r"\bla galaxy\b": "la galaxy",
    r"\blag\b": "la galaxy",
    r"\bnew york city fc\b": "nycfc",
    r"\bnew york city\b": "nycfc",
    r"\bny city\b": "nycfc",
    r"\bnew york red bulls\b": "red bulls",
    r"\bred bulls\b": "red bulls",
    r"\brbny\b": "red bulls",
    r"\bblazers\b": "trailblazers",

    # Italian Volleyball SuperLega Sponsor Stripping
    r"\bsir susa vim perugia\b": "perugia",
    r"\bitas trentino\b": "trentino",
    r"\bcucine lube civitanova\b": "civitanova",
    r"\ballianz milano\b": "milano",
    r"\bmint vero volley monza\b": "monza",
    r"\bvesa monza\b": "monza",
    r"\bgas sales bluenergy piacenza\b": "piacenza",
    r"\bgas sales piacenza\b": "piacenza",
    r"\brana verona\b": "verona",
    r"\bsonepar padova\b": "padova",
    r"\bgioiella prisma taranto\b": "taranto",
    r"\byuasa battery grottazzolina\b": "grottazzolina",
    r"\bcisterna volley\b": "cisterna",
}

# 3-Letter and 4-Letter Acronym Tokens allowed even if short
LEGIT_SHORT_ACRONYMS: Set[str] = {
    "lafc", "nycfc", "rbny", "psg", "bvb", "mufc", "mcfc",
    "psv", "az", "ofi", "aek", "paok", "f1", "f2", "f3", "fe",
    "nba", "nfl", "nhl", "mlb", "mls", "ufc", "wec", "dtm",
}

# Club qualifiers and sports noise words to exclude
EXCLUDED_CLUB_STOPWORDS: Set[str] = {
    "fc", "cf", "ac", "sc", "as", "ss", "ssd", "asd", "usd", "us", "bc",
    "calcio", "basket", "pallacanestro", "baloncesto", "basquet", "volley", "pallavolo",
    "club", "the", "stade", "citta di", "polisportiva", "unione sportiva",
    "lidocalcio", "lido calcio", "vs", "at", "u19", "u20", "u21", "u23", "live", "stream"
}

# Generic words that cannot alone establish a match (prevent "real vs real" false positives)
GENERIC_CLUB_WORDS: Set[str] = {
    "real", "atletico", "sporting", "racing", "deportivo", "dinamo",
    "inter", "union", "united", "city", "club", "estrella", "olimpia",
    "virtus", "san", "santa", "saint", "st"
}

# ==============================================================================
# 2. STRING NORMALIZATION
# ==============================================================================

def clean_team_name(name: str) -> str:
    """
    Standardizes a team name across all sports:
    - Normalizes unicode (NFKD to ASCII)
    - Strips flag emojis and noise
    - Applies master club aliases
    - Strips club prefixes and sports qualifiers
    """
    if not name:
        return ""
    t = name.lower().strip()
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
    t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()

    # Apply master regex aliases
    for pat, rep in MASTER_CLUB_ALIASES.items():
        t = re.sub(pat, rep, t)

    # Strip noisy club qualifiers
    t = re.sub(
        r"\b(fc|cf|bc|sc|ac|as|ss|ssd|asd|usd|us|lidocalcio|lido\s*calcio|citta\s*di|polisportiva|unione\s*sportiva|basket|pallacanestro|calcio|volley|pallavolo|the|stade)\b",
        " ",
        t,
    )
    # Strip youth and women indicators (coherence is strictly guaranteed by youth/women gatekeeper guards)
    t = re.sub(
        r"\b(u19|u20|u21|u23|u-19|u-20|u-21|u-23|youth|primavera|junior|women|femminile|femenino|ladies|frauen)\b",
        " ",
        t,
    )
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extract_team_words(name: str) -> Set[str]:
    """Extracts non-generic, significant words from a team name."""
    cleaned = clean_team_name(name)
    words = set(cleaned.split()) - EXCLUDED_CLUB_STOPWORDS
    return {w for w in words if len(w) > 1 or w in LEGIT_SHORT_ACRONYMS}


# ==============================================================================
# 3. FUZZY TEAM COMPARISON ENGINE
# ==============================================================================

def match_single_team(t1: str, t2: str) -> float:
    """
    Calculates similarity ratio (0.0 to 1.0) between two individual team names.
    Combines exact match, acronym match, substring coverage, word intersection, and SequenceMatcher.
    """
    if not t1 or not t2:
        return 0.0
    c1 = clean_team_name(t1)
    c2 = clean_team_name(t2)
    if not c1 or not c2:
        return 0.0

    # 1. Exact match after normalization
    if c1 == c2:
        return 1.0

    # 2. Space-compact match (e.g. "ostia mare" vs "ostiamare")
    c1_compact = c1.replace(" ", "")
    c2_compact = c2.replace(" ", "")
    if c1_compact == c2_compact:
        return 0.98

    # 3. Substring coverage (if length >= 4 and not generic)
    len1, len2 = len(c1), len(c2)
    if len1 >= 4 and len2 >= 4 and (c1 not in GENERIC_CLUB_WORDS) and (c2 not in GENERIC_CLUB_WORDS):
        if c1 in c2 or c2 in c1:
            ratio = min(len1, len2) / max(len1, len2)
            return max(0.85, ratio)

    # 4. Compact substring (if length >= 5 and not generic)
    if len(c1_compact) >= 5 and len(c2_compact) >= 5 and (c1_compact not in GENERIC_CLUB_WORDS) and (c2_compact not in GENERIC_CLUB_WORDS):
        if c1_compact in c2_compact or c2_compact in c1_compact:
            return 0.82

    # 5. Significant word set intersection (excluding generic terms)
    w1 = set(w for w in c1.split() if len(w) >= 3 or w in LEGIT_SHORT_ACRONYMS) - GENERIC_CLUB_WORDS
    w2 = set(w for w in c2.split() if len(w) >= 3 or w in LEGIT_SHORT_ACRONYMS) - GENERIC_CLUB_WORDS
    if w1 and w2:
        distinct_common = w1 & w2
        if distinct_common:
            return 0.85

    # 6. Fallback SequenceMatcher
    return SequenceMatcher(None, c1, c2).ratio()


def compare_teams_fuzzy(
    h1: str,
    a1: str,
    h2: str,
    a2: str,
    allow_inverted: bool = True,
) -> Tuple[float, float, bool]:
    """
    Symmetrically compares two pairs of teams.
    Returns (home_score, away_score, is_inverted).
    """
    # Direct comparison: H1 vs H2 and A1 vs A2
    h_dir = match_single_team(h1, h2)
    a_dir = match_single_team(a1, a2)
    avg_dir = (h_dir + a_dir) / 2.0

    if not allow_inverted:
        return h_dir, a_dir, False

    # Inverted comparison: H1 vs A2 and A1 vs H2 (for reversed streams or US "@" format)
    h_inv = match_single_team(h1, a2)
    a_inv = match_single_team(a1, h2)
    avg_inv = (h_inv + a_inv) / 2.0

    if avg_inv > avg_dir:
        return h_inv, a_inv, True
    return h_dir, a_dir, False


# ==============================================================================
# 4. LEVEL-SPECIFIC MATCH SCORING
# ==============================================================================

def calculate_match_score(
    match: Dict[str, Any],
    candidate: Dict[str, Any],
    phase: str = "phase1_diretta",
) -> Dict[str, Any]:
    """
    Calculates unified match score tailored for each phase:
    - phase1_diretta: Team similarity is primary (>= 0.75). Time is plausibility guard (<= 90m).
                      Competition is never penalized if missing; veto only on hard conflict (Youth/Women).
    - phase2_epg: Asymmetric window (-90m / +20m). Bonus for channels certified by Diretta.
    - phase3_tsdb: High precision time & competition. Dual-test on raw provider names AND Diretta normalized names.
    """
    result = {
        "matched": False,
        "score": 0.0,
        "is_inverted": False,
        "reason": "",
    }

    m_date = match.get("date") or 0
    c_date = candidate.get("date") or candidate.get("date_ms") or 0

    # --------------------------------------------------------------------------
    # FASE 1: CONVALIDATORE DIRETTA.IT
    # --------------------------------------------------------------------------
    if phase == "phase1_diretta":
        # 1. Time Proximity Guard: must be within 90 minutes if dates exist
        if m_date > 0 and c_date > 0:
            diff_min = abs(m_date - c_date) / 60000.0
            if diff_min > 90.0:
                result["reason"] = f"time_exceeded_{diff_min:.0f}m"
                return result

        # 2. Extract Home & Away
        m_teams = match.get("teams") or {}
        m_h = extract_team_name(m_teams.get("home")) if isinstance(m_teams, dict) else ""
        m_a = extract_team_name(m_teams.get("away")) if isinstance(m_teams, dict) else ""
        if not m_h or not m_a:
            t = match.get("title") or ""
            clean_t = re.sub(r"^[0-9:\s-]+(?:\s*:\s*)?", "", t)
            clean_t = re.sub(r"\([^)]*\)", "", clean_t)
            if re.search(r"\s+vs\.?\s+", clean_t, re.IGNORECASE):
                parts = re.split(r"\s+vs\.?\s+", clean_t, maxsplit=1, flags=re.IGNORECASE)
                m_h, m_a = parts[0].strip(), parts[1].strip()
            elif re.search(r"\s+[\-–—]\s+", clean_t):
                parts = re.split(r"\s+[\-–—]\s+", clean_t, maxsplit=1)
                m_h, m_a = parts[0].strip(), parts[1].strip()
            elif re.search(r"\s+v\.?\s+", clean_t, re.IGNORECASE):
                parts = re.split(r"\s+v\.?\s+", clean_t, maxsplit=1, flags=re.IGNORECASE)
                m_h, m_a = parts[0].strip(), parts[1].strip()

        c_h = candidate.get("home") or ""
        c_a = candidate.get("away") or ""
        if not c_h or not c_a:
            c_teams = candidate.get("teams") or {}
            if isinstance(c_teams, dict):
                c_h = c_h or extract_team_name(c_teams.get("home"))
                c_a = c_a or extract_team_name(c_teams.get("away"))
            if not c_h or not c_a:
                c_t = candidate.get("title") or ""
                clean_ct = re.sub(r"^[0-9:\s-]+(?:\s*:\s*)?", "", c_t)
                clean_ct = re.sub(r"\([^)]*\)", "", clean_ct)
                if re.search(r"\s+vs\.?\s+", clean_ct, re.IGNORECASE):
                    c_parts = re.split(r"\s+vs\.?\s+", clean_ct, maxsplit=1, flags=re.IGNORECASE)
                    c_h = c_h or c_parts[0].strip()
                    c_a = c_a or c_parts[1].strip()
                elif re.search(r"\s+[\-–—]\s+", clean_ct):
                    c_parts = re.split(r"\s+[\-–—]\s+", clean_ct, maxsplit=1)
                    c_h = c_h or c_parts[0].strip()
                    c_a = c_a or c_parts[1].strip()

        if not m_h or not m_a or not c_h or not c_a:
            result["reason"] = "missing_team_names"
            return result

        # 3. Trait Evaluation: Youth and Women
        # Neutral ('=') is the natural default when unspecified.
        # Only explicit markers indicate '+' (Women / Youth).
        # We never assign or infer '-' from league names.
        m_comp = (match.get("competition") or match.get("_competition") or "").lower()
        c_comp = (candidate.get("competition") or "").lower()
        m_text = f"{m_comp} {m_h} {m_a}".lower()
        c_text = f"{c_comp} {c_h} {c_a}".lower()

        m_youth = bool(re.search(r"\b(u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|youth|primavera|junior)\b", m_text))
        c_youth = bool(re.search(r"\b(u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|youth|primavera|junior)\b", c_text))

        m_women = bool(re.search(r"\b(women(?:[’']s)?|femminile|femenino|ladies|frauen|w-|wta|wnba|uwcl)\b", m_text))
        c_women = bool(re.search(r"\b(women(?:[’']s)?|femminile|femenino|ladies|frauen|w-|wta|wnba|uwcl)\b", c_text))

        # Penalty if stream claimed Women (+) but candidate lacks it (=)
        # When stream is neutral (=) and candidate is Women (+), penalty is 0.0 (inherits trait)
        trait_penalty = 0.0
        if m_women and not c_women:
            trait_penalty += 0.30
            result["reason"] = "conflict_women"
        elif m_youth != c_youth:
            trait_penalty += 0.30
            result["reason"] = "conflict_youth"

        # 4. Bilateral Team Similarity Score (SequenceMatcher ponderato)
        h_score, a_score, is_inv = compare_teams_fuzzy(m_h, m_a, c_h, c_a, allow_inverted=True)
        # Check alternate English names if available on candidate
        c_alt_h = candidate.get("alt_home") or ""
        c_alt_a = candidate.get("alt_away") or ""
        if c_alt_h and c_alt_a:
            alt_h, alt_a, alt_inv = compare_teams_fuzzy(m_h, m_a, c_alt_h, c_alt_a, allow_inverted=True)
            if (alt_h + alt_a) > (h_score + a_score):
                h_score, a_score, is_inv = alt_h, alt_a, alt_inv

        avg_score = max(0.0, (h_score + a_score) / 2.0 - trait_penalty)

        # Strict bilateral threshold: both teams must have acceptable similarity
        # (each >= 0.65 and average >= 0.75, or one exact >= 0.95 and other >= 0.60 and average >= 0.75)
        is_matched = (h_score >= 0.65 and a_score >= 0.65 and avg_score >= 0.75) or (
            max(h_score, a_score) >= 0.95 and min(h_score, a_score) >= 0.60 and avg_score >= 0.75
        )

        # 5. Flashscore Match ID Bridge: gentle +0.05 bonus on ID match, -0.05 penalty on mismatch
        m_fs_id = match.get("_flashscore_id")
        c_fs_id = candidate.get("flashscore_id") or candidate.get("id")
        if m_fs_id and c_fs_id:
            m_clean = str(m_fs_id).replace("diretta-", "").strip()
            c_clean = str(c_fs_id).replace("diretta-", "").strip()
            if m_clean == c_clean:
                avg_score = min(1.0, avg_score + 0.05)
                if (h_score >= 0.60 and a_score >= 0.60):
                    is_matched = True
            else:
                avg_score = max(0.0, avg_score - 0.05)
                if avg_score < 0.75:
                    is_matched = False

        # Optional Competition Bonus: if both have competition and they match, add small bonus
        bonus = 0.0
        if m_comp and c_comp:
            if m_comp in c_comp or c_comp in m_comp:
                bonus = 0.05

        final_score = min(1.0, avg_score + bonus)
        result["matched"] = is_matched
        result["score"] = final_score
        result["is_inverted"] = is_inv
        if is_matched:
            result["reason"] = "ok"
        elif not result.get("reason"):
            result["reason"] = f"low_similarity_h{h_score:.2f}_a{a_score:.2f}"
        return result

    # --------------------------------------------------------------------------
    # FASE 2: PALINSESTO TV ITALIANO (EPG SKY / TIVÙSAT)
    # --------------------------------------------------------------------------
    if phase == "phase2_epg":
        m_silo = (match.get("_silo") or match.get("category") or "").lower()
        if m_date > 0 and c_date > 0:
            pre_max = (120 * 60 * 1000) if m_silo == "motor-sports" else (90 * 60 * 1000)
            post_max = 20 * 60 * 1000
            time_diff = m_date - c_date  # Positive if EPG starts before match
            if time_diff > pre_max or time_diff < -post_max:
                result["reason"] = f"epg_asymmetric_window_exceeded_{time_diff / 60000:.0f}m"
                return result

        # Channel check against Diretta certified channels
        diretta_tvs = match.get("_diretta_tv_channels") or []
        channel_name = candidate.get("channel_name") or ""
        channel_confirmed = any(channel_name.lower() in dtv.lower() for dtv in diretta_tvs) if channel_name else False

        m_teams = match.get("teams") or {}
        m_h = extract_team_name(m_teams.get("home")) if isinstance(m_teams, dict) else ""
        m_a = extract_team_name(m_teams.get("away")) if isinstance(m_teams, dict) else ""
        c_teams = candidate.get("teams") or {}
        c_h = extract_team_name(c_teams.get("home")) if isinstance(c_teams, dict) else ""
        c_a = extract_team_name(c_teams.get("away")) if isinstance(c_teams, dict) else ""

        if m_h and m_a and c_h and c_a:
            h_score, a_score, is_inv = compare_teams_fuzzy(m_h, m_a, c_h, c_a, allow_inverted=True)
            avg_score = (h_score + a_score) / 2.0
            if (h_score >= 0.70 and a_score >= 0.70) or (channel_confirmed and avg_score >= 0.60):
                result["matched"] = True
                result["score"] = avg_score + (0.1 if channel_confirmed else 0.0)
                result["is_inverted"] = is_inv
                result["reason"] = "epg_teams_matched"
                return result

        # Token matching fallback
        m_title = match.get("title") or ""
        c_title = candidate.get("title") or ""
        seq = SequenceMatcher(None, clean_team_name(m_title), clean_team_name(c_title)).ratio()
        if seq >= 0.70 or (channel_confirmed and seq >= 0.50):
            result["matched"] = True
            result["score"] = seq + (0.1 if channel_confirmed else 0.0)
            result["reason"] = "epg_tokens_matched"
            return result

        result["reason"] = "epg_no_match"
        return result

    # --------------------------------------------------------------------------
    # FASE 3: THESPORTSDB LOCANDINE (DOPPIO BINARIO)
    # --------------------------------------------------------------------------
    if phase == "phase3_tsdb":
        # Check strict time proximity (max 90 min)
        if m_date > 0 and c_date > 0:
            diff_min = abs(m_date - c_date) / 60000.0
            if diff_min > 90.0:
                result["reason"] = f"tsdb_time_exceeded_{diff_min:.0f}m"
                return result

        c_h = candidate.get("home") or ""
        c_a = candidate.get("away") or ""
        if not c_h or not c_a:
            result["reason"] = "tsdb_missing_candidate_teams"
            return result

        best_score = 0.0
        best_inv = False

        # Test 1: Raw provider teams
        raw_teams = match.get("teams") or {}
        raw_h = extract_team_name(raw_teams.get("home")) if isinstance(raw_teams, dict) else ""
        raw_a = extract_team_name(raw_teams.get("away")) if isinstance(raw_teams, dict) else ""
        if raw_h and raw_a:
            h_score, a_score, is_inv = compare_teams_fuzzy(raw_h, raw_a, c_h, c_a, allow_inverted=True)
            if h_score >= 0.65 and a_score >= 0.65:
                best_score = (h_score + a_score) / 2.0
                best_inv = is_inv

        # Test 2: Diretta.it normalized teams (if present)
        dir_h = match.get("_diretta_home") or ""
        dir_a = match.get("_diretta_away") or ""
        if dir_h and dir_a:
            h_score, a_score, is_inv = compare_teams_fuzzy(dir_h, dir_a, c_h, c_a, allow_inverted=True)
            if h_score >= 0.65 and a_score >= 0.65:
                dir_avg = (h_score + a_score) / 2.0
                if dir_avg > best_score:
                    best_score = dir_avg
                    best_inv = is_inv

        # Test 3: Raw Title tokens if teams failed
        if best_score < 0.70:
            for title_cand in match.get("raw_titles", [match.get("title", "")]):
                if re.search(r"\s+vs\.?\s+", title_cand, re.IGNORECASE):
                    parts = re.split(r"\s+vs\.?\s+", title_cand, maxsplit=1, flags=re.IGNORECASE)
                    h_score, a_score, is_inv = compare_teams_fuzzy(parts[0], parts[1], c_h, c_a, allow_inverted=True)
                    if h_score >= 0.65 and a_score >= 0.65:
                        cand_avg = (h_score + a_score) / 2.0
                        if cand_avg > best_score:
                            best_score = cand_avg
                            best_inv = is_inv

        if best_score >= 0.75:
            result["matched"] = True
            result["score"] = best_score
            result["is_inverted"] = best_inv
            result["reason"] = "tsdb_matched"
        else:
            result["reason"] = f"tsdb_score_insufficient_{best_score:.2f}"

        return result

    result["reason"] = f"unknown_phase_{phase}"
    return result
