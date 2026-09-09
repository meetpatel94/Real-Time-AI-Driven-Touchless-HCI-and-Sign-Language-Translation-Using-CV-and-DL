/**
 * Connect — true two-way, simultaneous gesture communication.
 *
 * Every browser owns its own camera and MediaPipe recognition loop. The room
 * WebSocket transports only a completed gesture event or text message; it
 * never receives a webcam frame or a continuous landmark stream.
 */
(function () {
    'use strict';

    var LS_ROOM = 'gf_connect_room';
    var LS_CLIENT = 'gf_connect_client_id';
    var MEDIAPIPE_CDN = 'https://cdn.jsdelivr.net/npm/@mediapipe/hands/';
    var BUILTIN_FALLBACK = {
        one: { symbol: '☝️', meaning: 'Hii' },
        two: { symbol: '✌️', meaning: 'Peace' },
        three: { symbol: '🤟', meaning: 'I Love You' },
        four: { symbol: '🖖', meaning: 'Hi' },
        five: { symbol: '🖐️', meaning: 'Hello' },
        thumbs_up: { symbol: '👍', meaning: 'Okay' },
        fist: { symbol: '✊', meaning: 'Stop' }
    };

    var el = {};
    var conn = {
        ws: null,
        open: false,
        state: 'idle',
        attempts: 0,
        clientId: null,
        room: null,
        pendingIntent: null,
        outbox: [],
        peers: [],
        myPeer: null,
        msgIds: new Set(),
        mappings: { builtins: [], customs: [], ws_path: '/ws/connect' },
        settings: {
            hold_seconds: 2.0,
            min_stable_frames: 4,
            custom_stable_frames: 3,
            pose_min_quality: 0.90,
            custom_min_quality: 0.90,
            custom_max_match_distance: 0.45
        },
        cameraOn: false,
        recognitionOn: false,
        videoStream: null,
        hands: null,
        recognitionFrame: null,
        processingFrame: false,
        local: {
            tracked: null,
            stableFrames: 0,
            confirmedAt: null,
            sentKey: null,
            lastStatus: null,
            lastProgressPush: 0
        }
    };

    function $(id) { return document.getElementById(id); }

    function domReady(fn) {
        if (document.readyState !== 'loading') { fn(); } else {
            document.addEventListener('DOMContentLoaded', fn);
        }
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function fmtTime(ts) {
        var date = new Date((ts || Date.now() / 1000) * 1000);
        var hh = date.getHours().toString().padStart(2, '0');
        var mm = date.getMinutes().toString().padStart(2, '0');
        return hh + ':' + mm;
    }

    function roleLabel(role) {
        return String(role || '').toLowerCase() === 'creator' ? 'User 1' : 'User 2';
    }

    function roleName(role) {
        return String(role || '').toLowerCase() === 'creator' ? 'Creator' : 'Joiner';
    }

    function localUserLabel() {
        return conn.room && conn.room.user_label ? conn.room.user_label : roleLabel(conn.room && conn.room.role);
    }

    // ------------------------------------------------------------------
    // Toast
    // ------------------------------------------------------------------
    var toastTimer = null;
    function toast(message, kind) {
        if (!el.toast) return;
        el.toast.textContent = message;
        el.toast.className = 'connect-toast ' + (kind || '');
        el.toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.toast.hidden = true; }, 4600);
    }

    // ------------------------------------------------------------------
    // Per-tab room/session storage
    // ------------------------------------------------------------------
    function storage() {
        try {
            if (window.sessionStorage) {
                window.sessionStorage.setItem('__gf_probe__', '1');
                window.sessionStorage.removeItem('__gf_probe__');
                return window.sessionStorage;
            }
        } catch (e) { /* fall through */ }
        try { return window.localStorage; } catch (e2) { return null; }
    }

    function storageGet(key) {
        try { var store = storage(); return store ? store.getItem(key) : null; } catch (e) { return null; }
    }

    function storageSet(key, value) {
        try { var store = storage(); if (store) store.setItem(key, value); } catch (e) { /* ignore */ }
    }

    function storageRemove(key) {
        try { var store = storage(); if (store) store.removeItem(key); } catch (e) { /* ignore */ }
    }

    function storedClientId() {
        var id = storageGet(LS_CLIENT);
        if (!id || id.length < 8) {
            id = 'gf-' + Math.random().toString(36).slice(2, 10) +
                 Math.random().toString(36).slice(2, 10);
            storageSet(LS_CLIENT, id);
        }
        return id;
    }

    function storeRoom(room) { storageSet(LS_ROOM, JSON.stringify(room)); }
    function clearRoom() { storageRemove(LS_ROOM); }

    function loadRoom() {
        try {
            var raw = storageGet(LS_ROOM);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }

    // ------------------------------------------------------------------
    // Presence, role and independent device state
    // ------------------------------------------------------------------
    function setConnPill(state) {
        var label = '⚪ Not connected';
        if (state === 'connecting') label = '🟡 Connecting…';
        else if (state === 'open') label = '🟢 Connected';
        else if (state === 'reconnecting') label = '🔄 Reconnecting…';
        else if (state === 'disconnected') label = '🔴 Disconnected';
        if (el.connPill) el.connPill.textContent = label;
        if (el.youSessionBadge) el.youSessionBadge.textContent = label;
    }

    function setBadge(target, text, kind) {
        if (!target) return;
        target.textContent = text;
        target.className = 'badge ' + (kind || '');
    }

    function setOwnDeviceBadges() {
        setBadge(el.youCamBadge, conn.cameraOn ? '🟢 CAMERA ACTIVE' : '🔴 CAMERA OFF',
            conn.cameraOn ? 'badge-success' : 'badge-danger');
        setBadge(el.youRecognitionBadge,
            conn.recognitionOn ? '🟢 RECOGNITION ACTIVE' : '⚪ RECOGNITION OFF',
            conn.recognitionOn ? 'badge-success' : '');
    }

    function applyRoleLabels() {
        if (!conn.room) return;
        if (el.youUserLabel) el.youUserLabel.textContent = conn.room.user_label || roleLabel(conn.room.role);
        if (el.youRoleTag) el.youRoleTag.textContent = roleName(conn.room.role);
    }

    function updateOtherDeviceState(other) {
        if (!other) return;
        setBadge(el.otherCameraBadge,
            other.camera_active ? '🟢 CAMERA ACTIVE' : '⚪ CAMERA OFF',
            other.camera_active ? 'badge-success' : '');
        setBadge(el.otherRecognitionBadge,
            other.recognition_active ? '🟢 RECOGNITION ACTIVE' : '⚪ RECOGNITION OFF',
            other.recognition_active ? 'badge-success' : '');
    }

    function applyPeers() {
        if (!conn.room) return;
        conn.myPeer = null;
        conn.peers.forEach(function (participant) {
            if (participant.client_id === conn.room.client_id) conn.myPeer = participant;
        });
        var other = conn.peers.find(function (participant) {
            return participant.client_id !== conn.room.client_id;
        }) || null;

        applyRoleLabels();
        setOwnDeviceBadges();

        if (other) {
            el.otherWaiting.hidden = true;
            el.otherGestureBox.hidden = false;
            setBadge(el.otherConnPill, other.connected ? '🟢 Connected' : '🔴 Disconnected',
                other.connected ? 'badge-success' : 'badge-danger');
            el.otherUserLabel.textContent = other.user_label || roleLabel(other.role);
            el.otherRoleTag.textContent = roleName(other.role);
            updateOtherDeviceState(other);
            if (other.last_gesture) {
                updateOtherLastGesture(Object.assign({}, other.last_gesture, {
                    sender_user: other.user_label || roleLabel(other.role),
                    sender_name: other.display_name || other.user_label || roleLabel(other.role)
                }));
            }
        } else {
            el.otherWaiting.hidden = false;
            el.otherGestureBox.hidden = true;
            setBadge(el.otherConnPill, '🔴 Disconnected', 'badge-danger');
            var oppositeRole = conn.room.role === 'creator' ? 'joiner' : 'creator';
            el.otherUserLabel.textContent = roleLabel(oppositeRole);
            el.otherRoleTag.textContent = roleName(oppositeRole);
            el.otherPresenceText.textContent = conn.room.role === 'creator'
                ? 'Room is ready for User 2 to join.'
                : 'The room creator is not connected right now.';
            el.otherWaitingTip.textContent = conn.room.role === 'creator'
                ? 'Share the room code so User 2 can join.'
                : 'Reconnect to the same room to continue.';
        }
    }

    function updateOtherLastGesture(msg) {
        if (!msg || !el.otherGestureBox) return;
        el.otherGestureBox.hidden = false;
        el.otherSymbol.textContent = msg.symbol || '✋';
        el.otherMeaning.textContent = msg.meaning || '—';
        el.otherMeta.textContent = (msg.sender_user || msg.sender_name || 'Other User') + ' · ' +
            (msg.kind === 'custom' ? 'custom gesture' : 'gesture') + ' · ' + fmtTime(msg.ts) +
            (msg.confidence ? ' · ' + Math.round(msg.confidence * 100) + '%' : '');
        var replayBtn = el.btnReplayGesture;
        var replayNote = el.otherReplayNote;
        if (msg.kind === 'custom' && msg.has_replay) {
            replayBtn.hidden = false;
            replayNote.hidden = true;
            replayBtn.setAttribute('data-gesture-id', msg.gesture_id || '');
            replayBtn.onclick = function () { openReplay(msg.gesture_id, msg); };
        } else {
            replayBtn.hidden = true;
            replayNote.hidden = false;
            replayNote.textContent = msg.kind === 'custom'
                ? 'No saved sample exists for “' + (msg.meaning || msg.gesture_id) + '” — showing its name instead.'
                : '';
        }
    }

    // ------------------------------------------------------------------
    // Shared timeline (gesture and text events use the same stream)
    // ------------------------------------------------------------------
    function addTimelineRow(msg) {
        if (!msg) return;
        if (msg.id) {
            if (conn.msgIds.has(msg.id)) return;
            conn.msgIds.add(msg.id);
        }
        var self = !!(conn.room && msg.from === conn.room.client_id);
        var sender = self ? ('You · ' + localUserLabel()) :
            (msg.sender_user || msg.sender_name || roleLabel(msg.sender_role));
        var row = document.createElement('div');
        row.className = 'connect-msg ' + (self ? 'self' : 'other');

        var symbolEl = '';
        var body = '';
        if (msg.type === 'text') {
            symbolEl = '<span class="connect-msg-symbol">💬</span>';
            body = '<div class="connect-msg-body">' + escapeHtml(msg.text) + '</div>';
        } else {
            symbolEl = '<span class="connect-msg-symbol">' + escapeHtml(msg.symbol || '✋') + '</span>';
            var kindLabel = msg.kind === 'custom' ? '<span class="msg-kind-label">custom</span>' : '';
            var conf = msg.confidence ? ' <span class="connect-muted">' + Math.round(msg.confidence * 100) + '%</span>' : '';
            var replay = '';
            if (msg.kind === 'custom' && msg.has_replay && !self) {
                replay = '<button type="button" class="connect-secondary-btn connect-msg-replay-btn" data-replay="' +
                    escapeHtml(msg.gesture_id) + '">▶ Replay Gesture</button>';
            }
            body = '<div class="connect-msg-body">' + kindLabel +
                '<span class="msg-meaning">' + escapeHtml(msg.meaning || '') + '</span>' +
                ' <span class="connect-muted">(' + escapeHtml(msg.gesture_id || '') + ')</span>' + conf +
                '</div>' + replay;
        }

        row.innerHTML =
            symbolEl +
            '<div class="connect-msg-main">' +
            '  <div class="connect-msg-head">' +
            '    <span class="connect-msg-sender">' + escapeHtml(sender) + '</span>' +
            '    <span>' + (msg.type === 'text' ? 'Text' : 'Gesture') + '</span>' +
            '    <span class="connect-msg-time">' + fmtTime(msg.ts) + '</span>' +
            '  </div>' + body +
            '</div>';
        el.timeline.appendChild(row);
        el.timelineEmpty.hidden = true;
        el.timeline.scrollTop = el.timeline.scrollHeight;

        var replayBtns = row.querySelectorAll('[data-replay]');
        for (var i = 0; i < replayBtns.length; i++) {
            (function (button) {
                button.addEventListener('click', function () {
                    openReplay(button.getAttribute('data-replay'), msg);
                });
            })(replayBtns[i]);
        }

        var count = el.timeline.querySelectorAll('.connect-msg').length;
        el.timelineCount.textContent = count + ' message' + (count === 1 ? '' : 's');
    }

    function renderHistory(messages) {
        el.timeline.innerHTML = '';
        el.timeline.appendChild(el.timelineEmpty);
        conn.msgIds.clear();
        if (!messages || !messages.length) {
            el.timelineEmpty.hidden = false;
            el.timelineCount.textContent = '0 messages';
            return;
        }
        messages.slice().sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); })
            .forEach(function (msg) { addTimelineRow(msg); });
        var remoteGestures = messages.filter(function (msg) {
            return msg.type === 'gesture' && (!conn.room || msg.from !== conn.room.client_id);
        });
        if (remoteGestures.length) updateOtherLastGesture(remoteGestures[remoteGestures.length - 1]);
    }

    // ------------------------------------------------------------------
    // Local recognition state UI
    // ------------------------------------------------------------------
    function cameraLabel() { return conn.cameraOn; }

    function setLocalStatus(status) {
        status = status || { state: 'no_hand' };
        var state = status.state || 'no_hand';
        var stateText = el.youStateText;
        var symbol = el.youSymbol;
        var detectedVal = el.youDetectedValue;
        var meaningVal = el.youMeaningValue;
        var conf = el.youConf;
        var track = el.youProgressTrack;
        var fill = el.youProgressFill;
        var hint = el.youHoldHint;

        if (state === 'no_hand') {
            stateText.textContent = cameraLabel() ? 'Show a gesture — hold it for 2 seconds' : 'Camera is off — turn it on to send gestures';
            symbol.textContent = '✋';
            detectedVal.textContent = '—';
            meaningVal.textContent = '—';
            conf.textContent = '';
            track.hidden = true;
            fill.style.width = '0%';
            hint.hidden = false;
        } else if (state === 'recognition_off') {
            stateText.textContent = 'Recognition is unavailable on this browser';
            symbol.textContent = '✋';
            detectedVal.textContent = '—';
            meaningVal.textContent = '—';
            conf.textContent = '';
            track.hidden = true;
            fill.style.width = '0%';
            hint.hidden = true;
        } else if (state === 'detecting') {
            stateText.textContent = 'Recognizing…';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.gesture_id || '—';
            meaningVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = true;
            hint.hidden = false;
        } else if (state === 'holding') {
            var pct = Math.round((status.progress || 0) * 100);
            stateText.textContent = 'Hold to send… ' + pct + '%';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.gesture_id || '—';
            meaningVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = false;
            fill.style.width = pct + '%';
            hint.hidden = true;
        } else if (state === 'sent') {
            stateText.textContent = 'Sent ✓ — change the gesture to send another';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.gesture_id || '—';
            meaningVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = false;
            fill.style.width = '100%';
            hint.hidden = false;
        } else if (state === 'camera_off') {
            stateText.textContent = 'Camera is off — turn it on to send gestures';
            symbol.textContent = '✋';
            detectedVal.textContent = '—';
            meaningVal.textContent = '—';
            conf.textContent = '';
            track.hidden = true;
            fill.style.width = '0%';
            hint.hidden = true;
        }
        conn.local.lastStatus = status;
    }

    // ------------------------------------------------------------------
    // Gesture replay (uses the existing saved Custom Gesture samples)
    // ------------------------------------------------------------------
    var replayAnim = null;

    function openReplay(gestureId, msg) {
        var name = (msg && (msg.meaning || msg.gesture_id)) || gestureId;
        el.replayModalName.textContent = name ? ('Replaying saved sample of “' + name + '”') : '';
        el.replayModalOverlay.hidden = false;
        fetch('/api/connect/replay/' + encodeURIComponent(gestureId))
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (!data || !data.success || !data.replay || !data.replay.points || !data.replay.points.length) {
                    throw new Error('no-sample');
                }
                drawReplay(data.replay.points);
            })
            .catch(function () {
                stopReplay();
                el.replayModalName.textContent = 'No replayable saved sample exists for this gesture — showing its name and meaning instead.';
            });
    }

    function closeReplay() {
        stopReplay();
        el.replayModalOverlay.hidden = true;
    }

    function stopReplay() {
        if (replayAnim) { cancelAnimationFrame(replayAnim); replayAnim = null; }
        var ctx = el.replayCanvas.getContext('2d');
        ctx.clearRect(0, 0, el.replayCanvas.width, el.replayCanvas.height);
    }

    function drawReplay(points) {
        var canvas = el.replayCanvas;
        var ctx = canvas.getContext('2d');
        var connections = [
            [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8],
            [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15],
            [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]
        ];
        var pad = 26;
        var xs = points.map(function (p) { return p.x; });
        var ys = points.map(function (p) { return p.y; });
        var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
        var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
        var spanX = Math.max(0.001, maxX - minX), spanY = Math.max(0.001, maxY - minY);
        var scale = Math.min((canvas.width - pad * 2) / spanX, (canvas.height - pad * 2) / spanY);
        var start = performance.now();
        var draw = function () {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            var pulse = 1 + 0.018 * Math.sin((performance.now() - start) / 220);
            var cx = canvas.width / 2, cy = canvas.height / 2 - ((minY + maxY) / 2) * scale * pulse;
            var ox = cx - ((minX + maxX) / 2) * scale * pulse;
            var pts = points.map(function (p) {
                return { x: ox + p.x * scale * pulse, y: cy - p.y * scale * pulse };
            });
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2.2;
            ctx.lineCap = 'round';
            connections.forEach(function (pair) {
                ctx.beginPath(); ctx.moveTo(pts[pair[0]].x, pts[pair[0]].y);
                ctx.lineTo(pts[pair[1]].x, pts[pair[1]].y); ctx.stroke();
            });
            ctx.fillStyle = '#22c55e';
            pts.forEach(function (p) { ctx.beginPath(); ctx.arc(p.x, p.y, 3.4, 0, Math.PI * 2); ctx.fill(); });
            ctx.fillStyle = '#f8fafc';
            pts.forEach(function (p) { ctx.beginPath(); ctx.arc(p.x, p.y, 1.4, 0, Math.PI * 2); ctx.fill(); });
            if (performance.now() - start < 9000) replayAnim = requestAnimationFrame(draw);
            else replayAnim = null;
        };
        draw();
    }

    // ------------------------------------------------------------------
    // Mappings and local feature matching
    // ------------------------------------------------------------------
    function renderLegend(mappings) {
        var chips = [];
        (mappings.builtins || []).forEach(function (entry) {
            chips.push('<span class="connect-chip"><span class="chip-symbol">' + escapeHtml(entry.symbol) +
                '</span> ' + escapeHtml(entry.meaning) + '</span>');
        });
        (mappings.customs || []).filter(function (entry) { return entry.ready; }).slice(0, 18).forEach(function (entry) {
            chips.push('<span class="connect-chip"><span class="chip-symbol">✋</span> ' +
                escapeHtml(entry.gesture_name || entry.gesture_id) +
                '<span class="chip-kind">custom</span></span>');
        });
        el.legendChips.innerHTML = chips.join('');
    }

    function loadMappings() {
        fetch('/api/connect/mappings')
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data && data.success) {
                    conn.mappings = data;
                    conn.settings = Object.assign(conn.settings, data.local_recognition || {});
                    renderLegend(data);
                }
            })
            .catch(function () { /* built-in fallback remains available */ });
    }

    function coordinate(point, key) {
        var value = Number(point && point[key]);
        return Number.isFinite(value) ? value : 0;
    }

    function distance(a, b) {
        return Math.sqrt(Math.pow(coordinate(a, 'x') - coordinate(b, 'x'), 2) +
            Math.pow(coordinate(a, 'y') - coordinate(b, 'y'), 2) +
            Math.pow(coordinate(a, 'z') - coordinate(b, 'z'), 2));
    }

    function normalizedPoints(points) {
        if (!points || points.length < 21) return null;
        var wrist = points[0];
        var pairs = [[0, 9], [5, 17], [0, 5], [0, 17], [0, 12]];
        var spans = pairs.map(function (pair) { return distance(points[pair[0]], points[pair[1]]); })
            .filter(function (value) { return value > 0.00001; });
        var scale = spans.length ? spans.reduce(function (sum, value) { return sum + value; }, 0) / spans.length : 0;
        if (scale <= 0.00001) {
            var xs = points.map(function (point) { return point.x; });
            var ys = points.map(function (point) { return point.y; });
            scale = Math.max(0.00001, Math.hypot(Math.max.apply(null, xs) - Math.min.apply(null, xs),
                Math.max.apply(null, ys) - Math.min.apply(null, ys)));
        }
        var middle = points[9];
        var theta = -Math.PI / 2 - Math.atan2(coordinate(middle, 'y') - coordinate(wrist, 'y'),
            coordinate(middle, 'x') - coordinate(wrist, 'x'));
        var cos = Math.cos(theta), sin = Math.sin(theta);
        return points.slice(0, 21).map(function (point) {
            var dx = (coordinate(point, 'x') - coordinate(wrist, 'x')) / scale;
            var dy = (coordinate(point, 'y') - coordinate(wrist, 'y')) / scale;
            var dz = (coordinate(point, 'z') - coordinate(wrist, 'z')) / scale;
            return [round6(dx * cos - dy * sin), round6(dx * sin + dy * cos), round6(dz)];
        });
    }

    function round6(value) { return Math.round(value * 1000000) / 1000000; }

    function angleCosine(a, b, c) {
        var ba = [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
        var bc = [c[0] - b[0], c[1] - b[1], c[2] - b[2]];
        var normA = Math.sqrt(ba.reduce(function (sum, value) { return sum + value * value; }, 0));
        var normC = Math.sqrt(bc.reduce(function (sum, value) { return sum + value * value; }, 0));
        if (normA < 0.000001 || normC < 0.000001) return 0;
        var dot = ba[0] * bc[0] + ba[1] * bc[1] + ba[2] * bc[2];
        return Math.max(-1, Math.min(1, dot / (normA * normC)));
    }

    function featureVector(points) {
        var normalized = normalizedPoints(points);
        if (!normalized) return [];
        var vector = [];
        normalized.forEach(function (point) { vector.push(point[0], point[1], point[2]); });
        var distancePairs = [[0, 4], [0, 8], [0, 12], [0, 16], [0, 20], [4, 8], [8, 12],
            [12, 16], [16, 20], [4, 20], [5, 9], [9, 13], [13, 17], [5, 17], [2, 5],
            [5, 8], [9, 12], [13, 16], [17, 20]];
        distancePairs.forEach(function (pair) {
            var a = normalized[pair[0]], b = normalized[pair[1]];
            vector.push(round6(Math.sqrt(Math.pow(a[0] - b[0], 2) + Math.pow(a[1] - b[1], 2) + Math.pow(a[2] - b[2], 2))));
        });
        var angleTriples = [[1, 2, 3], [2, 3, 4], [5, 6, 7], [6, 7, 8], [9, 10, 11],
            [10, 11, 12], [13, 14, 15], [14, 15, 16], [17, 18, 19], [18, 19, 20],
            [0, 5, 8], [0, 9, 12], [0, 13, 16], [0, 17, 20]];
        angleTriples.forEach(function (triple) {
            vector.push(round6(angleCosine(normalized[triple[0]], normalized[triple[1]], normalized[triple[2]])));
        });
        return vector;
    }

    function mirroredFeatureVector(vector) {
        var result = vector.slice();
        for (var index = 0; index < Math.min(result.length, 63); index += 3) result[index] = round6(-result[index]);
        return result;
    }

    function vectorDistance(first, second) {
        var size = Math.max(first.length, second.length);
        if (!size) return 1;
        var total = 0;
        for (var index = 0; index < size; index++) {
            var left = index < first.length ? Number(first[index]) : 0;
            var right = index < second.length ? Number(second[index]) : 0;
            total += Math.pow(left - right, 2);
        }
        return Math.sqrt(total / size);
    }

    function trackingQuality(points) {
        if (!points || points.length < 21) return 0;
        return points.slice(0, 21).every(function (point) {
            return Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y));
        }) ? 1 : 0;
    }

    function customMatchForHand(points, handedness) {
        if (trackingQuality(points) < Number(conn.settings.custom_min_quality || 0.90)) return null;
        var candidateFeatures = featureVector(points);
        if (!candidateFeatures.length) return null;
        var best = null;
        (conn.mappings.customs || []).forEach(function (gesture) {
            if (!gesture.ready || !gesture.enabled) return;
            var gestureHand = String(gesture.hand || 'either').toLowerCase();
            if (gestureHand !== 'either' && gestureHand !== handedness) return;
            var variants = [candidateFeatures];
            if (gestureHand === 'either') variants.push(mirroredFeatureVector(candidateFeatures));
            var distances = [];
            var vectors = [];
            if (Array.isArray(gesture.prototype) && gesture.prototype.length) vectors.push(gesture.prototype);
            (gesture.sample_features || []).slice(0, 120).forEach(function (vector) { vectors.push(vector); });
            (gesture.variation_features || []).slice(0, 40).forEach(function (vector) { vectors.push(vector); });
            vectors.forEach(function (vector) {
                variants.forEach(function (variant) { distances.push(vectorDistance(variant, vector)); });
            });
            if (!distances.length) return;
            var bestDistance = Math.min.apply(null, distances);
            var maxDistance = Math.max(0.000001, Number(conn.settings.custom_max_match_distance || 0.45));
            var similarity = Math.max(0, Math.min(1, 1 - bestDistance / maxDistance));
            var threshold = Number(gesture.similarity_threshold || 0.85);
            if (similarity < threshold) return;
            if (!best || similarity > best.confidence) {
                best = {
                    kind: 'custom',
                    gesture_id: String(gesture.gesture_id || ''),
                    symbol: '✋',
                    meaning: String(gesture.gesture_name || gesture.gesture_id || ''),
                    confidence: similarity,
                    has_replay: !!gesture.has_replay,
                    stable_frames_required: Number(conn.settings.custom_stable_frames || 3)
                };
            }
        });
        return best;
    }

    function pointExtended(points, tipIndex, pipIndex, mcpIndex, wrist) {
        return distance(points[tipIndex], wrist) > distance(points[pipIndex], wrist) &&
            distance(points[pipIndex], wrist) > distance(points[mcpIndex], wrist) &&
            points[tipIndex].y < points[pipIndex].y;
    }

    function classifyPose(points) {
        var quality = trackingQuality(points);
        if (quality < Number(conn.settings.pose_min_quality || 0.90)) return null;
        var wrist = points[0];
        var index = pointExtended(points, 8, 6, 5, wrist);
        var middle = pointExtended(points, 12, 10, 9, wrist);
        var ring = pointExtended(points, 16, 14, 13, wrist);
        var pinky = pointExtended(points, 20, 18, 17, wrist);
        var count = (index ? 1 : 0) + (middle ? 1 : 0) + (ring ? 1 : 0) + (pinky ? 1 : 0);
        var thumb = distance(points[4], wrist) > distance(points[3], wrist) &&
            distance(points[4], wrist) > distance(points[2], wrist) && points[4].y < points[3].y;
        var id = null;
        if (count === 0) id = thumb ? 'thumbs_up' : 'fist';
        else if (count === 1 && index) id = 'one';
        else if (count === 2 && index && middle) id = 'two';
        else if (count === 3) id = 'three';
        else if (count === 4) id = thumb ? 'five' : 'four';
        if (!id) return null;
        var dictionary = (conn.mappings.builtins || []).find(function (entry) { return entry.gesture_id === id; }) ||
            Object.assign({ gesture_id: id }, BUILTIN_FALLBACK[id] || {});
        if (!dictionary.symbol || !dictionary.meaning) return null;
        return {
            kind: 'pose',
            gesture_id: id,
            symbol: dictionary.symbol,
            meaning: dictionary.meaning,
            confidence: quality,
            has_replay: false,
            stable_frames_required: Number(conn.settings.min_stable_frames || 4)
        };
    }

    function classifyLocalGesture(leftHand, rightHand) {
        var hands = [];
        if (rightHand) hands.push({ points: rightHand, handedness: 'right' });
        if (leftHand) hands.push({ points: leftHand, handedness: 'left' });
        var bestCustom = null;
        hands.forEach(function (hand) {
            var custom = customMatchForHand(hand.points, hand.handedness);
            if (custom && (!bestCustom || custom.confidence > bestCustom.confidence)) bestCustom = custom;
        });
        if (bestCustom) return bestCustom;
        // Keep the existing engine's right-hand-first preference for built-ins.
        return classifyPose(rightHand) || classifyPose(leftHand);
    }

    // ------------------------------------------------------------------
    // Local temporal stability, hold-to-send and duplicate suppression
    // ------------------------------------------------------------------
    function statusKey(status) {
        return [status.state, status.kind || '', status.gesture_id || ''].join(':');
    }

    function sameStatus(status) {
        return conn.local.lastStatus && statusKey(conn.local.lastStatus) === statusKey(status);
    }

    function pushLocalStatus(status, force) {
        var now = performance.now() / 1000;
        if (status.state === 'holding' && !force) {
            if (conn.local.lastStatus && conn.local.lastStatus.state === 'holding' &&
                now - conn.local.lastProgressPush < 0.10) return;
            conn.local.lastProgressPush = now;
        } else if (!force && sameStatus(status)) {
            return;
        }
        setLocalStatus(status);
    }

    function resetLocalDetector() {
        conn.local.tracked = null;
        conn.local.stableFrames = 0;
        conn.local.confirmedAt = null;
        conn.local.sentKey = null;
        pushLocalStatus({ state: conn.cameraOn ? 'no_hand' : 'camera_off' }, true);
    }

    function evaluateLocalGesture(candidate) {
        var now = performance.now() / 1000;
        if (!candidate) {
            resetLocalDetector();
            return;
        }
        var key = candidate.kind + ':' + candidate.gesture_id;
        if (conn.local.sentKey === key) {
            conn.local.tracked = null;
            conn.local.stableFrames = 0;
            conn.local.confirmedAt = null;
            pushLocalStatus(Object.assign({}, candidate, { state: 'sent', progress: 1 }), false);
            return;
        }
        var trackedKey = conn.local.tracked ? conn.local.tracked.kind + ':' + conn.local.tracked.gesture_id : null;
        if (trackedKey !== key) {
            conn.local.tracked = candidate;
            conn.local.stableFrames = 1;
            conn.local.confirmedAt = null;
            pushLocalStatus(Object.assign({}, candidate, { state: 'detecting', progress: 0 }), false);
            return;
        }
        conn.local.stableFrames += 1;
        var required = Number(candidate.stable_frames_required || conn.settings.min_stable_frames || 4);
        if (conn.local.stableFrames >= required && conn.local.confirmedAt === null) {
            conn.local.confirmedAt = now;
        }
        if (conn.local.confirmedAt === null) {
            pushLocalStatus(Object.assign({}, candidate, { state: 'detecting', progress: 0 }), false);
            return;
        }
        var elapsed = now - conn.local.confirmedAt;
        var hold = Math.max(0.05, Number(conn.settings.hold_seconds || 2.0));
        var progress = Math.min(1, elapsed / hold);
        if (elapsed >= hold) {
            conn.local.sentKey = key;
            conn.local.tracked = null;
            conn.local.stableFrames = 0;
            conn.local.confirmedAt = null;
            var eventId = 'gesture-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
            send({
                type: 'gesture',
                id: eventId,
                kind: candidate.kind,
                gesture_id: candidate.gesture_id,
                symbol: candidate.symbol,
                meaning: candidate.meaning,
                confidence: candidate.confidence,
                has_replay: !!candidate.has_replay
            }, true);
            pushLocalStatus(Object.assign({}, candidate, { state: 'sent', progress: 1 }), true);
            return;
        }
        pushLocalStatus(Object.assign({}, candidate, { state: 'holding', progress: progress }), false);
    }

    function onLocalResults(results) {
        if (!conn.cameraOn || !conn.recognitionOn) return;
        var left = null;
        var right = null;
        var hands = results && results.multiHandLandmarks || [];
        var handedness = results && results.multiHandedness || [];
        hands.forEach(function (landmarks, index) {
            var classification = handedness[index] && handedness[index].classification && handedness[index].classification[0];
            var label = classification && String(classification.label || '').toLowerCase();
            if (label === 'left') left = landmarks;
            else if (label === 'right') right = landmarks;
            else if (!right) right = landmarks;
        });
        evaluateLocalGesture(classifyLocalGesture(left, right));
    }

    // ------------------------------------------------------------------
    // Local camera and MediaPipe loop
    // ------------------------------------------------------------------
    function sendDeviceStatus() {
        if (!conn.room) return;
        send({
            type: 'device_status',
            camera_active: !!conn.cameraOn,
            recognition_active: !!(conn.cameraOn && conn.recognitionOn)
        }, true);
    }

    function startRecognition() {
        if (conn.hands || !conn.cameraOn) return;
        if (typeof window.Hands !== 'function') {
            conn.recognitionOn = false;
            setOwnDeviceBadges();
            pushLocalStatus({ state: 'recognition_off' }, true);
            sendDeviceStatus();
            toast('Local gesture recognition could not load. Check the network and reload Connect.', 'error');
            return;
        }
        try {
            conn.hands = new window.Hands({ locateFile: function (file) { return MEDIAPIPE_CDN + file; } });
            conn.hands.setOptions({
                maxNumHands: 2,
                modelComplexity: 0,
                minDetectionConfidence: 0.55,
                minTrackingConfidence: 0.55
            });
            conn.hands.onResults(onLocalResults);
            conn.recognitionOn = true;
            setOwnDeviceBadges();
            sendDeviceStatus();
        } catch (error) {
            conn.hands = null;
            conn.recognitionOn = false;
            setOwnDeviceBadges();
            pushLocalStatus({ state: 'recognition_off' }, true);
            sendDeviceStatus();
            toast('Local gesture recognition could not start on this device.', 'error');
        }
    }

    function recognitionLoop() {
        if (conn.cameraOn && conn.recognitionOn && conn.hands && el.localVideo &&
            el.localVideo.readyState >= 2 && !conn.processingFrame) {
            conn.processingFrame = true;
            conn.hands.send({ image: el.localVideo })
                .catch(function () { /* a transient frame failure is harmless */ })
                .finally(function () { conn.processingFrame = false; });
        }
        conn.recognitionFrame = requestAnimationFrame(recognitionLoop);
    }

    function startLocalCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            toast('This browser does not provide a local camera.', 'error');
            return;
        }
        navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'user' }, width: { ideal: 640 }, height: { ideal: 480 } },
            audio: false
        }).then(function (stream) {
            conn.videoStream = stream;
            el.localVideo.srcObject = stream;
            el.localVideo.classList.add('active');
            el.cameraPlaceholder.hidden = true;
            conn.cameraOn = true;
            el.btnCameraLocal.textContent = '📷 Turn Camera OFF';
            el.btnCameraLocal.classList.add('active');
            setOwnDeviceBadges();
            resetLocalDetector();
            sendDeviceStatus();
            startRecognition();
        }).catch(function () {
            conn.cameraOn = false;
            setOwnDeviceBadges();
            toast('Camera permission was denied or the camera is unavailable.', 'error');
        });
    }

    function stopLocalCamera(notify) {
        if (notify !== false) {
            conn.cameraOn = false;
            conn.recognitionOn = false;
            sendDeviceStatus();
        } else {
            conn.cameraOn = false;
            conn.recognitionOn = false;
        }
        if (conn.videoStream) {
            conn.videoStream.getTracks().forEach(function (track) { track.stop(); });
            conn.videoStream = null;
        }
        if (el.localVideo) {
            el.localVideo.pause();
            el.localVideo.srcObject = null;
            el.localVideo.classList.remove('active');
        }
        if (conn.hands && conn.hands.close) {
            try { conn.hands.close(); } catch (e) { /* ignore */ }
        }
        conn.hands = null;
        conn.processingFrame = false;
        el.cameraPlaceholder.hidden = false;
        if (el.btnCameraLocal) {
            el.btnCameraLocal.textContent = '📷 Turn Camera ON';
            el.btnCameraLocal.classList.remove('active');
        }
        setOwnDeviceBadges();
        resetLocalDetector();
    }

    function toggleCamera() {
        if (conn.cameraOn) stopLocalCamera(true);
        else startLocalCamera();
    }

    // ------------------------------------------------------------------
    // WebSocket room relay
    // ------------------------------------------------------------------
    function wsUrl() {
        var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        return proto + window.location.host + (conn.mappings.ws_path || '/ws/connect');
    }

    function send(payload, queueWhenClosed) {
        if (conn.open && conn.ws) {
            try {
                conn.ws.send(JSON.stringify(payload));
                return true;
            } catch (e) { /* queue below */ }
        }
        if (queueWhenClosed !== false) {
            conn.outbox.push(payload);
            connect();
        }
        return false;
    }

    function flushOutbox() {
        if (!conn.open || !conn.ws) return;
        var pending = conn.outbox.slice();
        conn.outbox = [];
        pending.forEach(function (payload) { send(payload, true); });
    }

    function connect() {
        if (conn.ws && (conn.ws.readyState === WebSocket.OPEN || conn.ws.readyState === WebSocket.CONNECTING)) return;
        conn.state = conn.room ? 'reconnecting' : 'connecting';
        setConnPill(conn.state);
        var ws;
        try { ws = new WebSocket(wsUrl()); }
        catch (e) { scheduleReconnect(); return; }
        conn.ws = ws;

        ws.onopen = function () {
            conn.open = true;
            conn.attempts = 0;
            conn.state = 'open';
            setConnPill('open');
            var intent = conn.pendingIntent;
            conn.pendingIntent = null;
            if (intent) send(intent, false);
            else if (conn.room) send({ type: 'resume', code: conn.room.code, client_id: conn.clientId, role: conn.room.role }, false);
            flushOutbox();
            sendDeviceStatus();
        };
        ws.onmessage = function (event) { handleMessage(event.data); };
        ws.onclose = function () {
            conn.open = false;
            if (conn.ws !== ws) return;
            conn.ws = null;
            if (conn.room || conn.pendingIntent || conn.outbox.length) {
                conn.state = 'reconnecting';
                setConnPill('reconnecting');
                scheduleReconnect();
            } else {
                conn.state = 'disconnected';
                setConnPill('disconnected');
            }
        };
        ws.onerror = function () { try { ws.close(); } catch (e) { /* ignore */ } };
    }

    function scheduleReconnect() {
        if (!conn.room && !conn.pendingIntent && !conn.outbox.length) return;
        var delay = Math.min(8000, 1200 * Math.pow(2, conn.attempts));
        conn.attempts += 1;
        setTimeout(function () {
            if ((conn.room || conn.pendingIntent || conn.outbox.length) && !conn.open) connect();
        }, delay);
    }

    function queueIntent(intent) {
        conn.pendingIntent = intent;
        connect();
    }

    function handleMessage(raw) {
        var msg;
        try { msg = JSON.parse(raw); } catch (e) { return; }
        switch (msg.type) {
            case 'room_snapshot':
                conn.room = {
                    code: msg.code,
                    role: msg.role,
                    client_id: msg.client_id,
                    user_number: msg.user_number || (msg.role === 'creator' ? 1 : 2),
                    user_label: msg.user_label || roleLabel(msg.role)
                };
                storeRoom(conn.room);
                showRoomView();
                el.roomCodeOutput.textContent = msg.code;
                el.createdRoomBox.hidden = conn.room.role !== 'creator';
                conn.peers = msg.participants || [];
                applyPeers();
                renderHistory(msg.history || []);
                setConnPill('open');
                sendDeviceStatus();
                break;
            case 'peers':
                conn.peers = msg.participants || [];
                applyPeers();
                break;
            case 'gesture':
                addTimelineRow(msg);
                if (!conn.room || msg.from !== conn.room.client_id) updateOtherLastGesture(msg);
                break;
            case 'text':
                addTimelineRow(msg);
                break;
            case 'local_status':
                // Only show a server fallback status when this browser is not
                // already running its own local recognizer.
                if (!conn.recognitionOn) setLocalStatus(msg);
                break;
            case 'error':
                handleServerError(msg);
                break;
            case 'pong':
                break;
            default:
                break;
        }
    }

    function handleServerError(msg) {
        var code = msg.error || '';
        var text = msg.message || 'Something went wrong.';
        if (code === 'room_full') {
            toast('Room is full. A Connect room supports exactly two participants.', 'error');
        } else if (code === 'invalid_room') {
            var expiredCode = conn.room && conn.room.code;
            toast(text, 'error');
            leaveRoomLocally();
            if (expiredCode) toast('Room ' + expiredCode + ' has expired. Create or join a new room.', 'error');
        } else if (code === 'invalid_session') {
            toast('Your session no longer belongs to this room.', 'error');
            leaveRoomLocally();
        } else if (code === 'invalid_gesture') {
            toast('A gesture could not be sent because its recognized meaning was invalid.', 'error');
        } else if (code === 'not_in_room') {
            leaveRoomLocally();
        } else {
            toast(text, 'error');
        }
    }

    // ------------------------------------------------------------------
    // View and room actions
    // ------------------------------------------------------------------
    function showRoomView() {
        el.lobbyView.hidden = true;
        el.roomView.hidden = false;
        el.roomCodeChip.textContent = conn.room.code;
        applyRoleLabels();
    }

    function showLobby() {
        el.roomView.hidden = true;
        el.lobbyView.hidden = false;
    }

    function leaveRoomLocally() {
        stopLocalCamera(false);
        conn.room = null;
        conn.peers = [];
        conn.myPeer = null;
        conn.msgIds.clear();
        conn.outbox = [];
        conn.pendingIntent = null;
        clearRoom();
        if (conn.ws) {
            try { conn.ws.close(); } catch (e) { /* ignore */ }
            conn.ws = null;
        }
        conn.open = false;
        conn.state = 'idle';
        el.timeline.innerHTML = '';
        el.timeline.appendChild(el.timelineEmpty);
        el.timelineEmpty.hidden = false;
        el.timelineCount.textContent = '0 messages';
        el.createdRoomBox.hidden = true;
        showLobby();
        setConnPill('disconnected');
    }

    function requestLeave() {
        if (conn.room) send({ type: 'leave' }, false);
        leaveRoomLocally();
    }

    function createRoom() {
        conn.clientId = storedClientId();
        conn.pendingIntent = null;
        conn.room = null;
        clearRoom();
        queueIntent({ type: 'create', client_id: conn.clientId, display_name: '' });
        toast('Creating a private room…', '');
        setConnPill('connecting');
    }

    function joinRoom() {
        var code = (el.joinRoomInput.value || '').trim().toUpperCase();
        if (!code) { showJoinError('Enter the room code you received.'); return; }
        hideJoinError();
        conn.clientId = storedClientId();
        conn.room = null;
        clearRoom();
        queueIntent({ type: 'join', code: code, client_id: conn.clientId, display_name: '' });
        setConnPill('connecting');
    }

    function showJoinError(message) { el.joinError.textContent = message; el.joinError.hidden = false; }
    function hideJoinError() { el.joinError.hidden = true; }

    function copyRoomCode() {
        var code = conn.room ? conn.room.code : el.roomCodeOutput.textContent;
        var done = function () { toast('Room code copied: ' + code, 'success'); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(code).then(done).catch(function () { fallbackCopy(code, done); });
        else fallbackCopy(code, done);
    }

    function fallbackCopy(text, done) {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); } catch (e) { /* ignore */ }
        document.body.removeChild(ta); done();
    }

    function maybeAutoResume() {
        var saved = loadRoom();
        if (!saved || !saved.code) return;
        conn.room = {
            code: saved.code,
            role: saved.role,
            client_id: storedClientId(),
            user_number: saved.user_number,
            user_label: saved.user_label || roleLabel(saved.role)
        };
        conn.clientId = storedClientId();
        showRoomView();
        setConnPill('reconnecting');
        connect();
    }

    // ------------------------------------------------------------------
    // Bindings and DOM cache
    // ------------------------------------------------------------------
    function bind() {
        el.btnCreateRoom.addEventListener('click', createRoom);
        el.btnJoinRoom.addEventListener('click', joinRoom);
        el.joinRoomInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') { event.preventDefault(); joinRoom(); }
        });
        el.joinRoomInput.addEventListener('input', hideJoinError);
        el.btnCopyCode.addEventListener('click', copyRoomCode);
        el.btnCopyCode2.addEventListener('click', copyRoomCode);
        el.btnLeaveRoom.addEventListener('click', requestLeave);
        el.btnCameraLocal.addEventListener('click', toggleCamera);
        el.chatForm.addEventListener('submit', function (event) {
            event.preventDefault();
            var text = (el.chatInput.value || '').trim();
            if (!text) return;
            if (!conn.room) { toast('Join a room first.', 'error'); return; }
            send({
                type: 'message',
                id: 'text-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
                text: text
            }, true);
            el.chatInput.value = '';
            el.chatInput.focus();
        });
        el.btnCloseReplay.addEventListener('click', closeReplay);
        el.replayModalOverlay.addEventListener('click', function (event) {
            if (event.target === el.replayModalOverlay) closeReplay();
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !el.replayModalOverlay.hidden) closeReplay();
        });
        window.addEventListener('beforeunload', function () {
            if (conn.room) send({ type: 'device_status', camera_active: false, recognition_active: false }, false);
            stopLocalCamera(false);
        });
    }

    function cacheDom() {
        el.lobbyView = $('lobby-view');
        el.roomView = $('room-view');
        el.btnCreateRoom = $('btn-create-room');
        el.btnJoinRoom = $('btn-join-room');
        el.joinRoomInput = $('join-room-input');
        el.joinError = $('join-error');
        el.createdRoomBox = $('created-room-box');
        el.roomCodeOutput = $('room-code-output');
        el.btnCopyCode = $('btn-copy-code');
        el.btnCopyCode2 = $('btn-copy-code-2');
        el.roomCodeChip = $('room-code-chip');
        el.btnLeaveRoom = $('btn-leave-room');
        el.connPill = $('conn-pill');
        el.youSessionBadge = $('you-session-badge');
        el.youUserLabel = $('you-user-label');
        el.youRoleTag = $('you-role-tag');
        el.otherUserLabel = $('other-user-label');
        el.otherRoleTag = $('other-role-tag');
        el.youCamBadge = $('you-cam-badge');
        el.youRecognitionBadge = $('you-recognition-badge');
        el.localVideo = $('connect-local-video');
        el.cameraPlaceholder = $('connect-camera-placeholder');
        el.btnCameraLocal = $('btn-camera-local');
        el.youStateText = $('you-state-text');
        el.youSymbol = $('you-symbol');
        el.youDetectedValue = $('you-detected-value');
        el.youMeaningValue = $('you-meaning-value');
        el.youConf = $('you-conf');
        el.youProgressTrack = $('you-progress-track');
        el.youProgressFill = $('you-progress-fill');
        el.youHoldHint = $('you-hold-hint');
        el.otherWaiting = $('other-waiting');
        el.otherPresenceText = $('other-presence-text');
        el.otherWaitingTip = $('other-waiting-tip');
        el.otherGestureBox = $('other-gesture-box');
        el.otherConnPill = $('other-conn-pill');
        el.otherCameraBadge = $('other-camera-badge');
        el.otherRecognitionBadge = $('other-recognition-badge');
        el.otherSymbol = $('other-symbol');
        el.otherMeaning = $('other-meaning');
        el.otherMeta = $('other-meta');
        el.btnReplayGesture = $('btn-replay-gesture');
        el.otherReplayNote = $('other-replay-note');
        el.timeline = $('connect-timeline');
        el.timelineEmpty = $('timeline-empty');
        el.timelineCount = $('timeline-count');
        el.chatForm = $('chat-form');
        el.chatInput = $('chat-input');
        el.replayModalOverlay = $('replay-modal-overlay');
        el.replayCanvas = $('replay-canvas');
        el.replayModalName = $('replay-modal-name');
        el.btnCloseReplay = $('btn-close-replay');
        el.toast = $('connect-toast');
        el.legendChips = $('connect-legend-chips');
    }

    function fitSmallScreen() {
        if (window.innerWidth > 860) return;
        var sidebar = document.getElementById('sidebar');
        if (sidebar && !sidebar.classList.contains('collapsed')) sidebar.classList.add('collapsed');
    }

    domReady(function () {
        document.body.classList.add('connect-local-mode');
        cacheDom();
        bind();
        loadMappings();
        setConnPill('idle');
        setOwnDeviceBadges();
        conn.clientId = storedClientId();
        fitSmallScreen();
        maybeAutoResume();
        conn.recognitionFrame = requestAnimationFrame(recognitionLoop);
    });
})();
