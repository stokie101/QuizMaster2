"""TikTok gift database helpers used by backend APIs and game modules."""
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Dict, Any, List, Optional


class TikTokGiftDatabase:
    """Singleton SQLite gateway for legacy gift stats and region-scoped gift catalog data."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls, db_path: str = None):
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """# Fix: test helper to isolate singleton state across test cases."""
        with cls._lock:
            cls._instance = None

    def __init__(self, db_path: str = None):
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.logger = logging.getLogger(self.__class__.__name__)

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        default_primary = os.path.join(project_root, "data", "tiktok_gifts.db")
        default_fallback = os.path.join(project_root, "core", "tiktok", "tiktok_gifts.db")

        # # Fix: prefer the app data path, but keep compatibility with legacy checked-in DB.
        if db_path is None:
            db_path = default_primary if os.path.exists(default_primary) else default_fallback

        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize_schema()
        self.logger.info(f"🎁 TikTok Gift Database ready: {self.db_path}")

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _initialize_schema(self):
        """# Fix: create region-aware tables while preserving legacy gifts/stat tables."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gifts (
                    gift_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    type TEXT DEFAULT 'normal',
                    description TEXT,
                    image_url TEXT,
                    emoji TEXT DEFAULT '🎁',
                    times_received INTEGER DEFAULT 0,
                    total_value_received INTEGER DEFAULT 0,
                    last_received TIMESTAMP,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gift_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gift_id TEXT NOT NULL,
                    username TEXT,
                    unique_id TEXT,
                    count INTEGER DEFAULT 1,
                    timestamp REAL NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gifts_by_region (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gift_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price INTEGER,
                    image_url TEXT,
                    last_updated REAL NOT NULL,
                    UNIQUE(gift_id, region)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gift_region_sync (
                    region TEXT PRIMARY KEY,
                    last_downloaded REAL,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            ''')
            conn.commit()

    def get_gift(self, gift_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM gifts WHERE gift_id = ?', (str(gift_id),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_gifts(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM gifts ORDER BY value DESC, name ASC')
            return [dict(row) for row in cursor.fetchall()]

    def get_gifts_by_region(self, region: str) -> List[Dict[str, Any]]:
        """# Fix: centralized region catalog used by API/quiz/leaderboard modules."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT gift_id, region, name, price, image_url, last_updated
                FROM gifts_by_region
                WHERE region = ?
                ORDER BY price DESC, name ASC
                ''',
                (region.upper(),)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_region_sync_status(self, region: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM gift_region_sync WHERE region = ?', (region.upper(),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_region_sync_status(self, region: str, last_downloaded: float, version: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO gift_region_sync (region, last_downloaded, version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(region) DO UPDATE SET
                    last_downloaded = excluded.last_downloaded,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                ''',
                (region.upper(), last_downloaded, version, time.time())
            )
            conn.commit()

    def replace_region_gifts(self, region: str, gifts: List[Dict[str, Any]], last_downloaded: Optional[float] = None) -> Dict[str, Any]:
        """# Fix: atomic region refresh to avoid partial reads during updates."""
        normalized_region = region.upper()
        downloaded_at = float(last_downloaded or time.time())
        gifts = gifts or []

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('BEGIN IMMEDIATE')
            cursor.execute('SELECT version FROM gift_region_sync WHERE region = ?', (normalized_region,))
            current = cursor.fetchone()
            next_version = int(current['version']) + 1 if current else 1

            cursor.execute('DELETE FROM gifts_by_region WHERE region = ?', (normalized_region,))
            for gift in gifts:
                cursor.execute(
                    '''
                    INSERT INTO gifts_by_region (gift_id, region, name, price, image_url, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        str(gift.get('gift_id') or gift.get('id')),
                        normalized_region,
                        gift.get('name', 'Unknown Gift'),
                        int(gift.get('price') or gift.get('value') or 0),
                        gift.get('image_url'),
                        downloaded_at,
                    )
                )

            cursor.execute(
                '''
                INSERT INTO gift_region_sync (region, last_downloaded, version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(region) DO UPDATE SET
                    last_downloaded = excluded.last_downloaded,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                ''',
                (normalized_region, downloaded_at, next_version, time.time())
            )
            conn.commit()

        return {
            'region': normalized_region,
            'count': len(gifts),
            'version': next_version,
            'last_downloaded': downloaded_at,
        }

    def get_top_gifts(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM gifts
                WHERE times_received > 0
                ORDER BY times_received DESC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_gift_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    gh.*,
                    g.name as gift_name,
                    g.value as gift_value,
                    g.image_url as gift_image_url,
                    g.emoji as gift_emoji
                FROM gift_history gh
                LEFT JOIN gifts g ON gh.gift_id = g.gift_id
                ORDER BY gh.timestamp DESC
                LIMIT ?
            ''', (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    COUNT(*) as total_gifts,
                    SUM(times_received) as total_received,
                    SUM(total_value_received) as total_value
                FROM gifts
            ''')
            row = cursor.fetchone()
            return {
                'total_gifts': row['total_gifts'] or 0,
                'total_received': row['total_received'] or 0,
                'total_value': row['total_value'] or 0
            }

    def record_gift_received(self, gift_id: str, username: str, unique_id: str,
                             count: int = 1, gift_name: Optional[str] = None,
                             gift_image_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM gifts WHERE gift_id = ?', (str(gift_id),))
            row = cursor.fetchone()

            if not row:
                self.logger.warning(f"⚠️ Gift ID {gift_id} not found in database")
                return None

            value = row['value']
            cursor.execute('''
                UPDATE gifts
                SET times_received = times_received + ?,
                    total_value_received = total_value_received + ?,
                    last_received = ?,
                    last_updated = ?
                WHERE gift_id = ?
            ''', (count, value * count, time.time(), time.time(), str(gift_id)))

            cursor.execute('''
                INSERT INTO gift_history
                (gift_id, username, unique_id, count, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(gift_id), username, unique_id, count, time.time()))

            conn.commit()
            cursor.execute('SELECT * FROM gifts WHERE gift_id = ?', (str(gift_id),))
            meta_row = cursor.fetchone()
            return dict(meta_row) if meta_row else None

    def export_gifts_json(self) -> str:
        gifts = self.get_all_gifts()
        return json.dumps(gifts, indent=2)
