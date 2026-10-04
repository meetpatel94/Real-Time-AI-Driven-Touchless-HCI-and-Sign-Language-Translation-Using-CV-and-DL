/**
 * Global state, sensitivity and camera controls.
 *
 * In the local desktop runtime this keeps the original Flask/OpenCV controls.
 * In Vercel mode it routes camera preview to BrowserCamera, because a serverless
 * process cannot access the visitor's webcam or desktop cursor.
 */
class StateClient {
    constructor() {
        this.pollInterval = 500;
        this.runtime = window.GestureForgeRuntime || {};
        this.browserCameraMode = Boolean(this.runtime.browserCameraRequired);
        this.init();
    }

    init() {
        this.bindEvents();
        this.initSensitivity();
        this.initScrollSensitivity();
        this.startPolling();
        if (this.browserCameraMode) {
            window.addEventListener('gestureforge:browser-camera', (event) => {
                const current = this.currentState || {};
                this.updateUI({ ...current, camera_enabled: Boolean(event.detail && event.detail.active) });
            });
            const gestureBtn = document.getElementById('btn-toggle-gesture');
            if (gestureBtn) gestureBtn.title = 'Server-side gesture execution is available in the local desktop runtime.';
        }
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
            slider.addEventListener('input', (event) => {
                const value = event.target.value;
                display.innerText = `${value}%`;
                localStorage.setItem('cursor_sensitivity', value);
                this.sendSensitivity(parseFloat(value) / 100.0);
            });
        }
    }

    initScrollSensitivity() {
        const select = document.getElementById('select-scroll-sensitivity');
        const saved = localStorage.getItem('scroll_sensitivity') || 'medium';
        if (select) {
            select.value = saved;
            this.sendScrollSensitivity(saved);
            select.addEventListener('change', (event) => {
                const level = event.target.value;
                localStorage.setItem('scroll_sensitivity', level);
                this.sendScrollSensitivity(level);
            });
        }
    }

    async sendSensitivity(value) {
        try {
            await fetch('/api/mouse/sensitivity', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sensitivity: value })
            });
        } catch (error) { /* preferences remain local to the UI */ }
    }

    async sendScrollSensitivity(level) {
        try {
            await fetch('/api/mouse/scroll-sensitivity', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level })
            });
        } catch (error) { /* preferences remain local to the UI */ }
    }

    async toggleCamera() {
        if (this.browserCameraMode) {
            const camera = window.GestureForgeBrowserCamera;
            if (!camera) {
                this.showDeploymentMessage('Browser camera controls are still initializing. Please try again.');
                return;
            }
            await camera.toggle();
            this.updateUI({ ...(this.currentState || {}), camera_enabled: camera.isActive() });
            return;
        }
        try {
            const response = await fetch('/api/camera/toggle', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
            });
            const data = await response.json();
            this.updateUI(data);
        } catch (error) { /* next state poll retries naturally */ }
    }

    async toggleGesture() {
        if (this.browserCameraMode) {
            this.showDeploymentMessage('Air gesture execution, hand scrolling and system cursor control need the local desktop runtime.');
            this.updateUI({ ...(this.currentState || {}), gesture_enabled: false });
            return;
        }
        try {
            const response = await fetch('/api/gesture/toggle', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
            });
            const data = await response.json();
            this.updateUI(data);
        } catch (error) { /* next state poll retries naturally */ }
    }

    startPolling() {
        setInterval(async () => {
            try {
                const response = await fetch('/api/state', { cache: 'no-store' });
                const state = await response.json();
                if (this.browserCameraMode && window.GestureForgeBrowserCamera) {
                    state.camera_enabled = window.GestureForgeBrowserCamera.isActive();
                    state.gesture_enabled = false;
                }
                this.updateUI(state);
            } catch (error) { /* serverless cold starts and transient networks are non-fatal */ }
        }, this.pollInterval);
    }

    showDeploymentMessage(message) {
        let node = document.getElementById('deployment-control-message');
        if (!node) {
            node = document.createElement('div');
            node.id = 'deployment-control-message';
            node.className = 'browser-camera-message';
            const header = document.getElementById('header');
            if (header && header.parentNode) header.parentNode.insertBefore(node, header.nextSibling);
        }
        node.textContent = message;
        clearTimeout(this.messageTimer);
        this.messageTimer = setTimeout(() => { if (node) node.textContent = ''; }, 5500);
    }

    updateUI(state) {
        this.currentState = state || {};
        const cameraEnabled = Boolean(state && state.camera_enabled);
        const gestureEnabled = Boolean(state && state.gesture_enabled) && !this.browserCameraMode;
        const camBtn = document.getElementById('btn-toggle-camera');
        const gestureBtn = document.getElementById('btn-toggle-gesture');
        const fpsBadge = document.getElementById('status-fps');
        const handBadge = document.getElementById('status-hand');
        const gestureBadge = document.getElementById('status-gesture');

        if (camBtn) {
            const icon = camBtn.querySelector('.btn-toggle-icon');
            const text = camBtn.querySelector('.btn-toggle-text');
            camBtn.classList.toggle('active', cameraEnabled);
            if (icon) icon.innerText = cameraEnabled ? '●' : '○';
            if (text) text.innerText = cameraEnabled ? 'CAMERA ON' : 'CAMERA OFF';
        }
        if (gestureBtn) {
            const icon = gestureBtn.querySelector('.btn-toggle-icon');
            const text = gestureBtn.querySelector('.btn-toggle-text');
            gestureBtn.classList.toggle('active', gestureEnabled);
            if (this.browserCameraMode) {
                if (icon) icon.innerText = '☁️';
                if (text) text.innerText = 'LOCAL AI ONLY';
            } else {
                if (icon) icon.innerText = gestureEnabled ? '✋' : '🛑';
                if (text) text.innerText = gestureEnabled ? 'AIR GESTURE ON' : 'AIR GESTURE OFF';
            }
        }
        if (fpsBadge) fpsBadge.innerText = this.browserCameraMode && cameraEnabled
            ? 'BROWSER CAM' : `${(state && state.fps) || 0} FPS`;
        if (handBadge) {
            const detected = Boolean(state && state.hand_detected) && !this.browserCameraMode;
            handBadge.innerText = this.browserCameraMode ? 'BROWSER PREVIEW' : (detected ? 'HAND DETECTED' : 'NO HAND');
            handBadge.className = `badge ${detected || (this.browserCameraMode && cameraEnabled) ? 'badge-success' : 'badge-danger'}`;
        }
        if (gestureBadge) gestureBadge.innerText = this.browserCameraMode ? 'LOCAL AI ONLY' : ((state && state.gesture) || 'NONE');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.stateClient = new StateClient();
});
