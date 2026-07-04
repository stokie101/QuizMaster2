/**
 * QuizSignals - Bulletproof Event System
 * FIXED: Removed throttling for quiz-critical signals
 */

class QuizSignals {
  constructor() {
    this._handlers = new Map();
    this._last = new Map();
    this._signal_history = [];
    this._max_history = 100;

    this._non_replayable_signals = new Set([
      "timer_tick", "answer_timer_tick"  // ✅ ONLY timer ticks are non-replayable
    ]);

    this._cleanup_scheduled = false;
    this._total_emits = 0;
    this._total_handlers = 0;

    // ✅ FIXED: Only throttle timer_tick (not answers_highlighted!)
    this._throttled_signals = new Set(['timer_tick']);
    this._last_emit_time = new Map();
    this._throttle_ms = 50;

    console.log('[QuizSignals] ✅ Initialized (throttling ONLY timer_tick)');
  }

  connect_signal(signal_name, handler, subscriber_id) {
    if (!signal_name || typeof signal_name !== 'string') {
      console.error('[QuizSignals] ❌ Invalid signal name:', signal_name);
      return false;
    }

    if (typeof handler !== 'function') {
      console.error('[QuizSignals] ❌ Handler must be a function');
      return false;
    }

    if (!subscriber_id) {
      console.warn('[QuizSignals] ⚠️ No subscriber_id provided, using anonymous');
      subscriber_id = 'anonymous_' + Date.now();
    }

    if (!this._handlers.has(signal_name)) {
      this._handlers.set(signal_name, []);
    }

    const handlers = this._handlers.get(signal_name);

    const existing = handlers.findIndex(h =>
      h.fn === handler && h.owner === subscriber_id
    );

    if (existing !== -1) {
      console.warn(`[QuizSignals] ⚠️ Handler already connected: '${signal_name}' for '${subscriber_id}'`);
      return false;
    }

    console.log(`[QuizSignals] 🔌 Connecting '${signal_name}' for '${subscriber_id}' (${handlers.length} → ${handlers.length + 1})`);

    handlers.push({
      fn: handler,
      owner: subscriber_id,
      connected_at: Date.now(),
      call_count: 0,
      last_called: null
    });

    this._total_handlers++;

    if (this._total_handlers > 100 && !this._cleanup_scheduled) {
      this._scheduleCleanup();
    }

    return true;
  }

  disconnect_signal(name, fn = null, owner = null) {
    if (!this._handlers.has(name)) {
      return 0;
    }

    const handlers = this._handlers.get(name);
    const initial_count = handlers.length;

    if (!fn && !owner) {
      this._handlers.delete(name);
      this._total_handlers -= initial_count;
      console.log(`[QuizSignals] 🔌 Disconnected all handlers for '${name}' (${initial_count} removed)`);
      return initial_count;
    }

    const remaining = handlers.filter(h => {
      const fn_match = fn && h.fn === fn;
      const owner_match = owner && h.owner === owner;
      return !(fn_match || owner_match);
    });

    const removed = initial_count - remaining.length;

    if (remaining.length === 0) {
      this._handlers.delete(name);
    } else {
      this._handlers.set(name, remaining);
    }

    this._total_handlers -= removed;

    if (removed > 0) {
      console.log(`[QuizSignals] 🔌 Disconnected ${removed} handler(s) from '${name}'`);
    }

    return removed;
  }

  disconnect_owner(owner) {
    if (!owner) return 0;

    let total_removed = 0;

    for (const [signal_name, handlers] of this._handlers.entries()) {
      const initial_count = handlers.length;
      const remaining = handlers.filter(h => h.owner !== owner);
      const removed = initial_count - remaining.length;

      if (remaining.length === 0) {
        this._handlers.delete(signal_name);
      } else {
        this._handlers.set(signal_name, remaining);
      }

      total_removed += removed;
      this._total_handlers -= removed;
    }

    if (total_removed > 0) {
      console.log(`[QuizSignals] 🧹 Removed ${total_removed} handler(s) for owner '${owner}'`);
    }

    return total_removed;
  }

