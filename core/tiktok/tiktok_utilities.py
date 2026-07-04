import multiprocessing as mp
import time
from typing import Optional

from core.tiktok.client_manager import ClientManager


def sleep_interruptible(stop_ev: mp.Event, seconds: float) -> None:
    """Sleep in small chunks so we can react to stop signal quickly."""
    end = time.time() + seconds
    while time.time() < end:
        if stop_ev.is_set():
            return
        time.sleep(0.1)


def _extract_avatar_url_loose(candidates) -> Optional[str]:
    """
    Best-effort extraction of a URL or uri string from various event.user avatar shapes.
    FIXED: Handle both dict and object access patterns, support multiple field name conventions.
    """

    def from_obj(obj):
        if not obj:
            return None

        # Handle string directly
        if isinstance(obj, str):
            return obj if obj.startswith("http") else None

        # Handle dict
        if isinstance(obj, dict):
            # Try common URL list keys
            for key in ("urls", "url_list", "m_urls", "urlList", "mUrls"):
                urls = obj.get(key)
                if urls and isinstance(urls, (list, tuple)) and len(urls) > 0:
                    if isinstance(urls[0], str) and urls[0].startswith("http"):
                        return urls[0]

            # Try URI keys
            for key in ("uri", "m_uri", "mUri"):
                uri = obj.get(key)
                if isinstance(uri, str) and uri:
                    return uri if uri.startswith("http") else f"https://p16-sign.tiktokcdn.com/{uri}"

        # Handle object attributes (with error handling for validation issues)
        try:
            # Try URL list attributes
            for attr in ("urls", "url_list", "m_urls", "urlList", "mUrls"):
                if hasattr(obj, attr):
                    try:
                        urls = getattr(obj, attr, None)
                        if urls and isinstance(urls, (list, tuple)) and len(urls) > 0:
                            if isinstance(urls[0], str) and urls[0].startswith("http"):
                                return urls[0]
                    except (TypeError, AttributeError):
                        continue

            # Try URI attributes
            for attr in ("uri", "m_uri", "mUri"):
                if hasattr(obj, attr):
                    try:
                        uri = getattr(obj, attr, None)
                        if isinstance(uri, str) and uri:
                            return uri if uri.startswith("http") else f"https://p16-sign.tiktokcdn.com/{uri}"
                    except (TypeError, AttributeError):
                        continue

            # Fallback: Try accessing __dict__ directly to bypass validation
            if hasattr(obj, '__dict__'):
                obj_dict = obj.__dict__

                # Try URL list keys
                for key in ("urls", "url_list", "m_urls", "urlList", "mUrls"):
                    if key in obj_dict:
                        urls = obj_dict[key]
                        if urls and isinstance(urls, (list, tuple)) and len(urls) > 0:
                            if isinstance(urls[0], str) and urls[0].startswith("http"):
                                return urls[0]

                # Try URI keys
                for key in ("uri", "m_uri", "mUri"):
                    if key in obj_dict:
                        uri = obj_dict[key]
                        if isinstance(uri, str) and uri:
                            return uri if uri.startswith("http") else f"https://p16-sign.tiktokcdn.com/{uri}"

        except Exception:
            # If all attribute access fails, try one more thing: raw string conversion
            try:
                obj_str = str(obj)
                if obj_str.startswith("http"):
                    return obj_str
            except:
                pass

        return None

    if not candidates:
        return None

    for c in candidates:
        url = from_obj(c)
        if url:
            return url

    return None


def _extract_gift_image_url(gift_obj) -> Optional[str]:
    """
    Extract gift image URL from TikTokLive gift object.
    FIXED: Handle both dict and object access, multiple field name conventions.
    """
    if not gift_obj:
        return None

    # Try to get image object first
    image = None

    # Try multiple ways to access image
    try:
        if hasattr(gift_obj, 'image'):
            try:
                image = gift_obj.image
            except (TypeError, AttributeError):
                pass

        if not image and isinstance(gift_obj, dict) and 'image' in gift_obj:
            image = gift_obj['image']

        if not image and hasattr(gift_obj, '__dict__') and 'image' in gift_obj.__dict__:
            image = gift_obj.__dict__['image']

    except Exception:
        pass

    if image:
        # Extract URL from image object
        candidates = [image]
        url = _extract_avatar_url_loose(candidates)
        if url:
            return url

    # Fallback: try direct icon/image_url attributes
    try:
        for attr in ('icon', 'image_url', 'imageUrl', 'thumbnail', 'preview', 'image'):
            # Try attribute access
            if hasattr(gift_obj, attr):
                try:
                    val = getattr(gift_obj, attr, None)
                    if isinstance(val, str) and val.startswith('http'):
                        return val
                    # Try to extract from object
                    url = _extract_avatar_url_loose([val])
                    if url:
                        return url
                except (TypeError, AttributeError):
                    continue

            # Try dict access
            if isinstance(gift_obj, dict) and attr in gift_obj:
                val = gift_obj[attr]
                if isinstance(val, str) and val.startswith('http'):
                    return val
                url = _extract_avatar_url_loose([val])
                if url:
                    return url

            # Try __dict__ access
            if hasattr(gift_obj, '__dict__') and attr in gift_obj.__dict__:
                val = gift_obj.__dict__[attr]
                if isinstance(val, str) and val.startswith('http'):
                    return val
                url = _extract_avatar_url_loose([val])
                if url:
                    return url

    except Exception:
        pass

    return None


class _ProcLogger:
    """
    Minimal logger for the TikTok worker process.
    Sends short, simplified error info to the front end and
    full tracebacks only for developer debugging (not user-facing).
    """

    def __init__(self, out_q: mp.Queue):
        self.service_locator = None
        self.logger = None
        self.client_manager = None
        self.out_q = out_q

    def exc(self, err, detail: str = None):
        """Send concise error info only."""
        import traceback
        # Short developer trace (logged internally)
        tb = traceback.format_exc(limit=1)

        # Send short status message to front end
        msg = str(err)
        if len(msg) > 200:
            msg = msg[:200] + "..."

        try:
            self.out_q.put({
                "type": "status",
                "level": "error",
                "message": f"{msg}"
            }, block=False)
        except Exception:
            pass

        # Full traceback for internal logs
        print("\n[Worker Exception]", msg)
        print(tb)

    # -------------------------------------------------------------------------
    def perform_soft_cleanup(self):
        """Keep memory low by trimming chat and avatar caches."""
        try:
            if self.client_manager is None:
                self.client_manager = ClientManager.get_instance()
            if self.client_manager and hasattr(self.client_manager, "soft_cleanup"):
                self.client_manager.soft_cleanup()

            # NOTE: service_locator/logger are not wired here in worker;
            # keeping the original shape, but you probably weren't using this.
            if self.service_locator:
                bridge = self.service_locator.get_service("HTTPBridgeServer")
                if bridge:
                    try:
                        bridge.broadcast_json({"action": "trim_chat", "keep_last": 100})
                    except Exception:
                        pass
        except Exception as e:
            if self.logger:
                self.logger.error(f"Soft cleanup failed: {e}")
            else:
                print(f"Soft cleanup failed: {e}")
