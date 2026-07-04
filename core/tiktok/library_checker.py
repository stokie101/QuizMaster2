"""
TikTok Live Manager - Library Checker Module

This module handles checking for and importing the TikTokLive library
and its required components.
"""

import logging
from typing import Dict, Any


class LibraryChecker:
    """
    Handles TikTokLive library detection and import with multiple fallback strategies.
    """

    def __init__(self, manager):
        self.manager = manager
        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        # Library status
        self._library_check_performed = False
        self._has_tiktok_live = False

        # TikTokLive classes (set after import)
        self._TikTokLiveClient = None
        self._CommentEvent = None
        self._ConnectEvent = None
        self._DisconnectEvent = None
        self._ErrorEvent = None
        self._GiftEvent = None
        self._FollowEvent = None
        self._ShareEvent = None
        self._LikeEvent = None
        self._JoinEvent = None

    @property
    def has_tiktok_live(self) -> bool:
        """Check if TikTokLive library is available"""
        if not self._library_check_performed:
            self.check_and_import_tiktoklive()
        return self._has_tiktok_live

    @property
    def GiftEvent(self):
        return self._GiftEvent

    @property
    def TikTokLiveClient(self):
        """Get TikTokLiveClient class"""
        return self._TikTokLiveClient

    @property
    def CommentEvent(self):
        """Get CommentEvent class"""
        return self._CommentEvent

    @property
    def ConnectEvent(self):
        """Get ConnectEvent class"""
        return self._ConnectEvent

    @property
    def DisconnectEvent(self):
        """Get DisconnectEvent class"""
        return self._DisconnectEvent

    @property
    def ErrorEvent(self):
        """Get ErrorEvent class"""
        return self._ErrorEvent

    @property
    def FollowEvent(self):
        """Get FollowEvent class"""
        return self._FollowEvent

    @property
    def ShareEvent(self):
        """Get ShareEvent class"""
        return self._ShareEvent

    @property
    def LikeEvent(self):
        """Get LikeEvent class"""
        return self._LikeEvent

    @property
    def JoinEvent(self):
        """Get JoinEvent class"""
        return self._JoinEvent

    def check_and_import_tiktoklive(self) -> bool:
        """
        Checks for TikTokLive library and imports required classes.
        Emits installation prompt if not found.

        Returns:
            bool: True if TikTokLive is available and classes imported.
        """
        self._library_check_performed = True

        try:
            # First, try to import the main TikTokLive module
            import TikTokLive
            self.logger.info(f"TikTokLive module found at: {TikTokLive.__file__}")

            # Debug: Check what's available in the TikTokLive package
            self.logger.info(f"TikTokLive package contents: {dir(TikTokLive)}")

            # Import client with multiple strategies
            if not self._import_client():
                raise ImportError("Could not find TikTokLiveClient in any expected location")

            # Import event classes with multiple strategies
            events_imported = self._import_events()

            if not events_imported:
                self.logger.warning("Could not import event classes, but client is available")

            self._has_tiktok_live = True
            self.logger.info("TikTokLive library successfully imported and configured")
            return True

        except ImportError as e:
            self._has_tiktok_live = False
            msg = (f"TikTokLive library not installed or missing components: {e}. "
                   "Install it via 'pip install TikTokLive'")
            self.logger.warning(msg)
            self.manager._safe_emit(self.manager.debug_message_signal, msg, "warning")
            self.manager._safe_emit(self.manager.installation_prompt, msg)
            return False
        except Exception as e:
            self._has_tiktok_live = False
            msg = f"Unexpected error importing TikTokLive: {e}"
            self.logger.error(msg)
            self.manager._safe_emit(self.manager.debug_message_signal, msg, "error")
            return False

    def _import_client(self) -> bool:
        """Import TikTokLiveClient with multiple strategies"""
        client_imported = False

        # Strategy 1: Direct import from TikTokLive
        try:
            from TikTokLive import TikTokLiveClient
            self._TikTokLiveClient = TikTokLiveClient
            client_imported = True
            self.logger.info("TikTokLiveClient imported directly from TikTokLive")
        except ImportError:
            pass

        # Strategy 2: Import from TikTokLive.client
        if not client_imported:
            try:
                from TikTokLive.client import TikTokLiveClient
                self._TikTokLiveClient = TikTokLiveClient
                client_imported = True
                self.logger.info("TikTokLiveClient imported from TikTokLive.client")
            except ImportError as e:
                self.logger.warning(f"Failed to import from TikTokLive.client: {e}")

        # Strategy 3: Check client module contents
        if not client_imported:
            try:
                import TikTokLive.client as client_module
                self.logger.info(f"TikTokLive.client contents: {dir(client_module)}")

                # Try different possible names
                possible_names = ['TikTokLiveClient', 'Client', 'LiveClient', 'TikTokClient']
                for name in possible_names:
                    if hasattr(client_module, name):
                        self._TikTokLiveClient = getattr(client_module, name)
                        client_imported = True
                        self.logger.info(f"TikTokLiveClient imported as {name} from TikTokLive.client")
                        break
            except ImportError as e:
                self.logger.warning(f"Failed to inspect TikTokLive.client: {e}")

        # Strategy 4: Try alternative module structures
        if not client_imported:
            alternative_paths = [
                'TikTokLive.TikTokLiveClient',
                'TikTokLive.live_client',
                'TikTokLive.client.client',
                'TikTokLive.client.live_client'
            ]

            for path in alternative_paths:
                try:
                    module_parts = path.split('.')
                    module = __import__(path, fromlist=[module_parts[-1]])
                    if hasattr(module, 'TikTokLiveClient'):
                        self._TikTokLiveClient = getattr(module, 'TikTokLiveClient')
                        client_imported = True
                        self.logger.info(f"TikTokLiveClient imported from {path}")
                        break
                except ImportError:
                    continue

        return client_imported

    def _import_events(self) -> bool:
        """Import event classes with multiple fallback strategies."""
        events_imported = False

        # Strategy 1: Import from TikTokLive.events
        try:
            from TikTokLive.events import (
                CommentEvent, ConnectEvent, DisconnectEvent, GiftEvent,
                FollowEvent, ShareEvent, LikeEvent, JoinEvent
            )

            self._CommentEvent = CommentEvent
            self._ConnectEvent = ConnectEvent
            self._DisconnectEvent = DisconnectEvent
            self._GiftEvent = GiftEvent
            self._FollowEvent = FollowEvent
            self._ShareEvent = ShareEvent
            self._LikeEvent = LikeEvent
            self._JoinEvent = JoinEvent
            events_imported = True
            self.logger.info("Core TikTokLive events imported successfully")
        except ImportError as e:
            self.logger.warning(f"Could not import events from TikTokLive.events: {e}")

        # Strategy 3: Check what's available in events module
        if not events_imported:
            try:
                import TikTokLive.events as events_module
                self.logger.info(f"TikTokLive.events contents: {dir(events_module)}")

                event_mapping = {
                    'CommentEvent': ['CommentEvent', 'Comment', 'MessageEvent'],
                    'ConnectEvent': ['ConnectEvent', 'Connect', 'ConnectionEvent'],
                    'DisconnectEvent': ['DisconnectEvent', 'Disconnect', 'DisconnectionEvent'],
                    'GiftEvent': ['GiftEvent', 'Gift', 'GiftSendEvent'],
                    'FollowEvent': ['FollowEvent', 'Follow', 'SubscribeEvent'],
                    'ShareEvent': ['ShareEvent', 'Share'],
                    'LikeEvent': ['LikeEvent', 'Like'],
                    'JoinEvent': ['JoinEvent', 'MemberJoinEvent', 'Join']
                }

                for attr_name, possible_names in event_mapping.items():
                    for name in possible_names:
                        if hasattr(events_module, name):
                            setattr(self, f'_{attr_name}', getattr(events_module, name))
                            self.logger.info(f"Found {attr_name} as {name}")
                            break

                events_imported = True
            except ImportError as e:
                self.logger.warning(f"Could not inspect events module: {e}")

        return events_imported

    def get_library_status(self) -> Dict[str, Any]:
        return {
            "library_available": self._has_tiktok_live,
            "library_check_performed": self._library_check_performed,
            "client_available": self._TikTokLiveClient is not None,
            "events_available": {
                "CommentEvent": self._CommentEvent is not None,
                "ConnectEvent": self._ConnectEvent is not None,
                "DisconnectEvent": self._DisconnectEvent is not None,
                "ErrorEvent": self._ErrorEvent is not None,
                "GiftEvent": self._GiftEvent is not None,
                "FollowEvent": self._FollowEvent is not None,
                "ShareEvent": self._ShareEvent is not None,
                "LikeEvent": self._LikeEvent is not None,
                "JoinEvent": self._JoinEvent is not None,
            }
        }
