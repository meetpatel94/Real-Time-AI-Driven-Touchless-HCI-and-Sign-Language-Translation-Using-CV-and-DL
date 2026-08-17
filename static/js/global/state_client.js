/**
 * Global State, Sensitivity & Scroll Controls Client
 */
class StateClient {
    constructor() {
        this.pollInterval = 500;
        this.init();
    }

    init() {
        this.bindEvents();
        this.initSensitivity();
        this.initScrollSensitivity();
        this.startPolling();
    }

    bindEvents() {
        const camBtn = document.getElementById('btn-toggle-camera');
        const gestureBtn = document.getElementById('btn-toggle-gesture');

        if (camBtn) camBtn.addEventListener('click', () => this.toggleCamera());
        if (gestureBtn) gestureBtn.addEventListener('click', () => this.toggleGesture());
    }

    initSensitivity() {
        const slider = document.getElementById('slider-cursor-sensitivity');
        const display = document.getElementById('sensitivity-display');

        const saved = localStorage.getItem('cursor_sensitivity') || '50';
        if (slider && display) {
            slider.value = saved;
            display.innerText = `${saved}%`;
            this.sendSensitivity(parseFloat(saved) / 100.0);

            slider.addEventListener('input', (e) => {
                const val = e.target.value;
                display.innerText = `${val}%`;
                localStorage.setItem('cursor_sensitivity', val);
                this.sendSensitivity(parseFloat(val) / 100.0);
            });
        }
    }

    initScrollSensitivity() {
        const select = document.getElementById('select-scroll-sensitivity');
        const saved = localStorage.getItem('scroll_sensitivity') || 'medium';
        if (select) {
            select.value = saved;
            this.sendScrollSensitivity(saved);

            select.addEventListener('change', (e) => {
                const level = e.target.value;
                localStorage.setItem('scroll_sensitivity', level);
                this.sendScrollSensitivity(level);
            });
        }
    }

    async sendSensitivity(val) {
        try {
            await fetch('/api/mouse/sensitivity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sensitivity: val })
            });
        } catch (err) {}
    }

    async sendScrollSensitivity(level) {
        try {
            await fetch('/api/mouse/scroll-sensitivity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level })
            });
        } catch (err) {}
    }

    async toggleCamera() {
        try {
            const res = await fetch('/api/camera/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await res.json();
            this.updateUI(data);
        } catch (err) {}
    }

    async toggleGesture() {
        try {
            const res = await fetch('/api/gesture/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await res.json();
            this.updateUI(data);
        } catch (err) {}
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