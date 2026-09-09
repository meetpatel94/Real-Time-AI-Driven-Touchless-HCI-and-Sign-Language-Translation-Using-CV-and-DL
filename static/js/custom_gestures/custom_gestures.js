/**
 * Custom Gesture Library client
 *
 * This page talks only to /api/custom-gestures/* endpoints. It never reads or
 * mutates the existing A-Z recognition state, sentence builder, translation, or
 * air-mouse prediction state.
 */
class CustomGestureManager {
    constructor() {
        this.pollInterval = 200;
        this.libraryInterval = 1500;
        this.statusTimer = null;
        this.libraryTimer = null;
        this.gestures = [];
        this.editingGestureId = null;
        this.init();
    }

    init() {
        this.bindEvents();
        this.loadGestures();
        this.startPolling();
        window.addEventListener('beforeunload', () => this.stopCustomMode(false));
    }

    bindEvents() {
        const showCreateBtn = document.getElementById('btn-show-create');
        const cancelCreateBtn = document.getElementById('btn-cancel-create');
        const form = document.getElementById('create-gesture-form');
        const refreshBtn = document.getElementById('btn-refresh-library');
        const liveBtn = document.getElementById('btn-start-live');
        const stopBtn = document.getElementById('btn-stop-custom-mode');

        if (showCreateBtn) showCreateBtn.addEventListener('click', () => this.showCreatePanel(true));
        if (cancelCreateBtn) cancelCreateBtn.addEventListener('click', () => this.showCreatePanel(false));
        if (form) form.addEventListener('submit', (event) => this.startCapture(event));
        if (refreshBtn) refreshBtn.addEventListener('click', () => this.loadGestures());
        if (liveBtn) liveBtn.addEventListener('click', () => this.startLiveRecognition());
        if (stopBtn) stopBtn.addEventListener('click', () => this.stopCustomMode(true));
    }

    showCreatePanel(show) {
        const panel = document.getElementById('create-panel');
        if (!panel) return;
        panel.hidden = !show;
        if (show) {
            const nameInput = document.getElementById('gesture-name');
            if (nameInput) nameInput.focus();
        }
    }

