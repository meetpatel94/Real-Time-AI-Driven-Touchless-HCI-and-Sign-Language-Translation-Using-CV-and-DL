/**
 * Sign Language Studio Client Controller
 * Dynamic Current Sign Real-Time Integration & UI Handlers
 */

class StudioSignManager {
    constructor() {
        this.pollInterval = 120; // ~8 requests/sec
        this.isPolling = false;
        this.timeoutId = null;
        this.confidenceThreshold = 70.0;
        
        // DOM Cache
        this.signDisplay = document.getElementById('display-current-sign');
        this.confDisplay = document.getElementById('display-confidence-val');
        this.confBar = document.getElementById('display-confidence-bar') || document.querySelector('.overlay-conf-fill');
        this.statusDisplay = document.getElementById('display-prediction-status') || document.querySelector('.overlay-status-row strong');

        this.init();
    }

    init() {
        this.startPolling();
        window.addEventListener('beforeunload', () => this.stopPolling());
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
                fetch('/api/recognition/status'),
                fetch('/api/state')
            ]);

            if (recRes.ok && stateRes.ok) {
                const recData = await recRes.json();
                const globalState = await stateRes.json();
                this.updateCurrentSignUI(recData, globalState);
            }
        } catch (err) {
            this.renderState({
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

    updateCurrentSignUI(recData, globalState) {
        const cameraOn = globalState.camera_enabled === true;
        const handDetected = recData.hand_detected === true;
        const stablePred = recData.prediction || 'NONE';
        const rawPred = recData.raw_prediction || 'NONE';
        const stableConf = parseFloat(recData.confidence) || 0.0;
        const rawConf = parseFloat(recData.raw_confidence) || 0.0;

        // 1. Camera is OFF
        if (!cameraOn) {
            this.renderState({
                letter: '--',
                confidence: 0.0,
                status: 'WAITING',
                statusClass: 'text-accent-muted'
            });
            return;
        }

        // 2. Camera ON + No Hand
        if (!handDetected) {
            this.renderState({
                letter: '--',
                confidence: 0.0,
                status: 'NO HAND',
                statusClass: 'text-accent-danger'
            });
            return;
        }

        // 3. Hand Detected + Low Confidence (< threshold)
        if (rawConf < this.confidenceThreshold || rawPred === 'UNKNOWN') {
            this.renderState({
                letter: '--',
                confidence: rawConf,
                status: 'LOW CONFIDENCE',
                statusClass: 'text-accent-warning'
            });
            return;
        }

        // 4. Hand Detected + Transitioning/Stabilizing
        if (stablePred === 'NONE' || stablePred === 'UNKNOWN') {
            this.renderState({
                letter: rawPred,
                confidence: rawConf,
                status: 'DETECTING',
                statusClass: 'text-accent-blue'
            });
            return;
        }

        // 5. Stable Recognized Sign
        this.renderState({
            letter: stablePred,
            confidence: stableConf,
            status: 'STABLE',
            statusClass: 'text-accent-green'
        });
    }

    renderState({ letter, confidence, status, statusClass }) {
        const clampedConf = Math.max(0.0, Math.min(100.0, confidence));

        if (this.signDisplay) {
            this.signDisplay.innerText = letter;
            this.signDisplay.style.color = (letter === '--') ? 'var(--text-muted)' : 'var(--accent-blue)';
        }

        if (this.confDisplay) {
            this.confDisplay.innerText = `${clampedConf.toFixed(1)}%`;
        }

        if (this.confBar) {
            this.confBar.style.width = `${clampedConf}%`;
            if (status === 'STABLE') {
                this.confBar.style.backgroundColor = 'var(--accent-green)';
            } else if (status === 'LOW CONFIDENCE') {
                this.confBar.style.backgroundColor = 'var(--accent-red)';
            } else {
                this.confBar.style.backgroundColor = 'var(--accent-blue)';
            }
        }

        if (this.statusDisplay) {
            this.statusDisplay.innerText = status;
            this.statusDisplay.className = statusClass;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.studioSignManager = new StudioSignManager();

    // Sentence & Suggestion UI Handlers
    const sentenceInput = document.getElementById('sentence-input-area');
    const charCountElem = document.getElementById('char-count');
    const wordCountElem = document.getElementById('word-count');
    const targetLangSelect = document.getElementById('select-target-lang');
    const metaTargetLang = document.getElementById('meta-target-lang');
    const translatedBox = document.getElementById('translated-output-box');

    function updateCounts() {
        if (!sentenceInput) return;
        const text = sentenceInput.value;
        const chars = text.length;
        const words = text.trim() ? text.trim().split(/\s+/).length : 0;
        if (charCountElem) charCountElem.innerText = chars;
        if (wordCountElem) wordCountElem.innerText = words;
    }

    const suggestionChips = document.querySelectorAll('.chip-btn, .chip-btn-sm, .chip-btn-vertical');
    suggestionChips.forEach(chip => {
        chip.addEventListener('click', () => {
            if (!sentenceInput) return;
            const current = sentenceInput.value.trim();
            const word = chip.innerText.replace('→', '').trim();
            sentenceInput.value = current ? `${current} ${word}` : word;
            updateCounts();
        });
    });

    const btnClear = document.getElementById('btn-clear-sentence');
    const btnBackspace = document.getElementById('btn-backspace-sentence');

    if (btnClear) {
        btnClear.addEventListener('click', () => {
            if (sentenceInput) {
                sentenceInput.value = '';
                updateCounts();
            }
        });
    }

    if (btnBackspace) {
        btnBackspace.addEventListener('click', () => {
            if (sentenceInput && sentenceInput.value.length > 0) {
                sentenceInput.value = sentenceInput.value.slice(0, -1);
                updateCounts();
            }
        });
    }

    if (targetLangSelect && metaTargetLang) {
        targetLangSelect.addEventListener('change', (e) => {
            metaTargetLang.innerText = e.target.value;
        });
    }

    const qaClear = document.getElementById('qa-clear');
    const qaTranslate = document.getElementById('qa-translate');
    const qaCopy = document.getElementById('qa-copy');
    const btnTranslate = document.getElementById('btn-trigger-translate');

    if (qaClear) {
        qaClear.addEventListener('click', () => {
            if (sentenceInput) {
                sentenceInput.value = '';
                updateCounts();
            }
        });
    }

    function mockTranslate() {
        if (!sentenceInput || !translatedBox) return;
        const text = sentenceInput.value.trim();
        translatedBox.innerText = text ? text : 'No text to translate';
    }

    if (qaTranslate) qaTranslate.addEventListener('click', mockTranslate);
    if (btnTranslate) btnTranslate.addEventListener('click', mockTranslate);

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

    updateCounts();
});