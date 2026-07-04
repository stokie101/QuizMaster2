# core/utils/signal_tracker.py
import datetime
import gc
import inspect
import logging
import threading
import time
import traceback
import weakref
from typing import Dict, List, Any, Optional, Callable, Set

from PySide6.QtCore import QObject


class SignalTracker(QObject):
    """Tracks and manages signal connections with enhanced safety and debugging - Singleton"""

    _instance = None
    _instance_lock = threading.Lock()
    _initialized = False

    def __new__(cls, parent=None):
        """Ensure only one instance exists"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super(SignalTracker, cls).__new__(cls)
        return cls._instance

    def __init__(self, parent=None):
        """Initialize only once"""
        if SignalTracker._initialized:
            return

        with SignalTracker._instance_lock:
            if SignalTracker._initialized:
                return

            super().__init__(parent)

            # Initialize instance variables
            self.connections: Dict[str, List[Dict[str, Any]]] = {}
            self._signal_refs: Dict[str, Any] = {}
            self.logger = logging.getLogger(self.__class__.__name__)
            self._cleanup_in_progress = False
            self._connection_history: List[Dict[str, Any]] = []
            self._max_history = 100
            self._connection_count = 0
            self._disconnection_count = 0
            self._active_connections: Set[str] = set()

            # Add thread safety
            self._lock = threading.RLock()
            self._destroyed = False

            # Add timeout protection
            self._operation_timeout = 5.0  # 5 seconds max per operation

            # Debug mode
            self._debug_mode = False

            # Mark as initialized
            SignalTracker._initialized = True

            self.logger.debug("SignalTracker singleton initialized")

    @classmethod
    def get_instance(cls, parent=None):
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (for testing purposes)"""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance.cleanup()
                except Exception as e:
                    logging.getLogger(cls.__name__).error(f"Error during instance reset cleanup: {e}")

            cls._instance = None
            cls._initialized = False

    def register_connection(self, connection_id: str, signal, slot, sender_obj: Optional[QObject] = None) -> bool:
        """Alias for connect method to maintain compatibility with older code"""
        return self.connect(connection_id, signal, slot, sender_obj)

    def set_debug_mode(self, enabled: bool):
        """Enable/disable debug mode for verbose logging"""
        self._debug_mode = enabled
        if enabled:
            self.logger.setLevel(logging.DEBUG)

    def _debug_log(self, message: str):
        """Log debug message if debug mode is enabled"""
        if self._debug_mode:
            self.logger.debug(f"[DEBUG] {message}")

    def _check_destroyed(self) -> bool:
        """Check if this object has been destroyed"""
        if self._destroyed:
            self.logger.warning("Attempting to use destroyed SignalTracker")
            return True
        return False

    def _with_timeout(self, operation_name: str, func: Callable, *args, **kwargs):
        """Execute function with timeout protection"""
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            if elapsed > 1.0:  # Warn if operation takes more than 1 second
                self.logger.warning(f"{operation_name} took {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            self.logger.error(f"{operation_name} failed after {elapsed:.3f}s: {e}")
            raise

    def connect(self, connection_id: str, signal, slot, sender_obj: Optional[QObject] = None) -> bool:
        """
        Connect a signal to a slot and track the connection with timeout protection
        """
        if self._check_destroyed():
            return False

        with self._lock:
            try:
                # First disconnect to avoid duplicate connections
                self.disconnect(connection_id)

                return self._with_timeout("connect", self._connect_internal,
                                          connection_id, signal, slot, sender_obj)
            except Exception as e:
                self.logger.error(f"Connect operation failed for {connection_id}: {e}")
                return False

    def _connect_internal(self, connection_id: str, signal, slot, sender_obj: Optional[QObject] = None) -> bool:
        """Internal connect method without locking"""
        try:
            self._debug_log(f"Connecting {connection_id}")

            # Validate inputs with timeout
            if not self._validate_signal_connection(connection_id, signal, slot):
                return False

            # Store connection info
            if connection_id not in self.connections:
                self.connections[connection_id] = []

            # Get caller information for debugging (with timeout protection)
            try:
                caller_info = self._get_caller_info()
            except Exception:
                caller_info = "unknown_caller"

            try:
                if hasattr(signal, 'connect') and callable(signal.connect):
                    # Handle both PySide signals and our custom Signal class
                    if hasattr(signal, 'connections'):  # Our custom Signal class
                        signal.connect(slot, connection_id)
                        connection_successful = True
                    else:  # PySide Signal
                        signal.connect(slot)
                        connection_successful = True
                else:
                    self.logger.error(f"Signal for {connection_id} does not have a connect method")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to make signal connection for {connection_id}: {e}")
                return False

            if not connection_successful:
                return False

            # Create weak reference safely
            sender_ref = None
            if sender_obj:
                try:
                    sender_ref = weakref.ref(sender_obj)
                except Exception as e:
                    self.logger.warning(f"Could not create weak reference for {connection_id}: {e}")

            # Track the connection with enhanced info
            connection_info = {
                'signal': signal,
                'slot': slot,
                'connected': True,
                'sender_ref': sender_ref,
                'slot_name': self._safe_get_name(slot),
                'connection_time': self._get_current_time(),
                'caller_info': caller_info
            }

            self.connections[connection_id].append(connection_info)

            # Store signal reference for status checking
            self._signal_refs[connection_id] = signal

            # Track active connection
            self._active_connections.add(connection_id)

            # Increment connection counter
            self._connection_count += 1

            # Add to history (with size limit)
            self._add_to_connection_history('connect', connection_id, slot, caller_info)

            self._debug_log(f"Successfully connected {connection_id} -> {connection_info['slot_name']}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect signal {connection_id}: {e}")
            self.logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _safe_get_name(obj) -> str:
        """Safely get name of an object"""
        try:
            if hasattr(obj, '__name__'):
                return obj.__name__
            elif hasattr(obj, '__class__'):
                return f"{obj.__class__.__name__}_instance"
            else:
                return str(obj)[:50]  # Limit string length
        except Exception:
            return "unknown_object"

    def _validate_signal_connection(self, connection_id: str, signal, slot) -> bool:
        """Validate signal connection parameters with timeout protection"""
        try:
            # Quick basic checks first
            if not connection_id or not isinstance(connection_id, str):
                self.logger.error("Invalid connection_id: must be non-empty string")
                return False

            if signal is None:
                self.logger.error(f"Signal is None for connection {connection_id}")
                return False

            if slot is None:
                self.logger.error(f"Slot is None for connection {connection_id}")
                return False

            # More expensive checks with timeout protection
            try:
                if not hasattr(signal, 'connect'):
                    self.logger.error(f"Signal object for {connection_id} does not have 'connect' method")
                    return False

                if not callable(slot):
                    self.logger.error(f"Slot for {connection_id} is not callable")
                    return False

                # Check if slot's instance still exists (for bound methods) - with timeout
                if hasattr(slot, '__self__'):
                    try:
                        instance = slot.__self__
                        if instance is None:
                            self.logger.error(f"Slot instance is None for {connection_id}")
                            return False
                    except (AttributeError, ReferenceError) as e:
                        self.logger.error(f"Slot instance is invalid for {connection_id}: {e}")
                        return False

                return True

            except Exception as e:
                self.logger.error(f"Error in detailed validation for {connection_id}: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Error validating connection {connection_id}: {e}")
            return False

    def disconnect(self, connection_id: str) -> bool:
        """Disconnect all signals for a given connection ID with timeout protection"""
        if self._check_destroyed():
            return True

        with self._lock:
            try:
                return self._with_timeout("disconnect", self._disconnect_internal, connection_id)
            except Exception as e:
                self.logger.error(f"Disconnect operation failed for {connection_id}: {e}")
                return False

    def _disconnect_internal(self, connection_id: str) -> bool:
        """Internal disconnect method without locking"""
        try:
            self._debug_log(f"Disconnecting {connection_id}")

            if connection_id not in self.connections:
                self._debug_log(f"Connection {connection_id} not found, already disconnected")
                return True

            success_count = 0
            total_count = len(self.connections[connection_id])

            # Get caller information for debugging
            try:
                caller_info = self._get_caller_info()
            except Exception:
                caller_info = "unknown_caller"

            # Create a copy to avoid modification during iteration
            connections_copy = list(self.connections[connection_id])

            for conn in connections_copy:
                try:
                    if conn.get('connected', False):
                        # Validate connection is still valid before disconnecting
                        if self._is_connection_valid(conn):
                            signal = conn['signal']
                            slot = conn['slot']

                            # Handle both PySide signals and our custom Signal class
                            try:
                                if hasattr(signal, 'disconnect') and callable(signal.disconnect):
                                    if hasattr(signal, 'connections'):  # Our custom Signal class
                                        signal.disconnect(slot)
                                    else:  # PySide Signal
                                        signal.disconnect(slot)

                                    success_count += 1
                                    self._disconnection_count += 1

                                    # Add to history
                                    slot_name = self._safe_get_name(slot)
                                    self._add_to_connection_history('disconnect', connection_id, slot_name, caller_info)

                            except Exception as disconnect_error:
                                self.logger.warning(
                                    f"Failed to disconnect signal for {connection_id}: {disconnect_error}")
                                # Still count as success if the error is because it's already disconnected
                                success_count += 1

                        conn['connected'] = False

                except Exception as e:
                    self.logger.warning(f"Failed to process connection in {connection_id}: {e}")

            # Clean up references
            try:
                del self.connections[connection_id]
            except KeyError:
                pass

            try:
                if connection_id in self._signal_refs:
                    del self._signal_refs[connection_id]
            except KeyError:
                pass

            # Remove from active connections
            try:
                if connection_id in self._active_connections:
                    self._active_connections.remove(connection_id)
            except KeyError:
                pass

            self._debug_log(f"Disconnected {success_count}/{total_count} signals for {connection_id}")
            return success_count == total_count

        except Exception as e:
            self.logger.error(f"Error disconnecting {connection_id}: {e}")
            return False

    @staticmethod
    def _is_connection_valid(conn_info: Dict[str, Any]) -> bool:
        """Check if a connection is still valid with timeout protection"""
        try:
            # Quick check first
            if not conn_info:
                return False

            # Check if sender object still exists (if tracked)
            sender_ref = conn_info.get('sender_ref')
            if sender_ref:
                try:
                    sender = sender_ref()
                    if sender is None:
                        return False
                except (ReferenceError, TypeError):
                    return False

            # Check if slot is still valid
            slot = conn_info.get('slot')
            if slot is None:
                return False

            # For bound methods, check if instance still exists
            if hasattr(slot, '__self__'):
                try:
                    instance = slot.__self__
                    if instance is None:
                        return False
                except (AttributeError, ReferenceError, TypeError):
                    return False

            return True

        except Exception:
            return False

    def disconnect_all(self) -> bool:
        """Disconnect all tracked signals with timeout protection"""
        if self._check_destroyed() or self._cleanup_in_progress:
            return True

        with self._lock:
            try:
                self._cleanup_in_progress = True
                return self._with_timeout("disconnect_all", self._disconnect_all_internal)
            except Exception as e:
                self.logger.error(f"Disconnect all operation failed: {e}")
                return False
            finally:
                self._cleanup_in_progress = False

    def _disconnect_all_internal(self) -> bool:
        """Internal disconnect all method"""
        try:
            success = True

            # Create a copy of keys to avoid modification during iteration
            connection_ids = list(self.connections.keys())

            self._debug_log(f"Disconnecting {len(connection_ids)} connection groups")

            for connection_id in connection_ids:
                try:
                    if not self._disconnect_internal(connection_id):
                        success = False
                except Exception as e:
                    self.logger.error(f"Error disconnecting {connection_id}: {e}")
                    success = False

            self._debug_log(f"Disconnected all signals ({'success' if success else 'with errors'})")
            return success

        except Exception as e:
            self.logger.error(f"Error disconnecting all signals: {e}")
            return False

    def cleanup_invalid_connections(self) -> int:
        """Clean up invalid connections with timeout protection"""
        if self._check_destroyed():
            return 0

        with self._lock:
            try:
                return self._with_timeout("cleanup_invalid", self._cleanup_invalid_internal)
            except Exception as e:
                self.logger.error(f"Cleanup invalid connections failed: {e}")
                return 0

    def _cleanup_invalid_internal(self) -> int:
        """Internal cleanup invalid connections method"""
        try:
            cleaned_count = 0
            connection_ids_to_remove = []

            self._debug_log("Starting cleanup of invalid connections")

            # Create copies to avoid modification during iteration
            connections_copy = dict(self.connections)

            for connection_id, connections in connections_copy.items():
                valid_connections = []

                for conn in connections:
                    if self._is_connection_valid(conn):
                        valid_connections.append(conn)
                    else:
                        cleaned_count += 1
                        # Try to disconnect if still possible
                        try:
                            if conn.get('connected', False):
                                signal = conn['signal']
                                slot = conn['slot']

                                if hasattr(signal, 'disconnect') and callable(signal.disconnect):
                                    if hasattr(signal, 'connections'):  # Our custom Signal class
                                        signal.disconnect(slot)
                                    else:  # PySide Signal
                                        signal.disconnect(slot)

                                slot_name = self._safe_get_name(slot)
                                self._add_to_connection_history('cleanup', connection_id, slot_name, "auto_cleanup")
                        except Exception:
                            pass  # Ignore errors for invalid connections

                if valid_connections:
                    self.connections[connection_id] = valid_connections
                else:
                    connection_ids_to_remove.append(connection_id)

            # Remove empty connection groups
            for connection_id in connection_ids_to_remove:
                try:
                    if connection_id in self.connections:
                        del self.connections[connection_id]
                    if connection_id in self._signal_refs:
                        del self._signal_refs[connection_id]
                    if connection_id in self._active_connections:
                        self._active_connections.remove(connection_id)
                except Exception as e:
                    self.logger.warning(f"Error removing {connection_id}: {e}")

            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} invalid connections")

            return cleaned_count

        except Exception as e:
            self.logger.error(f"Error cleaning up invalid connections: {e}")
            return 0

    @staticmethod
    def _get_caller_info() -> str:
        """Get information about the caller for debugging with timeout protection"""
        try:
            stack = inspect.stack()
            # Skip this function and its caller to get the actual caller
            if len(stack) > 3:  # Adjusted for additional wrapper methods
                caller = stack[3]
                filename = caller.filename.split('/')[-1].split('\\')[-1]  # Handle both / and \ separators
                return f"{filename}:{caller.lineno} in {caller.function}"
            return "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _get_current_time() -> str:
        """Get current time as string for logging"""
        try:
            return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        except Exception:
            return "unknown"

    def _add_to_connection_history(self, action: str, connection_id: str, slot, caller_info: str) -> None:
        """Add a connection/disconnection event to the history with size limits"""
        try:
            slot_name = slot if isinstance(slot, str) else self._safe_get_name(slot)

            # Limit the size of individual entries
            if len(slot_name) > 100:
                slot_name = slot_name[:97] + "..."
            if len(caller_info) > 100:
                caller_info = caller_info[:97] + "..."

            self._connection_history.append({
                'time': self._get_current_time(),
                'action': action,
                'connection_id': connection_id,
                'slot': slot_name,
                'caller': caller_info
            })

            # Trim history if needed
            if len(self._connection_history) > self._max_history:
                self._connection_history = self._connection_history[-self._max_history:]

        except Exception as e:
            self.logger.error(f"Error adding to connection history: {e}")

    def is_connected(self, connection_id: str) -> bool:
        """Check if a connection is active and valid"""
        if self._check_destroyed():
            return False

        try:
            with self._lock:
                return connection_id in self._active_connections
        except Exception as e:
            self.logger.error(f"Error checking connection status for {connection_id}: {e}")
            return False

    def get_connection_status(self, signal_name: str) -> Dict[str, Any]:
        """Get detailed status of a signal connection"""
        if self._check_destroyed():
            return {'exists': False, 'error': 'SignalTracker destroyed'}

        try:
            with self._lock:
                if signal_name in self.connections:
                    connections = self.connections[signal_name]
                    active_connections = 0
                    valid_connections = 0

                    for conn in connections:
                        if conn.get('connected', False):
                            active_connections += 1
                            if self._is_connection_valid(conn):
                                valid_connections += 1

                    return {
                        'exists': True,
                        'total_connections': len(connections),
                        'active_connections': active_connections,
                        'valid_connections': valid_connections,
                        'connection_details': [
                            {
                                'slot_name': conn.get('slot_name', 'unknown'),
                                'connected': conn.get('connected', False),
                                'valid': self._is_connection_valid(conn),
                                'connection_time': conn.get('connection_time', 'unknown'),
                                'caller_info': conn.get('caller_info', 'unknown')
                            }
                            for conn in connections
                        ]
                    }

                return {
                    'exists': False,
                    'total_connections': 0,
                    'active_connections': 0,
                    'valid_connections': 0,
                    'connection_details': []
                }

        except Exception as e:
            self.logger.error(f"Error getting connection status for {signal_name}: {e}")
            return {'exists': False, 'error': str(e)}

    def get_all_connections(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all connections"""
        if self._check_destroyed():
            return {}

        try:
            with self._lock:
                status = {}
                for connection_id in self.connections:
                    status[connection_id] = self.get_connection_status(connection_id)
                return status
        except Exception as e:
            self.logger.error(f"Error getting all connections: {e}")
            return {}

    def get_debug_info(self) -> Dict[str, Any]:
        """Get debug information about all tracked connections"""
        if self._check_destroyed():
            return {'error': 'SignalTracker destroyed'}

        try:
            with self._lock:
                return {
                    'singleton_instance_id': id(self),
                    'total_connection_groups': len(self.connections),
                    'total_individual_connections': sum(len(conns) for conns in self.connections.values()),
                    'active_connections': len(self._active_connections),
                    'connection_count': self._connection_count,
                    'disconnection_count': self._disconnection_count,
                    'connection_details': self.get_all_connections(),
                    'signal_refs_count': len(self._signal_refs),
                    'connection_history': self._connection_history[-10:]  # Last 10 events
                }
        except Exception as e:
            self.logger.error(f"Error getting debug info: {e}")
            return {'error': str(e)}

    def print_debug_info(self) -> None:
        """Print debug information about all tracked connections"""
        try:
            debug_info = self.get_debug_info()

            self.logger.info("=== SignalTracker Debug Information ===")
            self.logger.info(f"Singleton instance ID: {debug_info.get('singleton_instance_id', 'unknown')}")
            self.logger.info(f"Total connection groups: {debug_info.get('total_connection_groups', 0)}")
            self.logger.info(f"Total individual connections: {debug_info.get('total_individual_connections', 0)}")
            self.logger.info(f"Active connections: {debug_info.get('active_connections', 0)}")
            self.logger.info(f"Total connections made: {debug_info.get('connection_count', 0)}")
            self.logger.info(f"Total disconnections: {debug_info.get('disconnection_count', 0)}")

            # Print recent connection history
            history = debug_info.get('connection_history', [])
            if history:
                self.logger.info("Recent connection history:")
                for i, event in enumerate(history):
                    self.logger.info(
                        f"  {i + 1}. {event['time']} - {event['action']} {event['connection_id']} -> {event['slot']} from {event['caller']}")

            # Print active connections
            if self._active_connections:
                self.logger.info("Active connections:")
                for connection_id in sorted(self._active_connections):
                    status = self.get_connection_status(connection_id)
                    if status['exists']:
                        self.logger.info(
                            f"  {connection_id}: {status['active_connections']}/{status['total_connections']} active")

            self.logger.info("========================================")
        except Exception as e:
            self.logger.error(f"Error printing debug info: {e}")

    def cleanup(self):
        """Clean up all tracked connections with timeout protection"""
        if self._destroyed or self._cleanup_in_progress:
            return

        try:
            with self._lock:
                self._destroyed = True
                self._cleanup_in_progress = True

                self.logger.debug("Starting SignalTracker cleanup")

                # Disconnect all signals with timeout
                try:
                    self._with_timeout("cleanup_disconnect_all", self._disconnect_all_internal)
                except Exception as e:
                    self.logger.error(f"Error during cleanup disconnect: {e}")

                # Clear all references
                try:
                    self.connections.clear()
                    self._signal_refs.clear()
                    self._active_connections.clear()
                    self._connection_history.clear()
                except Exception as e:
                    self.logger.error(f"Error clearing references: {e}")

                self.logger.info("SignalTracker cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during SignalTracker cleanup: {e}")
        finally:
            try:
                # Force garbage collection
                gc.collect()
            except Exception:
                pass

    def __del__(self):
        """Destructor to ensure cleanup"""
        try:
            if not self._destroyed:
                self.cleanup()
        except Exception:
            pass  # Ignore errors during destruction


# Convenience function for getting the singleton instance
def get_signal_tracker() -> SignalTracker:
    """Get the SignalTracker singleton instance"""
    return SignalTracker.get_instance()
