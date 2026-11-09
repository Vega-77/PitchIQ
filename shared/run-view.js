// What the match page's "Video processing" card says, from the coach's request,
// the run's progress document and the step list in shared/run-steps.json.
//
// No DOM and no Firestore: coach/run.js draws what `runView` returns, and
// tests/run-view.test.js checks it. The run's side is pitchiq/run_progress.py.

// The run writes its progress at least once a minute while a step runs. Past
// this, the processing computer has probably stopped (a reboot, a crash).
export const STALE_MIN = 15;

const mid = (s) => (s.min[0] + s.min[1]) / 2;

/** '45 min', '3 h 20 min', '6 h'. */
export function durationText(minutes) {
    const m = Math.max(1, Math.round(minutes));
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    const rest = Math.round((m - 60 * h) / 5) * 5;
    if (rest === 60) return `${h + 1} h`;
    return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** How long a run takes and when the coach is asked to pin, before it starts. */
export function estimate(steps) {
    const work = steps.filter((s) => !s.you);
    const lo = work.reduce((a, s) => a + s.min[0], 0);
    const hi = work.reduce((a, s) => a + s.min[1], 0);
    // The first half's panorama goes on the match as soon as it is stitched, so
    // the coach can pin while the second half's is made.
    const before = steps.slice(0, steps.findIndex((s) => s.key === 'panorama_h1') + 1);
    const hours = (m) => Math.round(m / 60);
    return {
        lo,
        hi,
        text: `about ${hours(lo)} to ${hours(hi)} hours`,
        pinsAfter: `${before.reduce((a, s) => a + s.min[0], 0)} to ${before.reduce((a, s) => a + s.min[1], 0)} minutes`,
    };
}

function millis(t) {
    if (!t) return null;
    if (typeof t.toMillis === 'function') return t.toMillis();
    if (t instanceof Date) return t.getTime();
    if (typeof t === 'number') return t;
    return null;
}

/**
 * The card's state. `phase` is one of:
 *   'novideo'   no video link on the match yet
 *   'offer'     a video, nothing asked: the estimate and the button
 *   'requested' asked, the processing computer has not started it
 *   'running'   a step is running: the bar, the step, the time left
 *   'pins'      the run waits for the coach's pitch pins
 *   'stopped'   the run stopped; it resumes from that step when restarted
 *   'done'      the stats on the page come from the video
 */
export function runView({ steps, request, progress, hasVideo, now = Date.now() }) {
    const keys = steps.map((s) => s.key);
    const askedAt = millis(request?.requestedAt);
    const updatedAt = millis(progress?.updatedAt);
    // A finished run's progress stays; a new request after it starts afresh.
    const old = progress?.state === 'done' && askedAt && updatedAt && askedAt > updatedAt;
    const p = old ? null : progress;

    if (!p) {
        if (request) return { phase: 'requested', askedAt, ours: request.ours };
        return { phase: hasVideo ? 'offer' : 'novideo', estimate: estimate(steps) };
    }

    const done = new Set(p.done || []);
    const at = keys.indexOf(p.step);
    const total = steps.reduce((a, s) => a + mid(s), 0);
    const gone = steps.reduce((a, s) => a + (done.has(s.key) ? mid(s) : 0), 0);
    const now_ = at >= 0 && !done.has(p.step) ? mid(steps[at]) * (p.fraction || 0) : 0;
    const percent = p.state === 'done' ? 100 : Math.min(99, Math.round((100 * (gone + now_)) / total));
    const list = steps.map((s, i) => ({
        key: s.key,
        label: s.label,
        you: !!s.you,
        status: p.state === 'done' || done.has(s.key) ? 'done' : i === at ? 'now' : 'todo',
    }));
    const ago = updatedAt == null ? null : (now - updatedAt) / 60000;
    return {
        phase: ['running', 'pins', 'stopped', 'done'].includes(p.state) ? p.state : 'running',
        percent,
        step: at >= 0 ? { n: at + 1, of: steps.length, label: steps[at].label } : null,
        stepPercent: Math.round(100 * (p.fraction || 0)),
        leftText: p.leftMin != null ? durationText(p.leftMin) : null,
        etaAt: millis(p.etaAt),
        updatedAgo: ago,
        stale: p.state === 'running' && ago != null && ago > STALE_MIN,
        pinHalves: p.pinHalves || [],
        message: p.message || '',
        finishedAt: p.state === 'done' ? updatedAt : null,
        list,
    };
}
