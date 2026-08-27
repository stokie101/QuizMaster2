import logging
import threading


class QuizTimer:
    """Thread-safe countdown timer with generation-based worker cancellation."""

    def __init__(self, audio_handler=None):
        self.audio = audio_handler
        self.logger = logging.getLogger("QuizTimer")
        self.duration = 0
        self.remaining = 0
        self.running = False
        self.paused = False
        self._thread = None
        self._state_lock = threading.RLock()
        self._generation = 0
        self._thread_stop = None
        self.on_tick = None
        self.on_expired = None

    def _bump_generation(self):
        self._generation += 1
        stop_event = threading.Event()
        self._thread_stop = stop_event
        return self._generation, stop_event

    DEFAULT_BACKGROUND_FILE = "background_music.wav"

    def _background_file_name(self) -> str:
        """The configured background track, or the packaged default."""
        try:
            from config.config_manager import ConfigManager

            configured = ConfigManager.get_instance().get(
                "SOUND", "background_file", fallback=""
            )
            if configured and str(configured).strip():
                return str(configured).strip()
        except Exception:
            pass
        return self.DEFAULT_BACKGROUND_FILE

    def start(self, duration: int):
        if not isinstance(duration, (int, float)) or duration <= 0:
            self.logger.error(f"Invalid duration: {duration}")
            return False

        self.stop()

        with self._state_lock:
            self.duration = int(duration)
            self.remaining = int(duration)
            self.running = True
            self.paused = False
            generation, stop_event = self._bump_generation()

        self.logger.info(f"Starting timer: {duration}s (gen={generation})")

        if self.audio:
            try:
                self.audio.play_timer_for_duration("timer_sound.wav", duration)
            except Exception as e:
                self.logger.warning(f"Failed to play timer audio: {e}")
            try:
                # The bed is aligned the same way as the cue: its last `duration`
                # seconds play, so the music resolves exactly as the clock hits
                # zero instead of being cut off wherever it happened to be.
                self.audio.play_background_for_duration(
                    self._background_file_name(), duration
                )
            except Exception as e:
                self.logger.warning(f"Failed to play background audio: {e}")

        self._thread = threading.Thread(
            target=self._run,
            args=(generation, stop_event),
            daemon=True,
            name=f"QuizTimer-{generation}"
        )
        self.logger.debug(f"Timer thread created: {self._thread.name}")
        self._thread.start()
        return True

    def pause(self):
        with self._state_lock:
            if not self.running or self.paused:
                return False
            self.paused = True
            self.running = False
            stop_event = self._thread_stop

        if stop_event:
            stop_event.set()

        if self.audio:
            for sound_type in ("timer", "background"):
                try:
                    self.audio.pause_audio(sound_type)
                except Exception as e:
                    self.logger.warning(f"Failed to pause {sound_type} audio: {e}")
        return True

    def resume(self):
        with self._state_lock:
            if not self.paused or self.remaining <= 0:
                return False
            self.running = True
            self.paused = False
            generation, stop_event = self._bump_generation()

        if self.audio:
            for sound_type in ("timer", "background"):
                try:
                    self.audio.resume_audio(sound_type)
                except Exception as e:
                    self.logger.warning(f"Failed to resume {sound_type} audio: {e}")

        self._thread = threading.Thread(
            target=self._run,
            args=(generation, stop_event),
            daemon=True,
            name=f"QuizTimer-{generation}"
        )
        self.logger.debug(f"Timer thread created: {self._thread.name}")
        self._thread.start()
        return True

    def stop(self):
        with self._state_lock:
            was_running = self.running or self.paused
            self.running = False
            self.paused = False
            self._generation += 1
            stop_event = self._thread_stop

        if stop_event:
            stop_event.set()

        if self.audio:
            for sound_type in ("timer", "background"):
                try:
                    self.audio.stop_audio(sound_type)
                except Exception as e:
                    self.logger.warning(f"Failed to stop {sound_type} audio: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self.logger.warning("Timer thread did not stop cleanly")
            else:
                self.logger.debug("Timer thread exited")
        self._thread = None

        if was_running:
            self.logger.info("Timer stopped")
        return True

    def _run(self, generation: int, stop_event: threading.Event):
        thread_name = threading.current_thread().name
        try:
            while True:
                if stop_event.wait(timeout=1.0):
                    self.logger.debug(f"Timer thread stopping: {thread_name}")
                    return

                with self._state_lock:
                    if generation != self._generation or not self.running:
                        return
                    self.remaining -= 1
                    remaining = self.remaining

                if self.on_tick:
                    self.on_tick(remaining)

                if remaining <= 0:
                    with self._state_lock:
                        if generation != self._generation:
                            return
                        self.running = False
                        self.paused = False

                    if self.audio:
                        try:
                            self.audio.stop_audio("background")
                            self.audio.stop_audio("timer")
                        except Exception:
                            pass

                    if self.on_expired:
                        self.on_expired()
                    return
        except Exception as e:
            self.logger.error(f"Unexpected error in timer thread {thread_name}: {e}", exc_info=True)
            with self._state_lock:
                if generation == self._generation:
                    self.running = False
                    self.paused = False

    def is_running(self):
        with self._state_lock:
            return self.running

    def is_paused(self):
        with self._state_lock:
            return self.paused

    def get_remaining(self):
        with self._state_lock:
            return self.remaining

    def get_state(self):
        with self._state_lock:
            return {
                "duration": self.duration,
                "remaining": self.remaining,
                "running": self.running,
                "paused": self.paused,
                "elapsed": self.duration - self.remaining if self.duration > 0 else 0,
                "generation": self._generation,
            }

    def reset(self):
        with self._state_lock:
            self.remaining = self.duration
            self.running = False
            self.paused = False

    def add_time(self, seconds):
        if not isinstance(seconds, (int, float)):
            return False
        with self._state_lock:
            if not self.running:
                return False
            self.remaining = max(0, self.remaining + int(seconds))
        return True

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
