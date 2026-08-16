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

        if (camBtn) {
            const camIcon = camBtn.querySelector('.btn-toggle-icon');
            const camText = camBtn.querySelector('.btn-toggle-text');

            if (state.camera_enabled) {
                camBtn.classList.add('active');
                if (camIcon) camIcon.innerText = '●';
                if (camText) camText.innerText = 'CAMERA ON';
            } else {
                camBtn.classList.remove('active');
                if (camIcon) camIcon.innerText = '○';
                if (camText) camText.innerText = 'CAMERA OFF';
            }
        }

        if (gestureBtn) {
            const gestureIcon = gestureBtn.querySelector('.btn-toggle-icon');
            const gestureText = gestureBtn.querySelector('.btn-toggle-text');

            if (state.gesture_enabled) {
                gestureBtn.classList.add('active');
                if (gestureIcon) gestureIcon.innerText = '✋';
                if (gestureText) gestureText.innerText = 'AIR GESTURE ON';
            } else {
                gestureBtn.classList.remove('active');
                if (gestureIcon) gestureIcon.innerText = '🛑';
                if (gestureText) gestureText.innerText = 'AIR GESTURE OFF';
            }
        }

        if (fpsBadge && state.fps !== undefined) fpsBadge.innerText = `${state.fps} FPS`;
        if (handBadge) {
            handBadge.innerText = state.hand_detected ? 'HAND DETECTED' : 'NO HAND';
            handBadge.className = `badge ${state.hand_detected ? 'badge-success' : 'badge-danger'}`;
        }
        if (gestureBadge && state.gesture) gestureBadge.innerText = state.gesture;
    }

}

document.addEventListener('DOMContentLoaded', () => {
    window.stateClient = new StateClient();
});