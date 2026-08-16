/**
 * Sign Language Studio Client Controller
 * Dual-Hand Live Sign Recognition & Sentence Management
 */

class StudioSignManager {
    constructor() {
        this.pollInterval = 120; // ~8 req/sec
        this.isPolling = false;
        this.timeoutId = null;
        this.confidenceThreshold = 70.0;
        
        // DOM Cache
        this.signDisplay = document.getElementById('display-current-sign');
        this.confDisplay = document.getElementById('display-confidence-val');
        this.confBar = document.getElementById('display-confidence-bar') || document.querySelector('.overlay-conf-fill');
        this.statusDisplay = document.getElementById('display-prediction-status') || document.querySelector('.overlay-status-row strong');
        
        this.sentenceInput = document.getElementById('sentence-input-area');
        this.charCountElem = document.getElementById('char-count');
        this.wordCountElem = document.getElementById('word-count');

        this.init();
    }

    init() {
        this.bindEvents();
        this.startPolling();
        window.addEventListener('beforeunload', () => this.stopPolling());
    }

    bindEvents() {
        const btnClear = document.getElementById('btn-clear-sentence');
        const btnBackspace = document.getElementById('btn-backspace-sentence');

        if (btnClear) {
            btnClear.addEventListener('click', () => this.postSentenceAction('clear'));
        }

        if (btnBackspace) {
            btnBackspace.addEventListener('click', () => this.postSentenceAction('backspace'));
        }
        
        const suggestionChips = document.querySelectorAll('.chip-btn, .chip-btn-sm, .chip-btn-vertical');
        suggestionChips.forEach(chip => {
            chip.addEventListener('click', () => {
                if (!this.sentenceInput) return;
                const current = this.sentenceInput.value.trim();
                const word = chip.innerText.replace('→', '').trim();
                this.sentenceInput.value = current ? `${current} ${word}` : word;
                this.updateCounts();
            });
        });
    }

