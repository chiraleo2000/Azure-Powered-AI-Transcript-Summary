"""
UI Styles and CSS for Gradio Interface
Desktop-first web app design
"""

# OAuth2-style Session Persistence JavaScript with Tab Restoration
SESSION_PERSISTENCE_JS = """
<script>
(function() {
    const SESSION_KEY = 'ai_conference_ticket';
    const ACTIVITY_KEY = 'ai_conference_last_activity';
    const TAB_KEY = 'ai_conference_last_tab';
    const TAB_INDEX_KEY = 'ai_conference_last_tab_index';
    const INACTIVITY_TIMEOUT = 3600000; // 60 minutes

    // Guard against double-invocation of restore
    let _sessionRestoreInProgress = false;
    let _lastKnownSessionValue = '';

    window.getStoredSession = function() {
        try {
            const ticket = localStorage.getItem(SESSION_KEY);
            const lastActivity = localStorage.getItem(ACTIVITY_KEY);
            if (!ticket) return null;
            if (lastActivity) {
                const elapsed = Date.now() - parseInt(lastActivity);
                if (elapsed > INACTIVITY_TIMEOUT) {
                    localStorage.removeItem(SESSION_KEY);
                    localStorage.removeItem(ACTIVITY_KEY);
                    return null;
                }
            }
            return ticket;
        } catch (e) { return null; }
    };

    window.storeSession = function(ticketToken) {
        try {
            if (ticketToken && ticketToken.length > 20) {
                localStorage.setItem(SESSION_KEY, ticketToken);
                localStorage.setItem(ACTIVITY_KEY, Date.now().toString());
            }
        } catch (e) {}
    };

    window.clearSession = function() {
        try {
            localStorage.removeItem(SESSION_KEY);
            localStorage.removeItem(ACTIVITY_KEY);
            localStorage.removeItem(TAB_KEY);
            localStorage.removeItem(TAB_INDEX_KEY);
        } catch (e) {}
    };

    window.updateActivity = function() {
        try {
            if (localStorage.getItem(SESSION_KEY)) {
                localStorage.setItem(ACTIVITY_KEY, Date.now().toString());
            }
        } catch (e) {}
    };

    window.storeLastTab = function(tabName, tabIndex) {
        try {
            localStorage.setItem(TAB_KEY, tabName);
            if (tabIndex !== undefined) {
                localStorage.setItem(TAB_INDEX_KEY, tabIndex.toString());
            }
        } catch (e) {}
    };

    window.getLastTab = function() {
        try { return localStorage.getItem(TAB_KEY) || 'transcription'; }
        catch (e) { return 'transcription'; }
    };

    window.getLastTabIndex = function() {
        try { return parseInt(localStorage.getItem(TAB_INDEX_KEY) || '0'); }
        catch (e) { return 0; }
    };

    function classifyTab(text) {
        text = text.trim().toLowerCase();
        if (text.includes('\u0e2a\u0e23\u0e38\u0e1b') || text.includes('ai')) return 'ai_summary';
        if (text.includes('\u0e1b\u0e23\u0e30\u0e27\u0e31\u0e15\u0e34') || text.includes('history')) return 'history';
        if (text.includes('\u0e15\u0e31\u0e49\u0e07\u0e04\u0e48\u0e32') || text.includes('settings')) return 'settings';
        if (text.includes('\u0e0a\u0e48\u0e27\u0e22\u0e40\u0e2b\u0e25\u0e37\u0e2d') || text.includes('help')) return 'help';
        return 'transcription';
    }

    function setupTabTracking() {
        function trySetup(attempt) {
            var tabButtons = document.querySelectorAll('.tab-nav button');
            if (tabButtons.length === 0 && attempt < 10) {
                setTimeout(function() { trySetup(attempt + 1); }, 300);
                return;
            }
            tabButtons.forEach(function(btn, index) {
                btn.addEventListener('click', function() {
                    window.storeLastTab(classifyTab(btn.textContent), index);
                });
            });
        }
        trySetup(0);
    }

    function restoreTab(retryCount) {
        retryCount = retryCount || 0;
        var lastTab = window.getLastTab();
        var lastTabIndex = window.getLastTabIndex();
        var tabButtons = document.querySelectorAll('.tab-nav button');

        if (tabButtons.length === 0 && retryCount < 10) {
            setTimeout(function() { restoreTab(retryCount + 1); }, 300);
            return;
        }

        // Only restore non-default tabs (0 = transcription is default)
        if (lastTabIndex === 0 && lastTab === 'transcription') return;

        var foundTab = false;
        tabButtons.forEach(function(btn, index) {
            if (!foundTab && classifyTab(btn.textContent) === lastTab) {
                foundTab = true;
                btn.click();
            }
        });
        if (!foundTab && lastTabIndex > 0 && lastTabIndex < tabButtons.length) {
            tabButtons[lastTabIndex].click();
        }
    }

    // Activity tracking — throttled to max once per 5s
    var _lastActivityUpdate = 0;
    function throttledActivity() {
        var now = Date.now();
        if (now - _lastActivityUpdate > 5000) {
            _lastActivityUpdate = now;
            window.updateActivity();
        }
    }
    ['mousedown', 'keydown', 'scroll', 'touchstart'].forEach(function(evt) {
        document.addEventListener(evt, throttledActivity, { passive: true, capture: true });
    });

    // Polling-based watcher for session_id_hidden (most reliable with Gradio)
    function startSessionPoller() {
        setInterval(function() {
            try {
                var container = document.getElementById('session_id_hidden');
                if (!container) return;
                var input = container.querySelector('textarea') || container.querySelector('input');
                if (!input) return;
                var val = input.value || '';
                if (val === _lastKnownSessionValue) return;
                _lastKnownSessionValue = val;
                if (val.length > 20) {
                    window.storeSession(val);
                } else if (val === '') {
                    window.clearSession();
                }
            } catch (e) {}
        }, 400);
    }

    // Also listen for direct events as a fast path
    function attachInputListener() {
        var container = document.getElementById('session_id_hidden');
        if (!container) return;
        var input = container.querySelector('textarea') || container.querySelector('input');
        if (!input || input._sessionListenerAttached) return;
        input._sessionListenerAttached = true;
        input.addEventListener('input', function() {
            var val = input.value || '';
            if (val === _lastKnownSessionValue) return;
            _lastKnownSessionValue = val;
            if (val.length > 20) window.storeSession(val);
            else if (val === '') window.clearSession();
        });
    }

    // On page load: restore session and tab
    window.addEventListener('load', function() {
        attachInputListener();
        startSessionPoller();
        setupTabTracking();

        var storedTicket = window.getStoredSession();
        if (storedTicket) {
            // Inject stored ticket into the Gradio input so demo.load can read it
            var container = document.getElementById('session_id_hidden');
            if (container) {
                var input = container.querySelector('textarea') || container.querySelector('input');
                if (input) {
                    _lastKnownSessionValue = storedTicket;
                    input.value = storedTicket;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
            // Restore tab once the main_app div becomes visible
            function waitForMainApp(attempt) {
                attempt = attempt || 0;
                var mainApp = document.getElementById('main_app_section');
                if (mainApp && mainApp.offsetParent !== null) {
                    restoreTab(0);
                    return;
                }
                if (attempt < 20) {
                    setTimeout(function() { waitForMainApp(attempt + 1); }, 250);
                }
            }
            waitForMainApp(0);
        }
    });

    window.addEventListener('beforeunload', function() {
        window.updateActivity();
    });
})();
</script>
"""

