// The match page's "Video processing" card: what it says before, during and
// after a run, from the same step list the run itself reads.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { durationText, estimate, runView, STALE_MIN } from '../shared/run-view.js';

const { steps } = JSON.parse(readFileSync(new URL('../shared/run-steps.json', import.meta.url)));
const MIN = 60000;
const NOW = Date.UTC(2026, 10, 9, 20, 0);

test('the step list is the one the run reads, with one step for the coach', () => {
    const python = readFileSync(new URL('../pitchiq/run_progress.py', import.meta.url), 'utf8');
    assert.match(python, /shared' \/ 'run-steps\.json/);
    assert.deepEqual(steps.filter((s) => s.you).map((s) => s.key), ['pins']);
    for (const s of steps) assert.ok(s.min[0] <= s.min[1], s.key);
});

test('before a run: the estimate and when the pins are asked for', () => {
    const e = estimate(steps);
    assert.equal(e.text, 'about 5 to 9 hours');
    assert.equal(e.pinsAfter, '25 to 40 minutes');
    assert.equal(runView({ steps, request: null, progress: null, hasVideo: true }).phase, 'offer');
    assert.equal(runView({ steps, request: null, progress: null, hasVideo: false }).phase, 'novideo');
});

test('asked for and not started yet', () => {
    const v = runView({ steps, request: { ours: 'darker', requestedAt: NOW - 5 * MIN }, progress: null });
    assert.equal(v.phase, 'requested');
    assert.equal(v.ours, 'darker');
});

test('halfway through the first pass: the bar, the step and the time left', () => {
    const progress = {
        state: 'running', step: 'pass_h1', fraction: 0.5,
        done: ['halves', 'panorama_h1', 'panorama_h2', 'pins', 'calibrate'],
        leftMin: 290, etaAt: NOW + 290 * MIN, updatedAt: NOW - 2 * MIN,
    };
    const v = runView({ steps, request: { ours: 'lighter' }, progress, now: NOW });
    assert.equal(v.phase, 'running');
    assert.deepEqual(v.step, { n: 6, of: 12, label: 'Following the ball and the players, first half' });
    assert.equal(v.leftText, '4 h 50 min');
    assert.ok(v.percent > 20 && v.percent < 35, v.percent);
    assert.equal(v.stale, false);
    assert.deepEqual(v.list.slice(4, 7).map((s) => s.status), ['done', 'now', 'todo']);
});

test('a run that has gone quiet is called out, a pin wait is not', () => {
    const quiet = { state: 'running', step: 'map_h1', updatedAt: NOW - (STALE_MIN + 1) * MIN };
    assert.equal(runView({ steps, request: {}, progress: quiet, now: NOW }).stale, true);
    const pins = { ...quiet, state: 'pins', step: 'pins', pinHalves: ['h1'] };
    const v = runView({ steps, request: {}, progress: pins, now: NOW });
    assert.equal(v.stale, false);
    assert.equal(v.phase, 'pins');
    assert.deepEqual(v.pinHalves, ['h1']);
});

test('done, and a new request after a finished run starts afresh', () => {
    const done = { state: 'done', step: '', done: steps.map((s) => s.key), updatedAt: NOW - 60 * MIN };
    const v = runView({ steps, request: { requestedAt: NOW - 600 * MIN }, progress: done, now: NOW });
    assert.equal(v.phase, 'done');
    assert.equal(v.percent, 100);
    const again = runView({ steps, request: { requestedAt: NOW - 1 * MIN }, progress: done, now: NOW });
    assert.equal(again.phase, 'requested');
});

test('durations read the way a person says them', () => {
    assert.equal(durationText(0), '1 min');
    assert.equal(durationText(45), '45 min');
    assert.equal(durationText(60), '1 h');
    assert.equal(durationText(200), '3 h 20 min');
    assert.equal(durationText(358), '6 h');
});
