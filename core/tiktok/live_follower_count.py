"""Exact follower-count extraction from active TikTok LIVE payloads."""

from typing import Optional


def extract_live_follower_count(*objects) -> Optional[int]:
    """Read an exact creator follower count from LIVE room/user payloads."""
    preferred_keys = {"follower_count", "followerCount", "followers_count", "followersCount"}
    seen = set()

    def visit(value, depth=0):
        if value is None or depth > 8 or id(value) in seen:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, dict):
            seen.add(id(value))
            for key in preferred_keys:
                count = value.get(key)
                if isinstance(count, int) and count >= 0:
                    return count
            for key in ("owner", "user", "follow_info", "followInfo", "room", "data"):
                if key in value:
                    count = visit(value[key], depth + 1)
                    if count is not None:
                        return count
            return None
        seen.add(id(value))
        for key in preferred_keys:
            count = getattr(value, key, None)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count
        for key in ("owner", "user", "follow_info", "followInfo", "room", "data"):
            count = visit(getattr(value, key, None), depth + 1)
            if count is not None:
                return count
        return None

    for obj in objects:
        count = visit(obj)
        if count is not None:
            return count
    return None
