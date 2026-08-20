// Load every page the site serves, and check it came up.
//
// This is the suite the other two could not be. `tests/video.test.js` covers
// pure functions and cannot import a module that touches the DOM;
// `tests/rules.test.js` and `tests/flow.test.js` drive the real emulator and
// never load a page. Between them sat the entire user interface, and two bugs
// walked through the gap in two days: a `ReferenceError` on the second
// statement of the live-tagging entry point that left the tablet dead for eight
// days, and three section rails that showed the first player opened for every
// player after.
//
// What a page passing here does and does not mean:
//   IT DOES   — every module imported, every top-level statement ran, `init()`
//               wired its handlers, the auth callback fired, the data path
//               rendered, and the page put something on screen.
//   IT DOES NOT — say any of it is laid out, styled, legible or correct. There
//               is no CSS here and no layout engine. A bar of the wrong width
//               and a column that collapses on a phone are still browser work.

import './module-hooks.js';

import { test, before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { installDom } from './dom-shim.js';
import {
    reset, seed, signInAs, snapshotOf, pathsUnder,
    goOffline, goOnline, queuedWrites, refuseWrites, acceptWrites,
} from './fake-firebase.js';
import { fixture, filmed, COACH, STUDENT, TEAM_ID, MATCH_ID } from './fixtures.js';

const root = new URL('../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');

let live = null;
const failures = [];

/**
 * A module's top level runs once per URL, and several of these pages read
 * `location.search` there — so a second look at the same page under different
 * query parameters needs `variant`, which loads it under a specifier Node has
 * not cached. Everything the page imports still resolves to the one shared
 * copy; only the entry point is duplicated. `reset()` drops the previous
 * instance's auth callback, so the old page does not render over the new one.
 */
async function openPage({
    html, entry, url, user = COACH, documents = fixture(), variant = null,
}) {
    reset();
    seed(documents);

    live?.restore();
    live = installDom(read(html), { url });

    // A page that throws inside a promise nobody awaited still renders a blank
    // screen to a coach, so an unhandled rejection has to fail the test rather
    // than print a warning after it has passed.
    failures.length = 0;

    const specifier = new URL(entry, root).href + (variant ? `?case=${variant}` : '');
    await import(specifier);
    if (user) await signInAs(user);
    await settle();

    assert.deepEqual(failures, [], `${html} raised: ${failures.join(' | ')}`);
    return live;
}

/** Let the page's own promise chains finish before looking at the screen. */
async function settle(rounds = 12) {
    for (let i = 0; i < rounds; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

const text = (id) => live.document.getElementById(id)?.textContent.trim() ?? null;
const el = (id) => live.document.getElementById(id);
const shown = (id) => {
    const node = el(id);
    return !!node && !node.classList.contains('hidden');
};

before(() => {
    process.on('unhandledRejection', (err) => failures.push(String(err?.message || err)));
    process.on('uncaughtException', (err) => failures.push(String(err?.message || err)));
});

beforeEach(() => { failures.length = 0; });

after(() => live?.restore());

// ------------------------------------------------------------- the fixture
//
// A fixture that seeds values the app never writes tests a path the app never
// takes, and does it while looking like coverage. That has happened twice here
// already: a `kickoff_first` period, which the timeline printed as the literal
// words "kickoff first" among a column of sentences, and a `midfield` position,
// which `positionOf` correctly threw away — so every test touching a squad had
// been quietly exercising the nobody-said-yet path instead.
//
// Both were caught by eye. This is the check that means the next one is not.

test('the fixture only contains values the app recognises', async () => {
    const { POSITIONS, cvReportFields } = await import('../assets/report.js');
    const { EVENT_TYPES, PERIOD_LABELS } = await import('../assets/events.js');

    const ids = new Set(POSITIONS.map((p) => p.id));
    const types = new Set(EVENT_TYPES);
    const periods = new Set(Object.keys(PERIOD_LABELS));
    // Every `cv`-prefixed key a published report can carry, taken from the
    // function that writes them rather than listed again here. A misspelt one
    // is invisible: the page reads `undefined`, renders a dash, and looks
    // exactly like a match nobody filmed. `cvPasses` sat in this fixture doing
    // that until 2026-08-17 — the real names are cvPassesAttempted and
    // cvPassesCompleted, and neither the fixture nor any test ever said so.
    const cvKeys = new Set(Object.keys(cvReportFields(null)));

    for (const [path, doc] of Object.entries(fixture())) {
        if (/\/players\/[^/]+$/.test(path)) {
            assert.ok(doc.position === null || ids.has(doc.position),
                `${path}: "${doc.position}" is not a position this app stores`);
        }
        if (/\/playerReports\/[^/]+$/.test(path)) {
            for (const key of Object.keys(doc)) {
                if (!key.startsWith('cv')) continue;
                assert.ok(cvKeys.has(key),
                    `${path}: "${key}" is not a field the pipeline writes`);
            }
        }
        if (/\/log\/[^/]+$/.test(path)) {
            const known = doc.kind === 'period' ? periods
                : (doc.kind === 'sub' ? new Set(['sub']) : types);
            assert.ok(known.has(doc.type),
                `${path}: "${doc.type}" is not a ${doc.kind} this app writes`);
        }
    }
});

/**
 * `.btn` is the one class here with a family of variants — `primary`, `ghost`,
 * `danger`, `block`, `small`, `tiny`, `on`, `active` — which makes it the one
 * place where an invented variant reads as a deliberate choice and does
 * nothing. `class="btn secondary"` sat on five controls until 2026-08-17: no
 * rule in any stylesheet mentioned `.secondary`, and in a real browser those
 * buttons computed byte-identical to a plain `.btn`. Nothing looked broken,
 * which is exactly why it survived — the default already *is* the quiet
 * variant, so the markup was asking for what it would have got anyway, in a
 * word that looked like a decision. The cost was never the pixels; it was the
 * next person copying the line believing it meant something.
 *
 * A token beside `btn` passes if a stylesheet defines it **or** if JS queries
 * it: `.edit-save` and `.shot-header-btn` are handles for `querySelector`,
 * never styling, and writing rules for them to satisfy a test would be worse
 * than leaving them bare. A token that is neither is a variant that does not
 * exist.
 *
 * Scoped to `.btn` on purpose. A blanket "every class must be styled" sweep
 * over this repo turns up mostly structural wrappers and query handles, and a
 * test whose body is a twelve-name allowlist is a snapshot, not a check.
 */
test('every button variant the markup asks for is one the stylesheet has', () => {
    const skip = new Set([
        'node_modules', 'PitchIQHelper', '.git', 'runs', 'scratch_frames',
        'cv', 'baselines', 'tests',
    ]);
    const files = [];
    (function walk(dir) {
        for (const entry of readdirSync(dir, { withFileTypes: true })) {
            if (skip.has(entry.name)) continue;
            const path = `${dir}/${entry.name}`;
            if (entry.isDirectory()) walk(path);
            else if (/\.(js|html|css)$/.test(entry.name)) files.push(path);
        }
    })(new URL('.', root).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

    const styled = new Set();
    const queried = new Set();
    const lists = [];

    for (const path of files) {
        const source = readFileSync(path, 'utf8');
        if (path.endsWith('.css')) {
            const css = source.replace(/\/\*[\s\S]*?\*\//g, '');
            for (const m of css.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) styled.add(m[1]);
            continue;
        }
        for (const m of source.matchAll(/querySelector(?:All)?\(\s*(["'`])\.([\w-]+)/g)) {
            queried.add(m[2]);
        }
        // Literal class lists only. One built by interpolation cannot be read
        // statically, and guessing at the halves either side of a `${}` would
        // invent variants nobody wrote.
        const note = (value) => {
            const tokens = value.split(/\s+/).filter(Boolean);
            if (tokens.includes('btn')) lists.push({ tokens, path });
        };
        for (const m of source.matchAll(/class\s*=\s*"([^"$`]*)"/g)) note(m[1]);
        for (const m of source.matchAll(/class\s*=\s*'([^'$`]*)'/g)) note(m[1]);
        for (const m of source.matchAll(/className\s*=\s*(["'])([\w -]*)\1/g)) note(m[2]);
    }

    assert.ok(lists.length > 20, `only found ${lists.length} button class lists — the scan is broken, not the markup`);
    assert.ok(styled.has('primary') && styled.has('ghost'), 'no stylesheet was read');

    const dead = [];
    for (const { tokens, path } of lists) {
        for (const token of tokens) {
            if (token === 'btn' || styled.has(token) || queried.has(token)) continue;
            dead.push(`${path.split('/').slice(-2).join('/')}: .${token}`);
        }
    }
    assert.deepEqual(dead, [],
        `these buttons ask for a variant no stylesheet defines and no script queries:\n  ${dead.join('\n  ')}`);
});
/**
 * Every field a match page reads off a match document is one something writes.
 *
 * A read of a field nobody writes is the quietest bug this codebase has. It
 * throws nothing, logs nothing and renders nothing — `undefined` falls through
 * whatever `||` or `??` is beside it and the page shows the fallback, which
 * looks exactly like a match that simply had no opponent named. Two of them sat
 * on screen for weeks: a shot-map caption reading `match.opponent` when the
 * field is `opponentName`, and a downloaded label file stamping
 * `match.playedOn` when the field is `date`.
 *
 * So compare the two sides directly. A name is legitimate if it is either:
 *   - a field `firestore.rules` lets somebody write to the match document, or
 *   - a key the page merges on itself after loading it.
 * Both sides are read out of the real files, so renaming a field in the rules
 * or dropping one from the merge fails here rather than on the screen.
 *
 * The two pages get different answers on purpose. The coach page assembles its
 * match out of a document plus several subcollections, so its merge literal is
 * part of the writer side. The half-time page assigns the snapshot straight
 * through — `state.match = match` — so the document alone is the whole of what
 * it may read, and a field the coach page merges in is not one it can borrow.
 *
 * `player/player.js` is deliberately absent. Its `report` is a published player
 * report from another collection that happens to carry an opponent name and a
 * video link too; checking it against the match rules would compare a document
 * against somebody else's writer list and pass or fail for no reason.
 *
 * Scoped to `state.match` on purpose. It is the object with two authors — a
 * server document and a client merge — and that seam is where the mismatches
 * were. The subcollections it carries have single authors and single readers.
 */
test('every match field the match pages read is one something writes', () => {
    const read = (name) => readFileSync(new URL(name, root), 'utf8');
    const coach = read('coach/coach.js');
    const halftime = read('halftime/halftime.js');
    const rules = read('firestore.rules');
    const db = read('assets/db.js');

    // The rules are the writer of record for the document itself: a field not
    // named in a create or an update list cannot reach Firestore at all.
    const from = rules.indexOf('match /matches/{m}');
    const to = rules.indexOf('match /roster/', from);
    const block = rules.slice(from, to);
    const stored = new Set();
    for (const list of block.matchAll(/(?:hasOnly|changed)\(\s*\[([^\]]*)\]/g)) {
        for (const field of list[1].matchAll(/'(\w+)'/g)) stored.add(field[1]);
    }
    // ...plus `id`, which lives on the snapshot rather than inside it. That is
    // the document as any page receives it.
    for (const own of db.matchAll(/(\w+):\s*snap\.(?:id|ref)\b/g)) stored.add(own[1]);

    // The coach page alone merges the subcollections in, so the page has one
    // object rather than five.
    const merged = new Set(stored);
    const merge = coach.match(/state\.match\s*=\s*\{([^}]*)\}/);
    assert.ok(merge, 'no `state.match = {...}` literal found — the scan is broken');
    for (const key of merge[1].split(',')) {
        const token = key.trim();
        if (/^\w+$/.test(token)) merged.add(token);
    }

    assert.ok(stored.has('opponentName') && stored.has('xgCheck'),
        'no rules file was read');
    assert.ok(merged.has('id') && merged.has('roster'),
        'the client-assembled keys were not picked up');

    // The minimum is per page and only there to catch a scan that has stopped
    // finding anything: the coach page reads dozens, the half-time page a
    // handful, and a rename that empties either one should fail loudly rather
    // than pass with nothing to check.
    for (const [name, source, writable, least] of [
        ['coach/coach.js', coach, merged, 12],
        ['halftime/halftime.js', halftime, stored, 3],
    ]) {
        const readFields = [...new Set(
            [...source.matchAll(/state\.match\??\.(\w+)/g)].map((m) => m[1]),
        )].sort();
        assert.ok(readFields.length >= least,
            `only found ${readFields.length} reads of state.match in ${name}`
            + ' — the scan is broken, not the page');

        const dead = readFields.filter((field) => !writable.has(field));
        assert.deepEqual(dead, [],
            `${name} reads these off a match, and nothing writes them:\n  `
            + `${dead.join('\n  ')}`);
    }
});


/**
 * Every figure the pipeline publishes is one some page can put on a screen.
 *
 * The mirror of the test above, and the harder direction of the same seam.
 * That one catches a read with no writer — a page asking a match document for
 * a field nothing ever fills in. This one catches a write with no reader: a
 * number computed in Python, serialised, carried through `cv/publish.py` into
 * a Firestore document every client downloads, and then drawn by nothing at
 * all.
 *
 * It is the quietest of the three ways a write and a read can fail to meet,
 * because nothing anywhere is wrong. The pipeline is right, the document is
 * right, and the pages are right about everything they do draw. The only
 * symptom is a screen missing something, which is indistinguishable from a
 * feature nobody has asked for yet. `keepers` sat exactly like that: a
 * complete stat block per goalkeeper — saves, save percentage, claims, sweeper
 * actions, distribution accuracy by kick, punt and throw — in every published
 * summary, on no screen, for as long as the field existed. Goalkeeper
 * *detection* was a ticked roadmap item; goalkeeper *display* was never
 * written down anywhere, which is how a finished figure ends up with nowhere
 * to go.
 *
 * All five payloads, not just the summary. The first version of this test
 * scanned `summary_payload` alone, which was where `keepers` had been hiding,
 * and covering one of five is the kind of half-check that reads as a solved
 * problem. Widening it found `cvPositionNoiseM` on the player report the same
 * afternoon — published for every player, mirrored into the browser's own
 * `cvReportFields`, listed in `CV_REPORT_KEYS` so that clearing a run clears
 * it too, and rendered nowhere.
 *
 * Both sides come out of the real files. The keys are parsed from each
 * builder's own body rather than listed here, so a figure added to any payload
 * fails this the day it is added, instead of the month somebody notices the
 * screen never changed.
 *
 * A published key with no reader is not automatically a bug, so there is an
 * escape hatch — and it deliberately costs a sentence. Writing down why a
 * figure has nowhere to go is the entire point of the test; the five below
 * have real answers and `keepers` no longer does.
 *
 * What it cannot tell you is *which* object a `.key` was read off. A page that
 * happens to use `participants` for something unrelated would satisfy this on
 * the summary's behalf, and short generic names — `id`, `type`, `team` on an
 * event — are effectively unchecked, since every one of them is a property of
 * something else somewhere. That is the price of a check that needs no runtime
 * and no fixture, and it is worth paying: the failure this catches is a key
 * nobody anywhere mentions, which no amount of aliasing produces by accident.
 */
test('every figure the pipeline publishes is one some page reads', () => {
    const payload = read('cv/publish.py');

    // Every builder in the file, with where what it returns ends up. A new
    // payload added without a line here is the one gap this cannot see, so the
    // table is checked against the file rather than trusted.
    const builders = [
        ['summary_payload', 'cvStats/summary'],
        ['events_payload', 'cvStats/events'],
        ['identity_payload', 'cvStats/identity'],
        ['thumbs_payload', 'cvStats/thumbs'],
        ['player_report_fields', 'each player report'],
    ];
    const defined = [...payload.matchAll(/^def (\w+_payload|player_report_fields)\(/gm)]
        .map((match) => match[1]).sort();
    assert.deepEqual(defined, builders.map(([name]) => name).sort(),
        'cv/publish.py has gained or lost a payload builder — add it to the'
        + ' table above, or this scan quietly stops covering it');

    const published = new Map();
    for (const [name] of builders) {
        const from = payload.indexOf(`def ${name}(`);
        const body = payload.slice(from, payload.indexOf('\ndef ', from + 1));
        // A key of the returned dict, at the start of its own line. The
        // literals inside `.get('events')` and `cluster['thumb']` are lookups
        // rather than keys, and none of them is followed by a colon.
        const keys = new Set([
            ...[...body.matchAll(/^\s*(?:return\s*)?\{?\s*'(\w+)':/gm)].map((m) => m[1]),
            // `player_report_fields` builds its names out of one prefix and
            // twenty suffixes, so no finished field appears as a literal.
            ...[...body.matchAll(/^\s*f'\{CV_FIELD_PREFIX\}(\w+)':/gm)]
                .map((m) => `cv${m[1]}`),
        ]);
        assert.ok(keys.size >= 2,
            `only found ${keys.size} keys in ${name} — the scan is broken`);
        published.set(name, [...keys].sort());
    }

    assert.ok(published.get('summary_payload').includes('keepers')
        && published.get('events_payload').includes('startM')
        && published.get('player_report_fields').includes('cvTouches'),
        'the keys parsed out are not the ones cv/publish.py publishes');

    // Whatever the site serves. The fixture is skipped on purpose: it writes
    // these keys rather than reading them, so counting it as a reader would
    // let the preview keep a figure alive that no page has ever drawn.
    const skip = new Set([
        'node_modules', 'PitchIQHelper', '.git', 'runs', 'scratch_frames',
        'cv', 'baselines', 'tests', 'sample-report.js',
    ]);
    const sources = [];
    (function walk(dir) {
        for (const entry of readdirSync(dir, { withFileTypes: true })) {
            if (skip.has(entry.name)) continue;
            const path = `${dir}/${entry.name}`;
            if (entry.isDirectory()) walk(path);
            else if (entry.name.endsWith('.js')) sources.push(readFileSync(path, 'utf8'));
        }
    })(new URL('.', root).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

    // A property access, not a bare word. `window`, `source` and `period` are
    // all ordinary identifiers in this codebase and every one of them would
    // match a naked name search on a page that never touches the summary.
    const seen = new Set();
    for (const source of sources) {
        for (const hit of source.matchAll(/[\w)\]]\s*\??\.(\w+)\b/g)) seen.add(hit[1]);
    }

    // Five keys travel without a reader, and each is here because the reason
    // is worth stating rather than because the test was inconvenient. Three of
    // them say so in `cv/publish.py` already; this is where that claim stops
    // being a comment nobody rechecks.
    const unread = {
        // Provenance, and `summary_payload` says so itself: "No page branches
        // on the version, and none should — a client that renders differently
        // per version is two clients." It exists so a document found in the
        // console can be dated against cv/report_json.py::SCHEMA_VERSION.
        'summary_payload.schemaVersion': 'provenance — nothing may branch on it',
        'events_payload.schemaVersion': 'provenance — nothing may branch on it',
        // Derived, and its input is already fully on the screen.
        // `trustworthy` is `not warnings` (cv/report_json.py), and
        // `cvWarnings` in coach/coach.js draws every warning in full. A page
        // rendering the boolean as well would be telling a coach this run
        // cannot be trusted directly above the list of what was wrong with it.
        'summary_payload.trustworthy': 'derived from `warnings`, which is drawn in full',
        // For whoever is tuning the detector, not for a coach: the confidence
        // the event list was trimmed at. `events_payload` explains why no page
        // should draw it — confidence is a model-internal scale nothing on
        // screen explains, so a number a coach cannot act on is worse than the
        // sentence the notes already give them.
        'events_payload.droppedBelowConfidence': 'a tuning figure, deliberately not shown',
        // The naming as it stood when the run was published. Every page joins
        // on `cvMapping/players.byCluster` instead, which a coach can change
        // afterwards; this one records which naming produced the figures in
        // the document beside it.
        'identity_payload.playerByCluster': 'provenance — the live mapping is elsewhere',
    };

    const dead = [];
    for (const [name, destination] of builders) {
        for (const key of published.get(name)) {
            if (seen.has(key) || `${name}.${key}` in unread) continue;
            dead.push(`${key} — published to ${destination} by ${name}`);
        }
    }
    assert.deepEqual(dead, [],
        'cv/publish.py publishes these and no page reads them:\n  '
        + `${dead.join('\n  ')}`);

    // And the allowlist itself has to stay honest: a key that gains a reader,
    // or leaves its payload, should not keep its excuse.
    const stale = Object.keys(unread).filter((entry) => {
        const [name, key] = entry.split('.');
        return !(published.get(name) || []).includes(key) || seen.has(key);
    });
    assert.deepEqual(stale, [],
        'these are excused from needing a reader and no longer need excusing:\n  '
        + `${stale.join('\n  ')}`);
});

/**
 * And every field a client is allowed to write is one something reads.
 *
 * The third and last of the three ways a write and a read can fail to meet.
 * The first two tests above cover a match page reading a field nothing writes,
 * and the pipeline publishing a figure no page draws. This one covers the
 * client's own documents: a field the tablet or the dashboard writes to
 * Firestore on every match, stored, replicated, paid for, and consulted by
 * nothing.
 *
 * `firestore.rules` is the writer of record, and the only honest one. A field
 * not named in a `hasOnly` or a `changed` list cannot reach Firestore at all,
 * whatever the client passes, so the rules are the complete and exact set of
 * what may be stored — 75 fields across eleven document shapes, none of them
 * listed here by hand.
 *
 * A *reader* is one of four things: a property access in served JavaScript, a
 * field named as a string inside `where(...)` or `orderBy(...)`, a name the
 * Python pipeline uses, or a place the rules themselves consult the value.
 *
 * That last one needs a line, because it is where this scan earns its keep. A
 * type check is not a read. `request.resource.data.isStarter is bool` says the
 * write must be a boolean; nothing anywhere looks at the boolean. Counting
 * shape constraints as readers marks every field alive by construction — the
 * first version of this scan did, and found nothing at all, because a rules
 * file that validates a field necessarily mentions it. Comparisons are
 * different and do count: `request.resource.data.source in [...]` restricts a
 * vocabulary, which is a decision made on the value.
 *
 * The three it found were three different things, and only one was a bug.
 *
 * `isStarter` was written by `setLineup` on every match and read by nobody,
 * and it was covering a hole rather than sitting idle: the picker painted from
 * an in-memory flag, so a reload lost the saved XI off the screen while
 * leaving it in the database. The field was already the answer; nothing had
 * ever asked it. `live-tagging/tagging.js::restoreLineup` asks it now.
 *
 * `joinedAt` was written unconditionally by a function that runs on every
 * sign-in, so it held the last time somebody opened the dashboard under a name
 * claiming it held a joining date. Nothing read it, which is the only reason
 * nobody had been misled by it yet.
 *
 * `tappedAt` is genuinely dead and stays, and the escape hatch below costs a
 * sentence on purpose — writing down why a field has no reader is the whole
 * point, and two of the three turned out not to have an answer.
 *
 * The limit is the same one the test above states: this cannot tell which
 * object a `.key` was read off. `source` and `detail` are called dead in
 * `assets/db.js`'s own docstring and pass here, because the rules constrain
 * both values. Short generic names are effectively unchecked. What survives
 * that is the failure worth catching: a field no line anywhere mentions.
 */
test('every field the rules let a client write is one something reads', () => {
    // Comments first: a field named in a comment is not a reader of it, and
    // this file explains itself at length.
    const rules = read('firestore.rules').replace(/\/\/[^\n]*/g, '');

    const values = rules
        .replace(/[\w.$/()]+\s+is\s+\w+/g, ' ')
        .replace(/[\w.$/()]+\.(?:size|keys)\(\)/g, ' ');
    const consulted = new Set(
        [...values.matchAll(/\bdata\.(\w+)/g)].map((hit) => hit[1]),
    );
    assert.ok(consulted.has('coachUids') && consulted.has('version'),
        'no rules file was read');
    assert.ok(!consulted.has('isStarter'),
        'a shape constraint is being counted as a read — the scan is broken');

    // Every document shape the rules give a writable field list, keyed by the
    // path as the rules write it.
    const shapes = new Map();
    const heads = [...rules.matchAll(/match \/(\S+)/g)];
    for (let i = 0; i < heads.length; i += 1) {
        const to = i + 1 < heads.length ? heads[i + 1].index : rules.length;
        const block = rules.slice(heads[i].index, to);
        const fields = new Set();
        for (const list of block.matchAll(/(?:hasOnly|changed)\(\s*\[([^\]]*)\]/g)) {
            for (const field of list[1].matchAll(/'(\w+)'/g)) fields.add(field[1]);
        }
        if (fields.size) shapes.set(heads[i][1], [...fields].sort());
    }

    const total = [...shapes.values()].reduce((sum, list) => sum + list.length, 0);
    assert.ok(shapes.size >= 10 && total >= 70,
        `only found ${total} writable fields across ${shapes.size} shapes`
        + ' — the scan is broken, not the rules');

    // Everything the site serves, plus everything the pipeline runs. The tests
    // are skipped: a fixture naming a field is not a page reading it, and the
    // emulator suite writes most of these by hand.
    const skip = new Set([
        'node_modules', 'PitchIQHelper', '.git', 'runs', 'scratch_frames',
        'baselines', '.firebase', 'tests',
    ]);
    const seen = new Set();
    (function walk(dir) {
        for (const entry of readdirSync(dir, { withFileTypes: true })) {
            if (skip.has(entry.name)) continue;
            const path = `${dir}/${entry.name}`;
            if (entry.isDirectory()) { walk(path); continue; }
            if (entry.name.endsWith('.js')) {
                const source = readFileSync(path, 'utf8');
                for (const hit of source.matchAll(/[\w)\]]\s*\??\.(\w+)\b/g)) {
                    seen.add(hit[1]);
                }
                // A field named rather than reached: `where('emailLower', ...)`.
                for (const hit of source.matchAll(
                    /(?:where|orderBy)\(\s*['"](\w+)['"]/g)) seen.add(hit[1]);
            } else if (entry.name.endsWith('.py')) {
                const source = readFileSync(path, 'utf8');
                for (const hit of source.matchAll(
                    /\.get\(\s*'(\w+)'|\['(\w+)'\]|\.(\w+)\b/g)) {
                    seen.add(hit[1] || hit[2] || hit[3]);
                }
            }
        }
    })(new URL('.', root).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

    assert.ok(seen.has('opponentName') && seen.has('halfTimeClockS'),
        'the reader scan found no served source');

    // One field is stored for nobody, and the reason is already written down
    // twice — in `baseEntry`'s docstring in assets/db.js, and in the roadmap.
    // This is where that claim stops being a comment nobody rechecks.
    const unread = {
        // The only record of when the tap happened rather than when the write
        // landed. `createdAt` is a server timestamp, so an entry the SDK
        // queued while the tablet was off the network is stamped minutes after
        // the moment it describes, and nothing else on the entry could say so.
        // Nothing reads it because nothing has yet had to reconstruct a match
        // whose tablet was offline; the field exists so that day is possible.
        'log/{entryId}.tappedAt': 'the tap time, kept against a queued write',
    };

    const dead = [];
    for (const [shape, fields] of shapes) {
        for (const field of fields) {
            if (seen.has(field) || consulted.has(field)) continue;
            if (`${shape}.${field}` in unread) continue;
            dead.push(`${field} — writable on ${shape}, read nowhere`);
        }
    }
    assert.deepEqual(dead, [],
        'the rules let a client store these and nothing ever reads them:\n  '
        + `${dead.join('\n  ')}`);

    // And an excuse may not outlive its reason: a field that gains a reader,
    // or stops being writable at all, loses its place here.
    const stale = Object.keys(unread).filter((entry) => {
        const cut = entry.lastIndexOf('.');
        const [shape, field] = [entry.slice(0, cut), entry.slice(cut + 1)];
        return !(shapes.get(shape) || []).includes(field)
            || seen.has(field) || consulted.has(field);
    });
    assert.deepEqual(stale, [],
        'these are excused from needing a reader and no longer need excusing:\n  '
        + `${stale.join('\n  ')}`);
});



/**
 * And the preview carries the same shape the pipeline does.
 *
 * `sampleCvSummary` is what all three pages render when a coach presses the
 * sample button, and it is the only version of a match report anybody has
 * looked at, since no footage exists yet. So it is not a fixture in the usual
 * sense — it is the demo, and a key it spells differently from the pipeline is
 * a feature that works in the preview and fails on the first real match.
 *
 * That has already happened once: the sample's keeper block carried `track_id`
 * where the pipeline writes `track_ids`, alongside a dozen stats it did not
 * have at all, so the fixture was not exercising the real shape of the one
 * field nothing rendered.
 *
 * `isSample` is the only key excused, and only in one direction: it is the
 * fixture's own marker, the thing `isSample()` tests for so that a page can
 * label an invented figure as invented, and it has no business in a document
 * written from real footage.
 */
test('the sample summary carries the keys a published one does', async () => {
    const { sampleCvSummary } = await import('../assets/sample-report.js');
    const payload = read('cv/publish.py');
    const from = payload.indexOf('def summary_payload(');
    const body = payload.slice(from, payload.indexOf('\ndef ', from + 1));
    const published = [...body.matchAll(/^\s{8}'(\w+)':/gm)].map((m) => m[1]).sort();

    const sample = Object.keys(sampleCvSummary())
        .filter((key) => key !== 'isSample')
        .sort();

    assert.deepEqual(sample, published,
        'the preview and the pipeline disagree about what a summary holds');
});


/**
 * The shot map's marks, as controls.
 *
 * There is no other suite this can live in. `tests/video.test.js` covers pure
 * functions and cannot import a module that calls `createElementNS`; the
 * emulator suites never build an element. So the one renderer in the repo that
 * puts interactive controls inside an `<svg>` had no test of any kind until the
 * day it turned out not to have controls at all — a click listener on a bare
 * `<circle>` under `role="img"`, styled `cursor: pointer`, with a docstring
 * calling it a button.
 *
 * What is pinned here is the contract, not the shapes: a mark you can activate
 * has a role, a tab stop, a name, and answers to the two keys a real button
 * answers to; a mark you cannot activate has none of them and the map stays a
 * picture. Space is checked for `preventDefault` specifically, because on both
 * pages that draw this map the video being seeked to sits above it, and the
 * unprevented keystroke would jump the video and scroll it out of view at once.
 */
test('a shot mark you can activate is one a keyboard can reach', async () => {
    live?.restore();
    live = installDom('<main></main>', { url: 'http://localhost:5000/coach/' });

    const { shotMapSvg, markLabel } = await import(
        new URL('assets/shot-map.js', root).href
    );

    const marks = [
        { x_m: 96, y_m: 34, xg: 0.42, outcome: 'goal', on_target: true, video_s: 120 },
        { x_m: 78, y_m: 24, xg: 0.05, outcome: 'saved', on_target: true, video_s: 300 },
    ];
    const names = new Set(marks.map((m) => markLabel(m)));

    const inert = shotMapSvg(marks);
    assert.equal(inert.getAttribute('role'), 'img',
        'a map with nothing to activate is a picture');
    const still = inert.querySelectorAll('.shot-mark');
    assert.equal(still.length, marks.length, 'the map drew the wrong number of marks');
    for (const dot of still) {
        assert.equal(dot.getAttribute('tabindex'), null,
            'an inert mark is a tab stop that does nothing');
        assert.equal(dot.getAttribute('role'), null);
        assert.ok(!dot.classList.contains('is-pickable'));
    }

    const picked = [];
    const map = shotMapSvg(marks, { onPick: (mark) => picked.push(mark) });
    assert.equal(map.getAttribute('role'), 'group',
        'role="img" would collapse the marks into the picture and announce them as nothing');

    const dots = map.querySelectorAll('.shot-mark');
    assert.equal(dots.length, marks.length);
    for (const dot of dots) {
        assert.equal(dot.getAttribute('role'), 'button');
        assert.equal(dot.getAttribute('tabindex'), '0');
        // `<title>` is a hover tooltip; a control needs a name of its own.
        assert.ok(names.has(dot.getAttribute('aria-label')),
            `a mark is named ${JSON.stringify(dot.getAttribute('aria-label'))}, which is not one of its shots`);
    }

    for (const key of ['Enter', ' ']) {
        picked.length = 0;
        const event = new KeyboardEvent('keydown', { key, bubbles: true });
        dots[0].dispatchEvent(event);
        assert.equal(picked.length, 1, `${JSON.stringify(key)} did not activate the mark`);
        assert.equal(event.defaultPrevented, true,
            `${JSON.stringify(key)} was left to the page — Space would scroll the video out of view`);
    }

    picked.length = 0;
    const other = new KeyboardEvent('keydown', { key: 'a', bubbles: true });
    dots[0].dispatchEvent(other);
    assert.equal(picked.length, 0, 'every key seeked the video, not the two that should');
    assert.equal(other.defaultPrevented, false);

    picked.length = 0;
    dots[0].dispatchEvent(new Event('click', { bubbles: true }));
    assert.equal(picked.length, 1, 'the pointer path broke while the keyboard one was added');
});

// ------------------------------------------------------------------- pages

test('the landing page greets a signed-in coach with their squads', async () => {
    await openPage({
        html: 'index.html',
        entry: 'assets/landing.js',
        url: 'http://localhost:5000/',
    });

    assert.ok(!shown('loading'), 'the spinner is still up');
    assert.match(live.document.body.textContent, /Riverside High/);
});

test('the coach dashboard lists the squad and its matches', async () => {
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
    });

    assert.ok(!shown('loading'));
    const body = live.document.body.textContent;
    assert.match(body, /Rae Nkemelu/, 'the roster did not render');
    assert.match(body, /Northgate/, 'the match list did not render');
});

test('opening a second player replaces the first one, rail included', async () => {
    // The other bug this suite exists for. `mountRail` builds once and keeps
    // the callback it was handed, so a rail closing over its arguments pins
    // itself to whichever subject was opened first — and goes on reporting that
    // student's matches, minutes and goals beside every student after them. The
    // numbers are well formed, plausible, and somebody else's, which is why
    // reading the page is the only way this was ever going to be caught.
    const open = (name) => {
        const row = live.document.querySelectorAll('.open-player')
            .find((button) => button.closest('li, tr, div')?.textContent.includes(name));
        assert.ok(row, `no way to open ${name}`);
        row.click();
    };

    open('Alex Vega');
    await settle();
    assert.equal(text('pv-name'), 'Alex Vega');
    const alex = text('pv-rail');
    assert.match(alex, /2 matches|Matches/, 'the rail carries no facts at all');
    assert.match(alex, /152/, "Alex's minutes are missing from the rail");

    open('Rae Nkemelu');
    await settle();
    assert.equal(text('pv-name'), 'Rae Nkemelu');
    const rae = text('pv-rail');
    assert.notEqual(rae, alex, 'the rail beside the second player is the first');
    assert.doesNotMatch(rae, /152/, "the rail still shows Alex's minutes");
    assert.match(rae, /without a clock/, 'a season with no measured minutes reads as measured');
});

test('the review tool counts one list once', async () => {
    // Found by loading this page for the first time. The filter chips and the
    // truncation note under the rows are two counts of the same list, six lines
    // apart, and they disagreed by exactly the number of hand-tagged entries —
    // which `all` shows and the chip did not count.
    const documents = await filmed();
    const candidates =
        documents[`teams/${TEAM_ID}/matches/${MATCH_ID}/cvStats/events`].events.length;

    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'filmed',
        documents,
    });

    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    assert.ok(shown('cv-review-block'), 'the review tool never opened');

    const chip = live.document.querySelectorAll('#cv-review-filters .chip')
        .find((c) => c.textContent.startsWith('Everything'));
    const tagged = live.document.querySelectorAll('#cv-review-filters .chip')
        .find((c) => c.textContent.startsWith('tagged by hand'));
    assert.ok(chip && tagged, 'the filter chips are missing');

    const number = (node) => Number(/\((\d+)\)/.exec(node.textContent)?.[1]);
    const shownOf = Number(
        /of (\d+)\./.exec(el('cv-review-list').textContent)?.[1] ?? NaN,
    );

    assert.ok(shownOf > 0, 'the list is short enough that nothing was truncated');
    assert.equal(number(chip), shownOf,
        'the Everything chip and the row count disagree about one list');
    assert.equal(number(chip), number(tagged) + candidates,
        'Everything is not the candidates plus the tagged log');
});

test('a verdict taken back is a verdict taken back', async () => {
    // Found by tapping the buttons. `decide` clears a verdict when the same one
    // is tapped twice, keeping whatever the shot ledger had put beside it —
    // but it kept that under `if (kept)`, and `kept` is an object either way.
    // So the delete never ran and an undone verdict left `{}` behind: an entry
    // with no verdict in it that three separate counts still read as a checked
    // event.
    //
    // The costly one is the last: "Not checked yet" hid the row, so a mis-tap
    // took an event out of the only list built for working through them, and
    // the scorecard below went on correctly reporting nothing checked while the
    // line above it said one.
    const documents = await filmed();
    const path = `teams/${TEAM_ID}/matches/${MATCH_ID}/cvStats/events`;
    // Short enough that nothing is truncated, so a filtered list can be counted
    // exactly rather than against the 200-row cap.
    const events = documents[path].events.slice(0, 6);
    documents[path] = { ...documents[path], events };

    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'undo',
        documents,
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    const rows = () => live.document.querySelectorAll('.review-row');
    const chip = (label) => live.document.querySelectorAll('#cv-review-filters .chip')
        .find((c) => c.textContent.startsWith(label));
    const clockOf = (row) => row.querySelector('.review-clock').textContent.trim();

    const before = rows().length;
    assert.ok(before >= 2, 'the review list came up empty');
    const clock = clockOf(rows()[0]);

    rows()[0].querySelector('[data-act="confirmed"]').click();
    await settle();
    assert.match(text('cv-review-progress'), /^1 of \d+ checked/,
        'confirming a row did not count');

    rows()[0].querySelector('[data-act="confirmed"]').click();
    await settle();

    assert.match(text('cv-review-progress'), /^0 of \d+ checked/,
        'the progress line still counts a verdict that was taken back');
    assert.ok(!text('cv-review-progress').includes('% of those were real'),
        'nothing has been checked, so nothing can be a share of what was real');
    assert.ok(!rows()[0].className.includes('is-'),
        `the row kept a verdict class: ${rows()[0].className}`);

    chip('Not checked yet').click();
    await settle();
    assert.equal(rows().length, before,
        'un-confirming a row hid it from the list of rows still to check');
    assert.ok(rows().map(clockOf).includes(clock),
        `the row taken back at ${clock} is not in the unchecked list`);

    // And nothing is left in the document either — an empty map per mis-tap
    // would eat the 1500-entry budget `firestore.rules` allows.
    //
    // A real wait, not `settle`: the save is debounced on a 600 ms timer, and
    // spinning microtasks does not move a timer. Asserting on the document
    // before the debounce has fired would pass against any bug at all.
    await new Promise((resolve) => setTimeout(resolve, 800));
    await settle();
    const stored = snapshotOf(
        `teams/${TEAM_ID}/matches/${MATCH_ID}/cvReview/decisions`,
    );
    assert.deepEqual(stored?.byEvent ?? {}, {},
        'an entry with no verdict in it was written to the match');
});

test('marking what a shot did is not a verdict on whether it was one', async () => {
    // The other half of the entry above, and the one that arrives honestly:
    // tapping "Saved" in the shot ledger writes `{result: 'saved'}` against an
    // event nobody has yet agreed was a shot. `reviewScore` has always known
    // the difference — "counting it as a confirmation would let the xG log
    // quietly inflate this scorecard" — while the progress line, the rail badge
    // and the "not checked yet" filter counted every key in the map.
    //
    // This is also the first test to open the shot ledger and the xG check at
    // all. Both were unreachable until the fixture had a shot in it.
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'ledger',
        documents: await filmed(),
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    const shots = () => live.document.querySelectorAll('.shot-row');
    const railFor = (label) => live.document
        .querySelectorAll('#match-rail button, #match-rail a')
        .find((n) => n.textContent.includes(label))?.textContent.trim();

    assert.ok(shots().length, 'the shot ledger did not open');
    assert.ok(el('cv-xg-check').classList.contains('hidden'),
        'the xG check drew a comparison before anything was marked');
    assert.match(text('cv-review-progress'), /^0 of \d+ checked/);
    const reviewBefore = railFor('Review');
    const progressBefore = text('cv-review-progress');

    for (const row of shots()) {
        row.querySelectorAll('.shot-results .btn')
            .find((b) => b.textContent === 'Saved')
            .click();
        await settle(4);
    }
    await settle();

    // Marked, and the check below now has something to check.
    assert.match(text('cv-shotlog-note'), /^5 of 5 shots marked/);
    assert.ok(!el('cv-xg-check').classList.contains('hidden'),
        'five marked shots did not open the xG check');
    assert.match(text('cv-xg-check'), /the model expected/);

    // And nothing above it moved, because none of that was a verdict.
    assert.equal(text('cv-review-progress'), progressBefore,
        'marking a shot outcome counted as checking the event');
    assert.equal(railFor('Review'), reviewBefore,
        'the rail counted five marked shots as five events checked');
    assert.ok(el('cv-scorecard').classList.contains('hidden'),
        'the scorecard scored events nobody has given a verdict on');

    // The marks themselves are kept — this is only about what they mean.
    await new Promise((resolve) => setTimeout(resolve, 800));
    await settle();
    const stored = snapshotOf(
        `teams/${TEAM_ID}/matches/${MATCH_ID}/cvReview/decisions`,
    )?.byEvent ?? {};
    assert.equal(Object.keys(stored).length, 5, 'the marks were not saved');
    for (const [id, entry] of Object.entries(stored)) {
        assert.equal(entry.result, 'saved', id);
        assert.equal(entry.status, undefined,
            `${id} was given a verdict nobody tapped`);
    }

    // Opened again the next day, which is where this actually bit: the progress
    // line is written on load and on every verdict, and marking a shot outcome
    // does not redraw it — so in one sitting the wrong count was merely late,
    // and coming back to the match was what showed it.
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'ledger-again',
        documents: {
            ...await filmed(),
            [`teams/${TEAM_ID}/matches/${MATCH_ID}/cvReview/decisions`]: {
                byEvent: stored, missed: [], updatedBy: COACH.uid,
            },
        },
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    assert.equal(text('cv-review-progress'), progressBefore,
        'five shot outcomes came back as five checked events');
    assert.equal(railFor('Review'), reviewBefore,
        'the rail took five marked shots off the work still to do');
    assert.ok(el('cv-scorecard').classList.contains('hidden'),
        'the scorecard scored events nobody has given a verdict on');
});

test('publishing says so on the page the coach is looking at', async () => {
    // The write that reaches children, and the one place the screen most
    // needed to agree with the database. `doPublish` refreshed the dashboard
    // *behind* the match view and left the view itself reading
    // `finalized: false` — so after the most consequential thing a coach does,
    // the button still said "Publish player reports" and the subtitle still
    // omitted it, with a toast that clears itself in under three seconds as
    // the only sign anything had happened.
    const documents = fixture();
    const match = `teams/${TEAM_ID}/matches/${MATCH_ID}`;
    documents[match].status = 'full_time';
    documents[`${match}/log/dev-a_000009`] = {
        kind: 'period', type: 'full_time', matchClockS: 5400, side: null,
        playerId: null, assistPlayerId: null, cardColor: null, subOutId: null,
        subInId: null, detail: null, source: 'live_tag', seq: 9,
        deviceId: 'dev-a', revert: null, tappedAt: 0, createdBy: COACH.uid,
    };

    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'publish',
        documents,
    });

    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();
    assert.equal(text('btn-publish'), 'Publish player reports');

    el('btn-publish').click();
    await settle(40);

    assert.equal(text('btn-publish'), 'Re-publish player reports');
    assert.match(text('match-sub'), /reports published/);
    assert.equal(snapshotOf(match).finalized, true);

    // And the reports themselves: one per squad member, with the minutes the
    // stints actually add up to across the substitution.
    const written = Object.fromEntries(
        pathsUnder(`${match}/playerReports/`)
            .map((path) => { const r = snapshotOf(path); return [r.playerName, r]; }),
    );
    assert.deepEqual(Object.keys(written).sort(),
        ['Alex Vega', 'Rae Nkemelu', 'Sam Okonjo']);
    assert.equal(written['Alex Vega'].minutesPlayed, 30, 'came off on 30');
    assert.equal(written['Sam Okonjo'].minutesPlayed, 60, 'came on on 30');
    assert.equal(written['Rae Nkemelu'].minutesPlayed, 90, 'was on throughout');
    // Only the student who has signed in gets a uid on their report; the rest
    // are claimed when they do.
    assert.equal(written['Rae Nkemelu'].linkedUid, STUDENT.uid);
    assert.equal(written['Alex Vega'].linkedUid, null);
});

test('the player portal opens a season for the student it belongs to', async () => {
    await openPage({
        html: 'player/index.html',
        entry: 'player/player.js',
        url: 'http://localhost:5000/player/',
        user: STUDENT,
    });

    assert.ok(!shown('loading'));

    // And then the match under it, because the season list is not where the
    // figures are. Everything below this line is the other half of the
    // published-field guard further up the file: that one proves
    // `cvPositionNoiseM` is mentioned somewhere in the served JavaScript, which
    // is as far as a scan of source text can go. This proves the mention puts
    // it on a screen — the thing that was actually missing for as long as the
    // field existed, and the thing a scan can never tell you.
    const row = live.document.querySelectorAll('.match-row')
        .find((button) => button.textContent.includes('Westbrook'));
    assert.ok(row, 'the season list offers no match to open');
    row.click();
    await settle();
    assert.ok(shown('view-match'), 'the match never opened');

    const note = text('md-stats-note');
    assert.match(note, /wobbled about 0\.42m/, 'the wobble on their own track is unsaid');
    assert.match(note, /count of bursts is a count of that wobble/);
    // Said about a card that is genuinely absent. A note explaining away a
    // number sitting directly above it would be worse than no note, and the
    // fixture is built to be exactly that case: 0.42m is past the ceiling in
    // cv/metrics.py, so the pipeline withheld the count.
    assert.doesNotMatch(text('md-stats'), /Bursts/);
});

test('the half-time page reads the half', async () => {
    await openPage({
        html: 'halftime/index.html',
        entry: 'halftime/halftime.js',
        url: `http://localhost:5000/halftime/?team=${TEAM_ID}&match=${MATCH_ID}`,
    });

    assert.ok(shown('view-report'), 'the report never opened');
    assert.equal(text('ht-us'), 'Riverside High');
    assert.equal(text('ht-them'), 'Northgate');
    assert.equal(text('ht-score-us'), '1');
    assert.equal(text('ht-score-them'), '0');

    // The two things the page exists to say: who is a booking away from a red,
    // and who has run the whole half.
    const decisions = el('decisions').textContent;
    assert.match(decisions, /Rae Nkemelu is on a yellow/);
    assert.match(decisions, /25′/, 'the card minute is missing');
    assert.match(el('minutes').textContent, /Sam Okonjo/);
});

test('the live-tagging tool wires up its buttons for a signed-in coach', async () => {
    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=${MATCH_ID}`,
    });

    // The eight-day bug, in one assertion. `init()` threw on its second
    // statement, so nothing below it ever ran and every button on the tablet
    // was inert while the page looked completely normal.
    assert.ok(globalThis.window._tagger, 'the test seam was never installed');
    assert.ok(!shown('view-signin'), 'a signed-in coach was shown the sign-in screen');
});

// ------------------------------------------------------ the tablet, tap by tap
//
// Every number in this system is derived from what somebody tapped here, and
// until now this tool had exactly one assertion against it — that `init()` ran.
// The walkthrough below is the demo path: pick a match, set a lineup, kick off,
// tag, substitute, undo, half-time, restart. It reads the database back after
// each tap rather than the screen, because what the screen said and what
// reached Firestore is precisely the pair that came apart in August.

const entries = (matchId) => pathsUnder(`teams/${TEAM_ID}/matches/${matchId}/log/`)
    .sort()
    .map((path) => {
        const e = snapshotOf(path);
        return `${e.kind}/${e.type}@${Math.round(e.matchClockS)}`;
    });

const stints = (matchId) => Object.fromEntries(
    pathsUnder(`teams/${TEAM_ID}/matches/${matchId}/roster/`).map((path) => {
        const r = snapshotOf(path);
        return [r.playerName, { on: r.isActive, v: r.version, stints: r.stints }];
    }),
);

const activeView = () => live.document.querySelectorAll('.view')
    .filter((v) => v.classList.contains('active')).map((v) => v.id).join(',');

test('a match can be tagged from the first tap to the second half', async () => {
    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=match-2`,
        variant: 'walkthrough',
    });

    // ---- pick the match
    const cards = live.document.querySelectorAll('#match-cards .match-card');
    assert.ok(cards.length, 'no match to pick');
    cards.find((c) => c.textContent.includes('Eastvale')).click();
    await settle();

    // ---- the lineup. Two start and one sits, because a squad with nobody on
    // the bench cannot be substituted and would walk past the half of this
    // that matters.
    const picks = live.document.querySelectorAll('#roster-list .pick');
    assert.equal(picks.length, 3, 'the squad did not reach the lineup picker');
    picks.find((p) => p.textContent.includes('Rae')).click();
    picks.find((p) => p.textContent.includes('Alex')).click();
    await settle();
    assert.equal(text('starter-count'), '2');

    el('btn-save-lineup').click();
    await settle();
    assert.match(activeView(), /view-kickoff/, 'a saved lineup did not reach kick-off');

    // A starter's stint opens at zero the moment the lineup is saved — hours
    // before kick-off — which is the write that made `whistleFrom` necessary.
    const lineup = stints('match-2');
    assert.equal(Object.keys(lineup).length, 3, 'the whole squad was not written');
    assert.deepEqual(lineup['Rae Nkemelu'].stints, [{ inS: 0, outS: null }]);
    // And a substitute gets no stint at all rather than one of zero length —
    // an unused substitute has not played, which is not the same as having
    // played none of it.
    assert.deepEqual(lineup['Sam Okonjo'].stints, []);
    assert.equal(lineup['Sam Okonjo'].on, false);
    assert.deepEqual(entries('match-2'), [], 'a lineup wrote a log entry');

    // ---- kick off
    el('btn-kickoff').click();
    await settle();
    assert.match(activeView(), /view-live/);
    assert.deepEqual(entries('match-2'), ['period/kickoff_1st@0']);

    // ---- one tagged event, through the side sheet
    live.document.querySelectorAll('.ev')
        .find((b) => /corner/i.test(b.textContent)).click();
    await settle();
    assert.ok(el('overlay-event').classList.contains('open'),
        'the tag sheet never opened');
    globalThis.window._tagger.onSideChosen('us');
    await settle();
    assert.equal(entries('match-2').length, 2, 'the corner never reached the log');

    // ---- a substitution, which is the arithmetic the minutes column is made of
    el('btn-sub').click();
    await settle();
    assert.ok(el('overlay-sub').classList.contains('open'), 'the sub sheet never opened');
    assert.equal(el('btn-sub-confirm').disabled, true,
        'confirm was live before anybody had been chosen');

    live.document.querySelectorAll('#sub-off-list .pick')
        .find((p) => p.textContent.includes('Rae')).click();
    const bench = live.document.querySelectorAll('#sub-on-list .pick');
    assert.equal(bench.length, 1, 'the bench is not who was left out of the lineup');
    bench[0].click();
    await settle();
    assert.equal(el('btn-sub-confirm').disabled, false);

    el('btn-sub-confirm').click();
    await settle();
    const after = stints('match-2');
    assert.equal(after['Rae Nkemelu'].on, false, 'the player who came off is still on');
    assert.equal(after['Rae Nkemelu'].stints[0].outS != null, true,
        "the outgoing player's stint never closed");
    assert.equal(after['Rae Nkemelu'].v, 1, 'the version did not move');

    // ---- and undo puts all three writes back
    el('btn-undo').click();
    await settle();
    const undone = stints('match-2');
    assert.equal(undone['Rae Nkemelu'].on, true, 'undo left the player off');
    assert.deepEqual(undone['Rae Nkemelu'].stints, [{ inS: 0, outS: null }],
        'undo left a closed stint behind');
    assert.equal(entries('match-2').length, 2, 'undo left the log entry');

    // ---- half-time, and the restart
    assert.equal(text('btn-period'), 'Half-time');
    el('btn-period').click();
    await settle();
    const atBreak = snapshotOf(`teams/${TEAM_ID}/matches/match-2`);
    assert.equal(atBreak.status, 'halftime');
    // Without this the second half's video offset is a guess, and every
    // second-half moment lands late by however long the break ran.
    assert.ok(atBreak.halfTimeClockS != null, 'the break was not written to the match');
    assert.equal(text('btn-period'), 'Start 2nd half');

    el('btn-period').click();
    await settle();
    assert.equal(
        snapshotOf(`teams/${TEAM_ID}/matches/match-2`).status, 'second_half',
    );
    assert.match(entries('match-2').join(' '), /period\/kickoff_2nd/);
});

test('a tagger who had to restart lands back in the match, not in the lineup', async () => {
    // The setup screen promises this in as many words — "if you started one
    // earlier and had to stop, choose it again, nothing you already tapped is
    // lost" — and a tablet that dies mid-half is the likeliest thing to happen
    // on the day.
    const documents = fixture();
    documents[`teams/${TEAM_ID}/matches/${MATCH_ID}`].status = 'first_half';
    delete documents[`teams/${TEAM_ID}/matches/${MATCH_ID}/log/dev-a_000008`];

    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=${MATCH_ID}`,
        variant: 'resume',
        documents,
    });

    const card = live.document.querySelectorAll('#match-cards .match-card')
        .find((c) => c.textContent.includes('Northgate'));
    assert.ok(card, 'the match in progress was not offered');
    assert.match(card.textContent, /resume/, 'a match in progress was offered as new');

    card.click();
    await settle();

    assert.match(activeView(), /view-live/, 'resuming asked for a lineup again');
    // The clock picks up from the last thing anybody tapped, not from zero.
    assert.equal(Math.round(globalThis.window._tagger.state.clockOffset), 2100);

    // And a player who has already been off is offered back, marked as such,
    // rather than being hidden or offered as though they were fresh.
    el('btn-sub').click();
    await settle();
    const back = live.document.querySelectorAll('#sub-on-list .pick')
        .find((p) => p.textContent.includes('Alex Vega'));
    assert.ok(back, 'a substituted player cannot come back on');
    assert.match(back.textContent, /been on/);
});

test('a lineup saved before kick-off survives a reload of the tablet', async () => {
    // Between the team sheet and the whistle is most of an hour on a Saturday,
    // and a tablet that has been closed and reopened in it is the ordinary
    // case rather than the unlucky one. The roster document survives that; the
    // picker's memory of who was tapped does not, and until `isStarter` had a
    // reader the saved eleven was safe in Firestore and off the screen — on a
    // page that tells the coach "you can change this later".
    const documents = fixture();
    const roster = (id, playerName, jerseyNumber, isStarter) => {
        documents[`teams/${TEAM_ID}/matches/match-2/roster/${id}`] = {
            playerName, jerseyNumber, isStarter, isActive: isStarter,
            stints: isStarter ? [{ inS: 0, outS: null }] : [],
            version: 0,
        };
    };
    roster('p-rae', 'Rae Nkemelu', 7, true);
    roster('p-sam', 'Sam Okonjo', 14, true);
    roster('p-alex', 'Alex Vega', 9, false);

    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=match-2`,
        variant: 'saved-lineup',
        documents,
    });

    live.document.querySelectorAll('#match-cards .match-card')
        .find((card) => card.textContent.includes('Eastvale')).click();
    await settle();

    // A coach who is ready to start still lands where they always did. The
    // lineup is what is *behind* this screen, not in front of it.
    assert.match(activeView(), /view-kickoff/, 'a saved lineup did not resume');

    el('btn-back-setup').click();
    await settle();
    assert.match(activeView(), /view-setup/);
    assert.ok(shown('lineup-block'), 'going back landed somewhere else');
    assert.ok(!shown('match-picker'), 'the saved lineup was offered as a new one');
    assert.equal(text('starter-count'), '2', 'the saved eleven came back wrong');

    const pressed = live.document.querySelectorAll('#roster-list .pick')
        .filter((pick) => pick.getAttribute('aria-pressed') === 'true')
        .map((pick) => pick.textContent);
    assert.equal(pressed.length, 2, `${pressed.length} shown as starting`);
    assert.ok(pressed.some((name) => name.includes('Rae'))
        && pressed.some((name) => name.includes('Sam')),
        `the wrong players came back pressed: ${pressed.join(' / ')}`);

    // And the correction that was impossible before now saves. Every one of
    // these documents already exists at version 0, so this is an update under
    // an optimistic lock rather than a create — the write `firestore.rules`
    // accepts is the one that bumps the version, and the create-shaped write
    // this used to send would be refused.
    const pick = (name) => live.document.querySelectorAll('#roster-list .pick')
        .find((row) => row.textContent.includes(name));
    pick('Sam').click();
    pick('Alex').click();
    await settle();
    assert.equal(text('starter-count'), '2');

    el('btn-save-lineup').click();
    await settle();
    assert.match(activeView(), /view-kickoff/, 'the correction did not save');

    const lineup = stints('match-2');
    assert.deepEqual(lineup['Alex Vega'],
        { on: true, v: 1, stints: [{ inS: 0, outS: null }] });
    // Dropped, and dropped all the way: a player who no longer starts has no
    // stint at all, not one of zero length.
    assert.deepEqual(lineup['Sam Okonjo'], { on: false, v: 1, stints: [] });
    assert.equal(lineup['Rae Nkemelu'].v, 1, 'an untouched starter was left behind');

    const saved = (id) =>
        snapshotOf(`teams/${TEAM_ID}/matches/match-2/roster/${id}`).isStarter;
    assert.deepEqual([saved('p-rae'), saved('p-alex'), saved('p-sam')],
        [true, true, false], 'the corrected lineup is not what was picked');
});

test('the tablet keeps working when the server stops answering', async () => {
    // The most consequential bug this project has shipped, as an assertion.
    // Firestore makes a write durable the instant it is issued and resolves its
    // promise only on *server* acknowledgement — so six handlers that awaited
    // that promise before touching the screen did nothing at all at a field
    // with no signal. The sheet stayed open, the strip stayed stale, and the
    // remedy a person reaches for is to tap again.
    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=${MATCH_ID}`,
        variant: 'offline',
        documents: (() => {
            const docs = fixture();
            docs[`teams/${TEAM_ID}/matches/${MATCH_ID}`].status = 'first_half';
            delete docs[`teams/${TEAM_ID}/matches/${MATCH_ID}/log/dev-a_000008`];
            return docs;
        })(),
    });

    live.document.querySelectorAll('#match-cards .match-card')
        .find((c) => c.textContent.includes('Northgate')).click();
    await settle();
    const before = entries(MATCH_ID).length;

    goOffline();

    // ---- a tag, with the signal gone
    live.document.querySelectorAll('.ev')
        .find((b) => /corner/i.test(b.textContent)).click();
    await settle();
    globalThis.window._tagger.onSideChosen('them');
    await settle();

    assert.ok(!el('overlay-event').classList.contains('open'),
        'the sheet stayed open with no signal — the tap looks ignored');
    assert.equal(entries(MATCH_ID).length, before + 1,
        'the tag never reached the local cache');
    assert.ok(queuedWrites() > 0, 'nothing was left waiting for the server');

    // ---- and a substitution, which is the one that corrupts minutes when a
    // tagger presses Confirm again because nothing appeared to happen.
    el('btn-sub').click();
    await settle();
    live.document.querySelectorAll('#sub-off-list .pick')
        .find((p) => p.textContent.includes('Rae')).click();
    live.document.querySelectorAll('#sub-on-list .pick')[0].click();
    await settle();
    el('btn-sub-confirm').click();
    await settle();

    assert.ok(!el('overlay-sub').classList.contains('open'),
        'the substitution sheet never closed with no signal');
    const off = stints(MATCH_ID)['Rae Nkemelu'];
    assert.equal(off.on, false, 'the substitution did not reach the local cache');

    // Pressing Confirm again must do nothing. It used to queue a second
    // substitution, closing and reopening a stint each time — which is the
    // arithmetic the minutes column is made of.
    const logged = entries(MATCH_ID).length;
    el('btn-sub-confirm').click();
    el('btn-sub-confirm').click();
    await settle();
    assert.equal(entries(MATCH_ID).length, logged,
        'pressing Confirm again queued another substitution');

    // ---- the signal comes back and everything settles with no duplicates
    await goOnline();
    await settle();
    assert.equal(queuedWrites(), 0);
    assert.equal(entries(MATCH_ID).length, logged);
    assert.deepEqual(
        stints(MATCH_ID)['Rae Nkemelu'].stints.filter((s) => s.outS != null).length, 1,
        'the stint was closed more than once',
    );
});

test('a refused undo leaves the roster the way the record has it', async () => {
    // `sendWrite`'s compensator exists for one case: the server said no, so
    // whatever the screen did optimistically has to come back off it. Undo
    // moves four things and the compensator used to restore two — the two that
    // are easy to see. The roster is not one of them, and the roster is what
    // the event sheet offers to attribute the next goal to.
    //
    // So: make a substitution that lands, then undo it against a server that
    // refuses. The record still holds the substitution. If the screen keeps the
    // undo, the next goal is offered to a player the record has on the bench.
    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=${MATCH_ID}`,
        variant: 'refused-undo',
        documents: (() => {
            const docs = fixture();
            const match = docs[`teams/${TEAM_ID}/matches/${MATCH_ID}`];
            match.status = 'first_half';
            match.halfTimeClockS = null;
            delete docs[`teams/${TEAM_ID}/matches/${MATCH_ID}/log/dev-a_000008`];
            return docs;
        })(),
    });

    live.document.querySelectorAll('#match-cards .match-card')
        .find((c) => c.textContent.includes('Northgate')).click();
    await settle();
    const logged = entries(MATCH_ID).length;

    // Rae off, Alex on — the only two the fixture allows, and it puts them on
    // opposite sides of the sheet.
    el('btn-sub').click();
    await settle();
    live.document.querySelectorAll('#sub-off-list .pick')
        .find((p) => p.textContent.includes('Rae')).click();
    live.document.querySelectorAll('#sub-on-list .pick')
        .find((p) => p.textContent.includes('Alex')).click();
    await settle();
    el('btn-sub-confirm').click();
    await settle();

    assert.equal(entries(MATCH_ID).length, logged + 1, 'the substitution was not logged');
    assert.equal(stints(MATCH_ID)['Rae Nkemelu'].on, false, 'Rae did not come off');
    assert.equal(stints(MATCH_ID)['Alex Vega'].on, true, 'Alex did not come on');

    refuseWrites('permission denied');
    el('btn-undo').click();
    await settle();
    acceptWrites();

    // Nothing of the undo landed, which is the premise of the rest of this.
    assert.equal(entries(MATCH_ID).length, logged + 1,
        'the refused undo deleted the entry anyway');
    assert.equal(stints(MATCH_ID)['Rae Nkemelu'].on, false,
        'the refused undo put Rae back in the record');
    assert.equal(stints(MATCH_ID)['Alex Vega'].on, true,
        'the refused undo took Alex out of the record');

    // And the screen agrees with it. This is the assertion that failed before:
    // the roster was left reverted, so the goal sheet offered Rae — who the
    // record has on the bench — and not Alex, who is on the field.
    live.document.querySelectorAll('.ev')
        .find((b) => b.dataset.event === 'goal').click();
    await settle();
    globalThis.window._tagger.onSideChosen('us');
    await settle();

    const offered = live.document.querySelectorAll('#player-choices .pick')
        .map((p) => p.textContent).join(' / ');
    assert.ok(offered.includes('Alex'),
        `a goal cannot be given to the player who is on: ${offered}`);
    assert.ok(!offered.includes('Rae'),
        `a goal can be given to a player the record has on the bench: ${offered}`);
});

test('a refused period tap leaves the clock and the button where they were', async () => {
    // The other half of the same rule, on the control that is hardest to
    // recover from by hand. Half-time freezes the clock, relabels the period
    // and writes the reading the second half is anchored to; a compensator
    // that restores only the status leaves a screen saying the half is over
    // against a record that says it is still running.
    await openPage({
        html: 'live-tagging/index.html',
        entry: 'live-tagging/tagging.js',
        url: `http://localhost:5000/live-tagging/?team=${TEAM_ID}&match=${MATCH_ID}`,
        variant: 'refused-period',
        documents: (() => {
            const docs = fixture();
            const match = docs[`teams/${TEAM_ID}/matches/${MATCH_ID}`];
            match.status = 'first_half';
            match.halfTimeClockS = null;
            delete docs[`teams/${TEAM_ID}/matches/${MATCH_ID}/log/dev-a_000008`];
            return docs;
        })(),
    });

    live.document.querySelectorAll('#match-cards .match-card')
        .find((c) => c.textContent.includes('Northgate')).click();
    await settle();
    assert.equal(text('period-label'), '1st half');

    // ---- the tap the server refuses
    refuseWrites('permission denied');
    el('btn-period').click();
    await settle();
    acceptWrites();

    assert.equal(text('period-label'), '1st half',
        'a refused half-time left the screen in the break');
    assert.equal(text('btn-period'), 'Half-time',
        'a refused half-time left the button offering the second half');
    assert.equal(snapshotOf(`teams/${TEAM_ID}/matches/${MATCH_ID}`).status, 'first_half',
        'a refused half-time reached the record');

    // ---- and now the tap that lands, undone against a refusal
    el('btn-period').click();
    await settle();
    assert.equal(text('btn-period'), 'Start 2nd half', 'half-time did not take');

    refuseWrites('permission denied');
    el('btn-undo').click();
    await settle();
    acceptWrites();

    assert.equal(snapshotOf(`teams/${TEAM_ID}/matches/${MATCH_ID}`).status, 'halftime',
        'the refused undo restarted the half in the record');
    assert.equal(text('btn-period'), 'Start 2nd half',
        'the screen came out of the break the record still says it is in');
});

test('the calibrate page comes up with its picker ready', async () => {
    await openPage({
        html: 'calibrate/index.html',
        entry: 'calibrate/calibrate.js',
        url: 'http://localhost:5000/calibrate/',
        user: null,
    });

    assert.ok(globalThis.window._calib, 'the calibration seam was never installed');
    assert.equal(
        live.document.querySelectorAll('#landmark-list button').length, 27,
        'the landmark list is not the 27 places pitch-model.js knows',
    );
});

test('the calibrate page says which of four things is wrong', async () => {
    // The picker's solver is checked against the pipeline's in
    // tests/test_calibration_parity.py, to 0.2mm. What was never checked is the
    // sentence underneath it — and the sentence is the whole product here. A
    // coach does not read a homography; they read one line telling them whether
    // to save this or click again, and clicking again is the wrong answer for
    // two of the four states below.
    //
    // Same synthetic sideline camera as the parity test, so a landmark clicked
    // perfectly lands where that camera would have seen it and every error
    // figure below is one this page produced rather than one the fixture built
    // in.
    await openPage({
        html: 'calibrate/index.html',
        entry: 'calibrate/calibrate.js',
        url: 'http://localhost:5000/calibrate/',
        user: null,
        variant: 'verdicts',
    });

    const { landmarks } = await import(
        new URL('calibrate/pitch-model.js', root).href
    );
    const CAMERA = [
        [11.5, 2.1, 240.0],
        [-1.4, -9.8, 700.0],
        [0.0009, -0.0035, 1.0],
    ];
    const project = (x, y) => {
        const w = CAMERA[2][0] * x + CAMERA[2][1] * y + CAMERA[2][2];
        return [
            (CAMERA[0][0] * x + CAMERA[0][1] * y + CAMERA[0][2]) / w,
            (CAMERA[1][0] * x + CAMERA[1][1] * y + CAMERA[1][2]) / w,
        ];
    };

    const marks = landmarks(105, 68);
    const seam = globalThis.window._calib;
    // The picker only reports a spread once it knows how big the picture is,
    // which loading an image is what normally sets.
    seam.state.imageSize = [1920, 1080];

    const place = (names, moved = null) => {
        seam.state.points.clear();
        for (const name of names) {
            seam.state.points.set(name, project(...marks[name]));
        }
        if (moved) {
            const [name, dx] = moved;
            seam.state.points.set(name, project(marks[name][0] + dx, marks[name][1]));
        }
        seam.renderAll();
        return text('preview-note');
    };

    const ONE_END = [
        'corner_top_left', 'corner_bottom_left',
        'pen_left_top_corner', 'pen_left_bottom_corner',
    ];
    const SPREAD = [
        ...ONE_END, 'halfway_top', 'halfway_bottom', 'pen_spot_left', 'centre_spot',
    ];

    // 1. Too few to fit at all — a count, not a verdict, and never an error.
    assert.match(place(SPREAD.slice(0, 3)), /Place 1 more point/);

    // 2. Bunched. Said *before* anything about how small the errors are,
    //    because a tight cluster reports a beautiful error and is wrong
    //    everywhere else — which is exactly what this case is: perfect clicks,
    //    0.00m, and a fit nobody should trust.
    const bunched = place(ONE_END);
    assert.match(bunched, /0\.00m/);
    assert.match(bunched, /only cover 13% of the picture/);
    assert.ok(!bunched.includes('Good fit'),
        'a fit that covers an eighth of the frame was called good');

    // 3. Well spread and clicked perfectly.
    const good = place(SPREAD);
    assert.match(good, /Good fit/);
    assert.match(good, /35%/);

    // 4. One landmark six metres out. The page must not blame the clicking:
    //    mild barrel distortion produces the same picture with every point
    //    clicked perfectly, and a coach with an action camera would re-click
    //    forever. See the comment in renderQuality.
    const off = place(SPREAD, ['pen_spot_left', 6]);
    assert.match(off, /Something is off/);
    assert.match(off, /wide-angle lens/i);
    assert.ok(!/^Good fit/.test(off));
    const worst = Number(/([\d.]+)mWorst point/.exec(off)?.[1]);
    assert.ok(worst > 1.5,
        `six metres of error came back as ${worst}m at the worst point`);
});

test('the calibrate page can measure the field, and say when the job is done', async () => {
    // Two failures, one screenshot. A coach calibrating a school pitch got
    // 1.77m average error and no idea why; the pitch was about 100 x 50, the
    // page had assumed 105 x 68, and the two boxes for saying otherwise lived
    // in the Start card — which `loadImage` hides the moment a picture is
    // loaded. There was literally no way to change the field size while
    // clicking, and no way to find out that the size was what was wrong.
    //
    // So this test guards both halves: the inputs are reachable while working,
    // and the page works the size out on its own and offers it.
    await openPage({
        html: 'calibrate/index.html',
        entry: 'calibrate/calibrate.js',
        url: 'http://localhost:5000/calibrate/',
        user: null,
        variant: 'sizing',
    });

    // The regression itself, stated as a place in the tree rather than as a
    // symptom: anything inside #intro is unreachable once an image loads.
    for (const id of ['input-length', 'input-width']) {
        assert.equal(el(id).closest('#intro'), null,
            `${id} is back inside the card that hides when a picture loads`);
        assert.ok(el(id).closest('#workspace'),
            `${id} is not in the workspace, where the clicking happens`);
    }

    const { landmarks } = await import(
        new URL('calibrate/pitch-model.js', root).href
    );
    const CAMERA = [
        [11.5, 2.1, 240.0],
        [-1.4, -9.8, 700.0],
        [0.0009, -0.0035, 1.0],
    ];
    const project = (x, y) => {
        const w = CAMERA[2][0] * x + CAMERA[2][1] * y + CAMERA[2][2];
        return [
            (CAMERA[0][0] * x + CAMERA[0][1] * y + CAMERA[0][2]) / w,
            (CAMERA[1][0] * x + CAMERA[1][1] * y + CAMERA[1][2]) / w,
        ];
    };

    const seam = globalThis.window._calib;
    seam.state.imageSize = [1920, 1080];

    // Clicked perfectly on a 100 x 50 pitch, while the boxes still say the
    // full-size 105 x 68 default. Every click is right and the answer is
    // wrong, which is the case the old page could not distinguish.
    const place = (names, lengthM = 100, widthM = 50) => {
        const marks = landmarks(lengthM, widthM);
        seam.state.points.clear();
        for (const name of names) {
            seam.state.points.set(name, project(...marks[name]));
        }
        seam.renderAll();
    };

    const CORNERS = [
        'corner_top_left', 'corner_bottom_left',
        'corner_top_right', 'corner_bottom_right',
    ];
    // Both far corners included: a set clustered in one half covers too little
    // of the frame to pass the spread check, and this test is about the size
    // rather than about the spread.
    const SPREAD = [
        ...CORNERS,
        'pen_left_top_corner', 'pen_left_bottom_corner',
        'halfway_top', 'halfway_bottom', 'pen_spot_left', 'centre_spot',
    ];

    // 1. Four corners fit every size exactly, so there is nothing to measure
    //    and the page must not pretend there is.
    place(CORNERS);
    assert.match(text('size-measured'), /Place 1 more point/);

    // 2. Seven perfect points and still nothing measurable, because all of
    //    them scale with the pitch. The page has to say what would help.
    place([...CORNERS, 'halfway_top', 'halfway_bottom', 'centre_spot']);
    const stuck = text('size-measured');
    assert.match(stuck, /cannot measure the field/);
    assert.match(stuck, /penalty box corner/);
    assert.match(text('readiness'), /Field size cannot be measured/);

    // 3. Add markings with a fixed size in the Laws and the pitch falls out.
    place(SPREAD);
    const measured = text('size-measured');
    assert.match(measured, /Your points measure/);
    assert.match(measured, /50\.0m/, `the width was not measured: ${measured}`);
    assert.match(text('readiness'), /Field size disagrees/);
    assert.ok(el('btn-apply-size'), 'the measured size was reported with no way to take it');

    // 4. Taking it writes the boxes the coach could not reach before.
    el('btn-apply-size').click();
    assert.ok(Math.abs(Number(el('input-width').value) - 50) < 0.5,
        `width came back as ${el('input-width').value}`);
    assert.ok(Math.abs(Number(el('input-length').value) - 100) < 0.5,
        `length came back as ${el('input-length').value}`);
    assert.match(text('size-measured'), /agrees with what you typed/);
    assert.equal(el('btn-apply-size'), null, 'still offering a size already taken');

    // 5. And the fit the coach was looking at is fixed by it. This is the
    //    whole point: the clicking was never the problem.
    const fixed = text('preview-note');
    assert.match(fixed, /Good fit/, `the corrected size still reads: ${fixed}`);

    // 6. One thing left, and it is the one no software can check.
    assert.match(text('readiness'), /1 check left/);
    assert.match(text('btn-export'), /Save anyway/);

    el('chk-eyeball').checked = true;
    el('chk-eyeball').dispatchEvent(new Event('change', { bubbles: true }));
    assert.match(text('readiness'), /Done\. This calibration is ready to save/);
    assert.match(text('btn-export'), /Save calibration/);

    // 7. Clearing the points takes the tick with them — it was a statement
    //    about points that no longer exist.
    // The shim answers every confirm() with no, on purpose. This one path
    // needs a yes, and only for the length of the click.
    const answeredNo = globalThis.confirm;
    globalThis.confirm = () => true;
    try {
        el('btn-clear').click();
    } finally {
        globalThis.confirm = answeredNo;
    }
    assert.equal(el('chk-eyeball').checked, false);
    assert.ok(!text('readiness').includes('Done.'),
        'an empty picker still called itself ready to save');
});

test('the xG sandbox computes its features on load', async () => {
    await openPage({
        html: 'xg-sandbox/index.html',
        entry: 'xg-sandbox/sandbox.js',
        url: 'http://localhost:5000/xg-sandbox/',
        user: null,
    });

    // The model itself is not exercised: onnxruntime arrives as a plain
    // <script> tag setting a global `ort`, which nothing here provides. The
    // page's own handling of that is the interesting half anyway — it must come
    // up and stay usable with the geometry on screen and no probability, which
    // is exactly what a browser with a slow CDN gives a user.
    const seam = globalThis.window._sandbox;
    assert.ok(seam, 'the sandbox seam was never installed');
    assert.equal(Object.keys(seam.features()).length, 11);
    assert.ok(seam.measurements().distance_to_goal > 0, 'the sliders read nothing');
});

// ------------------------------------------------------- the absent cases
//
// Every page above renders a squad that exists. The bugs this repo has actually
// shipped were in the other direction — a match with no log reported as a match
// nobody played, a rail showing the previous subject — so the empty and the
// second-subject paths are worth as much as the full one.

// ------------------------------------------------------- the absent cases
//
// Every page above renders a squad that exists. The mistakes this repo has
// actually shipped were in the other direction — a match nobody tagged reported
// as a match nobody played, a squad of nought-minute starters — so the paths
// where the data is missing are worth at least as much as the path where it is
// all there.

test('a match nobody tagged says so, rather than reporting a nil-all half', async () => {
    await openPage({
        html: 'halftime/index.html',
        entry: 'halftime/halftime.js',
        url: `http://localhost:5000/halftime/?team=${TEAM_ID}&match=match-2`,
        variant: 'untagged',
    });

    assert.ok(shown('view-report'), 'the report never opened');
    assert.equal(text('ht-clock'), 'Nothing tagged yet');
    // The clock said so and the scoreline three inches above it said 0–0.
    assert.equal(text('ht-score-us'), '—');
    assert.equal(text('ht-score-them'), '—');
    assert.match(el('timeline').textContent, /Nothing tagged yet/);
    assert.match(el('minutes').textContent, /No lineup was saved/);
    assert.match(el('decisions').textContent, /Nothing needs a decision/);
});

test('a match nobody tagged has no score, on either page', async () => {
    // 0–0 in the largest type on the page, for a match nobody ran the tablet
    // for. Both pages did this; the half-time clock beneath it already said
    // "Nothing tagged yet" while the scoreline above contradicted it.
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'untagged-coach',
    });

    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Eastvale'))
        ?.closest('div')
        ?.click();
    await settle();

    assert.equal(text('score-us'), '—');
    assert.equal(text('score-them'), '—');
    assert.equal(el('team-stats').textContent.trim(), '',
        'a match nobody tagged still reported counts');
    assert.match(text('team-untagged'), /Nobody ran the tablet/);
});

test('the coach report and the half-time page agree, figure for figure', async () => {
    // The drift this consolidation was for. Two hand-written lists described
    // one tag log, and the three-minute half-time read carried figures the full
    // post-match report did not have at all.
    const readTallies = () => Object.fromEntries(
        live.document.querySelectorAll('.tally').map((row) => [
            row.querySelector('.t-label')?.textContent,
            `${row.querySelector('.t-us')?.textContent}/${row.querySelector('.t-them')?.textContent}`,
        ]),
    );

    await openPage({
        html: 'halftime/index.html',
        entry: 'halftime/halftime.js',
        url: `http://localhost:5000/halftime/?team=${TEAM_ID}&match=${MATCH_ID}`,
        variant: 'agree-ht',
    });
    const atHalfTime = readTallies();
    assert.ok(Object.keys(atHalfTime).length, 'the half-time page drew no tallies');

    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'agree-coach',
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();
    const inTheReport = readTallies();

    // Not equality: the report is a superset on purpose — it keeps the goals
    // row and the rows neither side registered, both of which the touchline
    // page drops for a reader who is standing up. What it may never be is
    // *missing* something the shorter page found room for.
    for (const [label, value] of Object.entries(atHalfTime)) {
        assert.ok(label in inTheReport,
            `the half-time page shows "${label}" and the full report does not`);
        assert.equal(inTheReport[label], value, `"${label}" disagrees between the two`);
    }
    assert.ok('Goals' in inTheReport, 'the report dropped its own goals row');
});

test('a squad with nobody in it is an empty squad, not a squad of zeroes', async () => {
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'bare',
        documents: {
            [`users/${COACH.uid}`]: { teamIds: [TEAM_ID] },
            [`teams/${TEAM_ID}`]: {
                name: 'Riverside High', coachUids: [COACH.uid], taggerUids: [],
                archived: false, createdBy: COACH.uid,
            },
        },
    });

    assert.ok(!shown('loading'), 'the spinner is still up on an empty squad');
    assert.doesNotMatch(live.document.body.textContent, /NaN|undefined|Infinity/);
});

test('a student who did not get on is told so in a sentence', async () => {
    // The lede on a student's own report, end to end. It read "Lost. You
    // played an unused substitute." — on the page of the one reader most
    // likely to go over it twice.
    const documents = fixture();
    documents[`teams/${TEAM_ID}/matches/match-00/playerReports/p-rae`] = {
        published: true, linkedUid: STUDENT.uid, playerName: 'Rae Nkemelu',
        jerseyNumber: 7, minutesPlayed: 0, minutesKnown: true, goals: 0,
        assists: 0, yellowCards: 0, redCards: 0, fouls: 0, stints: [],
        matchDate: '2026-07-31', opponentName: 'Southbank',
        teamName: 'Riverside High', scoreUs: 0, scoreThem: 3, teamCounts: null,
        timeline: [], matchId: 'match-00', videoUrl: null, videoOffsetS: 0,
        secondHalfVideoS: null, halfTimeClockS: null, cvTouches: null,
    };

    await openPage({
        html: 'player/index.html',
        entry: 'player/player.js',
        url: 'http://localhost:5000/player/',
        user: STUDENT,
        variant: 'bench',
        documents,
    });

    live.document.querySelectorAll('#match-list .match-row')
        .find((r) => r.textContent.includes('Southbank'))
        .click();
    await settle();

    assert.equal(text('md-line'), 'Lost. You did not get on.');
});

test('a student whose season has not started is not shown somebody else\'s', async () => {
    await openPage({
        html: 'player/index.html',
        entry: 'player/player.js',
        url: 'http://localhost:5000/player/',
        user: STUDENT,
        variant: 'nothing',
        documents: {
            [`users/${STUDENT.uid}`]: {
                teamIds: [],
                lastPlayerRef: { teamId: TEAM_ID, playerId: 'p-rae' },
            },
            [`teams/${TEAM_ID}/players/p-rae`]: {
                name: 'Rae Nkemelu', jerseyNumber: 7, position: 'midfield',
                linkedUid: STUDENT.uid, active: true, emailLower: STUDENT.email,
            },
        },
    });

    assert.ok(!shown('loading'));
    const body = live.document.body.textContent;
    assert.doesNotMatch(body, /NaN|undefined|Infinity/);
    assert.doesNotMatch(body, /Alex Vega/, "another player's season is on screen");
});


test('where each player played is drawn from the coach\'s own mapping', async () => {
    // The whole path in one go: a track's occupancy grid, merged over that
    // player's figures, oriented by the attacking end, turned into metres up
    // the pitch and drawn as a bar. Every step of it existed as a tested pure
    // function before this test and none of it had ever run against a page.
    //
    // Two rows, not six: four of the six figures are nobody yet, and a bar
    // under no name is a bar a coach cannot act on.
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'shape',
        documents: await filmed(),
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    assert.ok(shown('cv-shape-block'), 'the shape block did not come up');
    const rows = live.document.querySelectorAll('.shape-row');
    assert.equal(rows.length, 2, 'a bar was drawn for a figure nobody has named');

    const names = rows.map((r) => r.querySelector('.shape-name').textContent);
    assert.deepEqual(names, ['Rae Nkemelu', 'Alex Vega'],
        'the bars are not in order of how far up the pitch they averaged');

    // The figure beside the bar, and the bar itself, have to be the same
    // claim — a percentage width computed from one number and a caption from
    // another is exactly how these two drift apart.
    const metres = rows.map((r) => Number(r.querySelector('.shape-value').textContent.replace(' m', '')));
    assert.ok(metres[0] > 0 && metres[1] > metres[0],
        `the forward did not average further up than the midfielder: ${metres}`);
    const widths = rows.map((r) => Number.parseFloat(r.querySelector('.shape-fill').style.width));
    assert.ok(Math.abs(widths[0] - (metres[0] / 105) * 100) < 1.2,
        `the bar and the number disagree: ${widths[0]}% vs ${metres[0]} m`);

    for (const row of rows) {
        assert.match(row.querySelector('.shape-sub').textContent, /min tracked/,
            'a bar was drawn without saying how much of the match it covers');
    }

    // A fixture built so the two mapped figures sit in the lines they were
    // picked in. No remark is the right answer here, and the block still has
    // to say that out loud rather than leave an empty list looking broken.
    assert.equal(live.document.querySelectorAll('.shape-remarks li').length, 0);
    assert.match(text('cv-shape-note'), /which is the usual answer/);
    assert.match(text('cv-shape-note'), /Sides are not checked/);
});

test('the sample preview shows a whole team of bars, not two', async () => {
    // The other reader of this block: a coach with no footage at all, deciding
    // whether any of this is worth filming a match for.
    await openPage({
        html: 'coach/index.html',
        entry: 'coach/coach.js',
        url: `http://localhost:5000/coach/?team=${TEAM_ID}`,
        variant: 'shape-sample',
    });
    live.document.querySelectorAll('.title')
        .find((t) => t.textContent.includes('Northgate'))
        ?.closest('div')
        ?.click();
    await settle();

    assert.ok(!shown('cv-shape-block'), 'a match with no video drew position bars');
    el('btn-cv-sample').click();
    await settle();

    assert.ok(shown('cv-shape-block'), 'the preview did not bring the block up');
    const rows = live.document.querySelectorAll('.shape-row');
    assert.equal(rows.length, 11, 'the sample team is not eleven players');
    assert.ok(el('cv-shape-rows').className.includes('is-sample-plot'),
        'invented bars are not marked as invented');

    // Grouped by line, and the keeper's group is one of them — a goalkeeper is
    // never judged out of position and is still somebody a coach looks for.
    const lines = live.document.querySelectorAll('.shape-line').map((h) => h.textContent);
    assert.ok(lines.length >= 3, `the bars were not grouped by line: ${lines}`);

    el('btn-cv-sample').click();
    await settle();
    assert.ok(!shown('cv-shape-block'), 'the bars stayed up after the sample was hidden');
});
