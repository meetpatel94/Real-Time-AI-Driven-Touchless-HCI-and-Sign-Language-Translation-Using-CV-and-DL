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
        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.closeModal();
            });
        }
        if (overlay) overlay.addEventListener('click', (event) => {
            if (event.target === overlay) this.closeModal();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                const ov = document.getElementById('custom-modal-overlay');
                if (ov && !ov.hidden) {
                    this.closeModal();
                }
            }
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
                    <button type="button" data-action="details">Details</button>
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
                if (action === 'details') this.openDetailsModal(gesture);
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

    // ------------------------------------------------------------------
    // Feature 1-3: Gesture Details (DNA + Coach + Analytics)
    // ------------------------------------------------------------------
    async openDetailsModal(gesture) {
        try {
            const data = await this.request(`/api/custom-gestures/${encodeURIComponent(gesture.gesture_id)}/details`);
            this.renderDetailsModal(data, gesture);
        } catch (err) {
            this.notify(err.message, 'error');
        }
    }

    renderDetailsModal(data, gesture) {
        const dna = data.dna || {};
        const analytics = data.analytics || {};
        const coach = data.coach || {};
        const dnaComp = data.dna_comparison || {};
        const gestureRecord = data.gesture || gesture;

        const dnaHtml = this.renderDnaSection(dna, dnaComp);
        const coachHtml = this.renderCoachSection(coach);
        const analyticsHtml = this.renderAnalyticsSection(analytics);
        const evolutionHtml = this.renderEvolutionSection(analytics);
        const qualityHtml = this.renderQualitySection(analytics, gestureRecord);

        const lowQualityHtml = analytics.low_quality_warning ? `
            <div class="low-quality-warning">
                <div class="low-quality-title">⚠ Gesture quality is low</div>
                <div class="low-quality-reasons">
                    ${(analytics.low_quality_reasons || []).map(r => this.escape(r)).join('<br>')}
                </div>
                <div class="low-quality-action">
                    <button type="button" id="btn-improve-gesture" data-gesture-id="${this.attr(gesture.gesture_id)}">Improve Gesture</button>
                </div>
            </div>
        ` : '';

        this.renderModal(`${gesture.gesture_name || gesture.gesture_id} — Details`, `
            <div class="details-section">
                <h4 class="details-section-title">Basic Information</h4>
                <div class="analytics-grid">
                    <div class="analytics-metric">
                        <span class="analytics-metric-value">${gestureRecord.sample_count || 0}</span>
                        <span class="analytics-metric-label">Samples</span>
                    </div>
                    <div class="analytics-metric">
                        <span class="analytics-metric-value">${this.escape(gestureRecord.status || '—')}</span>
                        <span class="analytics-metric-label">Status</span>
                    </div>
                    <div class="analytics-metric">
                        <span class="analytics-metric-value">${this.formatHand(gestureRecord.hand)}</span>
                        <span class="analytics-metric-label">Hand</span>
                    </div>
                    <div class="analytics-metric">
                        <span class="analytics-metric-value">${Number((gestureRecord.similarity_threshold || 0.85) * 100).toFixed(0)}%</span>
                        <span class="analytics-metric-label">Threshold</span>
                    </div>
                </div>
            </div>

            <div class="details-section">
                <h4 class="details-section-title">Gesture DNA</h4>
                ${dnaHtml}
            </div>

            ${dnaComp.similarity != null ? `
            <div class="details-section">
                <h4 class="details-section-title">DNA Comparison (Current vs Stored)</h4>
                <div class="dna-overall">
                    <span class="dna-overall-label">Overall Similarity</span>
                    <span class="dna-overall-value">${Number(dnaComp.similarity || 0).toFixed(1)}%</span>
                    <span class="dna-match-level ${(dnaComp.match_level || '').toLowerCase().replace(' ', '-')}">${this.escape(dnaComp.match_level || '—')}</span>
                </div>
            </div>
            ` : ''}

            <div class="details-section">
                <h4 class="details-section-title">AI Coach</h4>
                ${coachHtml}
            </div>

            ${lowQualityHtml}

            <div class="details-section">
                <h4 class="details-section-title">Performance</h4>
                ${qualityHtml}
            </div>

            <div class="details-section">
                <h4 class="details-section-title">Analytics</h4>
                ${analyticsHtml}
            </div>

            <div class="details-section">
                <h4 class="details-section-title">Evolution Over Time</h4>
                ${evolutionHtml}
            </div>
        `);

        // Bind "Improve Gesture" button
        const improveBtn = document.getElementById('btn-improve-gesture');
        if (improveBtn) {
            improveBtn.addEventListener('click', () => {
                this.closeModal();
                this.showCreatePanel(true);
                const nameInput = document.getElementById('gesture-name');
                if (nameInput) nameInput.value = gesture.gesture_name || gesture.gesture_id;
            });
        }
    }

    renderDnaSection(dna, dnaComp) {
        if (!dna || !dna.sample_count) {
            return '<div class="custom-empty-state">No DNA data available yet. Capture samples to generate the DNA profile.</div>';
        }
        const rows = [
            { label: 'Hand Shape', value: dna.hand_shape || 0 },
            { label: 'Finger Extension', value: dna.finger_extension || 0 },
            { label: 'Palm Orientation', value: dna.palm_orientation || 0 },
            { label: 'Finger Spread', value: dna.finger_spread || 0 },
            { label: 'Stability', value: dna.stability || 0 },
            { label: 'Spatial Consistency', value: dna.spatial_consistency || 0 },
        ];
        return `
            <div class="dna-profile">
                ${rows.map(r => `
                    <div class="dna-row">
                        <span class="dna-label">${r.label}</span>
                        <div class="dna-bar-track">
                            <div class="dna-bar-fill ${this.dnaBarClass(r.value)}" style="width: ${Math.min(100, r.value)}%"></div>
                        </div>
                        <span class="dna-bar-value">${r.value.toFixed(1)}%</span>
                    </div>
                `).join('')}
            </div>
            <div style="margin-top: 0.4rem; font-size: 0.75rem; color: var(--text-muted);">
                Based on ${dna.sample_count} stored sample(s).
            </div>
        `;
    }

    renderCoachSection(coach) {
        if (!coach || (!coach.feedback && !coach.tips && !coach.sample_quality && !coach.guidance)) {
            return '<div class="custom-empty-state">Start a test or live recognition to receive coach feedback.</div>';
        }
        const feedback = coach.feedback || '';
        const tips = coach.tips || coach.guidance || [];
        const quality = coach.sample_quality;

        let qualityHtml = '';
        if (quality != null) {
            const cls = quality < 60 ? 'low' : '';
            qualityHtml = `
                <div class="coach-quality ${cls}">
                    <span class="coach-quality-label">Sample Quality</span>
                    <span class="coach-quality-value">${Number(quality).toFixed(1)}%</span>
                </div>
            `;
        }

        const tipsHtml = tips.length ? `
            <ul class="coach-tips">
                ${tips.map(t => `<li>${this.escape(t)}</li>`).join('')}
            </ul>
        ` : '';

        return `
            <div class="coach-box">
                <div class="coach-header">
                    <span class="coach-icon">🎯</span>
                    <span class="coach-title">AI Gesture Coach</span>
                </div>
                ${qualityHtml}
                ${feedback ? `<div class="coach-feedback">${this.escape(feedback)}</div>` : ''}
                ${tipsHtml}
            </div>
        `;
    }

    renderAnalyticsSection(analytics) {
        if (!analytics || !analytics.recognition_count) {
            return '<div class="custom-empty-state">No recognition events recorded yet. Use the gesture in live or test mode to build analytics.</div>';
        }
        const successClass = analytics.recognition_success_rate >= 80 ? 'good' : (analytics.recognition_success_rate >= 60 ? 'warn' : 'bad');
        const confClass = analytics.avg_confidence >= 80 ? 'good' : (analytics.avg_confidence >= 60 ? 'warn' : 'bad');
        const simClass = analytics.avg_similarity >= 80 ? 'good' : (analytics.avg_similarity >= 60 ? 'warn' : 'bad');

        return `
            <div class="analytics-grid">
                <div class="analytics-metric">
                    <span class="analytics-metric-value ${successClass}">${Number(analytics.recognition_success_rate || 0).toFixed(1)}%</span>
                    <span class="analytics-metric-label">Success Rate</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value ${confClass}">${Number(analytics.avg_confidence || 0).toFixed(1)}%</span>
                    <span class="analytics-metric-label">Avg Confidence</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value ${simClass}">${Number(analytics.avg_similarity || 0).toFixed(1)}%</span>
                    <span class="analytics-metric-label">Avg Similarity</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value">${analytics.recognition_count || 0}</span>
                    <span class="analytics-metric-label">Total Events</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value good">${analytics.successful_count || 0}</span>
                    <span class="analytics-metric-label">Successful</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value bad">${analytics.unknown_count || 0}</span>
                    <span class="analytics-metric-label">Unknown</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value">${analytics.corrections_caused || 0}</span>
                    <span class="analytics-metric-label">Corrections</span>
                </div>
                <div class="analytics-metric">
                    <span class="analytics-metric-value">${analytics.variation_count || 0}</span>
                    <span class="analytics-metric-label">Variations</span>
                </div>
            </div>
        `;
    }

    renderQualitySection(analytics, gesture) {
        if (!analytics) return '';
        const qs = analytics.quality_score || 0;
        const cls = qs >= 80 ? 'good' : (qs >= 60 ? 'warn' : 'bad');
        return `
            <div class="quality-score-box">
                <div>
                    <span class="quality-score-label">Overall Quality Score</span>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem;">
                        Based on recognition success, confidence, similarity, stability, and sample diversity.
                    </div>
                </div>
                <span class="quality-score-value ${cls}">${Number(qs).toFixed(1)}%</span>
            </div>
        `;
    }

    renderEvolutionSection(analytics) {
        const timeline = (analytics && analytics.evolution_timeline) || [];
        if (!timeline.length) {
            return '<div class="custom-empty-state">No timeline data yet. Recognition performance will be tracked over time.</div>';
        }
        return `
            <div class="evolution-timeline">
                ${timeline.map(entry => `
                    <div class="evolution-row">
                        <span class="evolution-date">${this.escape(entry.date || '')}</span>
                        <div class="evolution-bar-track">
                            <div class="evolution-bar-fill" style="width: ${Math.min(100, entry.success_rate || 0)}%"></div>
                        </div>
                        <span class="evolution-value">${Number(entry.success_rate || 0).toFixed(1)}%</span>
                        <span style="font-size: 0.7rem; color: var(--text-muted);">(${entry.event_count || 0})</span>
                    </div>
                `).join('')}
            </div>
        `;
    }

    dnaBarClass(value) {
        if (value >= 75) return 'high';
        if (value >= 45) return 'medium';
        return 'low';
    }

    renderModal(title, bodyHtml) {
        const overlay = document.getElementById('custom-modal-overlay');
        const titleEl = document.getElementById('custom-modal-title');
        const body = document.getElementById('custom-modal-body');
        if (!overlay || !titleEl || !body) return;
        titleEl.innerText = title;
        body.innerHTML = bodyHtml;
        overlay.hidden = false;
        overlay.style.display = 'flex';
        // Ensure close button works even if re-rendered or listener was lost
        const closeBtn = document.getElementById('btn-close-modal');
        if (closeBtn) {
            closeBtn.onclick = (e) => {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                this.closeModal();
            };
        }
        // Allow any button with data-action="close-modal" inside the modal to close it
        body.querySelectorAll('[data-action="close-modal"]').forEach(btn => {
            btn.addEventListener('click', () => this.closeModal());
        });
    }

    closeModal() {
        const overlay = document.getElementById('custom-modal-overlay');
        const body = document.getElementById('custom-modal-body');
        const titleEl = document.getElementById('custom-modal-title');
        if (overlay) {
            overlay.hidden = true;
            overlay.style.display = 'none';
        }
        if (body) body.innerHTML = '';
        if (titleEl) {
            // Reset title to default to avoid stale state on next open
            titleEl.innerText = 'Details';
        }
        // Restore page interaction and scrolling
        document.body.style.overflow = '';
        document.documentElement.style.overflow = '';
        const contentArea = document.getElementById('content-area');
        if (contentArea) {
            contentArea.style.overflow = '';
            contentArea.style.pointerEvents = '';
        }
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
        this.updateCoachInUI(runtime);
        this.updateCaptureCoach(runtime);
    }

    updateCoachInUI(runtime) {
        // Show inline coach feedback in the live/test result card
        const mode = runtime.mode || 'idle';
        if (mode !== 'test' && mode !== 'live') {
            this.hideCoachResult();
            return;
        }
        // Coach feedback is fetched asynchronously (not every frame)
        if (this._coachFetchTimer) return;
        const expectedId = runtime.expected_gesture_id || runtime.detected_gesture_id;
        if (!expectedId) {
            this.hideCoachResult();
            return;
        }
        // Only fetch every 3 seconds to avoid hammering the server
        if (this._lastCoachFetchTime && (Date.now() - this._lastCoachFetchTime) < 3000) return;
        this._lastCoachFetchTime = Date.now();
        this._coachFetchTimer = setTimeout(() => { this._coachFetchTimer = null; }, 200);

        this.request(`/api/custom-gestures/${encodeURIComponent(expectedId)}/coach`)
            .then(data => {
                const coach = data.coach || {};
                this.showCoachResult(coach, mode);
            })
            .catch(() => { /* silent */ });
    }

    showCoachResult(coach, mode) {
        let container = document.getElementById('coach-result-inline');
        if (!container) {
            const resultCard = document.querySelector('.custom-test-card .custom-live-result');
            if (!resultCard) return;
            container = document.createElement('div');
            container.id = 'coach-result-inline';
            container.className = 'coach-result-box';
            resultCard.appendChild(container);
        }
        const feedback = coach.feedback || '';
        const tips = coach.tips || [];
        const sim = coach.similarity;
        const matchLevel = coach.match_level;

        let html = '';
        if (feedback) {
            html += `<div class="coach-result-text">🎯 ${this.escape(feedback)}</div>`;
        }
        if (sim != null) {
            html += `<div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.2rem;">DNA similarity: <strong style="color: var(--accent-blue);">${Number(sim).toFixed(1)}%</strong> (${this.escape(matchLevel || '—')})</div>`;
        }
        if (tips.length) {
            html += `<div class="coach-result-tips">${tips.map(t => '→ ' + this.escape(t)).join('<br>')}</div>`;
        }
        container.innerHTML = html || '<div class="coach-result-text" style="color: var(--text-muted);">Show the gesture for coach feedback.</div>';
    }

    hideCoachResult() {
        const container = document.getElementById('coach-result-inline');
        if (container) container.innerHTML = '';
    }

    updateCaptureCoach(runtime) {
        // Show coach feedback during capture
        const mode = runtime.mode || 'idle';
        if (mode !== 'capture') {
            this.hideCaptureCoach();
            return;
        }
        if (this._captureCoachTimer) return;
        if (this._lastCaptureCoachTime && (Date.now() - this._lastCaptureCoachTime) < 3000) return;
        this._lastCaptureCoachTime = Date.now();
        this._captureCoachTimer = setTimeout(() => { this._captureCoachTimer = null; }, 200);

        this.request('/api/custom-gestures/capture/coach')
            .then(data => {
                const coach = data.coach || {};
                this.showCaptureCoach(coach);
            })
            .catch(() => { /* silent */ });
    }

    showCaptureCoach(coach) {
        let container = document.getElementById('capture-coach-inline');
        if (!container) {
            const progressCard = document.querySelector('.custom-progress-card');
            if (!progressCard) return;
            container = document.createElement('div');
            container.id = 'capture-coach-inline';
            container.className = 'coach-box';
            container.style.marginTop = '0.6rem';
            progressCard.appendChild(container);
        }
        const quality = coach.sample_quality;
        const issues = coach.issues || [];
        const guidance = coach.guidance || [];
        const qualityClass = (quality || 0) < 60 ? 'low' : '';

        let html = '<div class="coach-header"><span class="coach-icon">🎯</span><span class="coach-title">Capture Coach</span></div>';
        if (quality != null) {
            html += `<div class="coach-quality ${qualityClass}"><span class="coach-quality-label">Sample Quality</span><span class="coach-quality-value">${Number(quality).toFixed(1)}%</span></div>`;
        }
        if (issues.length) {
            html += `<ul class="coach-tips">${issues.map(i => `<li>${this.escape(i)}</li>`).join('')}</ul>`;
        }
        if (guidance.length) {
            html += `<div class="coach-feedback">${this.escape(guidance[0])}</div>`;
        }
        container.innerHTML = html;
    }

    hideCaptureCoach() {
        const container = document.getElementById('capture-coach-inline');
        if (container) container.innerHTML = '';
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
