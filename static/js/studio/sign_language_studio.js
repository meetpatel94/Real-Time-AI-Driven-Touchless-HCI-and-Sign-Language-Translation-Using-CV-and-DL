/**
 * Sign Language Studio Client Controller
 * Fixed Word Suggestions Click Flow & State Synchronization
 */

class StudioSignManager {
    constructor() {
        this.pollInterval = 120;
        this.isPolling = false;
        this.timeoutId = null;
        this.confidenceThreshold = 70.0;

        // Translation Tracker & Cache
        this.lastTranslatedSentence = null;
        this.lastTargetLang = "English";
        this.translationCache = new Map();
        this.manualDebounceTimer = null;

        // Word Suggestion Tracker & Cache
        this.lastSuggestedPrefix = null;
        this.suggestionCache = new Map();

        // DOM Cache
        this.signDisplay = document.getElementById('display-current-sign');
        this.confDisplay = document.getElementById('display-confidence-val');
        this.confBar = document.getElementById('display-confidence-bar') || document.querySelector('.overlay-conf-fill');
        this.statusDisplay = document.getElementById('display-prediction-status') || document.querySelector('.overlay-status-row strong');

        this.sentenceInput = document.getElementById('sentence-input-area');
        this.charCountElem = document.getElementById('char-count');
        this.wordCountElem = document.getElementById('word-count');
        this.suggestionsContainer = document.getElementById('suggestions-container');

        this.targetLangSelect = document.getElementById('select-target-lang');
        this.metaTargetLang = document.getElementById('meta-target-lang');
        this.translatedBox = document.getElementById('translated-output-box');
        this.translationStatus = document.getElementById('translation-status-text');

        this.init();
    }

    init() {
        this.bindEvents();
        this.startPolling();
        this.updateSuggestions("");
        window.addEventListener('beforeunload', () => this.stopPolling());
    }

    bindEvents() {
        const btnClear = document.getElementById('btn-clear-sentence');
        const btnBackspace = document.getElementById('btn-backspace-sentence');
        const qaClear = document.getElementById('qa-clear');
        const qaCopy = document.getElementById('qa-copy');

        if (btnClear) btnClear.addEventListener('click', () => this.clearSentence());
        if (qaClear) qaClear.addEventListener('click', () => this.clearSentence());
        if (btnBackspace) btnBackspace.addEventListener('click', () => this.postSentenceAction('backspace'));

        // Language Dropdown Auto-Trigger
        if (this.targetLangSelect) {
            this.targetLangSelect.addEventListener('change', (e) => {
                const newLang = e.target.value;
                if (this.metaTargetLang) this.metaTargetLang.innerText = newLang;
                this.lastTargetLang = newLang;
                this.onSentenceChanged(this.sentenceInput ? this.sentenceInput.value : "", true);
            });
        }

        // Manual Keystroke Debouncing
        if (this.sentenceInput) {
            this.sentenceInput.addEventListener('input', () => {
                this.updateCounts();
                const text = this.sentenceInput.value;
                this.onSentenceChanged(text);
                this.postSentenceAction('set', text);
                if (this.manualDebounceTimer) clearTimeout(this.manualDebounceTimer);
                this.manualDebounceTimer = setTimeout(() => {
                    this.triggerTranslation(this.sentenceInput.value);
                }, 350);
            });
        }

        if (qaCopy) {
            qaCopy.addEventListener('click', () => {
                const text = this.translatedBox ? this.translatedBox.innerText : (this.sentenceInput ? this.sentenceInput.value : '');
                if (text && text !== "Start signing...") {
                    navigator.clipboard.writeText(text).then(() => {
                        alert('Copied to clipboard!');
                    }).catch(() => {
                        alert('Copied: ' + text);
                    });
                }
            });
        }
    }

    clearSentence() {
        this.postSentenceAction('clear');
        if (this.sentenceInput) this.sentenceInput.value = '';
        if (this.translatedBox) this.translatedBox.innerText = 'Start signing...';
        if (this.translationStatus) {
            this.translationStatus.innerText = 'READY';
            this.translationStatus.className = 'text-accent-green';
        }
        this.lastTranslatedSentence = "";
        this.updateCounts();
        this.updateSuggestions("");
    }

