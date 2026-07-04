import logging
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class SubsystemRegistration:
    name: str
    start: Optional[Callable[[], None]] = None
    stop: Optional[Callable[[], None]] = None
    started: bool = False
    generation: int = 0


class SubsystemManager:
    """Centralized lifecycle manager for long-running subsystems."""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._lock = threading.RLock()
        self._subsystems: Dict[str, SubsystemRegistration] = {}

    @classmethod
    def get_instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register(self, name: str, *, start: Optional[Callable[[], None]] = None, stop: Optional[Callable[[], None]] = None):
        with self._lock:
            existing = self._subsystems.get(name)
            started = existing.started if existing else False
            generation = existing.generation if existing else 0
            self._subsystems[name] = SubsystemRegistration(
                name=name,
                start=start,
                stop=stop,
                started=started,
                generation=generation,
            )
            self.logger.debug(f"Registered subsystem '{name}' (started={started})")

    def start(self, name: str):
        with self._lock:
            entry = self._subsystems.get(name)
            if not entry:
                raise KeyError(f"Subsystem '{name}' not registered")
            if entry.started:
                self.logger.warning(f"Duplicate start prevented for subsystem '{name}'")
                return False
            entry.started = True
            entry.generation += 1
            generation = entry.generation
        self.logger.info(f"Subsystem start: {name} (gen={generation})")
        if entry.start:
            entry.start()
        return True

    def stop(self, name: str):
        with self._lock:
            entry = self._subsystems.get(name)
            if not entry:
                return False
            if not entry.started:
                return True
            entry.started = False
            generation = entry.generation
        self.logger.info(f"Subsystem stop: {name} (gen={generation})")
        if entry.stop:
            entry.stop()
        return True

    def status(self) -> Dict[str, Dict[str, object]]:
        with self._lock:
            return {
                name: {
                    "started": entry.started,
                    "generation": entry.generation,
                    "has_start": bool(entry.start),
                    "has_stop": bool(entry.stop),
                }
                for name, entry in self._subsystems.items()
            }

    def stop_all(self):
        with self._lock:
            names = list(self._subsystems.keys())
        for name in reversed(names):
            try:
                self.stop(name)
            except Exception as e:
                self.logger.error(f"Failed to stop subsystem '{name}': {e}")
