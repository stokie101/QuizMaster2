import logging
import os
import sys
import tempfile
import time

from PySide6.QtCore import Signal, QObject, QUrl, QMutexLocker, QMutex, QCoreApplication, Qt, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from config.config_manager import ConfigManager
from core.utils.audio_video_manager import AudioVideoManager

LOGGER = logging.getLogger("AudioHandler")

# Config key overrides where the SOUND section key differs from the audio
# category name (the UI/config uses "effects_volume", not "sound_effects_volume").
_VOLUME_KEYS = {"sound_effects": "effects_volume"}


def _volume_key(sound_type: str) -> str:
    return _VOLUME_KEYS.get(sound_type, f"{sound_type}_volume")


def resource_path(relative_path: str) -> str:
    """Get absolute path to resource (works for dev and PyInstaller exe)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def resolve_sound_file(category: str, file_name: str) -> str | None:
    """Return a playable local file path for a bundled sound.

    Works in dev and in the packaged (Nuitka/PyInstaller) app. Release builds
    ship no loose asset files -- the sounds are embedded in
    core.resources.web_assets_bundle -- so when the loose file is absent we
    extract the embedded bytes to a cached temp file that QMediaPlayer can play.
    """
    if not file_name:
        return None
    rel = f"core/assets/sounds/{category}/{file_name}"

    # 1) Loose file via the app resource loader (dev + some frozen layouts).
    try:
        from core.utils.resource_loader import get_resource_path
        candidate = str(get_resource_path(rel))
        if candidate and os.path.isfile(candidate):
            return candidate
    except Exception as exc:
        LOGGER.debug(f"resource_loader lookup failed for {rel}: {exc}")

    # 2) Embedded asset bundle (anti-copy release builds).
    try:
        from core.utils.embedded_web_assets import get_embedded_asset_bytes
        data = get_embedded_asset_bytes(rel)
        if data:
            cache_dir = os.path.join(tempfile.gettempdir(), "quizmaster_sounds")
            os.makedirs(cache_dir, exist_ok=True)
            cached = os.path.join(cache_dir, f"{category}__{file_name}")
            if not os.path.isfile(cached) or os.path.getsize(cached) != len(data):
                with open(cached, "wb") as handle:
                    handle.write(data)
            return cached
    except Exception as exc:
        LOGGER.debug(f"embedded asset lookup failed for {rel}: {exc}")

    return None


class AudioHandler(QObject):
    """Handles audio playback for different categories."""
    _instance = None
    _instance_mutex = QMutex()

    # Supported audio formats
    SUPPORTED_FORMATS = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac'}

    # Internal signals for thread-safe playback requests
    _request_play_effect = Signal(str, str)  # (sound_type, filename)
    _request_play_timer = Signal(str, int)  # (filename, duration_seconds)

    audio_finished = Signal(str)

    @classmethod
    def get_instance(cls, parent=None):
        with QMutexLocker(cls._instance_mutex):
            if cls._instance is None:
                cls._instance = cls(parent)

                # Ensure AudioHandler lives on the Qt main thread
                app = QCoreApplication.instance()
                if app is not None:
                    main_thread = app.thread()
                    if cls._instance.thread() is not main_thread:
                        cls._instance.moveToThread(main_thread)

                    # ✅ NOW connect internal signals (after thread move)
                    cls._instance._connect_internal_signals()

            return cls._instance

    @classmethod
    def destroy_instance(cls):
        """Clean up the singleton instance."""
        with QMutexLocker(cls._instance_mutex):
            if cls._instance is not None:
                cls._instance.stop_all()
                cls._instance.deleteLater()
                cls._instance = None

    def __init__(self, parent=None):
        """Initialize AudioHandler with configuration settings."""
        super().__init__(parent)
        self.audio_video_manager = AudioVideoManager.get_instance()
        self.timer_audio_position = 0  # store last timer position (ms)
        self.config_manager = ConfigManager.get_instance()
        self.players = {}  # Store active QMediaPlayer instances
        self.audio_outputs = {}  # Store active QAudioOutput instances

        # Set up logging level based on config
        self._configure_logging()

        LOGGER.debug("AudioHandler initialized")

    def _configure_logging(self):
        """Configure AudioHandler logger level without overriding global logging."""
        try:
            log_level_name = self.config_manager.get("LOGGING", "level", fallback="ERROR")
            log_level = getattr(logging, log_level_name.upper(), logging.ERROR)
            LOGGER.setLevel(log_level)
        except Exception:
            LOGGER.setLevel(logging.ERROR)

    def _connect_internal_signals(self):
        """Connect internal signals after thread setup."""
        self._request_play_effect.connect(
            self._play_effect_impl,
            Qt.ConnectionType.QueuedConnection
        )
        self._request_play_timer.connect(
            self._play_timer_impl,
            Qt.ConnectionType.QueuedConnection
        )
        LOGGER.debug("AudioHandler internal signals connected")

    @staticmethod
    def is_supported_format(file_path: str) -> bool:
        """Check if audio format is supported."""
        if not file_path:
            return False
        ext = os.path.splitext(file_path.lower())[1]
        return ext in AudioHandler.SUPPORTED_FORMATS

    def play_audio(self, sound_type, file_name):
        """Plays an audio file. For timer, plays from the last portion based on configured timer duration."""
        if not file_name:
            logging.warning(f"No file name provided for {sound_type} audio.")
            return

        # Only check enable_effects_sound if it's a sound effect
        if sound_type == "sound_effects":
            enable_effects_sound = self.config_manager.getboolean("SOUND", "enable_effects_sound", fallback=True)
            if not enable_effects_sound:
                logging.debug("Sound effects disabled in config")
                return

        # Separate logic for timer sound
        if sound_type == "timer":
            enable_timer_sound = self.get_enable_timer_sound()
            if not enable_timer_sound:
                logging.debug("Timer sound disabled in config")
                return

            try:
                timer_length = self.config_manager.getint("TIMER", "timer_duration", fallback=60)
            except Exception as e:
                logging.error(f"Failed to read timer duration: {e}")
                timer_length = 60

            # Stop any existing timer playback to avoid overlap
            self.stop_audio("timer")
        else:
            timer_length = 60

        # Validate sound type and file path
        if sound_type not in self.audio_video_manager.AUDIO_CATEGORIES:
            logging.error(f"Invalid sound type: {sound_type}")
            return

        file_path = resolve_sound_file(sound_type, file_name)
        if not file_path:
            logging.error(f"Audio file not found for {sound_type}: {file_name}")
            return

        # ✅ Use local format check instead of AudioVideoManager
        if not self.is_supported_format(file_path):
            logging.error(f"Unsupported audio format: {file_path}")
            return

        try:
            enabled = self.config_manager.getboolean(f"SOUND", f"enable_{sound_type}_sound", fallback=True)
            volume = self.config_manager.getint(f"SOUND", _volume_key(sound_type), fallback=50)
        except Exception:
            enabled = True
            volume = 50

        if not enabled:
            logging.debug(f"{sound_type} sound disabled in config")
            return

        # Thread-safe playback
        app = QCoreApplication.instance()
        if app and self.thread() != app.thread():
            # Queue to main thread
            logging.debug(f"Queueing {sound_type} audio to main thread")
            self._request_play_effect.emit(sound_type, file_name)
            return

        # Already on main thread - play directly
        self._play_audio_direct(sound_type, file_path, volume, timer_length)

    def play_timer_for_duration(self, file_name: str, duration_seconds: int):
        """
        Public API: play only the last `duration_seconds` of the timer file.
        Thread-safe; can be called from any thread.
        """
        try:
            if not file_name:
                logging.warning("No file name provided for timer audio.")
                return

            if not self.get_enable_timer_sound():
                logging.debug("Timer sound disabled in config")
                return

            duration_seconds = max(0, int(duration_seconds))

            app = QCoreApplication.instance()
            if app and self.thread() != app.thread():
                logging.debug("Queueing timer audio with duration to main thread")
                self._request_play_timer.emit(file_name, duration_seconds)
                return

            # Already on main thread
            self._play_timer_impl(file_name, duration_seconds)

        except Exception as e:
            logging.error(f"Error in play_timer_for_duration: {e}", exc_info=True)

    def _play_timer_impl(self, file_name: str, duration_seconds: int):
        """
        Runs on the main Qt thread. Validates inputs and starts timer playback
        from the end minus `duration_seconds`.
        """
        try:
            file_path = resolve_sound_file("timer", file_name)
            if not file_path:
                logging.error(f"Timer audio file not found: {file_name}")
                return

            if not self.is_supported_format(file_path):
                logging.error(f"Unsupported audio format: {file_path}")
                return

            # Volume from config
            try:
                volume = self.config_manager.getint("SOUND", "timer_volume", fallback=50)
            except Exception:
                volume = 50

            # Ensure we don't overlap with a previous timer sound
            self.stop_audio("timer")

            self._play_audio_direct("timer", file_path, volume, duration_seconds)

        except Exception as e:
            logging.error(f"Error in _play_timer_impl({file_name}, {duration_seconds}): {e}", exc_info=True)

    def _play_audio_direct(self, sound_type, file_path, volume, timer_length):
        """Direct audio playback (must be called on main thread)."""
        try:
            logging.info(f"Playing {sound_type}: {os.path.basename(file_path)}")

            player = QMediaPlayer()
            audio_output = QAudioOutput()
            player.setAudioOutput(audio_output)

            volume_normalized = max(0.0, min(1.0, volume / 100.0))
            audio_output.setVolume(volume_normalized)

            # Store references so we can pause/resume/stop
            self.players[sound_type] = player
            self.audio_outputs[sound_type] = audio_output
            player.setSource(QUrl.fromLocalFile(file_path))

            started = {"done": False}

            def begin_playback():
                # Start playback once, only after a valid duration is known so the
                # timer seek can be computed. Fires from LoadedMedia or, if the
                # duration is not ready then, from durationChanged.
                if started["done"]:
                    return
                total_duration_ms = player.duration()
                if total_duration_ms <= 0:
                    return
                started["done"] = True

                # For timer sounds the track ending must land exactly at zero,
                # regardless of timer length.
                if sound_type == "timer":
                    timer_ms = int(timer_length) * 1000
                    if timer_ms <= total_duration_ms:
                        # Play the final `timer` seconds so the end hits zero.
                        player.setPosition(max(total_duration_ms - timer_ms, 0))
                        player.play()
                    else:
                        # Timer longer than the track: wait, then play the full
                        # track from 0 so its ending still lands at zero.
                        player.setPosition(0)
                        delay_ms = timer_ms - total_duration_ms

                        def _start_if_current(p=player):
                            if self.players.get("timer") is p:
                                p.play()

                        QTimer.singleShot(delay_ms, _start_if_current)
                else:
                    player.play()
                logging.debug(f"Playback started for {sound_type}")

            def on_media_status_loaded(status):
                if status == QMediaPlayer.MediaStatus.LoadedMedia:
                    begin_playback()

            def on_media_status_cleanup(status, key=sound_type, p=player, o=audio_output):
                if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
                    logging.debug(f"Audio finished/invalid for key: {key}")
                    try:
                        self.players.pop(key, None)
                        self.audio_outputs.pop(key, None)
                    except Exception:
                        pass
                    try:
                        p.deleteLater()
                    except Exception:
                        pass
                    try:
                        o.deleteLater()
                    except Exception:
                        pass
                    self.audio_finished.emit(str(key))

            player.mediaStatusChanged.connect(on_media_status_loaded)
            player.mediaStatusChanged.connect(on_media_status_cleanup)
            # Fallback: some backends report duration after LoadedMedia.
            player.durationChanged.connect(lambda _dur: begin_playback())

            # Error handling
            player.errorOccurred.connect(
                lambda err, msg: logging.error(f"Playback error for {sound_type}: {err} - {msg}")
            )

        except Exception as e:
            logging.error(f"Error playing audio: {e}", exc_info=True)

    def play_answer_chime(self):
        """
        Public entrypoint: can be called from ANY thread.
        Safely queues playback to the main Qt thread.
        """
        try:
            logging.info("🔔 [AudioHandler] play_answer_chime() called")

            # Check if enabled
            if not self.get_enable_effects_sound():
                logging.debug("Answer chime disabled in config")
                return

            # Always use signal to ensure thread safety
            self._request_play_effect.emit("sound_effects", "answer_chime.wav")

        except Exception as e:
            logging.error(f"Error in play_answer_chime: {e}", exc_info=True)

    def _play_effect_impl(self, sound_type: str, file_name: str):
        """
        Runs on AudioHandler's thread (main Qt thread).
        Actual QMediaPlayer work happens here.
        """
        try:
            logging.info(f"🎵 _play_effect_impl: {sound_type}/{file_name}")

            # Check if sounds are enabled
            if sound_type == "sound_effects" and not self.get_enable_effects_sound():
                logging.warning("❌ Effects sound disabled in config")
                return

            file_path = resolve_sound_file(sound_type, file_name)
            logging.info(f"🔊 Attempting to play: {file_path}")

            if not file_path:
                logging.error(f"❌ File not found for {sound_type}: {file_name}")
                return

            # ✅ Validate format using local method
            if not self.is_supported_format(file_path):
                logging.error(f"❌ Unsupported format: {file_path}")
                return

            # Get volume (effects use the SOUND.effects_volume key)
            volume = self.config_manager.getint("SOUND", _volume_key(sound_type), fallback=70)
            logging.debug(f"Volume: {volume}")

            # Create player
            player = QMediaPlayer()
            output = QAudioOutput()
            player.setAudioOutput(output)
            output.setVolume(max(0.0, min(1.0, volume / 100.0)))

            player.setSource(QUrl.fromLocalFile(file_path))

            # Set to play once
            try:
                player.setLoops(1)
            except Exception:
                pass

            # Debug hooks
            player.errorOccurred.connect(
                lambda err, s: logging.error(f"QMediaPlayer ERROR {err}: {s}")
            )
            player.mediaStatusChanged.connect(
                lambda st: logging.debug(f"QMediaPlayer STATUS: {st}")
            )
            player.playbackStateChanged.connect(
                lambda st: logging.debug(f"QMediaPlayer STATE: {st}")
            )

            # Start playback
            player.play()
            logging.info("▶️ Audio playback started")

            # Store references
            key = f"effect_{time.time()}"
            self.players[key] = player
            self.audio_outputs[key] = output

            # Cleanup when finished
            def _on_status(st, k=key, p=player, o=output):
                if st == QMediaPlayer.MediaStatus.EndOfMedia:
                    logging.debug(f"Audio finished: {k}")
                    try:
                        self.players.pop(k, None)
                        self.audio_outputs.pop(k, None)
                    except Exception:
                        pass
                    try:
                        p.deleteLater()
                    except Exception:
                        pass
                    try:
                        o.deleteLater()
                    except Exception:
                        pass
                    self.audio_finished.emit(k)

            player.mediaStatusChanged.connect(_on_status)

        except Exception as e:
            logging.error(f"Error in _play_effect_impl({sound_type}, {file_name}): {e}", exc_info=True)

    def get_enable_timer_sound(self):
        """Check if timer sound is enabled in config."""
        try:
            return self.config_manager.getboolean("SOUND", "enable_timer_sound", fallback=True)
        except Exception as e:
            logging.error(f"Error fetching timer sound settings: {e}")
            return False

    def get_enable_effects_sound(self):
        """Check if sound effects are enabled in config."""
        try:
            return self.config_manager.getboolean("SOUND", "enable_effects_sound", fallback=True)
        except Exception as e:
            logging.error(f"Error fetching enable_effects_sound setting: {e}")
            return True

    def pause_audio(self, sound_type):
        """Pauses the audio and stores its current position (for timer)."""
        if sound_type in self.players:
            player = self.players[sound_type]
            if sound_type == "timer":
                self.timer_audio_position = player.position()
            player.pause()
            logging.debug(f"Paused {sound_type} audio")

    def resume_audio(self, sound_type="timer"):
        """Resumes the audio from the stored position (for timer)."""
        if sound_type in self.players:
            player = self.players[sound_type]
            if sound_type == "timer" and isinstance(self.timer_audio_position, int) and self.timer_audio_position > 0:
                player.setPosition(self.timer_audio_position)
            player.play()
            logging.debug(f"Resumed {sound_type} audio")

    def stop_audio(self, sound_type):
        """Stops a specific audio type."""
        if sound_type in self.players:
            player = self.players.pop(sound_type)
            try:
                player.stop()
            except Exception:
                pass
            try:
                player.deleteLater()
            except Exception:
                pass

        if sound_type in self.audio_outputs:
            output = self.audio_outputs.pop(sound_type)
            try:
                output.deleteLater()
            except Exception:
                pass

        if sound_type == "timer":
            self.timer_audio_position = 0

        logging.debug(f"Stopped {sound_type} audio")

    def stop_all(self):
        """Stops all playing media (audio & video)."""
        for sound_type, player in list(self.players.items()):
            try:
                player.stop()
            except Exception as e:
                logging.error(f"Error stopping {sound_type}: {e}")
            try:
                player.deleteLater()
            except Exception:
                pass
        for _, output in list(self.audio_outputs.items()):
            try:
                output.deleteLater()
            except Exception:
                pass
        self.players.clear()
        self.audio_outputs.clear()
        self.timer_audio_position = 0
        logging.info("All audio stopped")


    def stop_all_audio(self):
        """Backward-compatible alias used by older cleanup call sites."""
        self.stop_all()

    def cleanup_resources(self):
        """Service cleanup hook compatibility for generic lifecycle managers."""
        self.stop_all()

    def clear_cache(self):
        """Compatibility no-op for generic cache cleanup hooks."""
        # AudioHandler keeps runtime players only; ensure they are stopped.
        self.stop_all()

    def verify_paths(self):
        """Verify critical audio paths exist."""
        timer_dir = self.audio_video_manager.AUDIO_CATEGORIES.get("timer", "")

        if not os.path.exists(timer_dir):
            logging.error(f"Timer directory does not exist: {timer_dir}")
            return False

        try:
            dir_contents = os.listdir(timer_dir)
            if not dir_contents:
                logging.error(f"Timer directory is empty: {timer_dir}")
                return False
            logging.info(f"Timer directory OK: {len(dir_contents)} files found")
        except Exception as e:
            logging.error(f"Cannot access timer directory: {timer_dir}, Error: {e}")
            return False

        return True

    @staticmethod
    def debug_list_directory_files(directory_path):
        """Lists all files in the specified directory for debugging."""
        try:
            if os.path.exists(directory_path):
                files = os.listdir(directory_path)
                logging.info(f"Directory {directory_path}: {len(files)} files")
                for f in files:
                    logging.info(f"  - {f}")
                return files
            else:
                logging.error(f"Directory does not exist: {directory_path}")
                return []
        except Exception as e:
            logging.error(f"Error listing directory {directory_path}: {e}")
            return []
