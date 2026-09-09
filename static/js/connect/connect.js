/**
 * Connect — real-time two-person gesture communication client.
 *
 * Talks to the Connect room relay exclusively over WebSocket (no polling for
 * communication). Only lightweight JSON events (gesture id/meaning, text)
 * travel over the socket; the camera stream and recognition stay inside the
 * existing app pipeline.
 */
(function () {
    'use strict';

    var LS_ROOM = 'gf_connect_room';
    var LS_CLIENT = 'gf_connect_client_id';

    // DOM cache
    var el = {};
    var conn = {
        ws: null,
        open: false,
        state: 'idle',            // idle | connecting | open | reconnecting
        attempts: 0,
        clientId: null,
        room: null,               // {code, role, client_id}
        pendingIntent: null,      // message queued until socket opens
        peers: [],                // participants list from server
        myPeer: null,
        msgIds: new Set(),
        sentTimeout: null,
        mappings: { builtins: [], customs: [] },
        cameraOn: false,
        cameraPollTimer: null
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

    function shortTs() { return Math.round(Date.now() / 1000); }

    // ------------------------------------------------------------------
    // Toast
    // ------------------------------------------------------------------
    var toastTimer = null;
    function toast(message, kind) {
        el.toast.textContent = message;
        el.toast.className = 'connect-toast ' + (kind || '');
        el.toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.toast.hidden = true; }, 4600);
    }

    // ------------------------------------------------------------------
    // Storage helpers
    //
    // sessionStorage (per tab) is preferred so two browser tabs on the same
    // machine act as two independent participants, while a reload in the same
    // tab can still auto-resume its room. localStorage is the fallback for
    // browsers without sessionStorage.
    // ------------------------------------------------------------------
    function storage() {
        try {
            if (window.sessionStorage) {
                window.sessionStorage.setItem('__gf_probe__', '1');
                window.sessionStorage.removeItem('__gf_probe__');
                return window.sessionStorage;
            }
        } catch (e) { /* fall through */ }
        try { return window.localStorage; } catch (e2) { /* ignore */ }
        return null;
    }

    function storageGet(key) {
        try {
            var store = storage();
            return store ? store.getItem(key) : null;
        } catch (e) { return null; }
    }

    function storageSet(key, value) {
        try {
            var store = storage();
            if (store) store.setItem(key, value);
        } catch (e) { /* ignore */ }
    }

    function storageRemove(key) {
        try {
            var store = storage();
            if (store) store.removeItem(key);
        } catch (e) { /* ignore */ }
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

    function storeRoom(room) {
        storageSet(LS_ROOM, JSON.stringify(room));
    }

    function clearRoom() {
        storageRemove(LS_ROOM);
    }

    function loadRoom() {
        try {
            var raw = storageGet(LS_ROOM);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }

    // ------------------------------------------------------------------
    // Connection pills
    // ------------------------------------------------------------------
    function setConnPill(state) {
        var label = '🟡 Connecting…';
        if (state === 'open') label = '🟢 Connected';
        else if (state === 'reconnecting') label = '🔄 Reconnecting…';
        else if (state === 'disconnected') label = '🔴 Disconnected';
        if (el.connPill) { el.connPill.textContent = label; }
        if (el.youSessionBadge) { el.youSessionBadge.textContent = label; }
    }

    function applyPeers() {
        if (!conn.room) return;
        conn.myPeer = null;
        conn.peers.forEach(function (p) {
            if (p.client_id === conn.room.client_id) conn.myPeer = p;
        });
        var other = conn.peers.find(function (p) {
            return p.client_id !== conn.room.client_id;
        }) || null;

        // Other panel presence
        if (other) {
            el.otherWaiting.hidden = true;
            el.otherGestureBox.hidden = false;
            el.otherConnPill.textContent = other.connected ? '🟢 Connected' : '🔴 Disconnected';
            el.otherConnPill.className = 'badge ' + (other.connected ? 'badge-success' : 'badge-danger');
            el.otherNameTag.textContent = other.display_name || 'Other User';
        } else {
            el.otherWaiting.hidden = false;
            el.otherGestureBox.hidden = true;
            el.otherConnPill.textContent = '🔴 Disconnected';
            el.otherConnPill.className = 'badge badge-danger';
            el.otherNameTag.textContent = '';
            var tip = el.otherWaitingTip;
            tip.textContent = conn.room.role === 'creator'
                ? 'Share your room code so the other person can join. To connect another device, open this same address using your computer\u2019s LAN IP while both devices are connected to the same Wi-Fi.'
                : 'Waiting for the room creator to come back…';
        }

        // You panel: seat / sending state
        var mine = conn.myPeer;
        var seatMine = !!(mine && mine.seat);
        var seatOther = !!(other && other.seat);
        var seatFree = conn.peers.length > 0 && !seatMine && !seatOther && mine && mine.connected;

        var sendBtn = el.btnSendToggle;
        var cameraOn = el.btnCameraLocal.getAttribute('data-cam-on') === '1';
        if (!mine) {
            sendBtn.disabled = true;
            sendBtn.textContent = '✋ Gesture Sending: —';
            sendBtn.classList.remove('active');
        } else if (seatMine) {
            sendBtn.disabled = false;
            sendBtn.textContent = '✋ Gesture Sending: ON';
            sendBtn.classList.add('active');
        } else if (seatOther) {
            sendBtn.disabled = true;
            sendBtn.textContent = '✋ Other User is sending…';
            sendBtn.classList.remove('active');
        } else if (!cameraOn) {
            sendBtn.disabled = true;
            sendBtn.textContent = '📷 Turn the camera on first';
            sendBtn.classList.remove('active');
        } else {
            sendBtn.disabled = false;
            sendBtn.textContent = '✋ Gesture Sending: OFF';
            sendBtn.classList.remove('active');
        }
        el.youSendBadge.textContent = seatMine ? 'SENDING ON' : (seatOther ? 'OTHER SENDING' : 'SENDING OFF');
        el.youSendBadge.className = 'badge ' + (seatMine ? 'badge-success' : '');
    }

    function updateOtherLastGesture(msg) {
        el.otherGestureBox.hidden = false;
        el.otherSymbol.textContent = msg.symbol || '✋';
        el.otherMeaning.textContent = msg.meaning || '—';
        el.otherMeta.textContent = (msg.sender_name || 'Other User') + ' · ' +
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
    // Timeline
    // ------------------------------------------------------------------
    function addTimelineRow(msg) {
        if (msg.id) {
            if (conn.msgIds.has(msg.id)) return;
            conn.msgIds.add(msg.id);
        }
        var self = !!(conn.room && msg.from === conn.room.client_id);
        var sender = self ? 'You' : (msg.sender_name || 'Other User');
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
            (function (btn) {
                btn.addEventListener('click', function () {
                    openReplay(btn.getAttribute('data-replay'), msg);
                });
            })(replayBtns[i]);
        }

        var count = el.timeline.querySelectorAll('.connect-msg').length;
        el.timelineCount.textContent = count + ' message' + (count === 1 ? '' : 's');
    }

    function renderHistory(messages) {
        el.timeline.innerHTML = '';
        conn.msgIds.clear();
        if (!messages || !messages.length) {
            el.timelineEmpty.hidden = false;
            el.timelineCount.textContent = '0 messages';
            return;
        }
        messages.forEach(function (msg) { addTimelineRow(msg); });
        if (el.timelineEmpty) el.timelineEmpty.hidden = true;
        var last = messages[messages.length - 1];
        if (last && last.type === 'gesture' && last.from !== conn.room.client_id) {
            updateOtherLastGesture(last);
        }
    }

    // ------------------------------------------------------------------
    // Local detection UI (server push over WS)
    // ------------------------------------------------------------------
    function setLocalStatus(status) {
        var state = status.state || 'no_hand';
        var stateText = el.youStateText;
        var symbol = el.youSymbol;
        var detectedVal = el.youDetectedValue;
        var conf = el.youConf;
        var track = el.youProgressTrack;
        var fill = el.youProgressFill;
        var hint = el.youHoldHint;

        if (state === 'no_hand') {
            stateText.textContent = cameraLabel() ? 'Show a gesture — hold it for 2 seconds' : 'Camera is off — turn it on to send gestures';
            symbol.textContent = '✋';
            detectedVal.textContent = '—';
            conf.textContent = '';
            track.hidden = true;
            fill.style.width = '0%';
            hint.hidden = false;
        } else if (state === 'detecting') {
            stateText.textContent = 'Recognizing…';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = true;
            hint.hidden = false;
        } else if (state === 'holding') {
            var pct = Math.round((status.progress || 0) * 100);
            stateText.textContent = 'Hold to send… ' + pct + '%';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = false;
            fill.style.width = pct + '%';
            hint.hidden = true;
        } else if (state === 'sent') {
            stateText.textContent = 'Sent ✓ — change the gesture to send another';
            symbol.textContent = status.symbol || '✋';
            detectedVal.textContent = status.meaning || '—';
            conf.textContent = status.confidence ? Math.round(status.confidence * 100) + '%' : '';
            track.hidden = false;
            fill.style.width = '100%';
            hint.hidden = false;
        } else if (state === 'camera_off') {
            stateText.textContent = 'Camera is off — turn it on to send gestures';
            symbol.textContent = '✋';
            detectedVal.textContent = '—';
            conf.textContent = '';
            track.hidden = true;
            fill.style.width = '0%';
            hint.hidden = true;
        } else if (state === 'paused') {
            stateText.textContent = 'Gesture capture paused';
        }
    }

    function cameraLabel() {
        return el.btnCameraLocal ? el.btnCameraLocal.getAttribute('data-cam-on') === '1' : false;
    }

    // ------------------------------------------------------------------
    // Gesture replay (uses real saved Custom Gesture samples)
    // ------------------------------------------------------------------
    var replayAnim = null;

    function openReplay(gestureId, msg) {
        var name = (msg && (msg.meaning || msg.gesture_id)) || gestureId;
        el.replayModalName.textContent = name ? ('Replaying saved sample of “' + name + '”') : '';
        el.replayModalOverlay.hidden = false;
        fetch('/api/connect/replay/' + encodeURIComponent(gestureId))
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (!data || !data.success || !data.replay || !data.replay.points ||
                    !data.replay.points.length) {
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
            [0, 1], [1, 2], [2, 3], [3, 4],
            [0, 5], [5, 6], [6, 7], [7, 8],
            [5, 9], [9, 10], [10, 11], [11, 12],
            [9, 13], [13, 14], [14, 15], [15, 16],
            [13, 17], [17, 18], [18, 19], [19, 20],
            [0, 17]
        ];
        var pad = 26;
        var xs = points.map(function (p) { return p.x; });
        var ys = points.map(function (p) { return p.y; });
        var minX = Math.min.apply(null, xs);
        var maxX = Math.max.apply(null, xs);
        var minY = Math.min.apply(null, ys);
        var maxY = Math.max.apply(null, ys);
        var spanX = Math.max(0.001, maxX - minX);
        var spanY = Math.max(0.001, maxY - minY);
        var scale = Math.min((canvas.width - pad * 2) / spanX, (canvas.height - pad * 2) / spanY);
        var start = performance.now();
        var draw = function () {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            var pulse = 1 + 0.018 * Math.sin((performance.now() - start) / 220);
            var cx = canvas.width / 2;
            var cy = canvas.height / 2 - ((minY + maxY) / 2) * scale * pulse;
            var ox = cx - ((minX + maxX) / 2) * scale * pulse;
            var pts = points.map(function (p) {
                return { x: ox + p.x * scale * pulse, y: cy - p.y * scale * pulse };
            });
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2.2;
            ctx.lineCap = 'round';
            connections.forEach(function (pair) {
                ctx.beginPath();
                ctx.moveTo(pts[pair[0]].x, pts[pair[0]].y);
                ctx.lineTo(pts[pair[1]].x, pts[pair[1]].y);
                ctx.stroke();
            });
            ctx.fillStyle = '#22c55e';
            pts.forEach(function (p) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, 3.4, 0, Math.PI * 2);
                ctx.fill();
            });
            ctx.fillStyle = '#f8fafc';
            pts.forEach(function (p) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, 1.4, 0, Math.PI * 2);
                ctx.fill();
            });
            if (performance.now() - start < 9000) {
                replayAnim = requestAnimationFrame(draw);
            } else {
                replayAnim = null;
            }
        };
        draw();
    }

    // ------------------------------------------------------------------
    // Mappings legend
    // ------------------------------------------------------------------
    function renderLegend(mappings) {
        var chips = [];
        (mappings.builtins || []).forEach(function (entry) {
            chips.push('<span class="connect-chip"><span class="chip-symbol">' + escapeHtml(entry.symbol) +
                '</span> ' + escapeHtml(entry.meaning) + '</span>');
        });
        (mappings.customs || []).slice(0, 18).forEach(function (entry) {
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
                    renderLegend(data);
                }
            })
            .catch(function () { /* legend stays empty */ });
    }

    // ------------------------------------------------------------------
    // WebSocket
    //
    // The socket URL is always derived from the address the page itself was
    // opened with (window.location), so a phone or laptop on the same LAN
    // that opens http://192.168.x.x:5000/connect talks to the same Flask
    // server over ws://192.168.x.x:5000/ws/connect. No 127.0.0.1/localhost
    // is hardcoded anywhere in this client.
    // ------------------------------------------------------------------
    function wsUrl() {
        var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        return proto + window.location.host + (conn.mappings.ws_path || '/ws/connect');
    }

    function send(payload) {
        if (conn.open) {
            try {
                conn.ws.send(JSON.stringify(payload));
                return true;
            } catch (e) { /* fall through to reconnect */ }
        }
        return false;
    }

    function connect() {
        if (conn.ws && (conn.ws.readyState === WebSocket.OPEN || conn.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        conn.state = conn.room ? 'reconnecting' : 'connecting';
        setConnPill(conn.state);
        try {
            var ws = new WebSocket(wsUrl());
        } catch (e) {
            scheduleReconnect();
            return;
        }
        conn.ws = ws;

        ws.onopen = function () {
            conn.open = true;
            conn.attempts = 0;
            conn.state = 'open';
            setConnPill('open');
            var intent = conn.pendingIntent;
            conn.pendingIntent = null;
            if (intent) {
                send(intent);
            } else if (conn.room) {
                send({ type: 'resume', code: conn.room.code, client_id: conn.clientId, role: conn.room.role });
            }
        };
        ws.onmessage = function (event) { handleMessage(event.data); };
        ws.onclose = function () {
            conn.open = false;
            if (conn.ws !== ws) return;
            conn.ws = null;
            if (conn.room) {
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
        if (!conn.room) return;
        var delay = Math.min(8000, 1200 * Math.pow(2, conn.attempts));
        conn.attempts += 1;
        setTimeout(function () {
            if (conn.room && !conn.open) connect();
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
                    client_id: msg.client_id
                };
                storeRoom(conn.room);
                showRoomView();
                el.roomCodeOutput.textContent = msg.code;
                el.createdRoomBox.hidden = false;
                conn.peers = msg.participants || [];
                applyPeers();
                renderHistory(msg.history || []);
                setConnPill('open');
                syncCameraState();
                break;
            case 'peers':
                conn.peers = msg.participants || [];
                applyPeers();
                break;
            case 'gesture':
                addTimelineRow(msg);
                if (!conn.room || msg.from !== conn.room.client_id) {
                    updateOtherLastGesture(msg);
                }
                break;
            case 'text':
                addTimelineRow(msg);
                break;
            case 'local_status':
                setLocalStatus(msg);
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
            toast(text, 'error');
            // Resume failed because the room expired -> return to lobby.
            if (conn.room) {
                leaveRoomLocally();
                toast('Room ' + conn.room.code + ' has expired. Create or join a new room.', 'error');
            }
        } else if (code === 'invalid_session') {
            toast('Your session no longer belongs to this room.', 'error');
            leaveRoomLocally();
        } else if (code === 'seat_pending') {
            toast(text, '');
        } else if (code === 'seat_busy') {
            toast(text, 'error');
        } else if (code === 'not_in_room') {
            leaveRoomLocally();
        } else {
            toast(text, 'error');
        }
    }

    // ------------------------------------------------------------------
    // View switching
    // ------------------------------------------------------------------
    function showRoomView() {
        el.lobbyView.hidden = true;
        el.roomView.hidden = false;
        el.roomCodeChip.textContent = conn.room.code;
        el.youRoleTag.textContent = conn.room.role === 'creator' ? 'creator' : 'joiner';
    }

    function showLobby() {
        el.roomView.hidden = true;
        el.lobbyView.hidden = false;
    }

    function leaveRoomLocally() {
        conn.room = null;
        conn.peers = [];
        conn.myPeer = null;
        conn.msgIds.clear();
        clearRoom();
        if (conn.ws) {
            try { conn.ws.close(); } catch (e) { /* ignore */ }
            conn.ws = null;
        }
        conn.open = false;
        conn.pendingIntent = null;
        conn.state = 'idle';
        el.timeline.innerHTML = '';
        el.timelineEmpty.hidden = false;
        el.createdRoomBox.hidden = true;
        showLobby();
        setConnPill('disconnected');
    }

    function requestLeave() {
        if (conn.room) {
            send({ type: 'leave' });
        }
        leaveRoomLocally();
    }

    // ------------------------------------------------------------------
    // Room actions
    // ------------------------------------------------------------------
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
        if (!code) {
            showJoinError('Enter the room code you received.');
            return;
        }
        hideJoinError();
        conn.clientId = storedClientId();
        conn.room = null;
        clearRoom();
        queueIntent({ type: 'join', code: code, client_id: conn.clientId, display_name: '' });
        setConnPill('connecting');
    }

    function showJoinError(message) {
        el.joinError.textContent = message;
        el.joinError.hidden = false;
    }

    function hideJoinError() {
        el.joinError.hidden = true;
    }

    function copyRoomCode() {
        var code = conn.room ? conn.room.code : el.roomCodeOutput.textContent;
        var done = function () { toast('Room code copied: ' + code, 'success'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(code).then(done).catch(function () { fallbackCopy(code, done); });
        } else {
            fallbackCopy(code, done);
        }
    }

    function fallbackCopy(text, done) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
        done();
    }

    // ------------------------------------------------------------------
    // Camera + seat
    // ------------------------------------------------------------------
    function syncCameraState() {
        fetch('/api/state')
            .then(function (res) { return res.json(); })
            .then(function (state) {
                var on = !!(state && state.camera_enabled);
                conn.cameraOn = on;
                var btn = el.btnCameraLocal;
                btn.setAttribute('data-cam-on', on ? '1' : '0');
                btn.textContent = on ? '📷 Turn Camera OFF' : '📷 Turn Camera ON';
                btn.classList.toggle('active', on);
                el.youCamBadge.textContent = on ? 'CAMERA ON' : 'CAMERA OFF';
                el.youCamBadge.className = 'badge ' + (on ? 'badge-success' : 'badge-danger');
                if (conn.room) applyPeers();
            })
            .catch(function () { /* ignore */ });
    }

    function toggleCamera() {
        fetch('/api/camera/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data && data.status === 'error') {
                    toast(data.message || 'Camera is unavailable on this device.', 'error');
                } else {
                    syncCameraState();
                }
            })
            .catch(function () { toast('Could not reach the camera service.', 'error'); });
    }

    function toggleSend() {
        if (!conn.room) return;
        var mine = conn.myPeer;
        var targetState = !(mine && mine.seat);
        send({ type: 'gesture_cam', enabled: targetState });
    }

    // ------------------------------------------------------------------
    // Auto-resume after reload
    // ------------------------------------------------------------------
    function maybeAutoResume() {
        var saved = loadRoom();
        if (!saved || !saved.code) return;
        conn.room = { code: saved.code, role: saved.role, client_id: storedClientId() };
        conn.clientId = storedClientId();
        showRoomView();
        setConnPill('reconnecting');
        connect(); // on open -> resume
    }

    // ------------------------------------------------------------------
    // Bindings
    // ------------------------------------------------------------------
    function bind() {
        el.btnCreateRoom.addEventListener('click', createRoom);
        el.btnJoinRoom.addEventListener('click', joinRoom);
        el.joinRoomInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); joinRoom(); }
        });
        el.joinRoomInput.addEventListener('input', hideJoinError);
        el.btnCopyCode.addEventListener('click', copyRoomCode);
        el.btnCopyCode2.addEventListener('click', copyRoomCode);
        el.btnLeaveRoom.addEventListener('click', requestLeave);
        el.btnCameraLocal.addEventListener('click', toggleCamera);
        el.btnSendToggle.addEventListener('click', toggleSend);
        el.chatForm.addEventListener('submit', function (e) {
            e.preventDefault();
            var text = (el.chatInput.value || '').trim();
            if (!text) return;
            if (!conn.room) { toast('Join a room first.', 'error'); return; }
            send({ type: 'message', text: text });
            el.chatInput.value = '';
            el.chatInput.focus();
        });
        el.btnCloseReplay.addEventListener('click', closeReplay);
        el.replayModalOverlay.addEventListener('click', function (e) {
            if (e.target === el.replayModalOverlay) closeReplay();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !el.replayModalOverlay.hidden) closeReplay();
        });

        // Keep the camera badge in sync when the user turns the camera on from
        // the sidebar too. This is UI state only — room communication stays on WS.
        conn.cameraPollTimer = setInterval(function () {
            if (!el.roomView.hidden) syncCameraState();
        }, 2500);

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
        el.youRoleTag = $('you-role-tag');
        el.otherNameTag = $('other-name-tag');
        el.youCamBadge = $('you-cam-badge');
        el.youSendBadge = $('you-send-badge');
        el.videoFeed = $('video-feed-connect');
        el.btnCameraLocal = $('btn-camera-local');
        el.btnSendToggle = $('btn-send-toggle');
        el.youStateText = $('you-state-text');
        el.youSymbol = $('you-symbol');
        el.youDetectedValue = $('you-detected-value');
        el.youConf = $('you-conf');
        el.youProgressTrack = $('you-progress-track');
        el.youProgressFill = $('you-progress-fill');
        el.youHoldHint = $('you-hold-hint');
        el.otherWaiting = $('other-waiting');
        el.otherWaitingTip = $('other-waiting-tip');
        el.otherGestureBox = $('other-gesture-box');
        el.otherConnPill = $('other-conn-pill');
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

    // ------------------------------------------------------------------
    // Mobile / small screens
    // ------------------------------------------------------------------
    function fitSmallScreen() {
        // The app shell uses a fixed desktop sidebar. On phones the Connect
        // room UI needs that space, so collapse the sidebar to its icon rail
        // (the same state as pressing ☰) when the viewport is narrow.
        if (window.innerWidth > 860) return;
        var sidebar = document.getElementById('sidebar');
        if (sidebar && !sidebar.classList.contains('collapsed')) {
            sidebar.classList.add('collapsed');
        }
    }

    domReady(function () {
        cacheDom();
        bind();
        loadMappings();
        setConnPill('idle');
        conn.clientId = storedClientId();
        fitSmallScreen();
        maybeAutoResume();
        syncCameraState();
    });
})();
