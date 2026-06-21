import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


class SearchCache:
    def __init__(self, cache_dir: Path):
        self.db_path = cache_dir / "search_cache.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    query TEXT PRIMARY KEY,
                    result TEXT,
                    timestamp TEXT
                )
            """)

    def get(self, query: str, max_age_hours: int = 24) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT result, timestamp FROM cache WHERE query = ?",
                (query,)
            )
            row = cur.fetchone()
            if row:
                result, ts_str = row
                ts = datetime.fromisoformat(ts_str)
                if datetime.now() - ts < timedelta(hours=max_age_hours):
                    return result
                conn.execute("DELETE FROM cache WHERE query = ?", (query,))
                conn.commit()
        return None

    def set(self, query: str, result: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (query, result, timestamp) VALUES (?, ?, ?)",
                (query, result, datetime.now().isoformat())
            )
            conn.commit()