    async postSentenceAction(action) {
        try {
            const res = await fetch('/api/studio/sentence/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            const data = await res.json();
            if (this.sentenceInput && data.sentence !== undefined) {
                this.sentenceInput.value = data.sentence;
                this.updateCounts();
            }
        } catch (err) {}
    }

    startPolling() {
        if (this.isPolling) return;
        this.isPolling = true;
        this.fetchStatus();
    }

    stopPolling() {
        this.isPolling = false;
        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
    }

    async fetchStatus() {
        if (!this.isPolling) return;

        try {
            const [recRes, stateRes] = await Promise.all([
                fetch('/api/studio/status'),
                fetch('/api/state')
            ]);

            if (recRes.ok && stateRes.ok) {
                const recData = await recRes.json();
                const globalState = await stateRes.json();
                this.updateStudioUI(recData, globalState);
            }
        } catch (err) {
            this.renderPredictionState({
                letter: '--',
                confidence: 0.0,
                status: 'WAITING',
                statusClass: 'text-accent-muted'
            });
        } finally {
            if (this.isPolling) {
                this.timeoutId = setTimeout(() => this.fetchStatus(), this.pollInterval);
            }
        }
    }

    updateStudioUI(recData, globalState) {
        const cameraOn = globalState.camera_enabled === true;
        // Supports both left_hand_detected and hand_detected
        const leftHandDetected = recData.left_hand_detected === true || recData.hand_detected === true;
        const stablePred = recData.prediction || 'NONE';
        const rawPred = recData.raw_prediction || 'NONE';
        const stableConf = parseFloat(recData.confidence) || 0.0;
        const rawConf = parseFloat(recData.raw_confidence) || 0.0;

        // 1. Camera is OFF
        if (!cameraOn) {
            this.renderPredictionState({
                letter: '--', confidence: 0.0, status: 'WAITING', statusClass: 'text-accent-muted'
            });
        } else if (!leftHandDetected) {
            // 2. Left Hand Not Detected
            this.renderPredictionState({
                letter: '--', confidence: 0.0, status: 'NO HAND', statusClass: 'text-accent-danger'
            });
        } else if (rawConf < this.confidenceThreshold || rawPred === 'UNKNOWN') {
            // 3. Low Confidence
            this.renderPredictionState({
                letter: '--', confidence: rawConf, status: 'LOW CONFIDENCE', statusClass: 'text-accent-warning'
            });
        } else if (stablePred === 'NONE' || stablePred === 'UNKNOWN') {
            // 4. Transitioning / Stabilizing
            this.renderPredictionState({
                letter: rawPred, confidence: rawConf, status: 'DETECTING', statusClass: 'text-accent-blue'
            });
        } else {
            // 5. Stable Prediction
            this.renderPredictionState({
                letter: stablePred, confidence: stableConf, status: 'STABLE', statusClass: 'text-accent-green'
            });
        }

        // Auto-sync sentence updated by right-hand fist commits
        if (this.sentenceInput && recData.sentence !== undefined) {
            if (this.sentenceInput.value !== recData.sentence) {
                this.sentenceInput.value = recData.sentence;
                this.updateCounts();
            }
        }
    }

    renderPredictionState({ letter, confidence, status, statusClass }) {
        const clampedConf = Math.max(0.0, Math.min(100.0, confidence));

        if (this.signDisplay) {
            this.signDisplay.innerText = letter;
            this.signDisplay.style.color = (letter === '--') ? 'var(--text-muted)' : 'var(--accent-blue)';
        }
        if (this.confDisplay) this.confDisplay.innerText = `${clampedConf.toFixed(1)}%`;
        if (this.confBar) {
            this.confBar.style.width = `${clampedConf}%`;
            if (status === 'STABLE') this.confBar.style.backgroundColor = 'var(--accent-green)';
            else if (status === 'LOW CONFIDENCE') this.confBar.style.backgroundColor = 'var(--accent-red)';
            else this.confBar.style.backgroundColor = 'var(--accent-blue)';
        }
        if (this.statusDisplay) {
            this.statusDisplay.innerText = status;
            this.statusDisplay.className = statusClass;
        }
    }

    updateCounts() {
        if (!this.sentenceInput) return;
        const text = this.sentenceInput.value;
        const chars = text.length;
        const words = text.trim() ? text.trim().split(/\s+/).length : 0;
        if (this.charCountElem) this.charCountElem.innerText = chars;
        if (this.wordCountElem) this.wordCountElem.innerText = words;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.studioSignManager = new StudioSignManager();

    const targetLangSelect = document.getElementById('select-target-lang');
    const metaTargetLang = document.getElementById('meta-target-lang');
    const translatedBox = document.getElementById('translated-output-box');
    const sentenceInput = document.getElementById('sentence-input-area');

    if (targetLangSelect && metaTargetLang) {
        targetLangSelect.addEventListener('change', (e) => {
            metaTargetLang.innerText = e.target.value;
        });
    }

    const qaClear = document.getElementById('qa-clear');
    const qaTranslate = document.getElementById('qa-translate');
    const qaCopy = document.getElementById('qa-copy');
    const btnTranslate = document.getElementById('btn-trigger-translate');

    function mockTranslate() {
        if (!sentenceInput || !translatedBox) return;
        const text = sentenceInput.value.trim();
        translatedBox.innerText = text ? text : 'No text to translate';
    }

    if (qaTranslate) qaTranslate.addEventListener('click', mockTranslate);
    if (btnTranslate) btnTranslate.addEventListener('click', mockTranslate);

    if (qaClear) {
        qaClear.addEventListener('click', () => {
            if (window.studioSignManager) {
                window.studioSignManager.postSentenceAction('clear');
            }
        });
    }

    if (qaCopy) {
        qaCopy.addEventListener('click', () => {
            if (!sentenceInput) return;
            navigator.clipboard.writeText(sentenceInput.value).then(() => {
                alert('Sentence copied to clipboard!');
            }).catch(() => {
                alert('Copied: ' + sentenceInput.value);
            });
        });
    }
});