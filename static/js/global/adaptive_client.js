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
        this.calibration = null;
        this.personalization = { learned_gestures: [], mappings: [], corrections: [] };

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
        const calibrationStart = document.getElementById('personalization-calibration-start');
        const calibrationCapture = document.getElementById('personalization-capture');
        const calibrationComplete = document.getElementById('personalization-complete');
        const refreshPersonalization = document.getElementById('personalization-refresh');
        const correctionSubmit = document.getElementById('personalization-submit-correction');
        const personalizationReset = document.getElementById('personalization-reset');

        if (open) open.addEventListener('click', () => this.togglePanel(true));
        if (close) close.addEventListener('click', () => this.togglePanel(false));
        if (save) save.addEventListener('click', () => this.saveProfile());
        if (reset) reset.addEventListener('click', () => this.resetProfile());
        if (calibrationStart) calibrationStart.addEventListener('click', () => this.startCalibration());
        if (calibrationCapture) calibrationCapture.addEventListener('click', () => this.captureCalibrationSample());
        if (calibrationComplete) calibrationComplete.addEventListener('click', () => this.completeCalibration());
        if (refreshPersonalization) refreshPersonalization.addEventListener('click', () => this.loadPersonalization());
        if (correctionSubmit) correctionSubmit.addEventListener('click', () => this.submitCorrection());
        if (personalizationReset) personalizationReset.addEventListener('click', () => this.resetPersonalization());
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
            this.loadPersonalization();
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

    async loadPersonalization() {
        try {
            const response = await fetch('/api/personalization', { headers: this.headers() });
            if (!response.ok) throw new Error('Personalized data unavailable');
            const data = await response.json();
            this.personalization = data || { learned_gestures: [], mappings: [], corrections: [] };
            this.updateCalibration(data.active_calibration || null);
            this.renderLearnedGestures(data.learned_gestures || [], data.mappings || []);
            this.setText('adaptive-storage-status', data.storage && data.storage.available ? 'AVAILABLE' : 'OFFLINE');
        } catch (err) {
            this.setText('adaptive-storage-status', 'OFFLINE');
            this.renderLearnedGestures([], []);
        }
    }

    async pollCalibration() {
        if (!this.calibration) return;
        try {
            const response = await fetch('/api/personalization/calibration', { headers: this.headers() });
            if (!response.ok) return;
            const data = await response.json();
            this.updateCalibration(data.calibration || null);
        } catch (err) {
            // Calibration status is optional and must not affect legacy polling.
        }
    }

    async startCalibration() {
        const targetSelect = document.getElementById('personalization-target');
        const customName = document.getElementById('personalization-custom-name');
        let target = (targetSelect || {}).value || 'custom:my gesture';
        if (target.indexOf('custom:') === 0 && customName && customName.value.trim()) {
            target = `custom:${customName.value.trim()}`;
        }
        try {
            const response = await fetch('/api/personalization/calibration/start', {
                method: 'POST', headers: this.headers(true), body: JSON.stringify({ target, required_samples: 5 })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Calibration could not start');
            this.updateCalibration(data.calibration || null);
            this.setText('personalization-sample-message', 'Hold the pose steady, then accept stable samples.');
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Calibration could not start.');
        }
    }

    async captureCalibrationSample() {
        if (!this.calibration) return;
        try {
            const response = await fetch('/api/personalization/calibration/sample', {
                method: 'POST', headers: this.headers(true), body: JSON.stringify({ session_id: this.calibration.session_id })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Sample could not be captured');
            this.updateCalibration(data.calibration || this.calibration);
            this.setText('personalization-sample-message', data.pending
                ? 'Waiting for a live stable camera observation.'
                : (data.accepted ? 'Accepted stable sample.' : `Sample rejected: ${data.reason || 'not stable'}`));
            if (data.accepted) this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Sample could not be captured.');
        }
    }

    async completeCalibration() {
        if (!this.calibration) return;
        try {
            const response = await fetch('/api/personalization/calibration/complete', {
                method: 'POST', headers: this.headers(true), body: JSON.stringify({ session_id: this.calibration.session_id })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Calibration is not ready');
            this.updateCalibration(null);
            this.setText('personalization-sample-message', 'Gesture learned from explicitly accepted samples.');
            this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Calibration is not ready.');
            if (err && err.message) this.loadPersonalization();
        }
    }

    updateCalibration(calibration) {
        this.calibration = calibration && calibration.status === 'ACTIVE' ? calibration : null;
        const capture = document.getElementById('personalization-capture');
        const complete = document.getElementById('personalization-complete');
        const start = document.getElementById('personalization-calibration-start');
        const bar = document.getElementById('personalization-progress-bar');
        const label = document.getElementById('personalization-progress-label');
        if (!this.calibration) {
            if (capture) capture.disabled = true;
            if (complete) complete.disabled = true;
            if (bar) bar.style.width = '0%';
            if (label) label.innerText = 'No active calibration';
            return;
        }
        const accepted = Number(this.calibration.accepted_samples || 0);
        const required = Number(this.calibration.required_samples || 0);
        const rejected = Number(this.calibration.rejected_samples || 0);
        const progress = Number(this.calibration.progress_percent || 0);
        if (capture) capture.disabled = accepted >= required;
        if (complete) complete.disabled = accepted < required;
        if (start) start.innerText = 'RESTART';
        if (bar) bar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
        if (label) label.innerText = `${accepted}/${required} accepted · ${rejected} rejected`;
    }

    renderLearnedGestures(learned, mappings) {
        const container = document.getElementById('personalization-learned-list');
        if (!container) return;
        container.innerHTML = '';
        if (!learned.length) {
            const empty = document.createElement('span');
            empty.className = 'adaptive-help';
            empty.innerText = 'No validated personal gestures loaded.';
            container.appendChild(empty);
            return;
        }
        learned.forEach(item => {
            const row = document.createElement('div');
            row.className = 'adaptive-learned-row';
            const details = document.createElement('div');
            details.className = 'adaptive-learned-details';
            const title = document.createElement('strong');
            title.innerText = item.display_name || item.gesture_key;
            const meta = document.createElement('span');
            meta.innerText = `${item.validated_examples} validated · ${Math.round((item.reliability || 0) * 100)}% reliable`;
            details.append(title, meta);
            const controls = document.createElement('div');
            controls.className = 'adaptive-learned-controls';
            const current = mappings.find(mapping => mapping.learned_gesture_id === item.id);
            if (item.target_type !== 'sign') {
                const action = document.createElement('select');
                action.setAttribute('aria-label', 'Mapping action');
                [['click', 'Click'], ['back', 'Back'], ['scroll_up', 'Scroll Up'], ['scroll_down', 'Scroll Down']]
                    .forEach(([value, text]) => {
                        const option = document.createElement('option'); option.value = value; option.innerText = text; action.appendChild(option);
                    });
                if (current) action.value = current.action;
                const mapButton = document.createElement('button');
                mapButton.type = 'button'; mapButton.innerText = current ? 'UPDATE' : 'MAP';
                mapButton.addEventListener('click', () => this.saveMapping(item.id, action.value));
                controls.append(action, mapButton);
            } else {
                const signNote = document.createElement('span');
                signNote.className = 'adaptive-help';
                signNote.innerText = 'Left-hand sign; use the confirmation pose to commit it.';
                controls.appendChild(signNote);
            }
            row.append(details, controls);
            if (current && item.target_type !== 'sign') {
                const mapped = document.createElement('span');
                mapped.className = 'adaptive-mapping-label';
                mapped.innerText = `User-Learned Mapping: ${this.formatLabel(current.action)}`;
                row.appendChild(mapped);
                const remove = document.createElement('button');
                remove.type = 'button'; remove.className = 'adaptive-link-button'; remove.innerText = 'DELETE MAPPING';
                remove.addEventListener('click', () => this.deleteMapping(current.id));
                row.appendChild(remove);
            }
            container.appendChild(row);
        });
    }

    async saveMapping(gestureId, action) {
        try {
            const response = await fetch('/api/personalization/mappings', {
                method: 'POST', headers: this.headers(true), body: JSON.stringify({ learned_gesture_id: gestureId, action })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Mapping could not be saved');
            this.setText('personalization-sample-message', 'User-learned mapping saved.');
            this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Mapping could not be saved.');
        }
    }

    async deleteMapping(mappingId) {
        try {
            const response = await fetch(`/api/personalization/mappings/${encodeURIComponent(mappingId)}`, {
                method: 'DELETE', headers: this.headers()
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Mapping could not be deleted');
            this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Mapping could not be deleted.');
        }
    }

    async submitCorrection() {
        const input = document.getElementById('personalization-correction-label');
        const label = input ? input.value.trim() : '';
        if (!label) {
            this.setText('personalization-sample-message', 'Enter a correction label before submitting.');
            return;
        }
        try {
            const response = await fetch('/api/personalization/corrections', {
                method: 'POST', headers: this.headers(true),
                body: JSON.stringify({ correct_label: label, validated: true })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Correction could not be learned');
            if (input) input.value = '';
            this.setText('personalization-sample-message', 'Explicit correction recorded; more validated examples are required before matching.');
            this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Correction could not be learned.');
        }
    }

    async resetPersonalization() {
        if (!window.confirm('Delete learned gestures, mappings, corrections, calibration sessions, and adaptive history for this profile?')) return;
        try {
            const response = await fetch('/api/personalization/reset', { method: 'POST', headers: this.headers() });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Personalization could not be reset');
            this.updateCalibration(null);
            this.setText('personalization-sample-message', 'Personalized gestures, mappings, and adaptive history reset.');
            this.loadPersonalization();
        } catch (err) {
            this.setText('personalization-sample-message', err.message || 'Personalization could not be reset.');
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
                if (data.personalization_storage) {
                    this.setText('adaptive-storage-status', data.personalization_storage.available ? 'AVAILABLE' : 'OFFLINE');
                }
            }
        } catch (err) {
            // The adaptive panel is optional; do not disrupt legacy polling.
        } finally {
            if (this.calibration) this.pollCalibration();
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
        const personalization = runtime.personalization || {};
        if (runtime.calibration) this.updateCalibration(runtime.calibration);
        const sourceLabels = {
            'BASE_MODEL': 'Base Model',
            'PERSONALIZED_MODEL': 'Personalized Prediction',
            'USER_LEARNED_MAPPING': 'User-Learned Mapping'
        };
        const sourceLabel = sourceLabels[runtime.prediction_source] || sourceLabels[personalization.source] || 'Base Model';
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
        this.setText('adaptive-live-source', sourceLabel);
        this.setText('adaptive-prediction-source', sourceLabel);
        const personalizedDetail = personalization.used
            ? (personalization.mapping_action
                ? `${this.formatLabel(personalization.mapping_action)} · ${Math.round((personalization.confidence || 0) * 100)}%`
                : `${this.formatLabel(personalization.personalized_label || 'Personal gesture')} · ${Math.round((personalization.confidence || 0) * 100)}%`)
            : 'No personal override';
        this.setText('adaptive-prediction-detail', personalizedDetail);

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
