// The five bottom sheets on the tagging page, and where the keyboard goes.
//
// `live-tagging/sheet.js` is the only dialog implementation on the site, and
// until this file nothing measured it at all. The markup half of the contract
// — `role="dialog"`, `aria-modal`, `aria-labelledby`, `tabindex="-1"` — is a
// property of a file and is checked statically in `tests/test_dialog_seam.py`.
// This half is behaviour, and behaviour has to be run: `aria-modal="true"` is
// a promise that a screen reader may ignore everything outside the dialog, and
// that promise is only honest if the keyboard cannot get out there either. The
// one way to know whether it can is to press Tab.
//
// What this cannot see. The sheets are hidden with `display: none` until they
// carry `.open`, and there is no layout engine here, so the `offsetParent`
// half of `tabbable()` passes everything. That filter is never the thing under
// test below: every case opens a sheet before it looks inside one, and the CSS
// rule the filter depends on is pinned in the Python gate instead.

import { test, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom } from './dom-shim.js';

const root = new URL('../', import.meta.url);
const html = readFileSync(new URL('live-tagging/index.html', root), 'utf8');

/**
 * Every overlay the page carries, read off the markup rather than listed here.
 * A sixth sheet added tomorrow joins these tests without anybody remembering
 * to add it, which is the only way a list like this stays true.
 */
const OVERLAYS = [...html.matchAll(/<div id="(overlay-[a-z-]+)"/g)]
    .map((m) => m[1]);

let live = null;
let sheets = null;

const el = (id) => live.document.getElementById(id);
const sheetIn = (id) => el(id).querySelector('.sheet');
const isOpen = (id) => el(id).classList.contains('open');
const active = () => live.document.activeElement;

/** A keydown on the document, which is where the one listener lives. */
function press(key, init = {}) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, ...init });
    live.document.dispatchEvent(event);
    return event;
}

before(async () => {
    live = installDom(html, { url: 'http://localhost:5000/live-tagging/' });
    sheets = await import(new URL('live-tagging/sheet.js', root).href);
    sheets.mountSheets();
});

after(() => live?.restore());

// One document for the whole file, because `sheet.js` keeps its open stack at
// module scope and Node hands back the same module however often it is
// imported. Re-installing the DOM per test would leave entries in that stack
// pointing at a page nobody can reach any more, so instead each test starts
// from everything closed and the caret back on the body.
beforeEach(() => {
    for (const id of OVERLAYS) sheets.closeSheet(id);
    live.document.activeElement = live.document.body;
});

test('the page carries the five sheets these tests are about', () => {
    assert.deepEqual(OVERLAYS, [
        'overlay-event', 'overlay-clock', 'overlay-sub',
        'overlay-exit', 'overlay-log',
    ]);
});

test('every sheet takes the keyboard when it opens', () => {
    for (const id of OVERLAYS) {
        sheets.openSheet(id);
        assert.ok(isOpen(id), `${id} never opened`);
        assert.equal(active(), sheetIn(id), `${id} left the caret outside it`);
        sheets.closeSheet(id);
        assert.ok(!isOpen(id), `${id} never closed`);
    }
});

test('closing puts the keyboard back on whatever opened it', () => {
    const opener = el('btn-signin');
    opener.focus();

    sheets.openSheet('overlay-clock');
    assert.equal(active(), sheetIn('overlay-clock'));

    sheets.closeSheet('overlay-clock');
    assert.equal(active(), opener, 'the caret was left at the top of the page');
});

test('Escape closes the sheet that is up', () => {
    const opener = el('btn-signin');
    opener.focus();
    sheets.openSheet('overlay-log');

    const event = press('Escape');

    assert.ok(!isOpen('overlay-log'), 'Escape did nothing');
    assert.equal(active(), opener);
    assert.ok(event.defaultPrevented, 'the page never claimed the key');
});

test('Escape with no sheet up is left to the page', () => {
    // The tagging tool binds keys of its own, so swallowing Escape when there
    // is nothing to close would quietly take one away from it.
    const event = press('Escape');
    assert.ok(!event.defaultPrevented);
});

