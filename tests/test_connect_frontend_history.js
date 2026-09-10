/**
 * Connect frontend regression tests — per-participant gesture history.
 *
 * Loads the real static/js/connect/connect.js in Node with a minimal
 * DOM / WebSocket / fetch stub and drives the genuine ws.onmessage path,
 * so the assertions exercise the exact client code a browser would run:
 *
 *   1. LAST GESTURE shows only the newest received gesture.
 *   2. GESTURE HISTORY accumulates every received gesture (oldest → newest,
 *      newest highlighted) and its count updates dynamically.
 *   3. Histories are per participant: "other" gestures and "my" gestures
 *      land in separate lists and never mix.
 *   4. Text messages reach the timeline but never a gesture history.
 *   5. A duplicated event id creates no duplicate history entry.
 *   6. The list is capped at MAX_HISTORY (oldest entries drop first).
 *   7. ▶ Replay Gesture on a history item reuses the existing replay
 *      mechanism (GET /api/connect/replay/<gesture_id> + modal).
 *   8. Reconnect/resume rebuilds both lists from the room snapshot.
 *   9. Leaving the room resets the history (no leak into the next room).
 *
 * Run:  node tests/test_connect_frontend_history.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ---------------------------------------------------------------------------
// Tiny test harness
// ---------------------------------------------------------------------------
let passed = 0;
let failed = 0;

function check(name, cond, detail) {
    if (cond) {
        passed += 1;
        console.log('  ok - ' + name);
    } else {
        failed += 1;
        console.error('  FAIL - ' + name + (detail === undefined ? '' : ' :: ' + JSON.stringify(detail)));
    }
}

function tick() {
    return new Promise((resolve) => setImmediate(resolve));
}

async function settle(rounds) {
    for (let i = 0; i < (rounds || 4); i += 1) await tick();
}

// ---------------------------------------------------------------------------
// Fake DOM
// ---------------------------------------------------------------------------
const GLOBAL = {
    boundHandlers: [],   // addEventListener() calls made on selector-bound elements
    fetchCalls: [],      // { url, opts }
    ctxCalls: [],        // canvas 2d calls (replay drawing proof)
    createResult: null,  // canned /api/connect/room/create response
};

class FakeElement {
    constructor(id) {
        this.id = id || '';
        this.children = [];
        this.hidden = false;
        this.value = '';
        this.textContent = '';
        this.className = '';
        this.disabled = false;
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this._innerHTML = '';
        this._attributes = {};
        this.style = {};
        this._listeners = {};
        const self = this;
        this.classList = {
            _set: new Set(),
            add: (...classes) => classes.forEach((c) => self.classList._set.add(c)),
            remove: (...classes) => classes.forEach((c) => self.classList._set.delete(c)),
            contains: (c) => self.classList._set.has(c),
            toggle: (c, force) => {
                const on = force === undefined ? !self.classList._set.has(c) : !!force;
                if (on) self.classList._set.add(c); else self.classList._set.delete(c);
                return on;
            },
        };
    }

    addEventListener(type, fn) {
        (this._listeners[type] = this._listeners[type] || []).push(fn);
    }

    removeEventListener(type, fn) {
        this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn);
    }

    dispatch(type, event) {
        const base = Object.assign({ type, target: this, preventDefault() {}, stopPropagation() {} }, event || {});
        (this._listeners[type] || []).slice().forEach((fn) => fn(base));
    }

    appendChild(child) { this.children.push(child); return child; }

    setAttribute(name, value) { this._attributes[name] = String(value); }
    getAttribute(name) {
        if (name in this._attributes) return this._attributes[name];
        const match = this._innerHTML.match(new RegExp(name + '="([^"]*)"'));
        return match ? match[1] : null;
    }

    focus() {}
    select() {}
    pause() {}
    play() { return Promise.resolve(); }

    set innerHTML(html) { this._innerHTML = String(html); this.children = []; }
    get innerHTML() { return this._innerHTML; }

    // HTML that carries this element's own class attribute plus embedded markup.
    _htmlWithClass() {
        return 'class="' + this.className + '" ' + this._innerHTML;
    }

    static _classCount(html, token) {
        let count = 0;
        const re = /class="([^"]*)"/g;
        let match;
        while ((match = re.exec(html)) !== null) {
            if (match[1].split(/\s+/).includes(token)) count += 1;
        }
        return count;
    }

    static _attrCount(html, name) {
        const re = new RegExp(name + '="', 'g');
        return (html.match(re) || []).length;
    }

    _subtreeHtml() {
        let html = this._htmlWithClass();
        this.children.forEach((child) => { html += child._subtreeHtml(); });
        return html;
    }

    querySelector(selector) {
        const html = this._subtreeHtml();
        if (selector[0] === '.') {
            return FakeElement._classCount(html, selector.slice(1)) > 0 ? FakeElement._bound(this, selector) : null;
        }
        if (selector[0] === '[') {
            const name = selector.slice(1, selector.indexOf(']'));
            return FakeElement._attrCount(html, name) > 0 ? FakeElement._bound(this, selector) : null;
        }
        return null;
    }

    querySelectorAll(selector) {
        const html = this._subtreeHtml();
        let count = 0;
        if (selector[0] === '.') count = FakeElement._classCount(html, selector.slice(1));
        else if (selector[0] === '[') count = FakeElement._attrCount(html, selector.slice(1, selector.indexOf(']')));
        const out = [];
        for (let i = 0; i < count; i += 1) out.push(FakeElement._bound(this, selector));
        return out;
    }

    // Selector-bound element: records the listener so the test can simulate a
    // click, and resolves getAttribute() from the owner's markup (e.g.
    // data-replay="cg-thankyou" in the timeline row).
    static _bound(owner, selector) {
        const element = new FakeElement();
        element.addEventListener = (type, fn) => {
            GLOBAL.boundHandlers.push({ owner, selector, type, fn });
            (element._listeners[type] = element._listeners[type] || []).push(fn);
        };
        element._owner = owner;
        return element;
    }

    getContext(kind) {
        if (kind !== '2d') return null;
        const target = {};
        return new Proxy(target, {
            get(t, prop) {
                if (typeof prop === 'symbol') return t[prop];
                if (!(prop in t)) {
                    t[prop] = function () { GLOBAL.ctxCalls.push(String(prop)); };
                }
                return t[prop];
            },
            set(t, prop, value) { t[prop] = value; return true; },
        });
    }
}

function makeDocument() {
    const registry = new Map();
    const doc = {
        readyState: 'complete',
        body: new FakeElement('body'),
        _document: null,
        getElementById(id) {
            if (!registry.has(id)) registry.set(id, new FakeElement(id));
            return registry.get(id);
        },
        createElement(tag) { return new FakeElement(tag); },
        addEventListener() {},
        querySelectorAll() { return []; },
    };
    doc._document = doc;
    return doc;
}

function el(id) { return currentDocument.getElementById(id); }

// ---------------------------------------------------------------------------
// Fake WebSocket / fetch / browser globals
// ---------------------------------------------------------------------------
class FakeWebSocket {
    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.OPEN;
        this.sent = [];
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        FakeWebSocket.instances.push(this);
    }

    send(raw) { this.sent.push(JSON.parse(raw)); }

    close() { this.readyState = FakeWebSocket.CLOSED; }

    __receive(payload) { this.onmessage({ data: JSON.stringify(payload) }); }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];

function jsonResponse(body) {
    return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

function makeFetch() {
    return function fetch(url) {
        const urlText = String(url);
        GLOBAL.fetchCalls.push(urlText);
        if (urlText.indexOf('/api/connect/room/create') !== -1) {
            return Promise.resolve(jsonResponse(GLOBAL.createResult));
        }
        if (urlText.indexOf('/api/connect/replay/') !== -1) {
            return Promise.resolve(jsonResponse({
                success: true,
                replay: { points: new Array(21).fill(null).map(() => ({ x: 0.1, y: 0.2, z: 0 })) },
            }));
        }
        if (urlText.indexOf('/api/connect/mappings') !== -1) {
            return Promise.resolve(jsonResponse({
                success: true,
                ws_path: '/ws/connect',
                builtins: [],
                customs: [],
                local_recognition: {},
            }));
        }
        return Promise.resolve(jsonResponse({}));
    };
}

// A sessionStorage that survives a "page reload" (module re-load) so the
// auto-resume path can be exercised.
function makeStorage() {
    const map = new Map();
    return {
        getItem: (k) => (map.has(k) ? map.get(k) : null),
        setItem: (k, v) => { map.set(k, String(v)); },
        removeItem: (k) => { map.delete(k); },
    };
}

let currentDocument = null;

function setGlobal(name, value) {
    Object.defineProperty(global, name, { value, writable: true, configurable: true });
}

function installGlobals() {
    currentDocument = makeDocument();
    setGlobal('document', currentDocument);
    setGlobal('window', global);
    setGlobal('location', { protocol: 'http:', host: 'localhost:5000' });
    setGlobal('navigator', {});
    setGlobal('isSecureContext', false);
    setGlobal('Hands', undefined);
    setGlobal('WebSocket', FakeWebSocket);
    setGlobal('fetch', makeFetch());
    setGlobal('requestAnimationFrame', (fn) => { GLOBAL.rafCount = (GLOBAL.rafCount || 0) + 1; return GLOBAL.rafCount; });
    setGlobal('cancelAnimationFrame', () => {});
    setGlobal('addEventListener', () => {});
    setGlobal('removeEventListener', () => {});
    if (!global.sessionStorage) setGlobal('sessionStorage', makeStorage());
    return global.sessionStorage;
}

function loadConnectModule() {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'static', 'js', 'connect', 'connect.js'), 'utf8'
    );
    vm.runInThisContext(source, { filename: 'connect.js' });
}

// ---------------------------------------------------------------------------
// Message fixtures (exactly what the room relay puts on the wire)
// ---------------------------------------------------------------------------
const MY_CLIENT = 'device-a-client-id';
const OTHER_CLIENT = 'device-b-client-id';

let nowSec = Math.floor(Date.now() / 1000);

function remoteGesture(id, gestureId, meaning, extra) {
    nowSec += 1;
    return Object.assign({
        type: 'gesture',
        id,
        from: OTHER_CLIENT,
        sender_role: 'joiner',
        sender_name: 'Amit',
        sender_user: 'User 2',
        kind: 'pose',
        gesture_id: gestureId,
        symbol: '✋',
        meaning,
        confidence: 0.95,
        has_replay: false,
        ts: nowSec,
    }, extra || {});
}

function myGesture(id, gestureId, meaning, extra) {
    nowSec += 1;
    return Object.assign({
        type: 'gesture',
        id,
        from: MY_CLIENT,
        sender_role: 'creator',
        sender_name: 'Rahul',
        sender_user: 'User 1',
        kind: 'pose',
        gesture_id: gestureId,
        symbol: '✋',
        meaning,
        confidence: 0.91,
        has_replay: false,
        ts: nowSec,
    }, extra || {});
}

function textMessage(id, text, from) {
    nowSec += 1;
    return {
        type: 'text',
        id,
        from: from || OTHER_CLIENT,
        sender_role: 'joiner',
        sender_name: 'Amit',
        sender_user: 'User 2',
        text,
        ts: nowSec,
    };
}

function baseSnapshot(overrides) {
    return Object.assign({
        type: 'room_snapshot',
        code: 'GF-TEST',
        client_id: MY_CLIENT,
        role: 'creator',
        user_number: 1,
        user_label: 'User 1',
        display_name: 'Rahul',
        participants: [
            {
                client_id: MY_CLIENT, role: 'creator', user_number: 1, user_label: 'User 1',
                display_name: 'Rahul', connected: true, camera_active: false,
                recognition_active: false, last_gesture: null,
            },
            {
                client_id: OTHER_CLIENT, role: 'joiner', user_number: 2, user_label: 'User 2',
                display_name: 'Amit', connected: true, camera_active: false,
                recognition_active: false, last_gesture: null,
            },
        ],
        history: [],
        gesture_history: [],
        ts: nowSec,
    }, overrides || {});
}

function lastWs() { return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]; }

function lastBoundHandler() { return GLOBAL.boundHandlers[GLOBAL.boundHandlers.length - 1]; }

// ---------------------------------------------------------------------------
// Test 1 — live session: create room, then receive gestures
// ---------------------------------------------------------------------------
async function testLiveSession() {
    console.log('\n[live session] create room + per-participant history');
    installGlobals();
    GLOBAL.boundHandlers = [];
    GLOBAL.createResult = {
        success: true,
        code: 'GF-TEST',
        ws_path: '/ws/connect',
        session_token: 'tok-1',
        participant: {
            client_id: MY_CLIENT, role: 'creator', user_number: 1,
            user_label: 'User 1', display_name: 'Rahul',
        },
    };

    loadConnectModule();
    await settle();

    // The browser has no room stored yet, so the lobby is visible.
    check('lobby visible before joining', el('lobby-view').hidden === false, el('lobby-view').hidden);
    const startsEmpty = (container, countEl) =>
        !container.children.some((c) => (c.className || '').indexOf('connect-history-item') !== -1) &&
        ['0', ''].includes(countEl.textContent);
    check('history lists start empty',
        startsEmpty(el('other-gesture-history'), el('other-history-count')) &&
        startsEmpty(el('my-gesture-history'), el('my-history-count')));

    // Create the room through the real form button.
    el('create-display-name').value = 'Rahul';
    el('create-room-password').value = 'pass1234';
    el('btn-create-room').dispatch('click');
    await settle();

    const ws1 = lastWs();
    check('room API response opened a room WebSocket', !!ws1, FakeWebSocket.instances.length);

    // The client authenticates with the opaque session token (no password).
    ws1.onopen();
    const createMsg = ws1.sent.find((m) => m.type === 'create');
    check('client sends authenticated create intent', !!createMsg &&
        createMsg.session_token === 'tok-1' && createMsg.client_id === MY_CLIENT, ws1.sent);
    check('no password key ever sent on the socket', ws1.sent.every((m) => !/password/i.test(JSON.stringify(Object.keys(m)))));

    // Server accepts and sends the snapshot.
    ws1.__receive(baseSnapshot());
    await settle();

    check('room view visible after snapshot', el('room-view').hidden === false && el('lobby-view').hidden === true);
    check('other user card visible with peer', el('other-gesture-box').hidden === false);
    check('room code shown', el('room-code-output').textContent === 'GF-TEST');

    // ---- 1. First remote gesture: Last Gesture + one history row ----
    ws1.__receive(remoteGesture('ev-1', 'five', 'Hii', { symbol: '🖐️', confidence: 0.97 }));
    await settle();
    check('Last Gesture shows newest meaning', el('other-meaning').textContent === 'Hii', el('other-meaning').textContent);
    check('Last Gesture shows newest symbol', el('other-symbol').textContent === '🖐️');
    check('other history has exactly 1 item', el('other-gesture-history').children.length === 1,
        el('other-gesture-history').children.length);
    check('other history count = 1', el('other-history-count').textContent === '1');
    check('history row keeps meaning + sender + confidence',
        el('other-gesture-history').children[0]._innerHTML.indexOf('Hii') !== -1 &&
        el('other-gesture-history').children[0]._innerHTML.indexOf('Amit') !== -1 &&
        el('other-gesture-history').children[0]._innerHTML.indexOf('97%') !== -1);
    check('single item is marked LATEST',
        el('other-gesture-history').children[0].className.indexOf('is-latest') !== -1);
    check('timeline gained one message row', el('timeline-count').textContent === '1 message',
        el('timeline-count').textContent);
    check('my history untouched by remote gesture', el('my-gesture-history').children.length === 1 &&
        el('my-history-count').textContent === '0');

    // ---- 2. Second remote gesture: first stays, newest becomes Last ----
    ws1.__receive(remoteGesture('ev-2', 'two', 'bee', { symbol: '✌️', confidence: 0.93 }));
    await settle();
    check('Last Gesture updated to bee', el('other-meaning').textContent === 'bee', el('other-meaning').textContent);
    check('other history now has 2 items (Hii kept)', el('other-gesture-history').children.length === 2,
        el('other-gesture-history').children.length);
    check('history chronological: item 1 = Hii', el('other-gesture-history').children[0]._innerHTML.indexOf('Hii') !== -1);
    check('history chronological: item 2 = bee', el('other-gesture-history').children[1]._innerHTML.indexOf('bee') !== -1);
    check('only newest item is LATEST',
        el('other-gesture-history').children[0].className.indexOf('is-latest') === -1 &&
        el('other-gesture-history').children[1].className.indexOf('is-latest') !== -1);
    check('history count = 2', el('other-history-count').textContent === '2');
    check('timeline has 2 messages', el('timeline-count').textContent === '2 messages');

    // ---- 3. Duplicated event id on the wire -> no duplicate row ----
    ws1.__receive(remoteGesture('ev-2', 'two', 'bee', { symbol: '✌️', confidence: 0.93 }));
    await settle();
    check('duplicate event id adds no history row', el('other-gesture-history').children.length === 2);
    check('duplicate event id adds no timeline row', el('timeline-count').textContent === '2 messages');

    // ---- 4. Text message: timeline only, never a gesture history ----
    ws1.__receive(textMessage('tx-1', 'hi there'));
    await settle();
    check('text lands in the timeline', el('timeline-count').textContent === '3 messages');
    check('text never enters the gesture history', el('other-gesture-history').children.length === 2 &&
        el('my-gesture-history').children.length === 1);
    check('Last Gesture unchanged by text', el('other-meaning').textContent === 'bee');

    // ---- 5. My own gesture echo: MY history only ----
    ws1.__receive(myGesture('ev-me-1', 'thumbs_up', 'Okay', { symbol: '👍' }));
    await settle();
    check('my history has 1 item (Okay)', el('my-gesture-history').children.length === 1 &&
        el('my-gesture-history').children[0]._innerHTML.indexOf('Okay') !== -1);
    check('my history count = 1', el('my-history-count').textContent === '1');
    check('remote history untouched by my gesture', el('other-gesture-history').children.length === 2);
    check('Last Gesture (other) untouched by my gesture', el('other-meaning').textContent === 'bee');

    // ---- 6. Custom gesture with replay: button reuses existing mechanism ----
    GLOBAL.boundHandlers.length = 0;
    ws1.__receive(remoteGesture('ev-c-1', 'cg-thankyou', 'Thank you very much', {
        kind: 'custom', has_replay: true, confidence: 0.93,
    }));
    await settle();
    check('custom gesture appended to remote history', el('other-gesture-history').children.length === 3);
    const customRow = el('other-gesture-history').children[2];
    check('custom history row shows custom tag', customRow._innerHTML.indexOf('custom gesture') !== -1);
    check('custom history row renders a Replay Gesture button',
        customRow._innerHTML.indexOf('connect-history-replay-btn') !== -1 &&
        customRow._innerHTML.indexOf('▶ Replay Gesture') !== -1);
    check('pose history rows have no replay button',
        el('other-gesture-history').children[0]._innerHTML.indexOf('connect-history-replay-btn') === -1);

    const replayHandler = lastBoundHandler();
    check('history replay button has a bound click handler', !!replayHandler);
    if (replayHandler) {
        const fetchCountBefore = GLOBAL.fetchCalls.length;
        replayHandler.fn();
        await settle();
        check('click fetched the existing replay endpoint for the history item',
            GLOBAL.fetchCalls.slice(fetchCountBefore).indexOf('/api/connect/replay/cg-thankyou') !== -1,
            GLOBAL.fetchCalls.slice(fetchCountBefore));
        check('replay modal opened', el('replay-modal-overlay').hidden === false);
        check('replay modal names the gesture',
            el('replay-modal-name').textContent.indexOf('Thank you very much') !== -1,
            el('replay-modal-name').textContent);
        check('replay animation drew on the canvas', GLOBAL.ctxCalls.indexOf('clearRect') !== -1);
    }

    // ---- 7. MAX_HISTORY cap: 105 more remote gestures keep only 100 ----
    for (let i = 1; i <= 105; i += 1) {
        ws1.__receive(remoteGesture('cap-' + String(i).padStart(3, '0'), 'five', 'Cap ' + i));
    }
    await settle();
    const list = el('other-gesture-history').children;
    check('remote history capped at MAX_HISTORY=100', list.length === 100, list.length);
    check('history count shows 100', el('other-history-count').textContent === '100');
    // 3 earlier items + 105 new = 108 total -> oldest 8 drop (Hii, bee, custom, cap-001..cap-005).
    check('oldest entries dropped first (cap-006 first)', list[0]._innerHTML.indexOf('Cap 6') !== -1,
        list[0]._innerHTML);
    check('newest entry kept (cap-105)', list[99]._innerHTML.indexOf('Cap 105') !== -1);

    // The persisted session (from the snapshot) is what makes a mid-session
    // page reload auto-resume. Capture it before the leave clears it.
    const persisted = global.sessionStorage.getItem('gf_connect_room');
    check('session persisted while in the room', !!persisted && persisted.indexOf('tok-1') !== -1, persisted);

    // ---- 8. Leave resets everything (no leak into the lobby / next room) ----
    el('btn-leave-room').dispatch('click');
    await settle();
    const leaveSent = ws1.sent.find((m) => m.type === 'leave');
    check('leave message sent to the room', !!leaveSent);
    check('lobby visible again after leave', el('lobby-view').hidden === false && el('room-view').hidden === true);
    check('remote history reset to 0', el('other-history-count').textContent === '0' &&
        el('other-gesture-history').children.length === 1);
    check('my history reset to 0', el('my-history-count').textContent === '0' &&
        el('my-gesture-history').children.length === 1);
    check('timeline reset to 0 messages', el('timeline-count').textContent === '0 messages');
    check('leave clears the persisted session', global.sessionStorage.getItem('gf_connect_room') === null);

    return { ws1, persisted };
}

// ---------------------------------------------------------------------------
// Test 2 — reconnect/resume: snapshot rebuilds both per-participant lists
// ---------------------------------------------------------------------------
async function testResume(live) {
    console.log('\n[reconnect/resume] snapshot rebuilds per-participant history');
    installGlobals(); // fresh DOM, same sessionStorage (simulates a page reload)
    GLOBAL.boundHandlers = [];
    GLOBAL.rafCount = 0;

    // Simulate the page reload that happens while the user is still in the
    // room (not after an explicit leave): the tab still holds the session the
    // snapshot persisted, so auto-resume picks it up on load.
    global.sessionStorage.setItem('gf_connect_room', live.persisted);
    const saved = JSON.parse(global.sessionStorage.getItem('gf_connect_room'));
    check('session was persisted for auto-resume', !!saved && saved.session_token === 'tok-1',
        global.sessionStorage.getItem('gf_connect_room'));

    loadConnectModule();
    await settle();

    const ws2 = lastWs();
    check('auto-resume opened a fresh WebSocket', !!ws2 && FakeWebSocket.instances.length === 2 &&
        ws2 !== live.ws1, { instances: FakeWebSocket.instances.length });

    ws2.onopen();
    const resumeMsg = ws2.sent.find((m) => m.type === 'resume');
    check('client resumes with the same session token', !!resumeMsg && resumeMsg.session_token === 'tok-1',
        ws2.sent);

    // Server answers with the shared session timeline plus this participant's
    // own remote gesture history (only gestures received from the other user).
    const myA = myGesture('ev-resume-me', 'one', 'Mine A', { symbol: '☝️' });
    const otherB = remoteGesture('ev-resume-b', 'two', 'Theirs B', { symbol: '✌️' });
    const otherC = remoteGesture('ev-resume-c', 'three', 'Theirs C', { symbol: '🤟' });
    ws2.__receive(baseSnapshot({
        history: [myA, otherB, textMessage('tx-resume', 'resume text'), otherC],
        gesture_history: [otherB, otherC],
    }));
    await settle();

    check('timeline rebuilt with 4 events', el('timeline-count').textContent === '4 messages',
        el('timeline-count').textContent);
    check('remote history rebuilt from snapshot (Theirs B, C only)',
        el('other-gesture-history').children.length === 2 &&
        el('other-gesture-history').children[0]._innerHTML.indexOf('Theirs B') !== -1 &&
        el('other-gesture-history').children[1]._innerHTML.indexOf('Theirs C') !== -1,
        el('other-gesture-history').children.map((c) => c._innerHTML));
    check('remote history excludes my gestures',
        el('other-gesture-history').children.every((c) => c._innerHTML.indexOf('Mine A') === -1));
    check('my history rebuilt from the shared timeline',
        el('my-gesture-history').children.length === 1 &&
        el('my-gesture-history').children[0]._innerHTML.indexOf('Mine A') !== -1);
    check('Last Gesture is the newest remote one (Theirs C)',
        el('other-meaning').textContent === 'Theirs C', el('other-meaning').textContent);
    check('counts rebuilt', el('other-history-count').textContent === '2' &&
        el('my-history-count').textContent === '1');
}

// ---------------------------------------------------------------------------
(async function main() {
    try {
        const live = await testLiveSession();
        await testResume(live);
    } catch (error) {
        failed += 1;
        console.error('  FAIL - unexpected error:', error && error.stack || error);
    }
    console.log('\n' + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}());
