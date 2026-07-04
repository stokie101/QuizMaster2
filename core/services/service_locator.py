# service_locator.py
import logging
import threading
from typing import Any, List

from core.services.lifecycle_state import is_shutting_down


class ServiceLocator:
    """Enhanced ServiceLocator that supports lazy loading"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._services = {}
        self._lazy_registry = None
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register_service(self, name: str, instance: Any):
        """Register a service instance"""
        self._services[name] = instance
        self.logger.debug(f"Service registered: {name}")

    def get(self, name: str):
        """Alias for get_service for backward compatibility"""
        return self.get_service(name)

    def get_service(self, name: str) -> Any:
        """Get a service, using lazy loading if available"""
        # Check direct registrations first
        if name in self._services:
            return self._services[name]

        # Never create or lazy-load services during teardown
        if is_shutting_down():
            raise RuntimeError(f"Service resolution blocked during shutdown: {name}")

        # Try lazy loading
        if self._lazy_registry:
            try:
                service = self._lazy_registry.get_service(name)
                # Auto-register the service for future lookups
                self.register_service(name, service)
                return service
            except ValueError:
                raise
            except RuntimeError:
                raise
            except AttributeError as e:
                self.logger.warning(f"Invalid lazy registry implementation for service {name}: {e}")

        raise ValueError(f"Service not found: {name}")

    def reinitialize_service(self, service_name):
        """Reinitialize a specific service."""
        try:
            if service_name in self._services:
                # Clean up existing service if it has cleanup method
                existing_service = self._services[service_name]
                if hasattr(existing_service, 'cleanup'):
                    existing_service.cleanup()

                # Remove from services
                del self._services[service_name]

            # Reinitialize the service
            if service_name == "CSVHandler":
                from core.quiz.csv.csv_handler import CSVHandler
                self._services[service_name] = CSVHandler()
                self.logger.info(f"Reinitialized service: {service_name}")
            # Add other services as needed

        except Exception as e:
            self.logger.error(f"Error reinitializing service {service_name}: {e}")
            raise

    # In service_locator.py
    def set_lazy_registry(self, registry):
        """Set the lazy loading registry"""
        self._lazy_registry = registry
        self.logger.info("Lazy registry set successfully")
        if hasattr(registry, 'loaded_services'):
            for name, service in registry.loaded_services.items():
                self.register_service(name, service)
            self.logger.info(f"Registered {len(registry.loaded_services)} existing services")
        try:
            async_helper = self.get_service("AsyncHelper")
            if async_helper:
                # Get QApplication instance
                app = self.get_service("QApplication")
                # Pass it to AsyncHelper.initialize()
                async_helper.initialize(app)
        except ValueError:
            self.logger.debug("AsyncHelper unavailable during lazy registry setup")
        except TypeError as e:
            self.logger.error(f"AsyncHelper initialization signature mismatch: {e}")

    def has_service(self, name: str) -> bool:
        """Check if a service is available"""
        if name in self._services:
            return True
        if self._lazy_registry and hasattr(self._lazy_registry, 'is_service_defined'):
            return self._lazy_registry.is_service_defined(name)
        return False

    def get_all_registered_services(self) -> List[str]:
        """Get names of all directly registered services"""
        return list(self._services.keys())