# Desktop-First Web App CSS — supports dark/light theme
ENHANCED_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;600;700&display=swap');

* { font-family: 'Sarabun', system-ui, sans-serif !important; font-size-adjust: none; }
html { font-size: 18px !important; }

/* ========== THEME VARIABLES ========== */
:root {
    --bg-primary: #f1f5f9;
    --bg-secondary: #ffffff;
    --bg-tertiary: #f8fafc;
    --bg-hover: #f0f9ff;
    --text-primary: #1e293b;
    --text-secondary: #475569;
    --text-muted: #64748b;
    --text-label: #374151;
    --border-color: #e2e8f0;
    --border-hover: #cbd5e1;
    --accent: #0ea5e9;
    --accent-dark: #0284c7;
    --accent-darker: #0369a1;
    --accent-glow: rgba(14, 165, 233, 0.12);
    --accent-shadow: rgba(14, 165, 233, 0.25);
    --status-bg: linear-gradient(135deg, #f0f9ff, #e0f2fe);
    --stats-bg: linear-gradient(135deg, #ecfdf5, #d1fae5);
    --stats-border: #6ee7b7;
    --stats-text: #065f46;
    --refresh-bg: linear-gradient(135deg, #fef3c7, #fde68a);
    --refresh-border: #fbbf24;
    --refresh-text: #92400e;
    --tab-selected-bg: #ffffff;
    --file-drop-bg: #fafbfc;
    --table-header-bg: linear-gradient(135deg, #0ea5e9, #0284c7);
    --table-row-hover: #f0f9ff;
    --table-border: #f1f5f9;
    --scrollbar-track: #f1f5f9;
    --scrollbar-thumb: #cbd5e1;
    --scrollbar-hover: #94a3b8;
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
    --shadow-md: 0 4px 20px rgba(14, 165, 233, 0.25);
    --heading-border: #e2e8f0;
}

.dark {
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-tertiary: #1a2332;
    --bg-hover: #1e3a5f;
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --text-label: #cbd5e1;
    --border-color: #334155;
    --border-hover: #475569;
    --accent: #38bdf8;
    --accent-dark: #0ea5e9;
    --accent-darker: #0284c7;
    --accent-glow: rgba(56, 189, 248, 0.15);
    --accent-shadow: rgba(56, 189, 248, 0.3);
    --status-bg: linear-gradient(135deg, #0c2d48, #0f3460);
    --stats-bg: linear-gradient(135deg, #064e3b, #065f46);
    --stats-border: #34d399;
    --stats-text: #a7f3d0;
    --refresh-bg: linear-gradient(135deg, #78350f, #92400e);
    --refresh-border: #d97706;
    --refresh-text: #fde68a;
    --tab-selected-bg: #1e293b;
    --file-drop-bg: #1a2332;
    --table-header-bg: linear-gradient(135deg, #0369a1, #0284c7);
    --table-row-hover: #1e3a5f;
    --table-border: #334155;
    --scrollbar-track: #1e293b;
    --scrollbar-thumb: #475569;
    --scrollbar-hover: #64748b;
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
    --shadow-md: 0 4px 20px rgba(56, 189, 248, 0.15);
    --heading-border: #334155;
}

/* Hide decorative SVGs but keep functional button icons */
.gradio-container .contain > svg { display: none !important; }

/* File upload clear/remove/close buttons — always visible */
.gr-file button,
.file-preview button,
.upload-button button,
button.remove-all,
button[aria-label="Remove"],
button[aria-label="Clear"],
button[aria-label="close"],
button[aria-label="Remove All"],
.file-preview .remove-file,
.gradio-container .icon-button,
.gradio-container button svg {
    opacity: 1 !important;
    visibility: visible !important;
}

.gradio-container button svg {
    display: inline-block !important;
    stroke: var(--text-secondary) !important;
    width: 16px !important;
    height: 16px !important;
}

/* Clear/close/remove icon buttons — make visible with colored icons */
.gradio-container button[aria-label="Remove"] svg,
.gradio-container button[aria-label="Clear"] svg,
.gradio-container button[aria-label="close"] svg,
.gradio-container button[aria-label="Remove All"] svg {
    stroke: #ef4444 !important;
    width: 18px !important;
    height: 18px !important;
}

.dark .gradio-container button svg {
    stroke: #94a3b8 !important;
}

.dark .gradio-container button[aria-label="Remove"] svg,
.dark .gradio-container button[aria-label="Clear"] svg,
.dark .gradio-container button[aria-label="close"] svg,
.dark .gradio-container button[aria-label="Remove All"] svg {
    stroke: #f87171 !important;
}

.dark .gradio-container button[aria-label="Remove"]:hover svg,
.dark .gradio-container button[aria-label="Clear"]:hover svg,
.dark .gradio-container button[aria-label="close"]:hover svg,
.dark .gradio-container button[aria-label="Remove All"]:hover svg {
    stroke: #fca5a5 !important;
}

/* Upload icon visibility */
.dark .gr-file svg,
.dark .upload-button svg {
    stroke: #94a3b8 !important;
    display: inline-block !important;
}

.gradio-container {
    background: var(--bg-primary) !important;
    color: var(--text-primary) !important;
    max-width: 100% !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    font-size: 18px !important;
}

.gradio-container > .main {
    max-width: 1800px !important;
    width: 95% !important;
    margin: 0 auto !important;
    padding: 20px !important;
}

.gradio-container .row {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    gap: 24px !important;
    width: 100% !important;
}

.gradio-container .row > .column {
    flex: 1 1 0 !important;
    min-width: 400px !important;
}

/* Logo - Vertical stacked layout */
.logo-container {
    text-align: center;
    padding: 24px 40px;
    background: linear-gradient(135deg, #0369a1, #0284c7, #0ea5e9);
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: var(--shadow-md);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
}

.logo-container img { max-width: 100px; height: auto; margin-bottom: 8px; }
.logo-container h1 { color: white; margin: 0; font-size: 2.2em; font-weight: 700; }
.logo-container p { color: rgba(255,255,255,0.9); margin: 4px 0 0 0; font-size: 1.2em; }

.gr-box, .gr-panel, .gr-form {
    background: var(--bg-secondary) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: 12px !important;
    box-shadow: var(--shadow-sm) !important;
    padding: 24px !important;
    margin: 8px 0 !important;
}

/* Primary button */
.gr-button-primary, button.primary {
    background: linear-gradient(135deg, var(--accent), var(--accent-dark)) !important;
    border: none !important;
    border-radius: 6px !important;
    color: white !important;
    font-weight: 600 !important;
    font-size: 16px !important;
    padding: 10px 22px !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 6px var(--accent-shadow) !important;
    min-width: 120px !important;
}

.gr-button-primary:hover, button.primary:hover {
    background: linear-gradient(135deg, var(--accent-dark), var(--accent-darker)) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 10px var(--accent-shadow) !important;
}

/* Secondary button */
.gr-button-secondary, button.secondary {
    background: var(--bg-secondary) !important;
    border: 1px solid var(--border-hover) !important;
    border-radius: 6px !important;
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    font-size: 15px !important;
    padding: 8px 16px !important;
    min-width: 90px !important;
}

.gr-button-secondary:hover, button.secondary:hover {
    background: var(--bg-tertiary) !important;
    border-color: var(--accent) !important;
    color: var(--accent-dark) !important;
}

/* Large button */
button[size="lg"], .gr-button-lg {
    padding: 14px 32px !important;
    font-size: 17px !important;
    min-width: 150px !important;
}

/* Logout button */
.logout-btn {
    font-size: 12px !important;
    padding: 6px 12px !important;
    min-width: auto !important;
}

/* Text inputs */
.gr-textbox, .gr-dropdown, .gr-file, textarea, input[type="text"], input[type="password"] {
    border: 2px solid var(--border-color) !important;
    border-radius: 8px !important;
    background: var(--bg-secondary) !important;
    color: var(--text-primary) !important;
    padding: 12px 16px !important;
    font-size: 17px !important;
    transition: all 0.2s ease !important;
    width: 100% !important;
}

.gr-textbox:focus, .gr-dropdown:focus, textarea:focus, input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 4px var(--accent-glow) !important;
    outline: none !important;
}

.gr-dropdown select {
    cursor: pointer !important;
    padding: 12px 16px !important;
    font-size: 17px !important;
    background: var(--bg-secondary) !important;
    color: var(--text-primary) !important;
}

/* Status display */
.status-display {
    background: var(--status-bg) !important;
    border-left: 4px solid var(--accent) !important;
    border-radius: 0 8px 8px 0 !important;
    padding: 16px 20px !important;
    font-family: 'Sarabun', 'Consolas', 'Monaco', monospace !important;
    font-size: 17px !important;
    line-height: 1.7 !important;
    min-height: 200px !important;
    max-height: 600px !important;
    overflow-y: auto !important;
    color: var(--text-primary) !important;
}

.status-display textarea {
    min-height: 200px !important;
    max-height: 600px !important;
    overflow-y: auto !important;
    font-size: 17px !important;
    color: var(--text-primary) !important;
    background: transparent !important;
}

.user-stats {
    background: var(--stats-bg) !important;
    border: 2px solid var(--stats-border) !important;
    border-radius: 8px !important;
    padding: 12px 20px !important;
    font-size: 16px !important;
    color: var(--stats-text) !important;
    text-align: center !important;
    font-weight: 600 !important;
}

/* Tabs */
.tabs { 
    border-radius: 12px !important;
    overflow: hidden !important;
    background: var(--bg-secondary) !important;
}

.tab-nav {
    background: var(--bg-tertiary) !important;
    border-bottom: 2px solid var(--border-color) !important;
    padding: 10px 10px 0 10px !important;
    display: flex !important;
    gap: 8px !important;
}

.tab-nav button {
    font-weight: 700 !important;
    font-size: 20px !important;
    color: var(--text-muted) !important;
    padding: 16px 32px !important;
    border: none !important;
    background: transparent !important;
    border-radius: 10px 10px 0 0 !important;
    transition: all 0.2s ease !important;
}

.tab-nav button:hover {
    background: var(--border-color) !important;
    color: var(--text-primary) !important;
}

.tab-nav button.selected {
    color: var(--accent-dark) !important;
    background: var(--tab-selected-bg) !important;
    border-bottom: 4px solid var(--accent) !important;
    margin-bottom: -2px !important;
}

.tabitem { padding: 24px !important; }

.gr-file {
    border: 2px dashed var(--border-hover) !important;
    border-radius: 12px !important;
    padding: 32px !important;
    text-align: center !important;
    background: var(--file-drop-bg) !important;
    transition: all 0.2s ease !important;
    min-height: 100px !important;
    color: var(--text-secondary) !important;
}

.gr-file:hover {
    border-color: var(--accent) !important;
    background: var(--bg-hover) !important;
}

.gr-dataframe, .history-table {
    background: var(--bg-secondary) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    width: 100% !important;
}

.gr-dataframe table { width: 100% !important; }

.gr-dataframe thead th {
    background: var(--table-header-bg) !important;
    color: white !important;
    font-weight: 600 !important;
    font-size: 16px !important;
    padding: 14px 16px !important;
    text-align: left !important;
    white-space: nowrap !important;
}

.gr-dataframe tbody td {
    padding: 12px 16px !important;
    border-bottom: 1px solid var(--table-border) !important;
    font-size: 16px !important;
    color: var(--text-primary) !important;
}

.gr-dataframe tbody tr:hover { background: var(--table-row-hover) !important; }

.gr-accordion {
    border: 1px solid var(--border-color) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
    margin: 8px 0 !important;
}

.gr-accordion .label-wrap {
    background: var(--bg-tertiary) !important;
    padding: 12px 18px !important;
    font-weight: 600 !important;
    font-size: 17px !important;
    cursor: pointer !important;
    color: var(--text-primary) !important;
}

.gr-accordion .label-wrap:hover { background: var(--bg-primary) !important; }

.gr-checkbox { cursor: pointer !important; padding: 6px 0 !important; }

.gr-checkbox input[type="checkbox"] {
    width: 18px !important;
    height: 18px !important;
    accent-color: var(--accent) !important;
    cursor: pointer !important;
}

.gr-checkbox label {
    font-size: 16px !important;
    margin-left: 8px !important;
    color: var(--text-primary) !important;
}

/* Slider */
.gr-slider input[type="range"] {
    accent-color: var(--accent) !important;
    height: 4px !important;
}

.gr-slider { padding: 4px 0 !important; }

/* Markdown text */
.gr-markdown h2 {
    color: var(--text-primary) !important;
    font-weight: 700 !important;
    font-size: 1.8em !important;
    margin: 16px 0 12px 0 !important;
    padding-bottom: 8px !important;
    border-bottom: 2px solid var(--heading-border) !important;
}

.gr-markdown h3 {
    color: var(--text-secondary) !important;
    font-weight: 600 !important;
    font-size: 1.4em !important;
    margin: 12px 0 8px 0 !important;
}

.gr-markdown p {
    color: var(--text-secondary) !important;
    line-height: 1.7 !important;
    font-size: 18px !important;
}

/* Markdown list items and all block text */
.gr-markdown li {
    font-size: 17px !important;
    line-height: 1.7 !important;
    color: var(--text-secondary) !important;
}

.gr-markdown strong {
    color: var(--text-primary) !important;
}

.gr-markdown code {
    font-size: 16px !important;
}

.auto-refresh-indicator {
    /* Container: no styling when empty */
}

.auto-refresh-indicator:empty,
.auto-refresh-indicator > .prose:empty {
    display: none !important;
    padding: 0 !important;
    margin: 0 !important;
    border: none !important;
    min-height: 0 !important;
    height: 0 !important;
}

.refresh-badge {
    background: var(--refresh-bg) !important;
    border: 2px solid var(--refresh-border) !important;
    border-radius: 8px !important;
    padding: 10px 16px !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    color: var(--refresh-text) !important;
    text-align: center !important;
    animation: pulse 2s infinite !important;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
}

/* Download file info + button */
.download-info:empty,
.download-info > .prose:empty {
    display: none !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    height: 0 !important;
}

.download-file-info {
    display: flex !important;
    align-items: center !important;
    justify-content: space-between !important;
    background: var(--bg-tertiary) !important;
    border: 2px solid var(--accent-primary) !important;
    border-radius: 10px !important;
    padding: 14px 20px !important;
    margin-top: 10px !important;
}

.download-filename {
    font-size: 16px !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
}

.download-filesize {
    font-size: 15px !important;
    font-weight: 500 !important;
    color: var(--text-muted) !important;
    background: var(--bg-secondary) !important;
    padding: 4px 12px !important;
    border-radius: 6px !important;
}

.download-btn button {
    width: 100% !important;
    margin-top: 6px !important;
    font-size: 17px !important;
    font-weight: 700 !important;
    padding: 12px 24px !important;
    border-radius: 10px !important;
}

.auth-section { max-width: 500px !important; margin: 0 auto !important; }

label {
    font-weight: 600 !important;
    font-size: 16px !important;
    color: var(--text-label) !important;
    margin-bottom: 6px !important;
}

@media (min-width: 1200px) {
    .gradio-container > .main { max-width: 1800px !important; width: 95% !important; }
    .gradio-container .row > .column { min-width: 450px !important; }
}

@media (max-width: 1199px) {
    .gradio-container > .main { width: 98% !important; padding: 16px !important; }
    .gradio-container .row { flex-wrap: wrap !important; }
    .gradio-container .row > .column { min-width: 300px !important; flex: 1 1 100% !important; }
}

@media (max-width: 768px) {
    .logo-container { flex-direction: column !important; padding: 16px !important; }
    .logo-container h1 { font-size: 1.5em !important; }
    .tab-nav button { padding: 10px 16px !important; font-size: 14px !important; }
    .gr-button-primary, button.primary { width: 100% !important; padding: 14px 20px !important; }
}

.gr-form { display: flex !important; flex-direction: column !important; gap: 16px !important; }

footer { display: none !important; }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--scrollbar-track); border-radius: 4px; }
::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-hover); }
"""
