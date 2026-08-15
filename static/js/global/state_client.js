class StateClient {
    constructor() {
        this.pollInterval = 100;
        this.init();
    }

    init() {
        this.bindEvents();
        this.startPolling();
    }

    bindEvents() {
        const camBtn = document.getElementById('btn-toggle-camera');
        const gestureBtn = document.getElementById('btn-toggle-gesture');

        if (camBtn) camBtn.addEventListener('click', () => this.toggleCamera());
        if (gestureBtn) gestureBtn.addEventListener('click', () => this.toggleGesture());
    }

    async toggleCamera() {
        try {
            const res = await fetch('/api/camera/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await res.json();
            if (!res.ok || data.status === 'error') {
                alert(data.message || 'Camera error');
            } else {
                this.updateUI(data);
            }
        } catch (err) {
            console.error('Toggle camera error:', err);
        }
    }

    async toggleGesture() {
        try {
            const res = await fetch('/api/gesture/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await res.json();
            if (!res.ok || data.status === 'error') {
                alert(data.message || 'Gesture error');
            } else {
                this.updateUI(data);
            }
        } catch (err) {
            console.error('Toggle gesture error:', err);
        }
    }

    startPolling() {
        setInterval(async () => {
            try {
                const res = await fetch('/api/state');
                const state = await res.json();
                this.updateUI(state);
            } catch (err) {}
        }, this.pollInterval);
    }

    updateUI(state) {
        const camBtn = document.getElementById('btn-toggle-camera');
        const gestureBtn = document.getElementById('btn-toggle-gesture');
        const fpsBadge = document.getElementById('status-fps');
        const handBadge = document.getElementById('status-hand');
        const gestureBadge = document.getElementById('status-gesture');
        const stateBadge = document.getElementById('status-interaction');
        const dwellContainer = document.getElementById('dwell-container');
        const dwellBar = document.getElementById('dwell-progress-bar');
        const dwellText = document.getElementById('dwell-progress-text');

        if (camBtn) {
            if (state.camera_enabled) {
                camBtn.classList.add('active');
                camBtn.innerHTML = '● CAMERA ON';
            } else {
                camBtn.classList.remove('active');
                camBtn.innerHTML = '○ CAMERA OFF';
            }
        }

        if (gestureBtn) {
            if (state.gesture_enabled) {
                gestureBtn.classList.add('active');
                gestureBtn.innerHTML = '✋ AIR GESTURE ON';
            } else {
                gestureBtn.classList.remove('active');
                gestureBtn.innerHTML = '🛑 AIR GESTURE OFF';
            }
        }

        if (fpsBadge && state.fps !== undefined) fpsBadge.innerText = `${state.fps} FPS`;
        if (handBadge) {
            handBadge.innerText = state.hand_detected ? 'HAND DETECTED' : 'NO HAND';
            handBadge.className = `badge ${state.hand_detected ? 'badge-success' : 'badge-danger'}`;
        }
        if (gestureBadge && state.gesture) gestureBadge.innerText = state.gesture;
        if (stateBadge && state.interaction_state) stateBadge.innerText = state.interaction_state;

        // Dwell UI bar
        if (dwellBar && dwellContainer && dwellText) {
            if (state.dwell_active || state.dwell_progress > 0) {
                dwellContainer.style.opacity = '1';
                dwellBar.style.width = `${state.dwell_progress}%`;
                dwellText.innerText = `${state.dwell_progress}%`;
                if (state.dwell_progress >= 100) {
                    dwellBar.style.backgroundColor = '#22c55e';
                } else {
                    dwellBar.style.backgroundColor = '#38bdf8';
                }
            } else {
                dwellContainer.style.opacity = '0';
                dwellBar.style.width = '0%';
                dwellText.innerText = '0%';
            }
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.stateClient = new StateClient();
});