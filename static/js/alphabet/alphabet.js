/**
 * Sign Alphabet Dataset Collection Controller
 */
class AlphabetManager {
    constructor() {
        this.selectedLetter = 'A';
        this.pollInterval = 400;
        this.letters = Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i));
        this.init();
    }

    init() {
        this.renderLetterButtons();
        this.bindEvents();
        this.fetchSummary();
        this.startPolling();
    }

    renderLetterButtons() {
        const container = document.getElementById('letter-selector-container');
        if (!container) return;

        container.innerHTML = '';
        this.letters.forEach(letter => {
            const btn = document.createElement('button');
            btn.className = `btn-letter ${letter === this.selectedLetter ? 'active' : ''}`;
            btn.innerText = letter;
            btn.dataset.letter = letter;
            btn.addEventListener('click', () => this.selectLetter(letter));
            container.appendChild(btn);
        });
    }

    bindEvents() {
        const btnStart = document.getElementById('btn-start-collection');
        const btnStop = document.getElementById('btn-stop-collection');
        const btnClear = document.getElementById('btn-clear-class');
        const selectTarget = document.getElementById('select-target-count');

        if (btnStart) btnStart.addEventListener('click', () => this.startCapture());
        if (btnStop) btnStop.addEventListener('click', () => this.stopCapture());
        if (btnClear) btnClear.addEventListener('click', () => this.clearClass());
        if (selectTarget) {
            selectTarget.addEventListener('change', (e) => this.setTarget(e.target.value));
        }
    }

    async selectLetter(letter) {
        this.selectedLetter = letter;
        document.querySelectorAll('.btn-letter').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.letter === letter);
        });
        document.getElementById('display-selected-letter').innerText = letter;

        try {
            await fetch('/api/alphabet/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ letter })
            });
            this.updateStatus();
        } catch (err) {
            console.error('Failed to select letter:', err);
        }
    }

    async startCapture() {
        try {
            const target = parseInt(document.getElementById('select-target-count').value, 10);
            const res = await fetch('/api/alphabet/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ letter: this.selectedLetter, target })
            });
            const data = await res.json();
            if (data.status === 'error') {
                alert(data.message);
            }
            this.updateStatus();
        } catch (err) {
            console.error('Failed to start capture:', err);
        }
    }

    async stopCapture() {
        try {
            await fetch('/api/alphabet/stop', { method: 'POST' });
            this.updateStatus();
        } catch (err) {
            console.error('Failed to stop capture:', err);
        }
    }

    async clearClass() {
        const confirmed = confirm(`Are you sure you want to permanently clear all dataset images for letter '${this.selectedLetter}'?`);
        if (!confirmed) return;

        try {
            await fetch('/api/alphabet/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ letter: this.selectedLetter })
            });
            this.updateStatus();
            this.fetchSummary();
        } catch (err) {
            console.error('Failed to clear class:', err);
        }
    }

    async setTarget(target) {
        try {
            await fetch('/api/alphabet/set_target', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target: parseInt(target, 10) })
            });
            this.updateStatus();
            this.fetchSummary();
        } catch (err) {
            console.error('Failed to update target:', err);
        }
    }

    startPolling() {
        setInterval(() => {
            this.updateStatus();
        }, this.pollInterval);

        setInterval(() => {
            this.fetchSummary();
        }, this.pollInterval * 4);
    }

    async updateStatus() {
        try {
            const res = await fetch('/api/alphabet/status');
            const data = await res.json();

            const btnStart = document.getElementById('btn-start-collection');
            const btnStop = document.getElementById('btn-stop-collection');
            const statusPill = document.getElementById('capture-status-pill');
            const progressText = document.getElementById('display-class-progress');
            const progressBar = document.getElementById('class-progress-bar');
            const statusMsg = document.getElementById('capture-message');

            if (data.is_collecting) {
                if (btnStart) btnStart.style.display = 'none';
                if (btnStop) btnStop.style.display = 'flex';
                if (statusPill) {
                    statusPill.innerText = 'COLLECTING';
                    statusPill.className = 'badge badge-success';
                }
            } else {
                if (btnStart) btnStart.style.display = 'flex';
                if (btnStop) btnStop.style.display = 'none';
                if (statusPill) {
                    statusPill.innerText = data.is_completed ? 'COMPLETE' : 'IDLE';
                    statusPill.className = `badge ${data.is_completed ? 'badge-success' : 'badge-danger'}`;
                }
            }

            if (progressText) {
                progressText.innerText = `${data.count} / ${data.target} (${data.percentage}%)`;
            }

            if (progressBar) {
                progressBar.style.width = `${data.percentage}%`;
                progressBar.classList.toggle('done', data.is_completed);
            }

            if (statusMsg && data.status_message) {
                statusMsg.innerText = data.status_message;
            }
        } catch (err) {}
    }

    async fetchSummary() {
        try {
            const res = await fetch('/api/alphabet/summary');
            const data = await res.json();

            const matrixContainer = document.getElementById('summary-matrix-container');
            const totalText = document.getElementById('display-total-dataset');
            const totalBar = document.getElementById('total-progress-bar');

            if (totalText) {
                const totalPct = ((data.total_images / data.total_target) * 100).toFixed(1);
                totalText.innerText = `${data.total_images.toLocaleString()} / ${data.total_target.toLocaleString()} (${totalPct}%)`;
            }

            if (totalBar) {
                const totalPct = (data.total_images / data.total_target) * 100;
                totalBar.style.width = `${Math.min(100, totalPct)}%`;
            }

            if (matrixContainer && data.counts) {
                matrixContainer.innerHTML = '';
                this.letters.forEach(letter => {
                    const count = data.counts[letter] || 0;
                    const isDone = count >= data.target_per_class;

                    const item = document.createElement('div');
                    item.className = `matrix-item ${isDone ? 'complete' : ''}`;
                    item.innerHTML = `
                        <span class="matrix-letter">${letter}</span>
                        <span class="matrix-count">${count}/${data.target_per_class}</span>
                    `;
                    matrixContainer.appendChild(item);

                    // Update completed indicator on letter buttons
                    const btn = document.querySelector(`.btn-letter[data-letter="${letter}"]`);
                    if (btn) btn.classList.toggle('completed', isDone);
                });
            }
        } catch (err) {}
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.alphabetManager = new AlphabetManager();
});