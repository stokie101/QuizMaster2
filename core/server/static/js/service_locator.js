(function() {
  'use strict';

  class ServiceLocator {
    constructor() {
      this._services = new Map();
      console.log('[ServiceLocator] Instance created (deferred mode)');
    }

    static _ensure_instance() {
      if (!ServiceLocator._instance) {
        ServiceLocator._instance = new ServiceLocator();
      }
      return ServiceLocator._instance;
    }

    static get_instance() {
      return ServiceLocator._ensure_instance();
    }

    register_service(name, service) {
      if (!name || !service) {
        console.error('[ServiceLocator] Invalid registration:', name, service);
        return false;
      }
      const existing = this._services.get(name);
      if (existing === service) return true;
      this._services.set(name, service);
      console.log(`[ServiceLocator] ✅ Registered: ${name}`);
      return true;
    }

    get_service(name) {
      return this._services.get(name) || null;
    }

    has_service(name) {
      return this._services.has(name);
    }

    unregister_service(name) {
      const existed = this._services.delete(name);
      if (existed) console.log(`[ServiceLocator] Unregistered: ${name}`);
      return existed;
    }

    list_services() {
      return Array.from(this._services.keys());
    }

    clear_all() {
      this._services.clear();
      console.log('[ServiceLocator] All services cleared');
    }
  }

  ServiceLocator._instance = null;
  window.ServiceLocator = ServiceLocator;
  
  // ✅ ONLY create instance, don't auto-initialize anything
  if (typeof window !== 'undefined') {
    window.ServiceLocator.get_instance();
    console.log('[ServiceLocator] ✅ Ready (awaiting bootstrap)');
  }
})();