  emit_signal(name, ...args) {
    if (!name || typeof name !== 'string') {
      console.error('[QuizSignals] ❌ Cannot emit signal without name');
      return false;
    }

    // ✅ ONLY throttle timer_tick (high-frequency non-critical signal)
    if (this._throttled_signals.has(name)) {
      const now = Date.now();
      const last = this._last_emit_time.get(name) || 0;

      if (now - last < this._throttle_ms) {
        return false; // Silently drop - normal for timer_tick
      }

      this._last_emit_time.set(name, now);
    }

    this._total_emits++;

    // Store last signal value for replayable signals
    if (!this._non_replayable_signals.has(name)) {
      this._last.set(name, args.length === 1 ? args[0] : args);
    }

    // Add to signal history
    this._signal_history.push({
      name,
      args: args.length === 1 ? args[0] : args,
      timestamp: Date.now()
    });

    if (this._signal_history.length > this._max_history) {
      this._signal_history.shift();
    }

    const handlers = this._handlers.get(name);

    if (!handlers || handlers.length === 0) {
      return true;
    }

    let success_count = 0;
    let error_count = 0;

    for (const handler_data of handlers) {
      try {
        handler_data.fn(...args);
        handler_data.call_count++;
        handler_data.last_called = Date.now();
        success_count++;
      } catch (e) {
        error_count++;
        console.error(`[QuizSignals] ❌ Error in handler for '${name}' (owner: ${handler_data.owner}):`, e);
      }
    }

    if (error_count > 0) {
      console.warn(`[QuizSignals] ⚠️ Signal '${name}' had ${error_count} handler error(s)`);
    }

    return error_count === 0;
  }

  clear_last_signal(name) {
    return this._last.delete(name);
  }

  get_last_signal(name) {
    return this._last.get(name);
  }

  get_signal_history(count = 20) {
    return this._signal_history.slice(-count);
  }

  clear_all_handlers() {
    const total = this._total_handlers;

    this._handlers.clear();
    this._last.clear();
    this._signal_history = [];
    this._total_handlers = 0;

    console.log(`[QuizSignals] 🧹 Cleared all handlers (${total} removed)`);
  }

  get_stats() {
    const signal_counts = {};

    for (const [name, handlers] of this._handlers.entries()) {
      signal_counts[name] = handlers.length;
    }

    return {
      total_signals: this._handlers.size,
      total_handlers: this._total_handlers,
      total_emits: this._total_emits,
      signal_counts,
      history_length: this._signal_history.length
    };
  }

  debug_info() {
    const stats = this.get_stats();

    console.group('[QuizSignals] Debug Info');
    console.log('Total signals:', stats.total_signals);
    console.log('Total handlers:', stats.total_handlers);
    console.log('Total emits:', stats.total_emits);
    console.log('History length:', stats.history_length);
    console.log('Signal breakdown:', stats.signal_counts);

    const stale_threshold = Date.now() - (5 * 60 * 1000);
    const stale = [];

    for (const [name, handlers] of this._handlers.entries()) {
      for (const h of handlers) {
        if (h.last_called && h.last_called < stale_threshold) {
          stale.push({
            signal: name,
            owner: h.owner,
            call_count: h.call_count,
            last_called_ago: Math.floor((Date.now() - h.last_called) / 1000) + 's ago'
          });
        }
      }
    }

    if (stale.length > 0) {
      console.warn('Stale handlers (not called in 5min):', stale);
    }

    console.groupEnd();
  }

  _scheduleCleanup() {
    if (this._cleanup_scheduled) return;

    this._cleanup_scheduled = true;

    setTimeout(() => {
      this._cleanup_scheduled = false;
      this._cleanupStaleHandlers();
    }, 60000);
  }

  _cleanupStaleHandlers() {
    const stale_threshold = Date.now() - (10 * 60 * 1000);
    let removed = 0;

    for (const [name, handlers] of this._handlers.entries()) {
      const active = handlers.filter(h => {
        const is_stale = h.last_called && h.last_called < stale_threshold;
        if (is_stale) removed++;
        return !is_stale;
      });

      if (active.length === 0) {
        this._handlers.delete(name);
      } else if (active.length < handlers.length) {
        this._handlers.set(name, active);
      }
    }

    this._total_handlers -= removed;

    if (removed > 0) {
      console.log(`[QuizSignals] 🧹 Automatic cleanup removed ${removed} stale handler(s)`);
    }
  }
}

if (typeof window !== 'undefined') {
  window.QuizSignals = QuizSignals;
  console.log('[QuizSignals] ✅ Fixed class defined (no throttling for critical signals)');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = QuizSignals;
}