/**
 * Browser-owned camera preview for serverless deployments.
 *
 * Vercel functions cannot access a visitor's webcam.  In Vercel mode this
 * controller attaches a single getUserMedia stream to the existing page camera
 * panels.  It intentionally sends no image frames to the server.
 */
(function () {
    'use strict';

    var runtime = window.GestureForgeRuntime || {};

    function cameraErrorMessage(error) {
        var name = (error && error.name) || '';
        if (!window.isSecureContext || name === 'SecurityError') {
            return 'Camera access requires a secure HTTPS page.';
        }
        if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
            return 'Camera permission was denied. Allow it in your browser site settings and try again.';
        }
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
            return 'No camera was found on this device.';
        }
        if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
            return 'The camera is already in use by another application.';
        }
        return 'Unable to start the browser camera' + (name ? ' (' + name + ')' : '') + '.';
    }

    function BrowserCamera() {
        this.stream = null;
        this.starting = null;
        this.lastMessage = 'Turn on Camera in the sidebar to preview your browser camera.';
        this.lastError = false;
    }

    BrowserCamera.prototype.previews = function () {
        return Array.prototype.slice.call(document.querySelectorAll('video[data-browser-camera]'));
    };

    BrowserCamera.prototype.isActive = function () {
        return Boolean(this.stream && this.stream.getTracks().some(function (track) { return track.readyState === 'live'; }));
    };

    BrowserCamera.prototype.render = function () {
        var message = this.lastMessage;
        this.previews().forEach(function (video) {
            if (this.stream && video.srcObject !== this.stream) {
                video.srcObject = this.stream;
                video.play().catch(function () { /* autoplay may wait for user gesture */ });
            }
            var host = video.parentElement;
            if (!host) return;
            var status = host.querySelector('.browser-camera-message');
            if (!status) {
                status = document.createElement('p');
                status.className = 'browser-camera-message';
                host.appendChild(status);
            }
            status.textContent = message;
            status.classList.toggle('is-error', this.lastError);
        }, this);
    };

    BrowserCamera.prototype.emit = function () {
        this.render();
        window.dispatchEvent(new CustomEvent('gestureforge:browser-camera', {
            detail: { active: this.isActive(), message: this.lastMessage, error: this.lastError }
        }));
    };

    BrowserCamera.prototype.start = function () {
        var self = this;
        if (this.isActive()) return Promise.resolve(true);
        if (this.starting) return this.starting;
        if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.lastError = true;
            this.lastMessage = !window.isSecureContext
                ? 'Camera access requires HTTPS. Open the deployed HTTPS URL.'
                : 'This browser does not support camera access.';
            this.emit();
            return Promise.resolve(false);
        }

        this.lastError = false;
        this.lastMessage = 'Requesting browser camera permission…';
        this.emit();
        this.starting = navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'user' }, width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: false
        }).then(function (stream) {
            self.stream = stream;
            self.lastMessage = 'Browser camera preview is active. AI recognition remains local-runtime only.';
            self.lastError = false;
            self.emit();
            return true;
        }).catch(function (error) {
            self.stream = null;
            self.lastMessage = cameraErrorMessage(error);
            self.lastError = true;
            self.emit();
            return false;
        }).finally(function () {
            self.starting = null;
        });
        return this.starting;
    };

    BrowserCamera.prototype.stop = function () {
        if (this.stream) {
            this.stream.getTracks().forEach(function (track) { track.stop(); });
        }
        this.stream = null;
        this.previews().forEach(function (video) {
            video.pause();
            video.srcObject = null;
        });
        this.lastError = false;
        this.lastMessage = 'Browser camera is off.';
        this.emit();
    };

    BrowserCamera.prototype.toggle = function () {
        return this.isActive() ? Promise.resolve(this.stop() || false) : this.start();
    };

    document.addEventListener('DOMContentLoaded', function () {
        if (!runtime.browserCameraRequired) return;
        var camera = new BrowserCamera();
        window.GestureForgeBrowserCamera = camera;
        camera.render();
        window.addEventListener('beforeunload', function () { camera.stop(); }, { once: true });
    });
})();
