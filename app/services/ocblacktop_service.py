from __future__ import annotations
import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import OCBLACKTOP_API_BASE, OCBLACKTOP_API_KEY

logger = logging.getLogger("streamsport.ocblacktop")

# Supported motorsport series on Orange Cat Blacktop
OCB_SERIES_CONFIG = [
    # (namespace, catalog_id, genre_id)
    ("formula1", "motori", "Formula 1"),
    ("moto-gp", "motori", "MotoGP e Superbike"),
    ("moto2", "motori", "MotoGP e Superbike"),
    ("moto3", "motori", "MotoGP e Superbike"),
    ("nascar", "motori", "NASCAR e IndyCar"),
    ("nascar-truck", "motori", "NASCAR e IndyCar"),
    ("nascar-xfinity", "motori", "NASCAR e IndyCar"),
    ("indycar", "motori", "NASCAR e IndyCar"),
    ("wec", "motori", "NASCAR e IndyCar"),
]


class OCBlacktopService:
    """
    Official motorsport schedule and session registry powered by Orange Cat Blacktop API.
    Provides session-by-session precision (FP1, FP2, FP3, Qualifiche, Sprint, Gara)
    for MotoGP, Moto2, Moto3, Formula 1, NASCAR, and WEC.
    """

    def __init__(self):
        self._api_base = OCBLACKTOP_API_BASE
        self._api_key = OCBLACKTOP_API_KEY
        self._cached_sessions: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: float = 86400.0  # 24 hours TTL: season calendar is stable
        self._lock = asyncio.Lock()

    async def get_official_sessions(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches the complete season session schedules across all supported series.
        Cached in memory for 24h to strictly respect the 7,500 monthly request limit.
        """
        async with self._lock:
            now = time.time()
            if not force and self._cached_sessions and (now - self._cache_time < self._cache_ttl):
                return self._cached_sessions

            logger.info("Fetching official motorsport schedules from Orange Cat Blacktop API...")
            headers = {
                "x-api-key": self._api_key,
                "Accept": "application/json",
                "User-Agent": "StreamSport/1.0",
            }

            year = datetime.datetime.now(datetime.timezone.utc).year
            all_sessions: List[Dict[str, Any]] = []
            sem = asyncio.Semaphore(5)

            async with httpx.AsyncClient(timeout=10.0) as client:
                async def fetch_series(namespace: str, catalog: str, genre: str):
                    async with sem:
                        url = f"{self._api_base}/{namespace}/events?year={year}&limit=50"
                        try:
                            res = await client.get(url, headers=headers)
                            if res.status_code == 200:
                                data = res.json().get("data", [])
                                series_sessions = []
                                for ev in data:
                                    ev_name = ev.get("name") or ""
                                    loc = ev.get("location") or {}
                                    circ_name = loc.get("name") or ""
                                    city = loc.get("city") or ""
                                    country = (loc.get("country") or {}).get("name") or ""
                                    
                                    for sess in ev.get("schedule", []):
                                        sess_name = sess.get("name") or ""
                                        st = sess.get("startTime")
                                        ms = 0
                                        if st:
                                            try:
                                                dt = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
                                                ms = int(dt.timestamp() * 1000)
                                            except Exception:
                                                ms = 0

                                        series_sessions.append({
                                            "series": namespace,
                                            "catalog": catalog,
                                            "genre": genre,
                                            "event_name": ev_name,
                                            "circuit": circ_name,
                                            "city": city,
                                            "country": country,
                                            "session_name": sess_name,
                                            "session_type": sess.get("type") or "",
                                            "date_ms": ms,
                                        })
                                return series_sessions
                            else:
                                logger.debug("OCBlacktop %s returned status %d", namespace, res.status_code)
                        except Exception as e:
                            logger.warning("Error fetching OCBlacktop %s: %s", namespace, e)
                        return []

                tasks = [
                    fetch_series(namespace, catalog, genre)
                    for namespace, catalog, genre in OCB_SERIES_CONFIG
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res_list in results:
                    if isinstance(res_list, list):
                        all_sessions.extend(res_list)

            self._cached_sessions = all_sessions
            self._cache_time = now
            logger.info("OCBlacktop official registry loaded: %d sessions indexed.", len(all_sessions))
            return self._cached_sessions

    def _normalize_session_name(self, sess_name: str, series: str) -> str:
        """Converts raw API session name to clean user-friendly Italian/international format."""
        s = sess_name.strip()
        # Map common variations
        name_map = {
            "Free Practice 1": "FP1",
            "Free Practice 2": "FP2",
            "Free Practice 3": "FP3",
            "Practice": "FP2",
            "Qualifying 1": "Qualifiche (Q1)",
            "Qualifying 2": "Qualifiche (Q2)",
            "Qualifying": "Qualifiche",
            "Sprint": "Gara Sprint",
            "Sprint Race": "Gara Sprint",
            "Warm Up": "Warm Up",
            "Race": "Gara",
        }
        return name_map.get(s, s)

    def reconcile_matches(
        self,
        stream_matches: List[Dict[str, Any]],
        official_sessions: List[Dict[str, Any]],
    ) -> int:
        """
        Reconciles incoming stream matches against official Orange Cat Blacktop sessions.
        Strict verification: Only matches with official confirmation enter
        'Formula 1' or 'MotoGP e Superbike'.
        """
        if not official_sessions or not stream_matches:
            return 0

        matched_count = 0

        for m in stream_matches:
            title = m.get("title") or ""
            cat = (m.get("category") or "").lower()
            m_date = m.get("date") or 0

            # Only consider motorsport matches or titles mentioning racing keywords
            is_motor = (
                cat in ("motor-sports", "motorsports", "motorsport")
                or any(k in title.lower() for k in ("formula", "f1", "motogp", "moto gp", "moto2", "moto3", "nascar", "wec", "baku", "azerbaijan", "grand prix", "gp"))
            )
            if not is_motor:
                continue

            t_clean = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", title).lower()
            t_clean = re.sub(r"[^a-z0-9]+", " ", t_clean).strip()

            # Determine candidate series based on keywords in title
            candidate_series: List[str] = []
            if any(k in t_clean for k in ("f1", "formula 1", "formula one", "baku", "azerbaijan")):
                candidate_series.append("formula1")
            elif any(k in t_clean for k in ("motogp", "moto gp", "moto2", "moto3", "mugello", "misano", "motegi")):
                candidate_series.extend(["moto-gp", "moto2", "moto3"])
            elif any(k in t_clean for k in ("nascar", "truck", "xfinity", "cup series", "kansas")):
                candidate_series.extend(["nascar", "nascar-truck", "nascar-xfinity"])
            elif any(k in t_clean for k in ("wec", "fuji", "le mans", "6 hours")):
                candidate_series.append("wec")
            elif any(k in t_clean for k in ("indycar", "indy")):
                candidate_series.append("indycar")
            else:
                # General search across all series
                candidate_series = [s[0] for s in OCB_SERIES_CONFIG]

            best_session: Optional[Dict[str, Any]] = None

            for sess in official_sessions:
                if sess["series"] not in candidate_series:
                    continue

                # 1. Date / Time tolerance: if date is present, check within 3 hours
                time_ok = False
                if m_date > 0 and sess["date_ms"] > 0:
                    if abs(m_date - sess["date_ms"]) <= 3 * 3600 * 1000:
                        time_ok = True

                # 2. Grand Prix / Circuit / Location keyword recognition
                ev_name_words = [
                    w for w in re.sub(r"[^a-z0-9]+", " ", sess["event_name"].lower()).split()
                    if len(w) > 3 and w not in ("grand", "prix", "series", "nascar")
                ]
                circ_words = [
                    w for w in re.sub(r"[^a-z0-9]+", " ", sess["circuit"].lower()).split()
                    if len(w) > 3 and w not in ("circuit", "speedway", "international", "raceway", "autodromo")
                ]
                loc_words = [
                    w for w in re.sub(r"[^a-z0-9]+", " ", f"{sess['city']} {sess['country']}".lower()).split()
                    if len(w) > 3
                ]

                has_ev = (
                    any(w in t_clean for w in ev_name_words)
                    or any(w in t_clean for w in circ_words)
                    or any(w in t_clean for w in loc_words)
                )

                # 3. Session Type recognition
                sess_raw = sess["session_name"].lower()
                sess_ok = False
                if "race" in sess_raw and ("race" in t_clean or "gara" in t_clean):
                    sess_ok = True
                elif ("qualif" in sess_raw or "superpole" in sess_raw) and ("qualif" in t_clean or "pole" in t_clean):
                    sess_ok = True
                elif "sprint" in sess_raw and "sprint" in t_clean:
                    sess_ok = True
                elif ("practice" in sess_raw or "fp" in sess_raw) and ("practice" in t_clean or "fp" in t_clean or "prove" in t_clean):
                    # Check specific session number if mentioned
                    if "1" in t_clean and ("1" in sess_raw or "fp1" in sess_raw):
                        sess_ok = True
                    elif "2" in t_clean and ("2" in sess_raw or "fp2" in sess_raw):
                        sess_ok = True
                    elif "3" in t_clean and ("3" in sess_raw or "fp3" in sess_raw):
                        sess_ok = True
                    elif not any(c in t_clean for c in ("1", "2", "3")):
                        sess_ok = True
                elif "warm" in sess_raw and "warm" in t_clean:
                    sess_ok = True

                # Confirmation: must have event/circuit match AND (session match or time match)
                if has_ev and (sess_ok or time_ok):
                    best_session = sess
                    break

            if best_session:
                matched_count += 1
                series = best_session["series"]
                ev_name = best_session["event_name"].title()
                circ_name = best_session["circuit"]
                clean_sess = self._normalize_session_name(best_session["session_name"], series)

                # Canonical title formatting
                series_labels = {
                    "formula1": "Formula 1",
                    "moto-gp": "MotoGP",
                    "moto2": "Moto2",
                    "moto3": "Moto3",
                    "nascar": "NASCAR Cup",
                    "nascar-truck": "NASCAR Trucks",
                    "nascar-xfinity": "NASCAR Xfinity",
                    "indycar": "IndyCar",
                    "wec": "WEC",
                }
                label = series_labels.get(series, "Motori")
                
                # Format: "Formula 1: Azerbaijan Grand Prix - Gara (Baku City Circuit)"
                if circ_name:
                    m["title"] = f"{label}: {ev_name} - {clean_sess} ({circ_name})"
                else:
                    m["title"] = f"{label}: {ev_name} - {clean_sess}"

                if best_session["date_ms"] > 0:
                    m["date"] = best_session["date_ms"]

                m["_catalog"] = best_session["catalog"]
                m["_genre"] = best_session["genre"]
                m["_ocb_matched"] = True
                m["_circuit"] = circ_name
                m["_official_event"] = ev_name

                # Set optimized search query for TheSportsDB poster enrichment
                if series == "formula1":
                    m["_tsdb_query"] = f"{ev_name} Grand Prix"
                elif series in ("moto-gp", "moto2", "moto3"):
                    m["_tsdb_query"] = f"{ev_name}"
                else:
                    m["_tsdb_query"] = circ_name or ev_name

        logger.info("OCBlacktop reconciliation complete: %d motor events verified and canonized.", matched_count)
        return matched_count


ocblacktop_service = OCBlacktopService()