test('Tab off the last control comes back to the first', () => {
    sheets.openSheet('overlay-exit');
    const stay = el('btn-exit-stay');
    const go = el('btn-exit-go');

    go.focus();
    const event = press('Tab');

    assert.ok(event.defaultPrevented, 'Tab was allowed out of the sheet');
    assert.equal(active(), stay);
    sheets.closeSheet('overlay-exit');
});

test('Shift+Tab off the sheet itself lands on the last control', () => {
    // The caret starts on the sheet container, so the first backwards Tab a
    // tagger presses is this one. Without the `here === sheet` arm it would
    // walk straight out into the page behind.
    sheets.openSheet('overlay-exit');
    const event = press('Tab', { shiftKey: true });

    assert.ok(event.defaultPrevented);
    assert.equal(active(), el('btn-exit-go'));
    sheets.closeSheet('overlay-exit');
});

test('Tab between two controls is nobody else’s business', () => {
    sheets.openSheet('overlay-exit');
    el('btn-exit-stay').focus();
    const event = press('Tab');

    assert.ok(!event.defaultPrevented, 'the trap moved a caret it should not');
    assert.equal(active(), el('btn-exit-stay'));
    sheets.closeSheet('overlay-exit');
});

test('a sheet with nothing to tab to holds the caret anyway', () => {
    // None of the five is ever this empty, so this one is built rather than
    // found. The branch is worth covering: letting Tab through here is exactly
    // the case where the keyboard escapes and cannot be got back.
    const overlay = live.document.createElement('div');
    overlay.id = 'overlay-empty';
    overlay.className = 'overlay';
    overlay.innerHTML = '<div class="sheet" tabindex="-1"></div>';
    live.document.body.appendChild(overlay);

    sheets.openSheet('overlay-empty');
    const event = press('Tab');

    assert.ok(event.defaultPrevented);
    assert.equal(active(), overlay.querySelector('.sheet'));

    sheets.closeSheet('overlay-empty');
    overlay.remove();
});

test('opening the same sheet twice does not lose the way back', () => {
    // Two taps on one button, or a handler that opens the sheet it is already
    // in. A second push would record the sheet as its own opener and strand
    // the caret there forever.
    const opener = el('btn-signin');
    opener.focus();
    sheets.openSheet('overlay-sub');
    sheets.openSheet('overlay-sub');

    sheets.closeSheet('overlay-sub');
    assert.equal(active(), opener);

    // The caret alone does not prove it: a second push records the sheet
    // under itself, and `closeSheet` matches the *first* entry, so the caret
    // comes back correctly while the duplicate stays on the stack for good.
    // What that costs is Escape — `closeTop` keeps reporting a sheet it has
    // already shut, and swallows a key the page was owed.
    assert.ok(!isOpen('overlay-sub'));
    const event = press('Escape');
    assert.ok(!event.defaultPrevented, 'a duplicate was left on the stack');
});

test('closing a sheet that was never open moves nothing', () => {
    const opener = el('btn-signin');
    opener.focus();
    sheets.closeSheet('overlay-event');
    assert.equal(active(), opener);
});

test('an id that is not on the page is a no-op, not a crash', () => {
    const opener = el('btn-signin');
    opener.focus();
    sheets.openSheet('overlay-nothing');
    assert.equal(active(), opener);
    press('Escape');
    assert.equal(active(), opener);
});

test('the keyboard is not handed to an element that has left the page', () => {
    // The list inside a sheet is re-rendered while the sheet is up, so the row
    // that opened it can be gone by the time it closes. Focusing a detached
    // node in a real browser drops the caret on <body> with no announcement.
    const opener = live.document.createElement('button');
    opener.id = 'gone-by-then';
    live.document.body.appendChild(opener);
    opener.focus();

    sheets.openSheet('overlay-log');
    opener.remove();
    assert.ok(!opener.isConnected, 'the shim never noticed the removal');

    sheets.closeSheet('overlay-log');
    assert.notEqual(active(), opener, 'the caret went to a detached button');
    assert.equal(active(), sheetIn('overlay-log'));
});
