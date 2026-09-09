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
        this.learningInterval = 4000;
        this.statusTimer = null;
        this.libraryTimer = null;
        this.learningTimer = null;
        this.gestures = [];
        this.editingGestureId = null;
        this.lastRuntime = {};
        this.pendingCandidate = null;
        this.knownCandidateIds = new Set();
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
        const correctBtn = document.getElementById('btn-correct-prediction');
        const closeBtn = document.getElementById('btn-close-modal');
        const overlay = document.getElementById('custom-modal-overlay');

        if (showCreateBtn) showCreateBtn.addEventListener('click', () => {
            this.clearPendingCandidate();
            this.showCreatePanel(true);
        });
        if (cancelCreateBtn) cancelCreateBtn.addEventListener('click', () => this.showCreatePanel(false));
        if (form) form.addEventListener('submit', (event) => this.startCapture(event));
        if (refreshBtn) refreshBtn.addEventListener('click', () => this.loadGestures());
        if (liveBtn) liveBtn.addEventListener('click', () => this.startLiveRecognition());
        if (stopBtn) stopBtn.addEventListener('click', () => this.stopCustomMode(true));
        if (correctBtn) correctBtn.addEventListener('click', () => this.openCorrectionModal());
        if (closeBtn) closeBtn.addEventListener('click', () => this.closeModal());
        if (overlay) overlay.addEventListener('click', (event) => {
            if (event.target === overlay) this.closeModal();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') this.closeModal();
        });
    }

    showCreatePanel(show) {
        const panel = document.getElementById('create-panel');
        if (!panel) return;
        panel.hidden = !show;
        if (show) {
            const nameInput = document.getElementById('gesture-name');
            if (nameInput) nameInput.focus();
        } else {
            this.clearPendingCandidate();
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
            const variations = gesture.variation_count || 0;
            const pending = gesture.pending_variation_count || 0;
            const accuracy = gesture.recognition_accuracy != null ? `${gesture.recognition_accuracy}%` : '—';
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
                    <div class="custom-meta-box"><span>Variations</span><strong>${variations}${pending ? ` (+${pending} pending)` : ''}</strong></div>
                    <div class="custom-meta-box"><span>Recognition</span><strong>${accuracy}</strong></div>
                    <div class="custom-meta-box"><span>Status</span><strong>${this.escape(gesture.status || 'No samples')}</strong></div>
                    <div class="custom-meta-box"><span>Hand</span><strong>${this.formatHand(gesture.hand)}</strong></div>
                    <div class="custom-meta-box"><span>Folder</span><strong>${this.escape(gesture.storage_folder || gesture.gesture_id)}</strong></div>
                </div>
                ${variations ? `<div class="custom-evolution-note">${variations} personalized variation${variations > 1 ? 's' : ''} learned</div>` : ''}
                <div class="custom-card-actions">
                    <button type="button" data-action="toggle" class="${gesture.enabled ? '' : 'success'}">${gesture.enabled ? 'Disable' : 'Enable'}</button>
                    <button type="button" data-action="test" ${testDisabled}>Test</button>
                    <button type="button" data-action="evolution">Evolution</button>
                    <button type="button" data-action="accept-variations" class="success" ${pending ? '' : 'disabled'}>Accept</button>
                    <button type="button" data-action="ignore-variations" ${pending ? '' : 'disabled'}>Ignore</button>
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
                if (action === 'evolution') this.openEvolutionModal(gesture);
                if (action === 'accept-variations') this.acceptVariations(gesture);
                if (action === 'ignore-variations') this.ignoreVariations(gesture);
            });
        });
    }

    async acceptVariations(gesture) {
        try {
            const data = await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/variations/accept`, { method: 'POST' });
            this.notify(`Accepted ${data.accepted.length} variation(s) for “${gesture.gesture_name}”.`);
            await this.loadGestures();
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    async ignoreVariations(gesture) {
        try {
            await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/variations/ignore`, { method: 'POST' });
            this.notify(`Pending variations for “${gesture.gesture_name}” ignored.`);
            await this.loadGestures();
        } catch (err) {
            this.notify(err.message, 'error');
        }
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
        if (this.pendingCandidate) {
            payload.candidate_id = this.pendingCandidate.candidate_id;
        }

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
            this.clearPendingCandidate();
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
        this.learningTimer = setInterval(() => this.loadLearningStatus(), this.learningInterval);
        this.updateRuntimeStatus();
        this.loadLearningStatus();
    }

    // ------------------------------------------------------------------
    // Self-learning UI (unknown discovery, mistake memory, evolution)
    // ------------------------------------------------------------------
    async loadLearningStatus() {
        try {
            const data = await this.request('/api/custom-gestures/learning/status');
            this.renderCandidates(data.learning || {});
        } catch (err) {
            // Learning status is advisory; keep polling quiet.
        }
    }

    renderCandidates(learning) {
        const section = document.getElementById('custom-candidates-section');
        const list = document.getElementById('custom-candidate-list');
        if (!section || !list) return;
        const candidates = learning.candidates || [];
        section.hidden = candidates.length === 0;

        // Notify once when a brand-new candidate surfaces.
        candidates.forEach(item => {
            if (!this.knownCandidateIds.has(item.candidate_id)) {
                this.notify('New gesture detected — review the New Gesture Candidates panel.');
            }
        });
        this.knownCandidateIds = new Set(candidates.map(item => item.candidate_id));

        list.innerHTML = '';
        candidates.forEach((candidate, index) => {
            const card = document.createElement('article');
            card.className = 'custom-candidate-card';
            card.innerHTML = `
                <div class="custom-candidate-top">
                    <div>
                        <div class="custom-candidate-title">New Gesture Candidate ${index + 1}</div>
                        <div class="custom-candidate-desc">
                            Observed: <strong>${candidate.observed_count}</strong> times
                            &middot; Similarity within cluster: <strong>${candidate.cluster_similarity}%</strong>
                            &middot; Hand: ${this.formatHand(candidate.handedness)}
                        </div>
                    </div>
                    <span class="badge badge-success">NEW GESTURE DETECTED</span>
                </div>
                <div class="custom-candidate-actions">
                    <button type="button" data-action="learn" class="success">Learn Gesture</button>
                    <button type="button" data-action="ignore">Ignore</button>
                </div>
            `;
            card.querySelector('[data-action="learn"]').addEventListener('click', () => this.learnCandidate(candidate));
            card.querySelector('[data-action="ignore"]').addEventListener('click', () => this.ignoreCandidate(candidate));
            list.appendChild(card);
        });
    }

    async learnCandidate(candidate) {
        try {
            const data = await this.request(
                `/api/custom-gestures/learning/candidates/${encodeURIComponent(candidate.candidate_id)}/learn`,
                { method: 'POST' }
            );
            this.pendingCandidate = {
                candidate_id: candidate.candidate_id,
                observation_count: data.observation_count,
                handedness: data.handedness
            };
            const note = document.getElementById('candidate-prefill-note');
            if (note) {
                note.hidden = false;
                note.innerText = `${data.observation_count} captured sample(s) from this candidate are ready and will be used automatically when capture starts.`;
            }
            const handSelect = document.getElementById('gesture-hand');
            if (handSelect && data.handedness && data.handedness !== 'either') {
                handSelect.value = data.handedness;
            }
            this.showCreatePanel(true);
            const nameInput = document.getElementById('gesture-name');
            if (nameInput) nameInput.focus();
            this.notify('Name the gesture to finish learning it.');
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    clearPendingCandidate() {
        this.pendingCandidate = null;
        const note = document.getElementById('candidate-prefill-note');
        if (note) {
            note.hidden = true;
            note.innerText = '';
        }
    }

    async ignoreCandidate(candidate) {
        const ok = confirm('Ignore this candidate?\n\nIt will not be saved, and the same gesture will not resurface for a while.');
        if (!ok) return;
        try {
            await this.request(
                `/api/custom-gestures/learning/candidates/${encodeURIComponent(candidate.candidate_id)}/ignore`,
                { method: 'POST' }
            );
            this.notify('Candidate ignored. Nothing was saved.');
            await this.loadLearningStatus();
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    openCorrectionModal() {
        const runtime = this.lastRuntime || {};
        const currentId = runtime.detected_gesture_id || '';
        const options = this.gestures
            .filter(g => g.gesture_id !== currentId && g.enabled && (g.sample_count || 0) >= (g.target_samples || 1))
            .map(g => `<option value="${this.attr(g.gesture_id)}">${this.escape(g.gesture_name)}</option>`)
            .join('');
        if (!options) {
            this.notify('No other saved custom gesture is available to correct to.', 'error');
            return;
        }
        this.renderModal('Correct Prediction', `
            <p class="custom-muted-text">
                The system detected <strong>${this.escape(runtime.prediction || 'Unknown')}</strong>.
                What was it supposed to be?
            </p>
            <form id="correction-form" class="custom-form">
                <label for="correction-target">
                    Correct gesture
                    <select id="correction-target" name="correct_gesture_id">${options}</select>
                </label>
                <div class="custom-form-actions">
                    <button type="submit" class="success">Save Correction</button>
                    <button type="button" data-action="close-modal">Cancel</button>
                </div>
            </form>
            <p class="custom-form-note">
                Stored only in your personalized Custom Gesture memory. The A-Z model and other gestures are never changed.
            </p>
        `);
        const form = document.getElementById('correction-form');
        if (form) {
            form.addEventListener('submit', async (event) => {
                event.preventDefault();
                const select = document.getElementById('correction-target');
                try {
                    await this.request('/api/custom-gestures/corrections', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            predicted_gesture_id: currentId,
                            correct_gesture_id: select ? select.value : ''
                        })
                    });
                    this.notify('Correction saved. Similar gestures will use your correction.');
                    this.closeModal();
                } catch (err) {
                    this.notify(err.message, 'error');
                }
            });
        }
    }

    async openEvolutionModal(gesture) {
        try {
            const data = await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/evolution`);
            const e = data.evolution || {};
            const stats = e.stats || {};
            const accuracy = stats.recognition_accuracy != null ? `${stats.recognition_accuracy}%` : '—';
            const variationRows = (e.variations || []).map(v => `
                <div class="history-item">
                    <span class="history-label">${this.escape(v.variation_id)}</span>
                    <span class="history-conf">${v.observed_count || 0} obs</span>
                    <span class="history-conf">${v.similarity_to_gesture != null ? v.similarity_to_gesture + '%' : '—'} match</span>
                </div>`).join('')
                || '<div class="custom-empty-state">No personalized variations learned yet.</div>';
            const pendingRows = (e.pending || []).map(v => `
                <div class="history-item">
                    <span class="history-label">Variant (${v.observed_count || 0} observations)</span>
                    <span class="history-conf">${v.similarity_to_gesture != null ? v.similarity_to_gesture + '%' : '—'} match</span>
                    <span class="badge ${v.ready ? 'badge-success' : ''}">${v.ready ? 'READY' : 'GATHERING'}</span>
                </div>`).join('')
                || '<div class="custom-empty-state">No pending variations right now.</div>';
            const correctionRows = [
                ...((e.corrections && e.corrections.caused) || []).map(c => ({ ...c, kind: 'Corrected away from' })),
                ...((e.corrections && e.corrections.received) || []).map(c => ({ ...c, kind: 'Corrected to' }))
            ].map(c => `
                <div class="history-item">
                    <span class="history-label">${c.kind}: ${this.escape(c.kind === 'Corrected to' ? c.correct_gesture_id : c.predicted_gesture_id)}</span>
                    <span style="color: var(--text-muted); font-size: 0.75rem;">${this.escape((c.created_at || '').replace('T', ' '))}</span>
                </div>`).join('')
                || '<div class="custom-empty-state">No corrections recorded for this gesture.</div>';
            const hasPending = (e.pending || []).length > 0;
            this.renderModal(`Evolution: ${gesture.gesture_name}`, `
                <p class="custom-muted-text">
                    Personalized gesture profile: original samples + learned variations + correction memory.
                </p>
                <div class="custom-evolution-grid">
                    <div class="custom-meta-box"><span>Original samples</span><strong>${e.original_samples || 0}</strong></div>
                    <div class="custom-meta-box"><span>Learned variations</span><strong>${(e.variations || []).length}</strong></div>
                    <div class="custom-meta-box"><span>Recognition</span><strong>${accuracy}</strong></div>
                    <div class="custom-meta-box"><span>Detections</span><strong>${stats.detections || 0}</strong></div>
                    <div class="custom-meta-box"><span>Corrections caused</span><strong>${stats.corrections_caused || 0}</strong></div>
                    <div class="custom-meta-box"><span>Corrections applied</span><strong>${stats.corrections_applied || 0}</strong></div>
                </div>
                ${stats.last_detected_at ? `<p class="custom-form-note">Last detected: ${this.escape(stats.last_detected_at.replace('T', ' '))}</p>` : ''}
                <h4 class="custom-modal-subtitle">Learned variations</h4>
                ${variationRows}
                <h4 class="custom-modal-subtitle">Pending variations</h4>
                ${pendingRows}
                <div class="custom-form-actions">
                    <button id="btn-accept-variations" type="button" class="success" ${hasPending ? '' : 'disabled'}>Accept</button>
                    <button id="btn-ignore-variations" type="button" ${hasPending ? '' : 'disabled'}>Ignore</button>
                </div>
                <h4 class="custom-modal-subtitle">Correction history</h4>
                ${correctionRows}
            `);
            const acceptBtn = document.getElementById('btn-accept-variations');
            const ignoreBtn = document.getElementById('btn-ignore-variations');
            if (acceptBtn) {
                acceptBtn.addEventListener('click', async () => {
                    try {
                        const res = await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/variations/accept`, { method: 'POST' });
                        this.notify(`Accepted ${res.accepted.length} variation(s). The gesture profile now includes your style.`);
                        this.closeModal();
                        await this.loadGestures();
                    } catch (err) {
                        this.notify(err.message, 'error');
                    }
                });
            }
            if (ignoreBtn) {
                ignoreBtn.addEventListener('click', async () => {
                    try {
                        await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/variations/ignore`, { method: 'POST' });
                        this.notify('Pending variations ignored.');
                        this.closeModal();
                        await this.loadGestures();
                    } catch (err) {
                        this.notify(err.message, 'error');
                    }
                });
            }
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    renderModal(title, bodyHtml) {
        const overlay = document.getElementById('custom-modal-overlay');
        const titleEl = document.getElementById('custom-modal-title');
        const body = document.getElementById('custom-modal-body');
        if (!overlay || !titleEl || !body) return;
        titleEl.innerText = title;
        body.innerHTML = bodyHtml;
        overlay.hidden = false;
    }

    closeModal() {
        const overlay = document.getElementById('custom-modal-overlay');
        const body = document.getElementById('custom-modal-body');
        if (overlay) overlay.hidden = true;
        if (body) body.innerHTML = '';
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

        this.lastRuntime = runtime;

        this.setText('custom-live-label', prediction);
        const liveLabel = document.getElementById('custom-live-label');
        if (liveLabel) liveLabel.style.color = prediction === 'Unknown' ? 'var(--accent-red)' : 'var(--accent-blue)';
        this.setText('custom-live-confidence', `${confidence.toFixed ? confidence.toFixed(1) : confidence}%`);

        const correctionApplied = !!(runtime.correction && runtime.correction.corrected_name);
        const correctionPrompt = document.getElementById('custom-correction-prompt');
        if (correctionPrompt) {
            correctionPrompt.hidden = !(mode === 'live' && prediction && prediction !== 'Unknown' && !correctionApplied);
        }
        const appliedEl = document.getElementById('custom-correction-applied');
        if (appliedEl) {
            if (correctionApplied) {
                appliedEl.hidden = false;
                appliedEl.innerText = `Personalized correction: ${runtime.correction.original_name} → ${runtime.correction.corrected_name}`;
            } else {
                appliedEl.hidden = true;
            }
        }
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