    notify(message, type = 'success') {
        const toast = document.getElementById('custom-toast');
        if (!toast) {
            if (type === 'error') alert(message);
            return;
        }
        toast.hidden = false;
        toast.className = `custom-toast ${type}`;
        toast.innerText = message;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => {
            toast.hidden = true;
        }, 3500);
    }

    async request(url, options = {}) {
        const res = await fetch(url, options);
        let data = {};
        try {
            data = await res.json();
        } catch (err) {
            data = {};
        }
        if (!res.ok || data.success === false) {
            throw new Error(data.error || data.message || `Request failed: ${res.status}`);
        }
        return data;
    }

    async loadGestures() {
        try {
            const data = await this.request('/api/custom-gestures');
            this.gestures = data.gestures || [];
            this.renderGestureList();
        } catch (err) {
            console.error('Failed to load custom gestures:', err);
            this.notify(err.message, 'error');
        }
    }

    renderGestureList() {
        const container = document.getElementById('custom-gesture-list');
        if (!container) return;

        if (!this.gestures.length) {
            container.innerHTML = '<div class="custom-empty-state">No custom gestures created yet. Click “+ Create Gesture” to capture your first landmark library entry.</div>';
            return;
        }

        container.innerHTML = '';
        this.gestures.forEach(gesture => {
            const card = document.createElement('article');
            card.className = `custom-gesture-card ${gesture.enabled ? '' : 'disabled'}`;
            card.dataset.gestureId = gesture.gesture_id;

            if (this.editingGestureId === gesture.gesture_id) {
                card.innerHTML = this.editTemplate(gesture);
                container.appendChild(card);
                this.bindEditCard(card, gesture);
                return;
            }

            const statusClass = gesture.enabled ? 'badge-success' : 'badge-danger';
            const enabledLabel = gesture.enabled ? 'Enabled' : 'Disabled';
            const isReady = (gesture.sample_count || 0) >= (gesture.target_samples || 1);
            const testDisabled = (!gesture.enabled || !isReady) ? 'disabled' : '';
            card.innerHTML = `
                <div class="custom-card-top">
                    <div>
                        <div class="custom-card-title">${this.escape(gesture.gesture_name || gesture.gesture_id)}</div>
                        <div class="custom-card-desc">${this.escape(gesture.description || 'No description')}</div>
                    </div>
                    <span class="badge ${statusClass}">${enabledLabel}</span>
                </div>
                <div class="custom-card-meta">
                    <div class="custom-meta-box"><span>Samples</span><strong>${gesture.sample_count || 0} / ${gesture.target_samples || 30}</strong></div>
                    <div class="custom-meta-box"><span>Status</span><strong>${this.escape(gesture.status || 'No samples')}</strong></div>
                    <div class="custom-meta-box"><span>Hand</span><strong>${this.formatHand(gesture.hand)}</strong></div>
                    <div class="custom-meta-box"><span>Folder</span><strong>${this.escape(gesture.storage_folder || gesture.gesture_id)}</strong></div>
                </div>
                <div class="custom-card-actions">
                    <button type="button" data-action="toggle" class="${gesture.enabled ? '' : 'success'}">${gesture.enabled ? 'Disable' : 'Enable'}</button>
                    <button type="button" data-action="test" ${testDisabled}>Test</button>
                    <button type="button" data-action="edit">Edit</button>
                    <button type="button" data-action="delete" class="danger">Delete</button>
                </div>
            `;
            container.appendChild(card);
            this.bindGestureCard(card, gesture);
        });
    }

    editTemplate(gesture) {
        return `
            <div class="custom-card-top">
                <div>
                    <div class="custom-card-title">Edit</div>
                    <div class="custom-card-desc">Update metadata without touching the A-Z model.</div>
                </div>
                <span class="badge">${this.escape(gesture.gesture_id)}</span>
            </div>
            <form class="custom-edit-form">
                <label>Gesture Name
                    <input name="gesture_name" type="text" maxlength="80" value="${this.attr(gesture.gesture_name || '')}">
                </label>
                <label>Description
                    <input name="description" type="text" maxlength="160" value="${this.attr(gesture.description || '')}">
                </label>
                <label>Hand
                    <select name="hand">
                        <option value="right" ${gesture.hand === 'right' ? 'selected' : ''}>Right</option>
                        <option value="left" ${gesture.hand === 'left' ? 'selected' : ''}>Left</option>
                        <option value="either" ${gesture.hand === 'either' ? 'selected' : ''}>Either</option>
                    </select>
                </label>
                <label>Similarity Threshold
                    <input name="similarity_threshold" type="number" min="0.10" max="0.99" step="0.01" value="${Number(gesture.similarity_threshold || 0.85).toFixed(2)}">
                </label>
                <label class="custom-enable-row">
                    <span>Enabled</span>
                    <select name="enabled">
                        <option value="true" ${gesture.enabled ? 'selected' : ''}>Enabled</option>
                        <option value="false" ${!gesture.enabled ? 'selected' : ''}>Disabled</option>
                    </select>
                </label>
                <div class="custom-inline-edit-actions">
                    <button type="submit" class="success">Save</button>
                    <button type="button" data-action="cancel-edit">Cancel</button>
                </div>
            </form>
        `;
    }

    bindGestureCard(card, gesture) {
        card.querySelectorAll('button[data-action]').forEach(button => {
            button.addEventListener('click', () => {
                const action = button.dataset.action;
                if (action === 'toggle') this.toggleGesture(gesture);
                if (action === 'test') this.startTest(gesture);
                if (action === 'edit') {
                    this.editingGestureId = gesture.gesture_id;
                    this.renderGestureList();
                }
                if (action === 'delete') this.deleteGesture(gesture);
            });
        });
    }

    bindEditCard(card, gesture) {
        const form = card.querySelector('form');
        const cancel = card.querySelector('[data-action="cancel-edit"]');
        if (cancel) {
            cancel.addEventListener('click', () => {
                this.editingGestureId = null;
                this.renderGestureList();
            });
        }
        if (!form) return;
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const formData = new FormData(form);
            const payload = {
                gesture_name: String(formData.get('gesture_name') || '').trim(),
                description: String(formData.get('description') || '').trim(),
                hand: String(formData.get('hand') || 'either'),
                enabled: String(formData.get('enabled')) === 'true',
                similarity_threshold: parseFloat(formData.get('similarity_threshold')) || 0.85
            };
            try {
                await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                this.editingGestureId = null;
                this.notify('Custom gesture updated.');
                await this.loadGestures();
            } catch (err) {
                this.notify(err.message, 'error');
            }
        });
    }

    async startCapture(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const formData = new FormData(form);
        const payload = {
            gesture_name: String(formData.get('gesture_name') || '').trim(),
            description: String(formData.get('description') || '').trim(),
            hand: String(formData.get('hand') || 'either'),
            target_samples: parseInt(formData.get('target_samples'), 10) || 30
        };

        if (!payload.gesture_name) {
            this.notify('Gesture name is required.', 'error');
            return;
        }

        try {
            await this.request('/api/custom-gestures/capture/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            this.showCreatePanel(false);
            this.notify(`Capturing “${payload.gesture_name}”. Keep the gesture visible.`);
            await this.loadGestures();
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    async startLiveRecognition() {
        try {
            await this.request('/api/custom-gestures/live/start', { method: 'POST' });
            this.notify('Live Custom Gesture Recognition started.');
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    async startTest(gesture) {
        if (!gesture.enabled) {
            this.notify('Enable this gesture before testing. Disabled gestures are excluded from custom recognition.', 'error');
            return;
        }
        if ((gesture.sample_count || 0) < (gesture.target_samples || 1)) {
            this.notify('Capture all requested samples before testing this gesture.', 'error');
            return;
        }
        try {
            await this.request('/api/custom-gestures/test/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ gesture_id: gesture.gesture_id })
            });
            this.notify(`Testing “${gesture.gesture_name}”.`);
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    async stopCustomMode(showNotification = true) {
        try {
            await fetch('/api/custom-gestures/capture/stop', { method: 'POST', keepalive: true });
            await fetch('/api/custom-gestures/recognition/stop', { method: 'POST', keepalive: true });
            if (showNotification) this.notify('Custom gesture mode stopped.');
        } catch (err) {
            if (showNotification) this.notify('Could not stop custom mode.', 'error');
        }
    }

    async toggleGesture(gesture) {
        try {
            const data = await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/toggle`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: !gesture.enabled })
            });
            this.notify(`${data.gesture.gesture_name} ${data.gesture.enabled ? 'enabled' : 'disabled'}.`);
            await this.loadGestures();
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    async deleteGesture(gesture) {
        const ok = confirm(`Delete custom gesture “${gesture.gesture_name}”?\n\nOnly data/custom_gestures/${gesture.gesture_id}/ will be removed.`);
        if (!ok) return;
        try {
            await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}`, { method: 'DELETE' });
            this.notify(`Deleted “${gesture.gesture_name}”.`);
            await this.loadGestures();
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    startPolling() {
        this.statusTimer = setInterval(() => this.updateRuntimeStatus(), this.pollInterval);
        this.libraryTimer = setInterval(() => this.loadGestures(), this.libraryInterval);
        this.updateRuntimeStatus();
    }

    async updateRuntimeStatus() {
        try {
            const data = await this.request('/api/custom-gestures/recognition/status');
            this.updateStatusUI(data.runtime || {});
            const runtime = data.runtime || {};
            if (runtime.capture_completed) {
                await this.loadGestures();
            }
        } catch (err) {
            // Keep polling quiet; the explicit controls surface actionable errors.
        }
    }

    updateStatusUI(runtime) {
        const mode = runtime.mode || 'idle';
        const status = runtime.status || 'IDLE';
        const handDetected = Boolean(runtime.hand_detected);
        const prediction = runtime.prediction || 'Unknown';
        const confidence = Number(runtime.confidence || 0);
        const expected = runtime.expected_gesture || '—';
        const sampleCount = Number(runtime.sample_count || 0);
        const targetSamples = Number(runtime.target_samples || 30);
        const progress = Number(runtime.capture_progress || 0);
        const threshold = runtime.similarity_threshold || 85;
        const stable = Number(runtime.stable_frames || 0);
        const requiredStable = Number(runtime.required_stable_frames || 3);

        this.setText('custom-mode-value', this.titleCase(mode));
        this.setText('custom-hand-value', runtime.handedness || 'none');
        this.setText('custom-threshold-value', `${threshold}%`);
        this.setText('custom-stability-value', `${stable} / ${requiredStable}`);
        this.setText('custom-mode-badge', status);
        this.setText('custom-hand-badge', handDetected ? 'HAND DETECTED' : 'NO HAND');
        this.setClass('custom-hand-badge', `badge ${handDetected ? 'badge-success' : 'badge-danger'}`);

        const modeBadgeClass = status === 'MATCH' || status === 'READY' ? 'badge badge-success'
            : (status === 'NO_MATCH' || status === 'UNKNOWN' || status === 'NO_HAND' || status === 'LOW_QUALITY' || status === 'DISABLED') ? 'badge badge-danger'
            : 'badge';
        this.setClass('custom-mode-badge', modeBadgeClass);

        this.setText('capture-title', runtime.mode === 'capture' ? `Capturing ${runtime.gesture_name || ''}` : 'No active capture');
        this.setText('capture-status-badge', runtime.mode === 'capture' ? status : 'IDLE');
        this.setClass('capture-status-badge', runtime.capture_completed ? 'badge badge-success' : (runtime.mode === 'capture' ? 'badge' : 'badge'));
        this.setWidth('capture-progress-bar', `${Math.max(0, Math.min(100, progress))}%`);
        this.setText('capture-count-label', `${sampleCount} / ${targetSamples} samples`);
        this.setText('capture-progress-label', `${progress.toFixed ? progress.toFixed(1) : progress}%`);
        this.setText('capture-message', runtime.mode === 'capture' ? (runtime.message || runtime.detection_status || '') : 'Create a gesture to begin collecting landmark samples.');

        this.setText('custom-live-label', prediction);
        const liveLabel = document.getElementById('custom-live-label');
        if (liveLabel) liveLabel.style.color = prediction === 'Unknown' ? 'var(--accent-red)' : 'var(--accent-blue)';
        this.setText('custom-live-confidence', `${confidence.toFixed ? confidence.toFixed(1) : confidence}%`);
        this.setText('custom-expected', expected);
        this.setText('custom-detected', prediction);
        this.setText('custom-recognition-message', runtime.message || runtime.detection_status || '');
        this.setText('test-status-badge', status);
        this.setClass('test-status-badge', modeBadgeClass);

        const matchStatus = document.getElementById('custom-match-status');
        if (matchStatus) {
            if (mode === 'test') {
                matchStatus.innerText = status === 'MATCH' ? 'MATCH' : (status === 'STABILIZING' ? 'STABILIZING' : 'NO MATCH');
                matchStatus.className = `compare-badge badge ${status === 'MATCH' ? 'badge-success' : (status === 'STABILIZING' ? '' : 'badge-danger')}`;
            } else if (mode === 'live') {
                matchStatus.innerText = status === 'MATCH' ? 'MATCH' : (status === 'STABILIZING' ? 'STABILIZING' : 'UNKNOWN');
                matchStatus.className = `compare-badge badge ${status === 'MATCH' ? 'badge-success' : ''}`;
            } else {
                matchStatus.innerText = '—';
                matchStatus.className = 'compare-badge badge';
            }
        }

        this.renderHistory(runtime.recent_predictions || []);
    }

    renderHistory(history) {
        const container = document.getElementById('custom-history-list');
        if (!container) return;
        if (!history.length) {
            container.innerHTML = '<div class="custom-empty-state">No stable custom matches yet.</div>';
            return;
        }
        container.innerHTML = '';
        history.forEach(item => {
            const row = document.createElement('div');
            row.className = 'history-item';
            row.innerHTML = `
                <span class="history-label">${this.escape(item.label || 'Unknown')}</span>
                <span class="history-conf">${Number(item.confidence || 0).toFixed(1)}%</span>
                <span style="color: var(--text-muted); font-size: 0.75rem;">${this.escape(item.time || '')}</span>
            `;
            container.appendChild(row);
        });
    }

    setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.innerText = value;
    }

    setClass(id, value) {
        const element = document.getElementById(id);
        if (element) element.className = value;
    }

    setWidth(id, value) {
        const element = document.getElementById(id);
        if (element) element.style.width = value;
    }

    formatHand(value) {
        if (value === 'left') return 'Left';
        if (value === 'right') return 'Right';
        return 'Either';
    }

    titleCase(value) {
        return String(value || '').replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
    }

    escape(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    attr(value) {
        return this.escape(value).replace(/`/g, '&#096;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.customGestureManager = new CustomGestureManager();
});
