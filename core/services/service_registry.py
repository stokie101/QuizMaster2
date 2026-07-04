import importlib
import logging
import threading
import time
from typing import Any, Dict

from core.services.service_config import ServicePriority, ServiceConfig
from core.services.lifecycle_state import is_shutting_down
from core.services.service_configurations import get_service_configs


class ServiceRegistry:
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._loading: set = set()
        self._failed: set = set()
        self._lock = threading.RLock()
        self._service_class_cache: Dict[tuple[str, str], Any] = {}
        self._configs = get_service_configs()
        self.logger = self._setup_logger()
        self._service_locator = None

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger = logging.getLogger(f"{__name__}.ServiceRegistry")
        logger.setLevel(logging.NOTSET)
        return logger

    def set_service_locator(self, service_locator):
        """Set the service locator for integration"""
        self._service_locator = service_locator
        self._register_existing_services()

    # In service_registry.py
    def _register_existing_services(self):
        """Register all existing services with the ServiceLocator"""
        if not self._service_locator:
            return

        for name, service in self._services.items():
            try:
                try:
                    existing = self._service_locator.get_service(name)
                    if existing is service:
                        continue
                except ValueError:
                    # Service locator raises ValueError when a service is absent.
                    pass

                self._service_locator.register_service(name, service)
                self.logger.debug(f"Registered existing service {name} with ServiceLocator")
            except Exception as e:
                self.logger.warning(f"Failed to register existing service {name}: {e}")

    # Add to service_registry.py
    def get(self, service_name: str, timeout=5.0) -> Any:
        """Get a service, loading it if necessary with timeout"""
        start_time = time.monotonic()

        with self._lock:
            if service_name == "QuizManager":
                self.logger.debug(f"Requested QuizManager, checking if it exists in registry")
                if service_name in self._services:
                    service = self._services[service_name]
                    self.logger.debug(f"Found QuizManager in registry, type: {type(service).__name__}")
                    return service
                else:
                    self.logger.debug(f"QuizManager not in registry, will load it")

            if service_name in self._services:
                return self._services[service_name]

            if is_shutting_down():
                raise RuntimeError(f"Service resolution blocked during shutdown: {service_name}")

            if service_name in self._failed:
                config = self._configs.get(service_name)
                if not config or not config.optional:
                    raise RuntimeError(f"Service '{service_name}' previously failed to load")
                return None

            if service_name in self._loading:
                # Check if we've been waiting too long
                if time.monotonic() - start_time > timeout:
                    self.logger.error(f"Timeout loading service: {service_name}")
                    raise RuntimeError(f"Timeout loading service: {service_name}")
                raise RuntimeError(f"Circular dependency detected: {service_name}")

            return self._load_service(service_name)

    def _load_service(self, service_name: str) -> Any:
        """Load a service with enhanced error handling and retries"""
        global config
        if service_name == "QuizManager":
            self.logger.debug(f"Loading QuizManager service")

        if service_name in self._failed:
            config = self._configs.get(service_name)
            if config and not config.optional:
                # Try one more time for critical services
                self._failed.discard(service_name)
                self.logger.warning(f"Retrying previously failed critical service: {service_name}")

        if service_name in self._services:
            if service_name == "QuizManager":
                service = self._services[service_name]
                self.logger.debug(f"QuizManager already loaded, type: {type(service).__name__}")
                return self._services[service_name]

        try:
            config = self._configs.get(service_name)
            if not config:
                raise RuntimeError(f"No configuration found for service: {service_name}")

            # Load dependencies first with better error handling
            if config.dependencies:
                missing_deps = []
                for dep in config.dependencies:
                    if dep not in self._services:
                        try:
                            self.logger.debug(f"Loading dependency {dep} for {service_name}")
                            self.get(dep)
                        except Exception as e:
                            self.logger.error(f"Failed to load dependency {dep}: {e}")
                            missing_deps.append(dep)

                if missing_deps and not config.optional:
                    raise RuntimeError(f"Missing critical dependencies for {service_name}: {missing_deps}")

            self._loading.add(service_name)
            try:
                # Enhanced instance creation with retries
                max_retries = 3 if config.priority == ServicePriority.CRITICAL else 1
                instance = None

                for attempt in range(max_retries):
                    if is_shutting_down():
                        raise RuntimeError(f"Aborting load retries during shutdown: {service_name}")
                    try:
                        instance = self._create_instance(config)
                        break
                    except Exception as e:
                        if is_shutting_down():
                            raise RuntimeError(f"Aborting load retries during shutdown: {service_name}") from e
                        if attempt < max_retries - 1:
                            self.logger.warning(f"Attempt {attempt + 1} failed for {service_name}: {e}")
                            time.sleep(0.5)  # Brief delay between retries
                        else:
                            raise

                if instance is None:
                    raise RuntimeError(f"Failed to create instance after {max_retries} attempts")

                if service_name == "QuizManager":
                    self.logger.debug(f"Created QuizManager instance, type: {type(instance).__name__}")

                self._services[service_name] = instance
                # Register with ServiceLocator if available
                if self._service_locator and service_name != "ServiceLocator":
                    try:
                        self._service_locator.register_service(service_name, instance)
                        self.logger.debug(f"Registered {service_name} with ServiceLocator")
                    except Exception as e:
                        self.logger.warning(f"Failed to register {service_name} with ServiceLocator: {e}")

                self.logger.debug(f"Successfully loaded service: {service_name}")
                return instance

            finally:
                self._loading.discard(service_name)

        except Exception as e:
            self.logger.error(f"❌ Failed to load {service_name}: {e}")
            self._failed.add(service_name)

            # For critical services, don't give up easily
            if config and config.priority == ServicePriority.CRITICAL and not config.optional:
                self.logger.error(f"CRITICAL SERVICE FAILURE: {service_name} is required for application functionality")

            raise

    def check_for_duplicates(self):
        """Check for duplicate service registrations"""
        service_types = {}
        for name, service in self._services.items():
            service_type = type(service).__name__
            if service_type not in service_types:
                service_types[service_type] = []
            service_types[service_type].append(name)

        for service_type, names in service_types.items():
            if len(names) > 1:
                self.logger.warning(f"WARNING: Service type {service_type} registered under multiple names: {names}")

    def _resolve_service_class(self, config: ServiceConfig):
        cache_key = (config.module_path, config.class_name)
        service_class = self._service_class_cache.get(cache_key)
        if service_class is None:
            module = importlib.import_module(config.module_path)
            service_class = getattr(module, config.class_name)
            self._service_class_cache[cache_key] = service_class
        return service_class

    @staticmethod
    def _instantiate_service(config: ServiceConfig, service_class):
        if config.class_name == "QApplication":
            existing_app = service_class.instance()
            if existing_app is not None:
                return existing_app
            if is_shutting_down():
                raise RuntimeError("QApplication construction blocked during shutdown")
            import sys
            return service_class(sys.argv)

        if config.factory_method and hasattr(service_class, config.factory_method):
            return getattr(service_class, config.factory_method)()

        if config.singleton and hasattr(service_class, 'get_instance'):
            return service_class.get_instance()

        return service_class()

    def _create_instance(self, config: ServiceConfig):
        try:
            service_class = self._resolve_service_class(config)
            instance = self._instantiate_service(config, service_class)

            # Initialize if needed
            if hasattr(instance, 'initialize') and not getattr(instance, '_service_registry_initialized', False):
                self.logger.debug(f"Calling initialize() for {config.class_name}")
                init_result = instance.initialize()
                instance._service_registry_initialized = True
                if init_result is False:
                    raise RuntimeError(f"Service {config.class_name} initialization failed")
                self.logger.debug(f"Successfully initialized {config.class_name}")
            return instance
        except (ImportError, AttributeError, TypeError, RuntimeError) as e:
            self.logger.error(f"Failed to create {config.class_name}: {e}")
            raise

    # In service_registry.py
    def preload_critical(self):
        """Preload critical services first with enhanced error handling"""
        critical_services = [
            name for name, config in self._configs.items()
            if config.priority == ServicePriority.CRITICAL
        ]

        for service_name in critical_services:
            try:
                self.logger.debug(f"Loading critical service: {service_name}")
                self.get(service_name)
            except Exception as e:
                self.logger.error(f"Failed to preload critical service {service_name}: {str(e)}")
                # Continue loading other critical services instead of raising
                if service_name not in ["TabManager"]:  # These seem problematic
                    raise

    def load_by_priority(self, max_priority: ServicePriority = ServicePriority.LOW):
        """Load services up to a certain priority level"""
        services_by_priority = {}
        for name, config in self._configs.items():
            priority = config.priority.value
            if priority <= max_priority.value:
                if priority not in services_by_priority:
                    services_by_priority[priority] = []
                services_by_priority[priority].append(name)

        # Load in priority order
        for priority in sorted(services_by_priority.keys()):
            for service_name in services_by_priority[priority]:
                if service_name not in self._services:
                    try:
                        self.get(service_name)
                    except Exception as e:
                        config = self._configs.get(service_name)
                        if not config or not config.optional:
                            self.logger.error(f"Failed to load {service_name}: {e}")

    def cleanup(self):
        """Cleanup all services"""
        with self._lock:
            for name, service in reversed(list(self._services.items())):
                try:
                    if hasattr(service, 'cleanup'):
                        service.cleanup()
                except Exception as e:
                    self.logger.warning(f"Error cleaning up {name}: {e}")

            self._services.clear()
            self._failed.clear()

    def status(self) -> Dict[str, Any]:
        """Get registry status"""
        return {
            'total_configs': len(self._configs),
            'loaded': len(self._services),
            'failed': len(self._failed),
            'loading': len(self._loading),
            'loaded_services': list(self._services.keys()),
            'failed_services': list(self._failed)
        }