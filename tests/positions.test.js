/**
 * Stats per position on the coach page: names, layout, and who stood there.
 *
 * Run:  node --test tests/positions.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as P from '../shared/positions.js';
import { NOT_A_PLAYER } from '../shared/report-player.js';

const ROLES = [
    'back_left', 'back_centre', 'back_right',
    'middle_left', 'middle_centre', 'middle_right',
    'front_left', 'front_centre', 'front_right',
];

describe('positionName and positionLayout', () => {
    test('names every role the pipeline writes', () => {
        assert.deepEqual(ROLES.map(P.positionName), [
            'Left back', 'Centre back', 'Right back',
            'Left midfield', 'Centre midfield', 'Right midfield',
            'Left forward', 'Centre forward', 'Right forward',
        ]);
        assert.equal(P.positionName('keeper'), null);
    });

    test('lays the nine out attacking right, left wing on top', () => {
        const rows = P.positionLayout();
        assert.deepEqual(rows[0], ['back_left', 'middle_left', 'front_left']);
        assert.deepEqual(rows[2], ['back_right', 'middle_right', 'front_right']);
        assert.deepEqual(rows.flat().sort(), [...ROLES].sort());
    });
});

describe('hasPositions and busiestRole', () => {
    test('an empty block has nothing to show', () => {
        assert.equal(P.hasPositions(null), false);
        assert.equal(P.hasPositions({ roles: { back_left: { minutes: 0 } } }), false);
        assert.equal(P.busiestRole({ roles: { back_left: { minutes: 0 } } }), null);
    });

    test('the busiest role is the first view', () => {
        const block = { roles: { back_left: { minutes: 30 }, back_centre: { minutes: 70 } } };
        assert.equal(P.hasPositions(block), true);
        assert.equal(P.busiestRole(block), 'back_centre');
    });
});

describe('minuteRuns and whenText', () => {
    test('joins consecutive minutes', () => {
        assert.deepEqual(P.minuteRuns([9, 3, 4, 5, 4]), [[3, 5], [9, 9]]);
        assert.deepEqual(P.minuteRuns([]), []);
    });

    test('counts minutes from 1 within each half', () => {
        assert.equal(P.whenText({ 2: [0, 1, 2], 1: [3, 4, 5, 8] }), '1st half 4–6′, 9′ · 2nd half 1–3′');
    });
});

describe('positionHolders', () => {
    const row = {
        unnamed_minutes: 4.5,
        holders: [
            { half: 1, start_s: 0, minute: [0, 1, 1, 2], cluster_id: [10, 10, 11, 12], seconds: [60, 30, 30, 45] },
            { half: 2, start_s: 3000, minute: [0], cluster_id: [20], seconds: [60] },
        ],
    };
    const mapping = { 10: 'p1', 11: 'p2', 12: NOT_A_PLAYER, 20: 'p1' };
    const names = new Map([['p1', 'Sam'], ['p2', 'Alex']]);

    test("adds a player's clusters up, across halves, most minutes first", () => {
        const out = P.positionHolders(row, mapping, names);
        assert.deepEqual(out.players.map((p) => [p.name, p.minutes, p.when]), [
            ['Sam', 2.5, '1st half 1–2′ · 2nd half 1′'],
            ['Alex', 0.5, '1st half 2′'],
        ]);
        assert.equal(out.unmatchedMinutes, 0.75); // not a player
        assert.equal(out.unnamedMinutes, 4.5);
    });

    test('an unmatched cluster is not anybody', () => {
        const out = P.positionHolders(row, { 10: 'p1' }, names);
        assert.deepEqual(out.players.map((p) => p.name), ['Sam']);
        assert.equal(out.unmatchedMinutes, (30 + 45 + 60) / 60);
    });

    test('the other team has no holders', () => {
        const out = P.positionHolders({ holders: null, unnamed_minutes: null }, mapping, names);
        assert.deepEqual(out, { players: [], unmatchedMinutes: 0, unnamedMinutes: null });
    });
});

describe('roleStatRows', () => {
    test('reads the role row, distance per 90 included', () => {
        const rows = new Map(P.roleStatRows({
            minutes: 45, occupied_share: 0.98, players_mean: 1.04, touches: 31,
            passes_attempted: 20, passes_completed: 15, carries: 4, shots: 1, goals: 0,
            defensive_actions: 6, distance_m: 4500, seen_share: 0.71,
        }));
        assert.equal(rows.get('Minutes filled'), '45 min');
        assert.equal(rows.get('Passes completed'), '15 of 20 (75%)');
        assert.equal(rows.get('Distance'), '4.5 km');
        assert.equal(rows.get('Distance per 90 minutes there'), '9.0 km');
        assert.equal(rows.get('Seen by the camera, not guessed'), '71%');
    });

    test('a role nobody stood in has dashes, not zeros, where nothing was measured', () => {
        const rows = new Map(P.roleStatRows({
            minutes: 0, occupied_share: null, players_mean: null, touches: 0,
            passes_attempted: 0, passes_completed: 0, carries: 0, shots: 0, goals: 0,
            defensive_actions: 0, distance_m: 0, seen_share: null,
        }));
        assert.equal(rows.get('Distance per 90 minutes there'), '–');
        assert.equal(rows.get('Seen by the camera, not guessed'), '–');
        assert.equal(P.minutesText(0.4), '<1 min');
    });
});
