// What has actually reached the server.
//
// Part of `report.js`, which re-exports it.

// ------------------------------------------ what has actually reached the server
//
// The tablet tags a match at a field, and `firebase-init.js` turns on
// persistent local caching so a tap survives a dead connection. That part
// works. What did not work was telling the person holding the tablet, because
// the indicator read `navigator.onLine` — which reports a **link**, not a
// reachable server. A school Wi-Fi with a captive portal or a dead uplink is
// online by that definition, and the dot said "Saved" while nothing had been
// saved.
//
// Firestore knows the real answer and always did. Every snapshot carries
// `metadata.hasPendingWrites` — this document has local edits the server has
// not acknowledged — and `metadata.fromCache`, meaning the read never reached
// the server. Counting the first is an exact count of what is still on the
// tablet only.
//
//     Two signals, and disconnected wins.
//
// `fromCache` and `navigator.onLine` can disagree, and when they do the
// pessimistic one is taken. Claiming a connection that is not there is the
// error that loses a match; claiming none while one exists costs a person
// twenty seconds of looking at their phone.

/**
 * The sync indicator's state, from what Firestore reports.
 *
 * `pending` is how many entries carry unacknowledged local writes, `fromCache`
 * is whether the last snapshot was served without reaching the server.
 *
 * Returns a `tone` of `'ok'`, `'waiting'` or `'stale'`, a short label for the
 * chip and a sentence for its title. Zero pending while disconnected is a
 * genuinely reassuring answer and is worded like one: everything tapped so far
 * *is* on the server, because that is exactly what an acknowledged write means.
 */
export function syncState({ pending = 0, fromCache = false, online = true } = {}) {
    const connected = !fromCache && online !== false;

    // Nothing heard yet. Explicitly its own state rather than falling into
    // "everything is on the server", which is the one sentence here that must
    // never be said on no evidence.
    if (pending == null) {
        return {
            tone: 'stale',
            label: 'Checking',
            detail: 'Still finding out what has reached the server.',
        };
    }

    if (pending > 0) {
        return {
            tone: 'waiting',
            label: `${pending} waiting`,
            detail: `${pending} tap${pending === 1 ? '' : 's'} ${
                pending === 1 ? 'is' : 'are'} saved on this tablet and ${
                pending === 1 ? 'has' : 'have'} not reached the server yet. `
                + `Keep this page open until ${pending === 1 ? 'it does' : 'they do'}`
                + ` — ${pending === 1 ? 'it uploads' : 'they upload'} on `
                + `${pending === 1 ? 'its' : 'their'} own as soon as there is a `
                + 'connection.',
        };
    }

    if (!connected) {
        return {
            tone: 'stale',
            label: 'No connection',
            detail: 'Everything tapped so far is on the server. New taps will '
                + 'wait on this tablet until the connection is back.',
        };
    }

    return {
        tone: 'ok',
        label: 'Saved',
        detail: 'Every tap has reached the server.',
    };
}

/**
 * Whether it is safe to walk away from this tablet.
 *
 * The one question the indicator exists to answer, split out because the exit
 * path asks it too and asking it twice in two different ways is how the two
 * answers end up disagreeing.
 *
 * A missing or unknown count is **not** safe. Answering "yes, go ahead" on no
 * information is the same failure the `navigator.onLine` indicator made, in the
 * one place where the cost of it is a lost match.
 */
export function safeToClose(state) {
    return state?.pending === 0;
}
