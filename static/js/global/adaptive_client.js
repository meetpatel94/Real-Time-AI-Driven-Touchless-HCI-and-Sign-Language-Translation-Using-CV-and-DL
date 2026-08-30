/**
 * Personalized profile and adaptive runtime client.
 *
 * This client is additive: legacy camera, gesture, sentence and translation
 * clients continue using their existing endpoints.  Profile preferences are
 * sent through the new API and applied to the existing cursor/scroll controls.
 */
class AdaptiveProfileClient {
    constructor() {
        this.pollInterval = 650;
        this.pollTimer = null;
        this.profileId = this.getOrCreateProfileId();
        this.profile = null;

        this.panel = document.getElementById('adaptive-profile-panel');
        this.message = document.getElementById('adaptive-profile-message');
        this.form = document.getElementById('adaptive-profile-form');
        this.init();
    }

    getOrCreateProfileId() {
        const storageKey = 'gestureforge_profile_id';
        try {
            let value = localStorage.getItem(storageKey);
            if (!value) {
                value = (window.crypto && window.crypto.randomUUID)
                    ? window.crypto.randomUUID()
                    : `local-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
                localStorage.setItem(storageKey, value);
            }
            return value;
        } catch (err) {
            return 'local-user';
        }
    }

    headers(withJson = false) {
        const headers = { 'X-GestureForge-Profile': this.profileId };
        if (withJson) headers['Content-Type'] = 'application/json';
        return headers;
    }

    init() {
        this.bindEvents();
        this.loadProfile();
        this.pollRuntime();
        window.addEventListener('beforeunload', () => {
            if (this.pollTimer) clearTimeout(this.pollTimer);
        });
    }

    bindEvents() {
        const open = document.getElementById('adaptive-profile-trigger');
        const close = document.getElementById('adaptive-profile-close');
        const save = document.getElementById('adaptive-profile-save');
        const reset = document.getElementById('adaptive-profile-reset');
        const cursor = document.getElementById('profile-cursor-sensitivity');
        const cursorValue = document.getElementById('profile-cursor-value');
        const mode = document.getElementById('profile-interaction-mode');

        if (open) open.addEventListener('click', () => this.togglePanel(true));
        if (close) close.addEventListener('click', () => this.togglePanel(false));
        if (save) save.addEventListener('click', () => this.saveProfile());
        if (reset) reset.addEventListener('click', () => this.resetProfile());
        if (cursor && cursorValue) {
            cursor.addEventListener('input', () => {
                cursorValue.innerText = `${cursor.value}%`;
            });
        }
        if (mode) {
            mode.addEventListener('change', () => {
                const enabled = mode.value === 'adaptive';
                const adaptiveBadge = document.getElementById('status-adaptive');
                if (adaptiveBadge) adaptiveBadge.innerText = enabled ? 'ADAPTIVE AI' : 'LEGACY MODE';
            });
        }
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && this.panel && !this.panel.hidden) {
                this.togglePanel(false);
            }
        });
        if (this.form) {
            this.form.addEventListener('submit', (event) => event.preventDefault());
        }
    }

    togglePanel(open) {
        if (!this.panel) return;
        this.panel.hidden = !open;
        if (open) {
            const name = document.getElementById('profile-display-name');
            if (name) name.focus();
        }
    }

    async loadProfile() {
        try {
            const response = await fetch('/api/profile', { headers: this.headers() });
            if (!response.ok) throw new Error('Profile unavailable');
            const data = await response.json();
            this.applyProfile(data.profile || {});
            this.setMessage('');
        } catch (err) {
            this.setMessage('Personalized profile is unavailable; legacy controls remain active.', true);
        }
    }

    applyProfile(profile) {
        this.profile = profile;
        const fields = {
            'profile-display-name': profile.display_name || 'Local user',
            'profile-language': profile.preferred_language || 'English',
            'profile-module': profile.preferred_module || 'studio',
            'profile-scroll-sensitivity': profile.scroll_sensitivity || 'medium',
            'profile-interaction-mode': profile.interaction_mode || 'adaptive'
        };
        Object.keys(fields).forEach(id => {
            const element = document.getElementById(id);
            if (element) element.value = fields[id];
        });

        const cursor = document.getElementById('profile-cursor-sensitivity');
        const cursorValue = document.getElementById('profile-cursor-value');
        if (cursor) cursor.value = Math.round((parseFloat(profile.cursor_sensitivity) || 0.5) * 100);
        if (cursorValue) cursorValue.innerText = `${cursor ? cursor.value : 50}%`;

        const learning = document.getElementById('profile-learning-enabled');
        if (learning) learning.checked = profile.learning_enabled !== false;

        const display = document.getElementById('adaptive-profile-trigger');
        if (display) display.innerText = profile.display_name || 'PROFILE';

        const adaptiveBadge = document.getElementById('status-adaptive');
        if (adaptiveBadge) {
            adaptiveBadge.innerText = profile.adaptive_enabled === false ? 'LEGACY MODE' : 'ADAPTIVE AI';
        }

        // Keep the existing controls and the profile in sync.  These calls use
        // the existing endpoints; no second cursor or scroll implementation is
        // introduced by the adaptive client.
        const sensitivity = parseFloat(profile.cursor_sensitivity);
        if (Number.isFinite(sensitivity)) {
            const globalSlider = document.getElementById('slider-cursor-sensitivity');
            const globalDisplay = document.getElementById('sensitivity-display');
            const percent = Math.round(sensitivity * 100);
            if (globalSlider) globalSlider.value = percent;
            if (globalDisplay) globalDisplay.innerText = `${percent}%`;
            try { localStorage.setItem('cursor_sensitivity', String(percent)); } catch (err) {}
            if (window.stateClient) window.stateClient.sendSensitivity(sensitivity);
        }
        if (profile.scroll_sensitivity) {
            const globalScroll = document.getElementById('select-scroll-sensitivity');
            if (globalScroll) globalScroll.value = profile.scroll_sensitivity;
            try { localStorage.setItem('scroll_sensitivity', profile.scroll_sensitivity); } catch (err) {}
            if (window.stateClient) window.stateClient.sendScrollSensitivity(profile.scroll_sensitivity);
        }

        // The Studio translation dropdown is an existing UI. Apply the saved
        // language only when that option exists, so other pages are unaffected.
        const targetLanguage = document.getElementById('select-target-lang');
        if (targetLanguage && profile.preferred_language) {
            const optionExists = Array.from(targetLanguage.options)
                .some(option => option.value.toLowerCase() === profile.preferred_language.toLowerCase());
            if (optionExists) {
                targetLanguage.value = profile.preferred_language;
                const meta = document.getElementById('meta-target-lang');
                if (meta) meta.innerText = profile.preferred_language;
            }
        }
    }

    profilePayload() {
        const cursor = document.getElementById('profile-cursor-sensitivity');
        return {
            display_name: (document.getElementById('profile-display-name') || {}).value || 'Local user',
            preferred_language: (document.getElementById('profile-language') || {}).value || 'English',
            preferred_module: (document.getElementById('profile-module') || {}).value || 'studio',
            cursor_sensitivity: (parseFloat((cursor || {}).value) || 50) / 100,
            scroll_sensitivity: (document.getElementById('profile-scroll-sensitivity') || {}).value || 'medium',
            interaction_mode: (document.getElementById('profile-interaction-mode') || {}).value || 'adaptive',
            learning_enabled: Boolean((document.getElementById('profile-learning-enabled') || {}).checked)
        };
    }

    async saveProfile() {
        try {
            const response = await fetch('/api/profile', {
                method: 'PATCH',
                headers: this.headers(true),
                body: JSON.stringify(this.profilePayload())
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Profile could not be saved');
            this.applyProfile(data.profile || {});
            this.setMessage('Profile saved. New preferences are active.');
        } catch (err) {
            this.setMessage(err.message || 'Profile could not be saved.', true);
        }
    }

    async resetProfile() {
        if (!window.confirm('Reset this personalized profile and its preferences? Interaction history is retained.')) return;
        try {
            const response = await fetch('/api/profile/reset', {
                method: 'POST',
                headers: this.headers()
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Profile could not be reset');
            this.applyProfile(data.profile || {});
            this.setMessage('Profile reset to safe defaults.');
        } catch (err) {
            this.setMessage(err.message || 'Profile could not be reset.', true);
        }
    }

    setMessage(text, isError = false) {
        if (!this.message) return;
        this.message.innerText = text;
        this.message.style.color = isError ? 'var(--accent-red)' : 'var(--text-muted)';
    }

    async pollRuntime() {
        try {
            const response = await fetch('/api/adaptive/status', { headers: this.headers() });
            if (response.ok) {
                const data = await response.json();
                if (data.profile) this.applyProfileHeader(data.profile);
                if (data.runtime) this.updateRuntime(data.runtime);
            }
        } catch (err) {
            // The adaptive panel is optional; do not disrupt legacy polling.
        } finally {
            this.pollTimer = setTimeout(() => this.pollRuntime(), this.pollInterval);
        }
    }

    applyProfileHeader(profile) {
        this.profile = profile;
        const display = document.getElementById('adaptive-profile-trigger');
        if (display) display.innerText = profile.display_name || 'PROFILE';
        const adaptiveBadge = document.getElementById('status-adaptive');
        if (adaptiveBadge) {
            adaptiveBadge.innerText = profile.adaptive_enabled === false ? 'LEGACY MODE' : 'ADAPTIVE AI';
        }
    }

    updateRuntime(runtime) {
        const intent = runtime.intent || {};
        const unknown = runtime.unknown || {};
        const motion = runtime.motion || {};
        const context = runtime.context || {};
        const intentLabel = this.formatLabel(intent.name || 'IDLE');
        const unknownLabel = unknown.is_unknown ? this.formatLabel(unknown.status || 'UNKNOWN') : this.formatLabel(unknown.status || 'NO_HAND');
        const motionLabel = this.formatLabel(motion.direction || 'stationary');

        const statusIntent = document.getElementById('status-intent');
        if (statusIntent) statusIntent.innerText = `INTENT: ${intentLabel}`;
        const statusUnknown = document.getElementById('status-unknown');
        if (statusUnknown) {
            statusUnknown.hidden = !unknown.is_unknown;
            statusUnknown.innerText = unknown.is_unknown ? `UNKNOWN: ${unknownLabel}` : 'UNKNOWN';
        }

        this.setText('adaptive-live-intent', `${intentLabel} (${Math.round((intent.confidence || 0) * 100)}%)`);
        this.setText('adaptive-live-gesture', `${this.formatLabel(runtime.gesture || 'NONE')} · ${runtime.finger_count || 0} fingers`);
        this.setText('adaptive-live-motion', `${motionLabel} · ${Number(motion.speed || 0).toFixed(2)} speed`);
        this.setText('adaptive-live-unknown', unknown.is_unknown ? `${unknownLabel} · ${Math.round((unknown.score || 0) * 100)}%` : unknownLabel);

        this.setText('adaptive-intent-name', intentLabel);
        this.setText('adaptive-intent-confidence', `${Math.round((intent.confidence || 0) * 100)}% confidence`);
        this.setText('adaptive-gesture-name', this.formatLabel(runtime.gesture || 'NONE'));
        this.setText('adaptive-motion-name', `${motionLabel} · ${Number(motion.displacement || 0).toFixed(3)} travel`);
        this.setText('adaptive-unknown-name', unknown.is_unknown ? unknownLabel : 'NO UNKNOWN GESTURE');
        this.setText('adaptive-unknown-detail', unknown.reason || 'The detector is waiting for a hand.');
        this.setText('adaptive-context-name', this.formatLabel(context.module || 'overview'));
        this.setText('adaptive-context-detail', context.sentence_active ? 'Sentence context active' : 'No sentence context');
        this.setText('adaptive-insight-note', unknown.is_unknown
            ? (unknown.reason || 'The pose is outside the supported vocabulary; no command was issued.')
            : (intent.reason || 'Intent is inferred from the current gesture and context.'));
    }

    setText(id, text) {
        const element = document.getElementById(id);
        if (element) element.innerText = text;
    }

    formatLabel(value) {
        return String(value || 'IDLE')
            .replace(/[_\.]+/g, ' ')
            .replace(/\b\w/g, character => character.toUpperCase());
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.adaptiveProfileClient = new AdaptiveProfileClient();
});
