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
import { readFileSync } from 'node:fs';

import { installDom } from './dom-shim.js';
import { reset, seed, signInAs } from './fake-firebase.js';
import { fixture, COACH, STUDENT, TEAM_ID, MATCH_ID } from './fixtures.js';

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

test('the player portal opens a season for the student it belongs to', async () => {
    await openPage({
        html: 'player/index.html',
        entry: 'player/player.js',
        url: 'http://localhost:5000/player/',
        user: STUDENT,
    });

    assert.ok(!shown('loading'));
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

test('the calibrate page comes up with its picker ready', async () => {
    await openPage({
        html: 'calibrate/index.html',
        entry: 'calibrate/calibrate.js',
        url: 'http://localhost:5000/calibrate/',
        user: null,
    });

    assert.ok(globalThis.window._calib, 'the calibration seam was never installed');
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
    assert.match(el('timeline').textContent, /Nothing tagged yet/);
    assert.match(el('minutes').textContent, /No lineup was saved/);
    assert.match(el('decisions').textContent, /Nothing needs a decision/);
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

