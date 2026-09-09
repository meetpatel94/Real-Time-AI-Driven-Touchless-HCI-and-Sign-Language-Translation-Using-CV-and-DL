/**
 * Global browser-side hand scroll relay.
 *
 * MediaPipe runs once in the server's existing GestureEngine.  The server emits
 * only a rate-limited { direction, amount, sequence } event after it detects
 * intentional right-palm movement.  This client is loaded by base.html, so it
 * applies the event to every application workspace without opening a camera or
 * interpreting any custom-gesture label.
 */
class GlobalHandScrollController {
    constructor() {
        this.pollIntervalMs = 120;
        this.lastSequence = null;
        this.pollInFlight = false;
        this.timer = null;
        this.start();
    }

    start() {
        // Establish the current sequence as a baseline.  A page opened after a
        // hand motion must not replay a scroll event that happened elsewhere.
        this.poll();
        this.timer = window.setInterval(() => this.poll(), this.pollIntervalMs);
        window.addEventListener('beforeunload', () => this.stop(), { once: true });
    }

    stop() {
        if (this.timer !== null) {
            window.clearInterval(this.timer);
            this.timer = null;
        }
    }

    async poll() {
        if (this.pollInFlight) return;
        this.pollInFlight = true;

        try {
            const after = this.lastSequence === null ? '' : `?after=${encodeURIComponent(this.lastSequence)}`;
            const response = await fetch(`/api/hand-scroll/events${after}`, {
                cache: 'no-store',
                headers: { Accept: 'application/json' }
            });
            if (!response.ok) return;

            const payload = await response.json();
            const latestSequence = this.number(payload.latest_sequence, 0);
            const oldestSequence = this.number(payload.oldest_sequence, latestSequence + 1);
            const events = Array.isArray(payload.events) ? payload.events : [];

            if (this.lastSequence === null) {
                this.lastSequence = latestSequence;
                return;
            }

            // A browser can be suspended longer than the bounded server relay
            // retains events.  Do not apply a backlog of old hand motion when
            // it resumes; begin safely from the latest known sequence instead.
            if (this.lastSequence < oldestSequence - 1) {
                this.lastSequence = latestSequence;
                return;
            }

            events
                .slice()
                .sort((first, second) => this.number(first.sequence, 0) - this.number(second.sequence, 0))
                .forEach((event) => {
                    const sequence = this.number(event.sequence, 0);
                    if (sequence > this.lastSequence) {
                        this.applyScroll(event);
                        this.lastSequence = sequence;
                    }
                });

            // If the in-memory relay was ever pruned while this page was idle,
            // acknowledge the latest sequence rather than replaying stale input.
            this.lastSequence = Math.max(this.lastSequence, latestSequence);
        } catch (error) {
            // The normal camera/status controls already tolerate a temporarily
            // unavailable server.  Retrying on the next interval is sufficient.
        } finally {
            this.pollInFlight = false;
        }
    }

    number(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    applyScroll(event) {
        // Never cause a background tab to jump when it becomes visible again.
        if (document.hidden) return;

        const direction = String(event.direction || '').toLowerCase();
        if (direction !== 'up' && direction !== 'down') return;

        // The server has already clamped this.  Validate and clamp again at the
        // browser boundary so malformed responses can never produce scroll.
        const requestedAmount = this.number(event.amount, 0);
        if (requestedAmount <= 0) return;
        const amount = Math.max(1, Math.min(520, Math.round(requestedAmount)));

        // Browser scroll coordinates increase downward: a hand moving up must
        // therefore use a negative top offset and a hand moving down a positive
        // one.
        const delta = direction === 'up' ? -amount : amount;
        const target = this.activeScrollTarget();
        if (!target) return;

        if (typeof target.scrollBy === 'function') {
            target.scrollBy({ top: delta, left: 0, behavior: 'auto' });
        } else {
            target.scrollTop += delta;
        }
    }

    activeScrollTarget() {
        // GestureForge's application layout deliberately keeps the document
        // fixed and makes #content-area the page scroll container.  Prefer it,
        // then fall back to a normal document scroller for any future template
        // that uses document-level scrolling.
        const contentArea = document.getElementById('content-area');
        if (this.isScrollable(contentArea)) return contentArea;

        const documentScroller = document.scrollingElement || document.documentElement || document.body;
        if (this.isScrollable(documentScroller)) return documentScroller;
        return null;
    }

    isScrollable(element) {
        return Boolean(element && element.scrollHeight > element.clientHeight);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.globalHandScrollController = new GlobalHandScrollController();
});
