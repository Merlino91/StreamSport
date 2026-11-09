from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import DATA_DIR, DEFAULT_DATA_DIR, ENABLE_REPLAYS

import tempfile

logger = logging.getLogger("streamsport.db")

DB_PATH = DATA_DIR / "events.db"

class DBService:
    """Manages SQLite storage for keeping sports events active for 72 hours for replays."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._fallback_path: Optional[Path] = None
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._all_conns: List[sqlite3.Connection] = []
        self._ensure_db()

    def _get_connection(self) -> sqlite3.Connection:
        target_path = self._fallback_path or self._db_path
        cached_conn = getattr(self._local, "conn", None)
        cached_path = getattr(self._local, "conn_path", None)
        if cached_conn is not None and cached_path == target_path:
            try:
                cached_conn.total_changes
                return cached_conn
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                self._local.conn = None
                self._local.conn_path = None

        try:
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            conn = sqlite3.connect(
                str(target_path),
                timeout=30.0,
                check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute("PRAGMA synchronous = NORMAL")
            except Exception:
                pass
            self._local.conn = conn
            self._local.conn_path = target_path
            with self._conn_lock:
                self._all_conns.append(conn)
            return conn
        except (sqlite3.OperationalError, OSError, PermissionError) as e:
            if not self._fallback_path:
                logger.error(
                    "SQLite cannot open '%s' (%s). Switching to fallback directory in temp storage.",
                    target_path, e
                )
                fallback_dir = Path(tempfile.gettempdir()) / "streamsport_data"
                try:
                    fallback_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                self._fallback_path = fallback_dir / "events.db"
                self._ensure_db(path_to_ensure=self._fallback_path)
                return self._get_connection()
            raise

    def close(self):
        """Closes all cached connections across threads."""
        with self._conn_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
        self._local.conn = None
        self._local.conn_path = None

    def _ensure_db(self, path_to_ensure: Optional[Path] = None):
        """Initializes data directory and database schema."""
        target = path_to_ensure or self._fallback_path or self._db_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        category TEXT NOT NULL,
                        date INTEGER NOT NULL,
                        end_date INTEGER DEFAULT 0,
                        poster TEXT,
                        popular INTEGER DEFAULT 0,
                        sources TEXT,
                        competition TEXT,
                        country TEXT,
                        canonical_competition TEXT,
                        teams TEXT,
                        alt_home TEXT,
                        alt_away TEXT,
                        alt_title TEXT,
                        is_women INTEGER DEFAULT 0,
                        is_youth INTEGER DEFAULT 0,
                        updated_at INTEGER NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_cat ON matches(category)")

                # Safe migrations: ensure optional columns exist if table already existed
                cursor.execute("PRAGMA table_info(matches)")
                cols = {c[1] for c in cursor.fetchall()}
                if "end_date" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN end_date INTEGER DEFAULT 0")
                if "competition" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN competition TEXT")
                if "country" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN country TEXT")
                if "canonical_competition" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN canonical_competition TEXT")
                if "teams" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN teams TEXT")
                if "alt_home" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN alt_home TEXT")
                if "alt_away" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN alt_away TEXT")
                if "alt_title" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN alt_title TEXT")
                if "is_women" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN is_women INTEGER DEFAULT 0")
                if "is_youth" not in cols:
                    cursor.execute("ALTER TABLE matches ADD COLUMN is_youth INTEGER DEFAULT 0")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS thesportsdb_cache (
                        query_key TEXT PRIMARY KEY,
                        thumb_url TEXT,
                        status TEXT NOT NULL,
                        checked_at INTEGER NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_thesportsdb_status ON thesportsdb_cache(status)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tennis_athletes_cache (
                        slug TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        image_url TEXT NOT NULL,
                        gender TEXT NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tennis_athletes_slug ON tennis_athletes_cache(slug)")
                conn.commit()
        except Exception as e:
            logger.error("Failed to ensure database schema at %s: %s", target, e)

    def upsert_matches(self, matches: List[Dict[str, Any]]):
        """Inserts or updates matches in SQLite store."""
        if not matches:
            return

        now = int(time.time())
        records = []
        for m in matches:
            m_id = m.get("id")
            if not m_id:
                continue
            title = m.get("title", "Live Sports")
            category = (m.get("category") or "other").lower()
            date = int(m.get("date", 0))
            end_date = int(m.get("end_date") or 0)
            poster = m.get("poster") or ""
            popular = 1 if m.get("popular") else 0
            sources = json.dumps(m.get("sources", []))
            comp = m.get("competition") or m.get("_competition") or ""
            country = m.get("country") or m.get("_country") or ""
            canon = m.get("canonical_competition") or m.get("_canonical_competition") or ""
            teams_str = json.dumps(m["teams"]) if isinstance(m.get("teams"), dict) else ""
            alt_home = m.get("alt_home") or ""
            alt_away = m.get("alt_away") or ""
            alt_title = m.get("alt_title") or ""
            is_w = 1 if m.get("is_women") else 0
            is_y = 1 if m.get("is_youth") else 0
            records.append((m_id, title, category, date, end_date, poster, popular, sources, comp, country, canon, teams_str, alt_home, alt_away, alt_title, is_w, is_y, now))

        if not records:
            return

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany("""
                    INSERT INTO matches (id, title, category, date, end_date, poster, popular, sources, competition, country, canonical_competition, teams, alt_home, alt_away, alt_title, is_women, is_youth, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        category = excluded.category,
                        date = excluded.date,
                        end_date = CASE
                            WHEN excluded.end_date IS NOT NULL AND excluded.end_date > 0 THEN excluded.end_date
                            ELSE matches.end_date
                        END,
                        poster = CASE 
                            WHEN excluded.poster IS NOT NULL AND excluded.poster != '' THEN excluded.poster 
                            ELSE matches.poster 
                        END,
                        popular = excluded.popular,
                        sources = excluded.sources,
                        competition = CASE
                            WHEN excluded.competition IS NOT NULL AND excluded.competition != '' THEN excluded.competition
                            ELSE matches.competition
                        END,
                        country = CASE
                            WHEN excluded.country IS NOT NULL AND excluded.country != '' THEN excluded.country
                            ELSE matches.country
                        END,
                        canonical_competition = CASE
                            WHEN excluded.canonical_competition IS NOT NULL AND excluded.canonical_competition != '' THEN excluded.canonical_competition
                            ELSE matches.canonical_competition
                        END,
                        teams = CASE
                            WHEN excluded.teams IS NOT NULL AND excluded.teams != '' THEN excluded.teams
                            ELSE matches.teams
                        END,
                        alt_home = CASE
                            WHEN excluded.alt_home IS NOT NULL AND excluded.alt_home != '' THEN excluded.alt_home
                            ELSE matches.alt_home
                        END,
                        alt_away = CASE
                            WHEN excluded.alt_away IS NOT NULL AND excluded.alt_away != '' THEN excluded.alt_away
                            ELSE matches.alt_away
                        END,
                        alt_title = CASE
                            WHEN excluded.alt_title IS NOT NULL AND excluded.alt_title != '' THEN excluded.alt_title
                            ELSE matches.alt_title
                        END,
                        is_women = CASE
                            WHEN excluded.is_women = 1 THEN 1
                            ELSE matches.is_women
                        END,
                        is_youth = CASE
                            WHEN excluded.is_youth = 1 THEN 1
                            ELSE matches.is_youth
                        END,
                        updated_at = excluded.updated_at
                """, records)
                conn.commit()
        except Exception as e:
            logger.error("Failed to upsert matches to SQLite: %s", e)

    def update_match_poster(self, match_id: str, poster_url: str):
        """Updates the poster of a specific match in SQLite."""
        if not match_id or not poster_url:
            return
        now = int(time.time())
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE matches SET poster = ?, updated_at = ? WHERE id = ?
                """, (poster_url, now, match_id))
                conn.commit()
            from app.services.banner_service import banner_service
            banner_service.invalidate_match(match_id)
        except Exception as e:
            logger.error("Failed to update match poster for %s: %s", match_id, e)

    def get_poster_from_cache(self, query_key: str) -> Optional[Tuple[str, Optional[str], int]]:
        """
        Retrieves cached TheSportsDB poster result.
        Returns (status, thumb_url, checked_at) or None.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status, thumb_url, checked_at FROM thesportsdb_cache WHERE query_key = ?", (query_key,))
                row = cursor.fetchone()
                if not row:
                    return None
                return (row["status"], row["thumb_url"], row["checked_at"])
        except Exception as e:
            logger.debug("Failed to get poster from cache for %s: %s", query_key, e)
            return None

    def save_poster_to_cache(self, query_key: str, thumb_url: Optional[str], status: str):
        """Saves a poster query result to the SQLite cache."""
        now = int(time.time())
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO thesportsdb_cache (query_key, thumb_url, status, checked_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(query_key) DO UPDATE SET
                        thumb_url = excluded.thumb_url,
                        status = excluded.status,
                        checked_at = excluded.checked_at
                """, (query_key, thumb_url, status, now))
                conn.commit()
        except Exception as e:
            logger.debug("Failed to save poster to cache for %s: %s", query_key, e)

    def clear_failed_poster_cache(self):
        """Clears all 'not_found' entries from thesportsdb_cache so they can be re-evaluated."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM thesportsdb_cache WHERE status = 'not_found'")
                conn.commit()
        except Exception as e:
            logger.debug("Failed to clear failed poster cache: %s", e)

    def get_match_by_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single match from the database by ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_match(row)
        except Exception as e:
            logger.warning("Failed to fetch match %s from DB: %s", match_id, e)
            return None

    def save_matches(self, matches: List[Dict[str, Any]]):
        """Alias for upsert_matches."""
        self.upsert_matches(matches)

    def get_active_matches(self, max_age_hours: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Returns all active matches: current/upcoming or concluded within max_age_hours.
        Defaults to 72h if ENABLE_REPLAYS is True, else 6h.
        """
        if max_age_hours is None:
            max_age_hours = 72 if ENABLE_REPLAYS else 6
        now_ms = int(time.time() * 1000)
        cutoff_oldest = now_ms - (max_age_hours * 3600 * 1000)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM matches
                    WHERE (date = 0) OR (date >= ?)
                    ORDER BY date DESC
                """, (cutoff_oldest,))
                rows = cursor.fetchall()
                return [self._row_to_match(r) for r in rows]
        except Exception as e:
            logger.error("Failed to get active matches from DB: %s", e)
            return []

    def purge_expired_matches(self, max_age_hours: Optional[int] = None) -> int:
        """Deletes matches concluded more than max_age_hours ago (defaults to 72h if ENABLE_REPLAYS else 6h)."""
        if max_age_hours is None:
            max_age_hours = 72 if ENABLE_REPLAYS else 6
        now_ms = int(time.time() * 1000)
        cutoff_oldest = now_ms - (max_age_hours * 3600 * 1000)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE date > 0 AND date < ?", (cutoff_oldest,))
                deleted = cursor.rowcount
                # Purge failed ('not_found') poster lookups older than 24 hours to keep cache lean
                now_sec = int(time.time())
                cursor.execute("DELETE FROM thesportsdb_cache WHERE status = 'not_found' AND checked_at < ?", (now_sec - 86400,))
                conn.commit()
                if deleted > 0:
                    logger.info("Purged %d expired matches older than %d hours (ENABLE_REPLAYS=%s)", deleted, max_age_hours, ENABLE_REPLAYS)
                return deleted
        except Exception as e:
            logger.error("Failed to purge expired matches from DB: %s", e)
            return 0

    def delete_matches_by_ids(self, match_ids: List[str]) -> int:
        """Deletes specific matches by their IDs from the database."""
        if not match_ids:
            return 0
        total_deleted = 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                chunk_size = 500
                for i in range(0, len(match_ids), chunk_size):
                    chunk = match_ids[i:i + chunk_size]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", chunk)
                    total_deleted += cursor.rowcount
                conn.commit()
                if total_deleted > 0:
                    logger.info("Purged %d ineligible concluded matches from SQLite database.", total_deleted)
                return total_deleted
        except Exception as e:
            logger.error("Failed to delete matches from DB: %s", e)
            return 0

    # Asynchronous non-blocking wrappers using asyncio.to_thread
    async def upsert_matches_async(self, matches: List[Dict[str, Any]]) -> None:
        return await asyncio.to_thread(self.upsert_matches, matches)

    async def save_matches_async(self, matches: List[Dict[str, Any]]) -> None:
        return await asyncio.to_thread(self.save_matches, matches)

    async def get_active_matches_async(self, max_age_hours: Optional[int] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_active_matches, max_age_hours)

    async def get_match_by_id_async(self, match_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_match_by_id, match_id)

    async def purge_expired_matches_async(self, max_age_hours: Optional[int] = None) -> int:
        return await asyncio.to_thread(self.purge_expired_matches, max_age_hours)

    async def delete_matches_by_ids_async(self, match_ids: List[str]) -> int:
        return await asyncio.to_thread(self.delete_matches_by_ids, match_ids)

    async def update_match_poster_async(self, match_id: str, poster_url: str) -> None:
        return await asyncio.to_thread(self.update_match_poster, match_id, poster_url)

    async def get_poster_from_cache_async(self, query_key: str) -> Optional[Tuple[str, Optional[str], int]]:
        return await asyncio.to_thread(self.get_poster_from_cache, query_key)

    async def save_poster_to_cache_async(self, query_key: str, thumb_url: Optional[str], status: str) -> None:
        return await asyncio.to_thread(self.save_poster_to_cache, query_key, thumb_url, status)

    async def clear_failed_poster_cache_async(self) -> None:
        return await asyncio.to_thread(self.clear_failed_poster_cache)

    def _row_to_match(self, row: sqlite3.Row) -> Dict[str, Any]:
        sources = []
        try:
            if row["sources"]:
                sources = json.loads(row["sources"])
        except Exception:
            sources = []

        keys = row.keys() if hasattr(row, "keys") else []
        comp = row["competition"] if "competition" in keys and row["competition"] else None
        country = row["country"] if "country" in keys and row["country"] else None
        canon = row["canonical_competition"] if "canonical_competition" in keys and row["canonical_competition"] else None
        end_date = row["end_date"] if "end_date" in keys and row["end_date"] else 0

        d = {
            "id": row["id"],
            "title": row["title"],
            "category": row["category"],
            "date": row["date"],
            "poster": row["poster"],
            "popular": bool(row["popular"]),
            "sources": sources,
        }
        if end_date:
            d["end_date"] = end_date
        if comp:
            d["competition"] = comp
            d["_competition"] = comp
        if country:
            d["country"] = country
            d["_country"] = country
        if canon:
            d["canonical_competition"] = canon
            d["_canonical_competition"] = canon

        if "teams" in keys and row["teams"]:
            try:
                teams_obj = json.loads(row["teams"])
                if isinstance(teams_obj, dict):
                    d["teams"] = teams_obj
                    if "home" in teams_obj and isinstance(teams_obj["home"], dict):
                        d["home"] = teams_obj["home"].get("name", "")
                    if "away" in teams_obj and isinstance(teams_obj["away"], dict):
                        d["away"] = teams_obj["away"].get("name", "")
            except Exception:
                pass

        if "alt_home" in keys and row["alt_home"]:
            d["alt_home"] = row["alt_home"]
        if "alt_away" in keys and row["alt_away"]:
            d["alt_away"] = row["alt_away"]
        if "alt_title" in keys and row["alt_title"]:
            d["alt_title"] = row["alt_title"]
        if "is_women" in keys and row["is_women"]:
            d["is_women"] = True
        if "is_youth" in keys and row["is_youth"]:
            d["is_youth"] = True

        return d

    def get_tennis_athlete(self, slug: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached image URL metadata for a tennis player."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT slug, provider, image_url, gender, updated_at FROM tennis_athletes_cache WHERE slug = ?",
                    (slug,)
                ).fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.warning("Error fetching cached tennis athlete '%s': %s", slug, e)
        return None

    def save_tennis_athlete(self, slug: str, provider: str, image_url: str, gender: str = "unknown"):
        """Saves or updates image URL for a tennis player."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tennis_athletes_cache (slug, provider, image_url, gender, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        provider = excluded.provider,
                        image_url = excluded.image_url,
                        gender = excluded.gender,
                        updated_at = excluded.updated_at
                    """,
                    (slug, provider, image_url, gender, int(time.time()))
                )
        except Exception as e:
            logger.warning("Error saving tennis athlete '%s': %s", slug, e)

    def delete_tennis_athlete(self, slug: str):
        """Removes a broken or obsolete athlete image URL from cache."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM tennis_athletes_cache WHERE slug = ?", (slug,))
        except Exception as e:
            logger.warning("Error deleting tennis athlete '%s': %s", slug, e)

    def purge_contaminated_tennis_athletes(self):
        """Purges any legacy banner/logo entries erroneously cached from WTA Open Graph or placeholders."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    DELETE FROM tennis_athletes_cache 
                    WHERE LOWER(image_url) LIKE '%home-share%' 
                       OR LOWER(image_url) LIKE '%logo%' 
                       OR LOWER(image_url) LIKE '%banner%'
                """)
        except Exception as e:
            logger.warning("Error purging contaminated tennis athletes: %s", e)


db_service = DBService()