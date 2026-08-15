/**
 * Optimized Sign Recognition Client
 */
class SignRecognitionManager {
    constructor() {
        this.pollInterval = 120; // ~8 requests/sec
        this.selectedLetter = 'A';
        this.letters = Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i));
        this.init();
    }

    init() {
        this.renderLetterPicker();
        this.bindEvents();
        this.startPolling();
    }

    renderLetterPicker() {
        const container = document.getElementById('letter-picker-container');
        if (!container) return;

        container.innerHTML = '';
        this.letters.forEach(letter => {
            const btn = document.createElement('button');
            btn.className = `btn-pick-letter ${letter === this.selectedLetter ? 'active' : ''}`;
            btn.innerText = letter;
            btn.addEventListener('click', () => this.selectLetter(letter));
            container.appendChild(btn);
        });
    }

    selectLetter(letter) {
        this.selectedLetter = letter;
        document.querySelectorAll('.btn-pick-letter').forEach(btn => {
            btn.classList.toggle('active', btn.innerText === letter);
        });
        document.getElementById('compare-expected').innerText = letter;
        this.updateComparisonMatch();
    }

    bindEvents() {
        const btnMark = document.getElementById('btn-mark-test');
        const btnReset = document.getElementById('btn-reset-test');

        if (btnMark) btnMark.addEventListener('click', () => this.markTest());
        if (btnReset) btnReset.addEventListener('click', () => this.resetTests());

        window.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && e.target.tagName !== 'INPUT') {
                e.preventDefault();
                this.markTest();
            }
        });
    }

    async markTest() {
        try {
            const res = await fetch('/api/recognition/mark-test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ expected: this.selectedLetter })
            });
            const data = await res.json();
            this.updateStatsUI(data.stats);
        } catch (err) {
            console.error('Failed to mark test:', err);
        }
    }

    async resetTests() {
        try {
            const res = await fetch('/api/recognition/reset-tests', { method: 'POST' });
            const data = await res.json();
            this.updateStatsUI(data.stats);
        } catch (err) {
            console.error('Failed to reset tests:', err);
        }
    }

    updateStatsUI(stats) {
        if (!stats) return;
        document.getElementById('test-stat-total').innerText = stats.total;
        document.getElementById('test-stat-correct').innerText = stats.correct;
        document.getElementById('test-stat-incorrect').innerText = stats.incorrect;
        document.getElementById('test-stat-accuracy').innerText = `${stats.accuracy}%`;
    }

    startPolling() {
        setInterval(async () => {
            try {
                const res = await fetch('/api/recognition/status');
                const data = await res.json();
                this.updateUI(data);
            } catch (err) {}
        }, this.pollInterval);
    }

    updateUI(state) {
        const badgeModel = document.getElementById('badge-model');
        const badgeHand = document.getElementById('badge-hand');
        const valModel = document.getElementById('val-model-name');
        const valCamFps = document.getElementById('val-cam-fps');
        const valInfFps = document.getElementById('val-inf-fps');

        if (badgeModel) {
            badgeModel.innerText = `MODEL: ${state.model_status}`;
            badgeModel.className = `badge ${state.model_status === 'READY' ? 'badge-success' : 'badge-danger'}`;
        }
        if (badgeHand) {
            badgeHand.innerText = state.hand_detected ? 'HAND DETECTED' : 'NO HAND';
            badgeHand.className = `badge ${state.hand_detected ? 'badge-success' : 'badge-danger'}`;
        }
        if (valModel) valModel.innerText = state.model_name || 'None';
        if (valCamFps) valCamFps.innerText = `${state.camera_fps || 0} FPS`;
        if (valInfFps) valInfFps.innerText = `${state.inference_fps || 0} FPS`;

        const predLetter = document.getElementById('live-prediction-letter');
        const predConf = document.getElementById('live-prediction-conf');

        if (predLetter) {
            predLetter.innerText = state.prediction || 'NONE';
            predLetter.style.color = state.prediction === 'UNKNOWN' ? 'var(--accent-red)' : 'var(--accent-blue)';
        }
        if (predConf) {
            predConf.innerText = `${state.confidence || 0.0}%`;
        }

        const comparePred = document.getElementById('compare-predicted');
        if (comparePred) comparePred.innerText = state.prediction || 'NONE';
        this.updateComparisonMatch(state.prediction, state.confidence);

        this.renderHistory(state.recent_predictions);
        if (state.test_stats) this.updateStatsUI(state.test_stats);
    }

    updateComparisonMatch(predicted, confidence) {
        const matchBadge = document.getElementById('compare-result');
        if (!matchBadge) return;

        predicted = predicted || document.getElementById('compare-predicted').innerText;
        confidence = confidence !== undefined ? confidence : 0;

        if (!predicted || predicted === 'NONE' || predicted === 'UNKNOWN') {
            matchBadge.innerText = '—';
            matchBadge.className = 'compare-badge badge';
        } else if (predicted === this.selectedLetter && confidence >= 70.0) {
            matchBadge.innerText = '✓ CORRECT';
            matchBadge.className = 'compare-badge badge badge-success';
        } else {
            matchBadge.innerText = '✗ INCORRECT';
            matchBadge.className = 'compare-badge badge badge-danger';
        }
    }

    renderHistory(history) {
        const container = document.getElementById('history-list-container');
        if (!container || !history) return;

        if (history.length === 0) {
            container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem; padding: 0.5rem 0;">No stable predictions recorded yet.</div>';
            return;
        }

        container.innerHTML = '';
        history.forEach(item => {
            const row = document.createElement('div');
            row.className = 'history-item';
            row.innerHTML = `
                <span class="history-label">${item.label}</span>
                <span class="history-conf">${item.confidence}%</span>
                <span style="color: var(--text-muted); font-size: 0.75rem;">${item.time}</span>
            `;
            container.appendChild(row);
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.signRecognitionManager = new SignRecognitionManager();
});