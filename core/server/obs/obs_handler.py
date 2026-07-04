import asyncio
import logging
from typing import Dict

from core.server.obs.obs_manager import OBSManager

logger = logging.getLogger(__name__)


class OBSHandler:
    def __init__(self, server=None):
        self.server = server
        self.manager = OBSManager.get_instance(server)

    def _run_async(self, coro):
        try:
            loop = getattr(self.server, 'main_loop', None) if self.server else None
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                return fut.result(timeout=20)
            return asyncio.run(coro)
        except Exception as exc:
            logger.debug(f"OBS handler async execution warning: {exc}")
            return None

    def process_tiktok_event(self, event_type: str, event_data: dict):
        if event_type == 'gift':
            self.process_gift(event_data)
        elif event_type == 'comment':
            self.process_comment(event_data)

    def process_gift(self, event_data: Dict):
        cfg = self.manager.get_config()
        if not cfg.get('enabled') or not self.manager.is_connected():
            return

        gift_id = str(event_data.get('giftId', '') or '').strip()
        gift_name = str(event_data.get('giftName', '') or '').strip()
        gift_value = int(event_data.get('giftValue', 0) or event_data.get('diamondCount', 0) or 0)

        for trigger in cfg.get('sceneTriggers', []):
            if not trigger.get('enabled', True):
                continue
            if trigger.get('triggerType') != 'gift':
                continue

            trigger_gift_id = str(trigger.get('giftId', '') or '').strip()
            trigger_gift_name = str(trigger.get('giftName', '') or '').strip()
            min_value = int(trigger.get('giftMinValue', 0) or 0)

            id_match = bool(trigger_gift_id and trigger_gift_id == gift_id)
            name_match = bool((not trigger_gift_id) and trigger_gift_name and trigger_gift_name.lower() == gift_name.lower())
            if (id_match or name_match) and gift_value >= min_value:
                prev_scene = self._run_async(self.manager.get_current_scene())
                self._run_async(self.manager.switch_scene(str(trigger.get('sceneName', ''))))
                delay = int(trigger.get('returnAfterSeconds', 0) or 0)
                if delay > 0:
                    self._run_async(self.manager.schedule_return(prev_scene or '', delay))
                break

    def process_comment(self, event_data: Dict):
        cfg = self.manager.get_config()
        if not cfg.get('enabled') or not self.manager.is_connected():
            return

        text = str(event_data.get('comment', '') or event_data.get('message', '') or '').strip().lower()
        if not text:
            return

        for trigger in cfg.get('sceneTriggers', []):
            if not trigger.get('enabled', True):
                continue
            if trigger.get('triggerType') != 'chat':
                continue

            command = str(trigger.get('chatCommand', '') or '').strip().lower()
            if command and text == command:
                prev_scene = self._run_async(self.manager.get_current_scene())
                self._run_async(self.manager.switch_scene(str(trigger.get('sceneName', ''))))
                delay = int(trigger.get('returnAfterSeconds', 0) or 0)
                if delay > 0:
                    self._run_async(self.manager.schedule_return(prev_scene or '', delay))
                break
