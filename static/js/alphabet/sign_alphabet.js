/**
 * Sign Alphabet Dataset Management & Real-time Training Dashboard Controller
 */
class SignAlphabetManager {
    constructor() {
        this.pollInterval = 750;
        this.init();
    }

    init() {
        this.fetchDatasetInfo();
        this.fetchModelInfo();
        this.bindEvents();
        this.startPolling();
    }

    bindEvents() {
        const btnStart = document.getElementById('btn-start-train');
        const btnStop = document.getElementById('btn-stop-train');

        if (btnStart) btnStart.addEventListener('click', () => this.startTraining());
        if (btnStop) btnStop.addEventListener('click', () => this.stopTraining());
    }

    async fetchDatasetInfo() {
        try {
            const res = await fetch('/sign-alphabet/dataset-info');
            const data = await res.json();

            document.getElementById('stat-total-images').innerText = data.total_images.toLocaleString();
            document.getElementById('stat-total-classes').innerText = data.total_classes;
            document.getElementById('stat-split-images').innerText = `${data.training_images.toLocaleString()} / ${data.validation_images.toLocaleString()}`;
            document.getElementById('stat-dataset-size').innerText = `${data.dataset_size_mb} MB`;
            document.getElementById('classes-badge').innerText = `${data.total_classes} CLASSES DETECTED`;

            this.renderClassesGrid(data.classes);
        } catch (err) {
            console.error('Failed to load dataset info:', err);
        }
    }

    renderClassesGrid(classesDict) {
        const container = document.getElementById('classes-grid-container');
        if (!container || !classesDict) return;

        container.innerHTML = '';
        Object.keys(classesDict).sort().forEach(letter => {
            const count = classesDict[letter];
            const card = document.createElement('div');
            card.className = 'class-card';
            card.innerHTML = `
                <img class="class-preview-img" src="/sign-alphabet/class-preview/${letter}" alt="${letter}" onerror="this.src='/static/assets/placeholder.png';">
                <span class="class-letter">${letter}</span>
                <span class="class-count">${count} imgs</span>
            `;
            container.appendChild(card);
        });
    }

    async fetchModelInfo() {
        try {
            const res = await fetch('/sign-alphabet/model-info');
            const data = await res.json();

            const statusElem = document.getElementById('stat-model-status');
            const exportBtn = document.getElementById('btn-export-model');

            if (data.model_exists) {
                statusElem.innerText = `TRAINED (${data.model_size_mb} MB)`;
                statusElem.style.color = 'var(--accent-green)';
                exportBtn.style.pointerEvents = 'auto';
                exportBtn.style.opacity = '1';
            } else {
                statusElem.innerText = 'NOT TRAINED';
                statusElem.style.color = 'var(--text-muted)';
                exportBtn.style.pointerEvents = 'none';
                exportBtn.style.opacity = '0.5';
            }
        } catch (err) {
            console.error('Failed to fetch model info:', err);
        }
    }

    async startTraining() {
        const epochs = parseInt(document.getElementById('input-epochs').value, 10) || 15;
        try {
            const res = await fetch('/sign-alphabet/start-training', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ epochs })
            });
            const data = await res.json();
            if (!res.ok) {
                alert(data.message || 'Failed to start training');
            }
            this.updateTrainingStatus();
        } catch (err) {
            console.error('Error starting training:', err);
        }
    }

    async stopTraining() {
        try {
            await fetch('/sign-alphabet/stop-training', { method: 'POST' });
            this.updateTrainingStatus();
        } catch (err) {
            console.error('Error stopping training:', err);
        }
    }

    startPolling() {
        setInterval(() => this.updateTrainingStatus(), this.pollInterval);
    }

    formatTime(seconds) {
        const m = Math.floor(seconds / 60).toString().padStart(2, '0');
        const s = (seconds % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
    }

    async updateTrainingStatus() {
        try {
            const res = await fetch('/sign-alphabet/training-status');
            const state = await res.json();

            const btnStart = document.getElementById('btn-start-train');
            const btnStop = document.getElementById('btn-stop-train');
            const statusText = document.getElementById('train-status-text');
            const epochText = document.getElementById('train-epoch-text');
            const progressBar = document.getElementById('train-progress-bar');
            const devicePill = document.getElementById('device-pill');

            if (devicePill && state.device) {
                devicePill.innerText = `DEVICE: ${state.device}`;
            }

            if (statusText) statusText.innerText = state.status;
            if (epochText) epochText.innerText = `Epoch ${state.current_epoch} / ${state.total_epochs}`;
            if (progressBar) progressBar.style.width = `${state.progress_percent}%`;

            document.getElementById('metric-train-acc').innerText = `${state.train_accuracy}%`;
            document.getElementById('metric-val-acc').innerText = `${state.val_accuracy}%`;
            document.getElementById('metric-train-loss').innerText = state.train_loss;
            document.getElementById('metric-val-loss').innerText = state.val_loss;

            document.getElementById('time-elapsed').innerText = this.formatTime(state.elapsed_seconds || 0);
            document.getElementById('time-remaining').innerText = this.formatTime(state.estimated_remaining_seconds || 0);

            if (state.status === 'TRAINING' || state.status === 'PREPARING') {
                btnStart.style.display = 'none';
                btnStop.style.display = 'block';
                progressBar.style.backgroundColor = 'var(--accent-blue)';
            } else {
                btnStart.style.display = 'block';
                btnStop.style.display = 'none';
                if (state.status === 'COMPLETED') {
                    progressBar.style.backgroundColor = 'var(--accent-green)';
                    this.fetchModelInfo();
                } else if (state.status === 'ERROR') {
                    progressBar.style.backgroundColor = 'var(--accent-red)';
                }
            }
        } catch (err) {}
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.signAlphabetManager = new SignAlphabetManager();
});