    async postSentenceAction(action, text = '') {
        try {
            const res = await fetch('/api/studio/sentence/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, text })
            });
            const data = await res.json();
            if (this.sentenceInput && data.sentence !== undefined) {
                this.sentenceInput.value = data.sentence;
                this.updateCounts();
            }
        } catch (err) {}
    }

    /**
     * Extracts the active incomplete trailing word (ignoring complete trailing spaces)
     */
    extractCurrentWord(sentence) {
        if (!sentence) return "";
        if (sentence.endsWith(" ")) return "";
        const parts = sentence.trimEnd().split(/\s+/);
        return parts.length > 0 ? parts[parts.length - 1] : "";
    }

    /**
     * Dynamic Word Suggestion Query & DOM Renderer
     */
    async updateSuggestions(sentence) {
        const currentWord = this.extractCurrentWord(sentence);

        if (!currentWord) {
            this.renderSuggestionChips([]);
            this.lastSuggestedPrefix = "";
            return;
        }

        if (currentWord === this.lastSuggestedPrefix) {
            return;
        }
        this.lastSuggestedPrefix = currentWord;

        const cacheKey = currentWord.toUpperCase();
        if (this.suggestionCache.has(cacheKey)) {
            this.renderSuggestionChips(this.suggestionCache.get(cacheKey));
            return;
        }

        try {
            const res = await fetch(`/api/studio/suggestions?prefix=${encodeURIComponent(currentWord)}`);
            if (res.ok) {
                const data = await res.json();
                const suggestions = data.suggestions || [];
                this.suggestionCache.set(cacheKey, suggestions);
                if (currentWord === this.lastSuggestedPrefix) {
                    this.renderSuggestionChips(suggestions);
                }
            }
        } catch (err) {
            this.renderSuggestionChips([]);
        }
    }

    renderSuggestionChips(suggestions) {
        if (!this.suggestionsContainer) return;

        this.suggestionsContainer.innerHTML = '';

        if (!suggestions || suggestions.length === 0) {
            const emptyLabel = document.createElement('span');
            emptyLabel.style.color = 'var(--text-muted)';
            emptyLabel.style.fontSize = '0.78rem';
            emptyLabel.style.padding = '0.3rem 0';
            emptyLabel.innerText = this.lastSuggestedPrefix ? 'No matching words' : 'Type or sign letters...';
            this.suggestionsContainer.appendChild(emptyLabel);
            return;
        }

        suggestions.slice(0, 5).forEach(word => {
            const btn = document.createElement('button');
            btn.className = 'chip-btn-sm';
            btn.innerText = word;
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.applySuggestion(word);
            });
            this.suggestionsContainer.appendChild(btn);
        });
    }

    /**
     * Replaces the incomplete trailing word with the selected word and appends a trailing space
     */
    applySuggestion(selectedWord) {
        if (!this.sentenceInput) return;

        let currentText = this.sentenceInput.value;
        let newSentence = "";

        if (currentText.endsWith(" ") || !currentText.trim()) {
            newSentence = currentText.trimEnd() ? `${currentText.trimEnd()} ${selectedWord} ` : `${selectedWord} `;
        } else {
            const lastSpaceIdx = currentText.lastIndexOf(" ");
            if (lastSpaceIdx === -1) {
                newSentence = `${selectedWord} `;
            } else {
                newSentence = `${currentText.substring(0, lastSpaceIdx + 1)}${selectedWord} `;
            }
        }

        // 1. Update DOM textarea immediately
        this.sentenceInput.value = newSentence;

        // 2. Synchronize Backend State immediately to prevent polling overwrite
        this.postSentenceAction('set', newSentence);

        // 3. Update character and word counters immediately
        this.updateCounts();

        // 4. Trigger Real-Time Translation and Refresh Suggestions
        this.onSentenceChanged(newSentence);
    }

    /**
     * Centralized pipeline called whenever sentence text changes
     */
    onSentenceChanged(sentence, force = false) {
        this.updateSuggestions(sentence);
        this.triggerTranslation(sentence, force);
    }

    /**
     * Non-blocking Event-Driven Translation Pipeline with LRU Cache & Request Deduplication
     */
    async triggerTranslation(sentence, force = false) {
        const text = (sentence || "").trim();
        const targetLang = this.targetLangSelect ? this.targetLangSelect.value : this.lastTargetLang;

        if (!text) {
            if (this.translatedBox) this.translatedBox.innerText = 'Start signing...';
            if (this.translationStatus) {
                this.translationStatus.innerText = 'READY';
                this.translationStatus.className = 'text-accent-green';
            }
            this.lastTranslatedSentence = "";
            return;
        }

        if (!force && text === this.lastTranslatedSentence && targetLang === this.lastTargetLang) {
            return;
        }

        this.lastTranslatedSentence = text;
        this.lastTargetLang = targetLang;

        const cacheKey = `${text.toLowerCase()}::${targetLang.toLowerCase()}`;
        if (this.translationCache.has(cacheKey)) {
            if (this.translatedBox) this.translatedBox.innerText = this.translationCache.get(cacheKey);
            if (this.translationStatus) {
                this.translationStatus.innerText = 'UPDATED';
                this.translationStatus.className = 'text-accent-green';
            }
            return;
        }

        if (this.translationStatus) {
            this.translationStatus.innerText = 'TRANSLATING...';
            this.translationStatus.className = 'text-accent-warning';
        }

        try {
            const res = await fetch('/api/studio/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text, target_lang: targetLang })
            });

            if (res.ok) {
                const data = await res.json();
                const translated = data.translated_text || text;
                this.translationCache.set(cacheKey, translated);

                if (text === this.lastTranslatedSentence && targetLang === this.lastTargetLang) {
                    if (this.translatedBox) this.translatedBox.innerText = translated;
                    if (this.translationStatus) {
                        this.translationStatus.innerText = 'UPDATED';
                        this.translationStatus.className = 'text-accent-green';
                    }
                }
            }
        } catch (err) {
            if (this.translationStatus) {
                this.translationStatus.innerText = 'Translation unavailable';
                this.translationStatus.className = 'text-accent-danger';
            }
        }
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
            const res = await fetch('/api/studio/status');
            if (res.ok) {
                const recData = await res.json();
                this.updateStudioUI(recData);
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

    updateStudioUI(recData) {
        const leftHandDetected = recData.left_hand_detected === true || recData.hand_detected === true;
        const stablePred = recData.prediction || 'NONE';
        const rawPred = recData.raw_prediction || 'NONE';
        const stableConf = parseFloat(recData.confidence) || 0.0;
        const rawConf = parseFloat(recData.raw_confidence) || 0.0;

        if (!leftHandDetected) {
            this.renderPredictionState({
                letter: '--',
                confidence: 0.0,
                status: 'NO LEFT HAND',
                statusClass: 'text-accent-danger'
            });
        } else if (rawConf < this.confidenceThreshold || rawPred === 'UNKNOWN') {
            this.renderPredictionState({
                letter: '--',
                confidence: rawConf,
                status: 'LOW CONFIDENCE',
                statusClass: 'text-accent-warning'
            });
        } else if (stablePred === 'NONE' || stablePred === 'UNKNOWN') {
            this.renderPredictionState({
                letter: rawPred,
                confidence: rawConf,
                status: 'DETECTING',
                statusClass: 'text-accent-blue'
            });
        } else {
            let statusText = 'STABLE';
            let statusClass = 'text-accent-green';

            if (recData.confirmed_active) {
                statusText = `ADDED: ${recData.last_confirmed}`;
                statusClass = 'text-accent-green';
            } else if (recData.is_confirming) {
                statusText = 'CONFIRMING...';
                statusClass = 'text-accent-warning';
            }

            this.renderPredictionState({
                letter: stablePred,
                confidence: stableConf,
                status: statusText,
                statusClass: statusClass
            });
        }

        // Automatic trigger when right-hand fist commits a new letter
        if (this.sentenceInput && recData.sentence !== undefined) {
            if (this.sentenceInput.value !== recData.sentence) {
                this.sentenceInput.value = recData.sentence;
                this.updateCounts();
                this.onSentenceChanged(recData.sentence);
            }
        }
    }

    renderPredictionState({ letter, confidence, status, statusClass }) {
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
            if (status.includes('ADDED') || status === 'STABLE') {
                this.confBar.style.backgroundColor = 'var(--accent-green)';
            } else if (status === 'LOW CONFIDENCE' || status === 'NO LEFT HAND') {
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
});