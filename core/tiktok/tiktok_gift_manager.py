"""Background TikTok gift sync manager (region-aware)."""
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

from core.tiktok.tiktok_gift_database import TikTokGiftDatabase

logger = logging.getLogger(__name__)


class TikTokGiftManager:
    """# Fix: central service that deduplicates region refreshes and updates gifts atomically."""

    def __init__(
        self,
        gift_db: Optional[TikTokGiftDatabase] = None,
        fetcher: Optional[Callable[[str], List[Dict]]] = None,
        refresh_interval_seconds: int = 3600,
    ):
        self.gift_db = gift_db or TikTokGiftDatabase.get_instance()
        self.fetcher = fetcher or self._default_fetcher
        self.refresh_interval_seconds = max(60, int(refresh_interval_seconds))
        self._region_locks: Dict[str, threading.Lock] = {}
        self._region_locks_guard = threading.Lock()
        self._session_seen = set()
        self._stop_event = threading.Event()
        self._thread = None

    def _default_fetcher(self, region: str) -> List[Dict]:
        # # Fix: use existing scraper flow as fallback TikTok source.
        try:
            from giftdownloader import scrape_tiktok_gifts, add_manual_gifts

            gifts = scrape_tiktok_gifts(region=region)
            manual = add_manual_gifts(region=region)
            deduped = {str(item.get('id')): item for item in gifts + manual}
            return [
                {
                    'gift_id': str(item.get('id')),
                    'name': item.get('name', 'Unknown Gift'),
                    'price': int(item.get('value') or 0),
                    'image_url': item.get('image_url'),
                }
                for item in deduped.values()
            ]
        except Exception as exc:
            logger.warning(f"Gift fetch failed for {region}: {exc}")
            return []

    def _lock_for_region(self, region: str) -> threading.Lock:
        key = region.upper()
        with self._region_locks_guard:
            if key not in self._region_locks:
                self._region_locks[key] = threading.Lock()
            return self._region_locks[key]

    def ensure_region_loaded_for_session(self, region: str, session_id: str) -> Dict:
        """# Fix: only run one initial download per session+region pair."""
        key = f"{session_id}:{region.upper()}"
        if key in self._session_seen:
            status = self.gift_db.get_region_sync_status(region) or {}
            return {'downloaded': False, 'reason': 'session_cached', 'status': status}

        self._session_seen.add(key)
        sync = self.refresh_region(region=region, force=False)
        return {'downloaded': True, 'status': sync}

    def refresh_region(self, region: str, force: bool = False) -> Dict:
        normalized = region.upper()
        lock = self._lock_for_region(normalized)
        if not lock.acquire(blocking=False):
            # # Fix: avoid concurrent writers for the same region table.
            status = self.gift_db.get_region_sync_status(normalized) or {}
            return {'region': normalized, 'skipped': True, 'reason': 'in_progress', 'status': status}

        try:
            current = self.gift_db.get_region_sync_status(normalized)
            now = time.time()
            if current and not force:
                age = now - float(current.get('last_downloaded') or 0)
                if age < self.refresh_interval_seconds:
                    return {
                        'region': normalized,
                        'skipped': True,
                        'reason': 'fresh_cache',
                        'seconds_until_refresh': int(self.refresh_interval_seconds - age),
                        'status': current,
                    }

            existing_gifts = self.gift_db.get_gifts_by_region(normalized)
            new_gifts = self.fetcher(normalized)

            # # Fix: avoid destructive syncs when upstream scraping changes and returns a partial/empty catalog.
            if existing_gifts:
                minimum_expected = max(5, int(len(existing_gifts) * 0.35))
                if len(new_gifts) < minimum_expected:
                    return {
                        'region': normalized,
                        'skipped': True,
                        'reason': 'source_low_count',
                        'existing_count': len(existing_gifts),
                        'fetched_count': len(new_gifts),
                        'minimum_expected': minimum_expected,
                        'status': current or self.gift_db.get_region_sync_status(normalized) or {},
                    }

            return self.gift_db.replace_region_gifts(normalized, new_gifts, last_downloaded=now)
        finally:
            lock.release()

    def start_background_sync(self, default_regions: Optional[List[str]] = None):
        if self._thread and self._thread.is_alive():
            return

        regions = [r.upper() for r in (default_regions or ['US'])]

        def _loop():
            while not self._stop_event.is_set():
                for region in regions:
                    try:
                        self.refresh_region(region, force=False)
                    except Exception as exc:
                        logger.warning(f"Background gift refresh failed for {region}: {exc}")
                self._stop_event.wait(self.refresh_interval_seconds)

        # # Fix: background scheduler keeps region datasets fresh without blocking requests.
        self._thread = threading.Thread(target=_loop, daemon=True, name='GiftSyncLoop')
        self._thread.start()

    def stop_background_sync(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
