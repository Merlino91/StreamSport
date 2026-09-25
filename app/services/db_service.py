from __future__ import annotations
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("easysports.db")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "data"
DB_PATH = DATA_DIR / "events.db"

class DBService:
    """Manages SQLite storage for keeping sports events active for 72 hours for replays."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._ensure_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self):
        """Initializes data directory and database schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    date INTEGER NOT NULL,
                    poster TEXT,
                    popular INTEGER DEFAULT 0,
                    sources TEXT,
                    updated_at INTEGER NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_cat ON matches(category)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thesportsdb_cache (
                    query_key TEXT PRIMARY KEY,
                    thumb_url TEXT,
                    status TEXT NOT NULL,
                    checked_at INTEGER NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_thesportsdb_status ON thesportsdb_cache(status)")
            conn.commit()

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
            poster = m.get("poster") or ""
            popular = 1 if m.get("popular") else 0
            sources = json.dumps(m.get("sources", []))
            records.append((m_id, title, category, date, poster, popular, sources, now))

        if not records:
            return

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT INTO matches (id, title, category, date, poster, popular, sources, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    category = excluded.category,
                    date = excluded.date,
                    poster = CASE 
                        WHEN excluded.poster IS NOT NULL AND excluded.poster != '' THEN excluded.poster 
                        ELSE matches.poster 
                    END,
                    popular = excluded.popular,
                    sources = excluded.sources,
                    updated_at = excluded.updated_at
            """, records)
            conn.commit()

    def update_match_poster(self, match_id: str, poster_url: str):
        """Updates the poster of a specific match in SQLite."""
        if not match_id or not poster_url:
            return
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE matches SET poster = ?, updated_at = ? WHERE id = ?
            """, (poster_url, now, match_id))
            conn.commit()

    def get_poster_from_cache(self, query_key: str) -> Optional[Tuple[str, Optional[str], int]]:
        """
        Retrieves cached TheSportsDB poster result.
        Returns (status, thumb_url, checked_at) or None.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, thumb_url, checked_at FROM thesportsdb_cache WHERE query_key = ?", (query_key,))
            row = cursor.fetchone()
            if not row:
                return None
            return (row["status"], row["thumb_url"], row["checked_at"])

    def save_poster_to_cache(self, query_key: str, thumb_url: Optional[str], status: str):
        """Saves a poster query result to the SQLite cache."""
        now = int(time.time())
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

    def get_match_by_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single match from the database by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_match(row)

    def get_concluded_matches(self, category_id: str = "all", max_age_hours: int = 72) -> List[Dict[str, Any]]:
        """
        Returns matches that started more than 4 hours ago, but less than max_age_hours ago.
        """
        now_ms = int(time.time() * 1000)
        # 4 hours after start
        cutoff_recent = now_ms - (4 * 3600 * 1000)
        # 72 hours ago
        cutoff_oldest = now_ms - (max_age_hours * 3600 * 1000)

        query = """
            SELECT * FROM matches
            WHERE date > 0 AND date <= ? AND date >= ?
        """
        params: List[Any] = [cutoff_recent, cutoff_oldest]

        if category_id != "all":
            query += " AND category = ?"
            params.append(category_id.lower())

        query += " ORDER BY date DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_match(r) for r in rows]

    def save_matches(self, matches: List[Dict[str, Any]]):
        """Alias for upsert_matches."""
        self.upsert_matches(matches)

    def get_active_matches(self, max_age_hours: int = 72) -> List[Dict[str, Any]]:
        """
        Returns all active matches: current/upcoming or concluded within max_age_hours.
        """
        now_ms = int(time.time() * 1000)
        cutoff_oldest = now_ms - (max_age_hours * 3600 * 1000)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM matches
                WHERE (date = 0) OR (date >= ?)
                ORDER BY date DESC
            """, (cutoff_oldest,))
            rows = cursor.fetchall()
            return [self._row_to_match(r) for r in rows]

    def purge_expired_matches(self, max_age_hours: int = 72) -> int:
        """Deletes matches concluded more than max_age_hours ago."""
        now_ms = int(time.time() * 1000)
        cutoff_oldest = now_ms - (max_age_hours * 3600 * 1000)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM matches WHERE date > 0 AND date < ?", (cutoff_oldest,))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info("Purged %d expired matches older than %d hours", deleted, max_age_hours)
            return deleted

    def delete_matches_by_ids(self, match_ids: List[str]) -> int:
        """Deletes specific matches by their IDs from the database."""
        if not match_ids:
            return 0
        total_deleted = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Batch in chunks of 500 to stay well under SQLite parameter limits
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

    def _row_to_match(self, row: sqlite3.Row) -> Dict[str, Any]:
        sources = []
        try:
            if row["sources"]:
                sources = json.loads(row["sources"])
        except Exception:
            sources = []

        return {
            "id": row["id"],
            "title": row["title"],
            "category": row["category"],
            "date": row["date"],
            "poster": row["poster"],
            "popular": bool(row["popular"]),
            "sources": sources,
        }

db_service = DBService()