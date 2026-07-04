import asyncio
import multiprocessing as mp
import random
from queue import Full
import traceback
from typing import Optional, List, Dict

from .live_follower_count import extract_live_follower_count
from .tiktok_utilities import _ProcLogger, _extract_avatar_url_loose, _extract_gift_image_url, sleep_interruptible


def _extract_user_payload(event):
    """Extract normalized user fields from TikTok event user object."""
    user = event.user if hasattr(event, 'user') else None

    username_val = "Anonymous"
    if user:
        username_val = (
                getattr(user, "nickname", None) or
                getattr(user, "uniqueId", None) or
                getattr(user, "display_name", None) or
                "Anonymous"
        )

    user_id = str(username_val)
    avatar_url = None
    if user:
        user_id = str(
            getattr(user, "id", None) or
            getattr(user, "userId", None) or
            getattr(user, "uniqueId", None) or
            username_val
        )
        candidates = [
            getattr(user, "avatar_large", None),
            getattr(user, "avatarLarger", None),
            getattr(user, "avatar_thumb", None),
        ]
        avatar_url = _extract_avatar_url_loose(candidates)

    return user, username_val, user_id, avatar_url


def _tiktok_worker_main(stop_ev: mp.Event, out_q: mp.Queue, username: str,
                        header_profiles: List[Dict[str, str]]) -> None:
    """
    Runs in a separate process. Sets up TikTokLive and pushes plain dict events into out_q.
    FIXED: Stop retrying after successful connection and natural disconnect.
    """

    logger = _ProcLogger(out_q)

    def retry_wait(attempt_i: int, retry_after: Optional[int] = None) -> float:
        if retry_after:
            return float(retry_after)
        return min(60.0, (2 ** attempt_i) + random.uniform(0, 1))


    dropped_events = 0

    def _queue_put(payload: dict, *, critical: bool = False) -> bool:
        nonlocal dropped_events
        try:
            out_q.put_nowait(payload)
            return True
        except Full:
            if critical:
                out_q.put(payload, timeout=1.0)
                return True

            dropped_events += 1
            if dropped_events == 1 or dropped_events % 100 == 0:
                try:
                    out_q.put_nowait({
                        "type": "status",
                        "level": "warning",
                        "message": f"Worker queue saturated - dropped {dropped_events} non-critical events"
                    })
                except Full:
                    pass
            return False


    try:
        from core.tiktok.library_checker import LibraryChecker

        class _Shim:
            pass

        lc = LibraryChecker(_Shim())
        lc.check_and_import_tiktoklive()

        if not lc.has_tiktok_live or not lc.TikTokLiveClient:
            _queue_put({"type": "error", "message": "TikTokLive library not available in worker"}, critical=True)
            return

        attempt = 0
        had_successful_connection = False  # ✅ Track if we ever connected successfully

        while not stop_ev.is_set():
            loop = None
            client = None

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                profile = header_profiles[attempt % len(header_profiles)]
                client = lc.TikTokLiveClient(unique_id=username)

                try:
                    if hasattr(client, "web") and hasattr(client.web, "headers") and isinstance(profile, dict):
                        client.web.headers.update(profile)
                except Exception:
                    pass

                # Event handlers
                if hasattr(client, "on"):
                    if lc.ConnectEvent:
                        @client.on(lc.ConnectEvent)
                        async def on_connect(event):
                            nonlocal had_successful_connection
                            had_successful_connection = True  # ✅ Mark success
                            _queue_put({"type": "connected", "message": f"Connected to @{username}"}, critical=True)
                            follower_count = extract_live_follower_count(
                                getattr(client, "room_info", None),
                                event,
                            )
                            if follower_count is not None:
                                _queue_put({
                                    "type": "live_follower_count",
                                    "follower_count": follower_count,
                                    "source": "tiktok_live_profile",
                                }, critical=True)
                            else:
                                _queue_put({"type": "live_follower_count_unavailable"}, critical=True)
                            _queue_put({"type": "status", "level": "success", "message": "Connection established"}, critical=True)

                    if lc.DisconnectEvent:
                        @client.on(lc.DisconnectEvent)
                        async def on_disconnect(event):
                            _queue_put({"type": "disconnected", "message": "Disconnected from live stream"}, critical=True)

                    if lc.ErrorEvent:
                        @client.on(lc.ErrorEvent)
                        async def on_error(event):
                            _queue_put({"type": "error", "message": f"Client error: {event}"}, critical=True)

                    if lc.CommentEvent:
                        @client.on(lc.CommentEvent)
                        async def on_comment(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                text = ""
                                if hasattr(event, "comment"):
                                    text = str(event.comment)
                                elif hasattr(event, "text"):
                                    text = str(event.text)

                                if not text.strip():
                                    return

                                _queue_put({
                                    "type": "comment",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "comment": text,
                                    "avatar_url": avatar_url
                                }, critical=True)

                            except Exception as e:
                                logger.exc(f"Comment error: {e}")
                    if lc.GiftEvent:
                        @client.on(lc.GiftEvent)
                        async def on_gift(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                # Get gift info - TikTokLive API puts gift data in event.gift object
                                gift = getattr(event, 'gift', None)
                                if gift:
                                    gift_id = getattr(gift, 'id', 0)
                                    if not isinstance(gift_id, int):
                                        gift_id = 0
                                    gift_name = getattr(gift, 'name', "Unknown")
                                else:
                                    gift_id = 0
                                    gift_name = "Unknown"

                                # Gift count is on event directly - ensure it's always an int
                                gift_count = getattr(event, 'repeat_count', None)
                                if not isinstance(gift_count, int) or gift_count < 1:
                                    gift_count = 1

                                # Only forward the terminal packet for streak/repeat gifts.
                                # TikTok emits incremental GiftEvent updates during a streak,
                                # which can otherwise trigger downstream actions multiple times.
                                repeat_end_raw = getattr(event, 'repeat_end', None)
                                is_repeat_final = True
                                if repeat_end_raw is not None:
                                    if isinstance(repeat_end_raw, bool):
                                        is_repeat_final = repeat_end_raw
                                    elif isinstance(repeat_end_raw, (int, float)):
                                        is_repeat_final = int(repeat_end_raw) == 1
                                    elif isinstance(repeat_end_raw, str):
                                        is_repeat_final = repeat_end_raw.strip().lower() in {"1", "true", "yes", "y"}

                                if not is_repeat_final:
                                    print(
                                        f"[TikTokWorker] Skipping non-final repeat gift packet for @{user_id} "
                                        f"(gift={gift_name}, repeat_count={gift_count})"
                                    )
                                    return

                                # Get gift image URL
                                gift_image_url = None
                                if gift:
                                    gift_image_url = _extract_gift_image_url(gift)

                                # Get avatar
                                _queue_put({
                                    "type": "gift",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "gift_id": gift_id,
                                    "gift_name": gift_name,
                                    "gift_count": gift_count,
                                    "gift_image_url": gift_image_url,
                                    "avatar_url": avatar_url
                                }, critical=True)

                            except Exception as e:
                                logger.exc(f"Gift error: {e}")

                    if lc.FollowEvent:
                        @client.on(lc.FollowEvent)
                        async def on_follow(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                _queue_put({
                                    "type": "follow",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "avatar_url": avatar_url,
                                })
                            except Exception as e:
                                logger.exc(f"Follow error: {e}")

                    if lc.ShareEvent:
                        @client.on(lc.ShareEvent)
                        async def on_share(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                _queue_put({
                                    "type": "share",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "avatar_url": avatar_url,
                                })
                            except Exception as e:
                                logger.exc(f"Share error: {e}")

                    if lc.LikeEvent:
                        @client.on(lc.LikeEvent)
                        async def on_like(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                like_count = getattr(event, "count", None)
                                if not isinstance(like_count, int) or like_count < 1:
                                    like_count = getattr(event, "likeCount", None)
                                if not isinstance(like_count, int) or like_count < 1:
                                    like_count = 1

                                _queue_put({
                                    "type": "like",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "count": like_count,
                                    "avatar_url": avatar_url,
                                })
                            except Exception as e:
                                logger.exc(f"Like error: {e}")

                    if lc.JoinEvent:
                        @client.on(lc.JoinEvent)
                        async def on_join(event):
                            try:
                                _, username_val, user_id, avatar_url = _extract_user_payload(event)

                                _queue_put({
                                    "type": "join",
                                    "username": username_val,
                                    "unique_id": user_id,
                                    "avatar_url": avatar_url,
                                })
                            except Exception as e:
                                logger.exc(f"Join error: {e}")
                # Get run method
                method = getattr(client, "run", None) or getattr(client, "start", None) or getattr(client, "connect",
                                                                                                   None)
                if method is None:
                    _queue_put({"type": "error", "message": "No run/start/connect method on TikTokLiveClient"}, critical=True)
                    return

                # ✅ Run client with proper monitoring
                async def run_with_monitoring():
                    connection_task = None
                    follower_snapshot_task = None

                    async def poll_exact_follower_count():
                        last_count = None
                        while not stop_ev.is_set():
                            try:
                                room_info = await client.web.fetch_room_info(room_id=client.room_id)
                                count = extract_live_follower_count(room_info)
                                if count is not None and count != last_count:
                                    last_count = count
                                    _queue_put({
                                        "type": "live_follower_count",
                                        "follower_count": count,
                                        "source": "tiktok_live_stats",
                                    }, critical=True)
                                elif count is None:
                                    _queue_put({"type": "live_follower_count_unavailable"}, critical=True)
                            except Exception as exc:
                                logger.info(f"exact_live_follower_count_unavailable error={exc}")
                                _queue_put({"type": "live_follower_count_unavailable"}, critical=True)
                            await asyncio.sleep(60)

                    try:
                        if asyncio.iscoroutinefunction(method):
                            connection_task = asyncio.create_task(method())
                            follower_snapshot_task = asyncio.create_task(poll_exact_follower_count())
                        else:
                            await loop.run_in_executor(None, method)
                            return

                        # Monitor for stop signal
                        while not stop_ev.is_set():
                            if connection_task.done():
                                break
                            await asyncio.sleep(0.1)

                        # Cancel if still running
                        if not connection_task.done():
                            connection_task.cancel()
                            try:
                                await asyncio.wait_for(connection_task, timeout=2.0)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass

                    except asyncio.CancelledError:
                        pass
                    finally:
                        if follower_snapshot_task and not follower_snapshot_task.done():
                            follower_snapshot_task.cancel()
                            try:
                                await follower_snapshot_task
                            except asyncio.CancelledError:
                                pass
                        if hasattr(client, 'disconnect'):
                            try:
                                if asyncio.iscoroutinefunction(client.disconnect):
                                    await client.disconnect()
                                else:
                                    client.disconnect()
                            except Exception:
                                pass

                loop.run_until_complete(run_with_monitoring())

                # ✅ CRITICAL: Check if we should stop retrying
                if stop_ev.is_set():
                    break

                if had_successful_connection:
                    # We connected successfully, then disconnected naturally - STOP RETRYING
                    _queue_put({"type": "status", "level": "info", "message": "Stream ended"})
                    break  # Exit the retry loop

                # Never connected successfully - retry
                _queue_put({"type": "status", "level": "warning", "message": "Connection failed, retrying..."})

            except Exception as e:
                _queue_put({"type": "error", "message": f" {e}"}, critical=True)
                logger.exc(e)

            finally:
                attempt += 1

                # Cleanup asyncio
                try:
                    if loop and loop.is_running():
                        loop.stop()

                    if loop:
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()

                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

                        loop.run_until_complete(asyncio.sleep(0.1))
                        loop.close()
                except Exception as cleanup_error:
                    logger.exc(f"Cleanup error: {cleanup_error}")
                finally:
                    asyncio.set_event_loop(None)

            # Check stop before retry
            if stop_ev.is_set() or had_successful_connection:
                break

            # Backoff
            wait = retry_wait(attempt)
            _queue_put({"type": "status", "level": "info", "message": f"Retrying in {wait:.1f}s (attempt {attempt})"})
            sleep_interruptible(stop_ev, wait)

        _queue_put({"type": "status", "level": "info", "message": "Worker exiting"})

    except Exception as e:
        try:
            _queue_put({"type": "error", "message": f"Fatal worker error: {e}\n{traceback.format_exc()}"}, critical=True)
        except Exception:
            pass
