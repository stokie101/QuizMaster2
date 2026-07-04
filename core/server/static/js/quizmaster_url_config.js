(function() {
    'use strict';

    const PUBLIC_URL_BLOCKLIST = new Set(['/obs/control']);

    const HOSTED_WIDGETS_BASE_URL = 'https://widgets.quizmaster.online';
    const LOCAL_BASE_URL = 'http://localhost:5555';
    const WIDGETS_BASE_URL = LOCAL_BASE_URL;
    const DEFAULTS = {
        WIDGETS_BASE_URL,
        PUBLIC_BASE_URL: WIDGETS_BASE_URL,
        LOCAL_BASE_URL,
        HOSTED_WIDGETS_BASE_URL,
        API_BASE_URL: LOCAL_BASE_URL,
        URL_MODE: 'local',
        PROFILE_ID: null,
        PUBLIC_WIDGET_ID: null,
        ACTIVE_RUNTIME_ID: null,
        CAN_GENERATE_PUBLIC_URLS: false,
        WIDGET_DEBUG: false
    };
    let runtimeConfig = { ...(window.QUIZMASTER_URL_CONFIG || {}) };

    function trimTrailingSlash(value) {
        return String(value || '').replace(/\/+$/, '');
    }

    function config() {
        return { ...DEFAULTS, ...runtimeConfig, ...(window.QUIZMASTER_URL_CONFIG || {}) };
    }

    function debug(event, details = {}) {
        if (!config().WIDGET_DEBUG) return;
        console.info('[QuizMasterWidgetDebug]', event, details);
    }

    function publicWidgetIdFromPath() {
        const match = window.location.pathname.match(/^\/u\/([^/]+)(?:\/|$)/);
        return match ? decodeURIComponent(match[1]) : null;
    }

    function activeRuntimeId() {
        const current = config();
        const id = publicWidgetIdFromPath() || current.PUBLIC_WIDGET_ID || current.ACTIVE_RUNTIME_ID;
        return id ? String(id) : null;
    }

    function publicWidgetId() {
        const current = config();
        const id = activeRuntimeId() || current.PROFILE_ID || current.QUIZMASTER_USER_ID;
        if (id) return String(id);
        if (current.URL_MODE !== 'public') return 'local-dev';
        return null;
    }

    const readyPromise = fetch('/api/quizmaster/url-config', { cache: 'no-store' })
        .then(response => {
            debug('url_config_http', { status: response.status, host: window.location.host });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(payload => {
            runtimeConfig = { ...runtimeConfig, ...(payload.config || {}) };
            window.QUIZMASTER_URL_CONFIG = runtimeConfig;
            return config();
        })
        .catch(error => {
            debug('url_config_error', { message: error.message, host: window.location.host });
            return config();
        });

    function activeBaseUrl() {
        const current = config();
        return trimTrailingSlash(current.URL_MODE === 'public' ? current.WIDGETS_BASE_URL : current.LOCAL_BASE_URL);
    }

    function userPrefix() {
        const profileId = publicWidgetId();
        if (!profileId) {
            throw new Error('Your QuizMaster account is missing a public_widget_id; public widget URLs cannot be generated.');
        }
        return `/u/${encodeURIComponent(profileId)}`;
    }

    function normalizePath(path) {
        let cleanPath = String(path || '').startsWith('/') ? path : `/${path || ''}`;
        if (cleanPath.startsWith('/actions-events/overlay')) {
            cleanPath = cleanPath.replace('/actions-events/overlay', '/actions_events/overlay');
        }
        return cleanPath;
    }

    function appendQuery(url, query) {
        Object.entries(query || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null) url.searchParams.set(key, value);
        });
        return url.toString();
    }

    function publicWidgetPath(path) {
        const cleanPath = normalizePath(path);
        if (PUBLIC_URL_BLOCKLIST.has(cleanPath)) {
            throw new Error(`Public URL generation is disabled until a scoped route exists: ${cleanPath}`);
        }
        return cleanPath.startsWith('/u/') ? cleanPath : `${userPrefix()}${cleanPath}`;
    }

    function get_public_url(path, query) {
        try {
            if (config().URL_MODE !== 'public') return get_internal_url(path, query);
            const url = appendQuery(new URL(`${trimTrailingSlash(config().WIDGETS_BASE_URL)}${publicWidgetPath(path)}`), query);
            debug('generated_url', {
                route: normalizePath(path),
                baseHost: new URL(config().WIDGETS_BASE_URL).host,
                publicWidgetIdPresent: Boolean(publicWidgetId()),
                transport: 'https'
            });
            return url;
        } catch (error) {
            debug('generated_url_error', { route: normalizePath(path), publicWidgetIdPresent: false, message: error.message });
            return '';
        }
    }

    function get_internal_url(path, query) {
        const cleanPath = String(path || '').startsWith('/') ? path : `/${path || ''}`;
        return appendQuery(new URL(`${trimTrailingSlash(config().LOCAL_BASE_URL)}${cleanPath}`), query);
    }

    function get_socket_url(path) {
        const base = config().URL_MODE === 'public' ? trimTrailingSlash(config().WIDGETS_BASE_URL) : trimTrailingSlash(config().LOCAL_BASE_URL);
        const parsed = new URL(base);
        parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
        parsed.pathname = String(path || '/socket.io').startsWith('/') ? String(path || '/socket.io') : `/${path}`;
        parsed.search = '';
        parsed.hash = '';
        debug('socket_url', { host: parsed.host, protocol: parsed.protocol });
        return parsed.toString();
    }

    function socketOrigin() {
        return window.location.origin || activeBaseUrl();
    }

    function apiPrefix() {
        if (config().URL_MODE !== 'public') return '';
        const id = activeRuntimeId();
        return id ? `/u/${encodeURIComponent(id)}` : '';
    }

    function apiBaseUrl() {
        const current = config();
        const configuredApiBase = current.API_BASE_URL || current.ACTIVE_BASE_URL;
        const base = current.URL_MODE === 'public'
            ? (configuredApiBase || current.WIDGETS_BASE_URL)
            : current.LOCAL_BASE_URL;
        return trimTrailingSlash(base || window.location.origin);
    }

    function sessionId() {
        return new URL(window.location.href).searchParams.get('session');
    }

    function sessionQuery(extra = {}) {
        const id = sessionId();
        return id ? { session: id, ...extra } : { ...extra };
    }

    function sessionApiUrl(path) {
        const url = new URL(`${apiBaseUrl()}${apiPrefix()}${normalizePath(path)}`);
        Object.entries(sessionQuery()).forEach(([key, value]) => url.searchParams.set(key, value));
        return url.toString();
    }

    function controlToken() {
        const id = sessionId();
        return id ? sessionStorage.getItem(`quizmaster_control_token:${id}`) : null;
    }

    async function exchangeControlToken() {
        const url = new URL(window.location.href);
        const code = url.searchParams.get('control_exchange');
        if (!code) return controlToken();
        const response = await fetch(`${apiBaseUrl()}${apiPrefix()}/api/widget-sessions/control/exchange`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code })
        });
        if (!response.ok) throw new Error(`Control authorization failed: HTTP ${response.status}`);
        const payload = await response.json();
        sessionStorage.setItem(`quizmaster_control_token:${payload.session_id}`, payload.control_token);
        url.searchParams.delete('control_exchange');
        window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
        return payload.control_token;
    }

    function authorizedFetch(path, options = {}) {
        const headers = new Headers(options.headers || {});
        const token = controlToken();
        if (token) headers.set('X-QuizMaster-Control-Token', token);
        return fetch(sessionApiUrl(path), { ...options, headers });
    }

    window.QuizMasterURLs = {
        WIDGETS_BASE_URL,
        HOSTED_WIDGETS_BASE_URL,
        config,
        diagnostic: debug,
        ready: () => readyPromise,
        activeBaseUrl,
        normalizePath,
        publicWidgetPath,
        publicWidgetId,
        activeRuntimeId,
        userPrefix,
        apiPrefix,
        apiBaseUrl,
        sessionId,
        sessionQuery,
        sessionApiUrl,
        controlToken,
        exchangeControlToken,
        authorizedFetch,
        get_public_url,
        get_internal_url,
        get_socket_url,
        buildUrl: get_public_url,
        socketOrigin,
        overlayUrl(screen) { return get_public_url('/actions_events/overlay', { screen }); },
        controlDockUrl() { return get_public_url('/actions-events/control-dock'); },
        socketOptions(extra = {}) {
            return {
                transports: ['websocket', 'polling'],
                path: '/socket.io',
                query: {
                    public_widget_id: publicWidgetId() || '',
                    ...((extra && extra.query) || {})
                },
                ...extra
            };
        }
    };
})();
