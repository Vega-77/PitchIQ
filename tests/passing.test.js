/**
 * The passing network.
 *
 * Run:  node --test tests/passing.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as sample from '../shared/sample-report.js';
import * as passing from '../shared/passing.js';
// Only the sizing rules are exercised here; everything else in the module
// touches the DOM, and these suites deliberately have none.
import * as passMod from '../shared/pass-map.js';

// -------------------------------------------------------- the passing network
//
// The first thing in the app that draws a shape rather than a total, so the
// rules about what it refuses to draw carry more weight than the arithmetic. A
// network built from two thirds of the passes looks exactly like one built from
// all of them.

describe('playersByTrack', () => {
    const clusters = [
        { cluster_id: 0, track_ids: [1, 2] },
        { cluster_id: 1, track_ids: [3] },
        { cluster_id: 2, track_ids: [4] },
    ];

    test('every track of a named cluster resolves to that player', () => {
        const byTrack = passing.playersByTrack(clusters, { 0: 'ada', 1: 'dee' });
        assert.equal(byTrack.get(1), 'ada');
        assert.equal(byTrack.get(2), 'ada');
        assert.equal(byTrack.get(3), 'dee');
    });

    test('an unnamed cluster resolves to nobody', () => {
        const byTrack = passing.playersByTrack(clusters, { 0: 'ada' });
        assert.equal(byTrack.has(4), false);
    });

    test('a cluster ruled out as not a player is not a player', () => {
        const byTrack = passing.playersByTrack(
            clusters, { 0: 'ada', 2: '__not_a_player' }, '__not_a_player',
        );
        assert.equal(byTrack.has(4), false);
    });
});

describe('passingNetwork', () => {
    const byTrack = new Map([[1, 'ada'], [2, 'dee'], [3, 'noor']]);
    const pass = (trackId, receiverTrackId, startM, outcome = 'completed') => ({
        type: 'pass', team: 'team_a', trackId, receiverTrackId, startM, outcome,
    });
    const build = (events, options) =>
        passing.passingNetwork(events, { byTrack, team: 'team_a', ...options });

    test('a node sits at the mean of where that player passed from', () => {
        // Not their heatmap centroid. The edges are passes, so the positions
        // have to answer the same question the edges ask — a diagram that mixes
        // "where they stood" with "who they passed to" gives a reader no way to
        // tell the two apart.
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [40, 50]),
        ]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.x, 30);
        assert.equal(ada.y, 40);
    });

    test('an incomplete pass counts for the passer and joins no line', () => {
        const net = build([pass(1, 2, [20, 30], 'incomplete')]);
        assert.equal(net.nodes.find((n) => n.playerId === 'ada').passes, 1);
        assert.equal(net.nodes.find((n) => n.playerId === 'ada').completed, 0);
        assert.equal(net.edges.length, 0);
        assert.equal(net.incomplete, 1);
    });

    test('a pass from a figure nobody has named is counted, never guessed', () => {
        // A line drawn to an unnamed figure looks exactly like a fact.
        const net = build([pass(99, 2, [20, 30])]);
        assert.equal(net.nodes.length, 0);
        assert.equal(net.unmapped, 1);
        assert.equal(net.edges.length, 0);
    });

    test('a completed pass to an unnamed figure still counts as completed', () => {
        // It did find a team-mate. It just cannot be drawn to one, which is a
        // different fact from the pass having failed.
        const net = build([pass(1, 99, [20, 30])]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.completed, 1);
        assert.equal(net.incomplete, 0);
        assert.equal(net.edges.length, 0);
    });

    test('an uncalibrated run gives a player counts but nowhere to stand', () => {
        const net = build([pass(1, 2, null), pass(1, 2, undefined)]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.passes, 2);
        assert.equal(ada.x, null);
        assert.equal(net.unplaced, 2);
    });

    test('a pass to yourself is not a connection', () => {
        // Two fragments of one player, both mapped to the same name. The
        // tracker lost them mid-carry; the ball never changed hands.
        const two = new Map([[1, 'ada'], [5, 'ada']]);
        const net = build([pass(1, 5, [20, 30])], { byTrack: two });
        assert.equal(net.edges.length, 0);
    });

    test('the other team is left out when a team is named', () => {
        const theirs = { ...pass(1, 2, [20, 30]), team: 'team_b' };
        assert.equal(build([theirs]).nodes.length, 0);
    });

    test('only passes count — a shot is not a link', () => {
        const shot = { ...pass(1, 2, [20, 30]), type: 'shot' };
        assert.equal(build([shot]).nodes.length, 0);
    });

    test('attacking left mirrors the whole picture', () => {
        // The same flip the shot maps do. A second-half network drawn at the
        // wrong end is a diagram nobody can compare to the first half.
        const net = build([pass(1, 2, [20, 30])], { attackingEnd: 'left' });
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.x, 105 - 20);
        assert.equal(ada.y, 68 - 30);
    });

    test('edges are directed and counted', () => {
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [20, 30]), pass(2, 1, [40, 30]),
        ]);
        const there = net.edges.find((e) => e.from === 'ada' && e.to === 'dee');
        const back = net.edges.find((e) => e.from === 'dee' && e.to === 'ada');
        assert.equal(there.count, 2);
        assert.equal(back.count, 1);
    });

    test('busiest player first, so a renderer drops the quietest', () => {
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [20, 30]), pass(3, 1, [50, 30]),
        ]);
        assert.equal(net.nodes[0].playerId, 'ada');
    });
});

describe('foldEdges', () => {
    test('two directions become one line that remembers the split', () => {
        // "14 passes" between two players says nothing about whether one was
        // feeding the other or they were sharing it, and that difference is
        // most of what a coach reads this for.
        const folded = passing.foldEdges([
            { from: 'ada', to: 'dee', count: 9 },
            { from: 'dee', to: 'ada', count: 5 },
        ]);
        assert.equal(folded.length, 1);
        assert.equal(folded[0].count, 14);
        assert.equal(folded[0].aToB, 9);
        assert.equal(folded[0].bToA, 5);
    });

    test('a one-way link keeps its direction', () => {
        const folded = passing.foldEdges([{ from: 'dee', to: 'ada', count: 6 }]);
        assert.equal(folded[0].a, 'ada');
        assert.equal(folded[0].bToA, 6);
        assert.equal(folded[0].aToB, 0);
    });

    test('busiest pair first', () => {
        const folded = passing.foldEdges([
            { from: 'a', to: 'b', count: 2 },
            { from: 'c', to: 'd', count: 9 },
        ]);
        assert.equal(folded[0].count, 9);
    });
});

describe('strongestLink', () => {
    const name = (id) => ({ ada: 'Ada', dee: 'Dee' }[id] || id);

    test('a real link is named, both ways', () => {
        const text = passing.strongestLink(
            [{ a: 'ada', b: 'dee', count: 14, aToB: 9, bToA: 5 }], name,
        );
        assert.match(text, /Ada and Dee/);
        assert.match(text, /14 passes, 9 one way and 5 back/);
    });

    test('a thin link is not a finding', () => {
        // "Your main link was Ada and Dee, with two passes" is a sentence about
        // nothing that reads exactly like a sentence about something.
        assert.equal(passing.strongestLink(
            [{ a: 'ada', b: 'dee', count: 2, aToB: 1, bToA: 1 }], name,
        ), null);
        assert.equal(passing.strongestLink([], name), null);
    });
});

describe('networkNote', () => {
    const note = (network) => passing.networkNote(network);

    test('it says how many players are actually drawn', () => {
        const text = note({ nodes: [{ x: 1, y: 1 }, { x: 2, y: 2 }, { x: null }] });
        assert.match(text, /2 players placed/);
    });

    test('unnamed passers are reported, with what to do about it', () => {
        const text = note({ nodes: [], unmapped: 40 });
        assert.match(text, /40 passes came from a tracked figure nobody has named/);
        assert.match(text, /Who the video tracked/);
    });

    test('an uncalibrated run says so rather than blaming the mapping', () => {
        // Two different absences with two different fixes: one wants a coach at
        // the picker, the other wants a calibration.
        const text = note({ nodes: [], unplaced: 12 });
        assert.match(text, /no pitch calibration/);
        assert.doesNotMatch(text, /nobody has named/);
    });

    test('a trimmed event list is disclosed', () => {
        const text = passing.networkNote({ nodes: [] }, { truncated: true });
        assert.match(text, /trimmed to the most confident/);
    });
});

describe('the passing network draws what it claims to draw', () => {
    test('four times the passes is four times the circle', () => {
        // The obvious `MIN + (MAX - MIN) * sqrt(share)` looks identical and is
        // proportional to nothing: on the sample squad a player with three
        // times another's passes came out 1.85 times the area, so the diagram
        // quietly flattened the difference it exists to show.
        const big = passMod.nodeRadius(80, 80);
        const quarter = passMod.nodeRadius(20, 80);
        assert.ok(Math.abs((big * big) / (quarter * quarter) - 4) < 1e-9);
    });

    test('a barely-involved player is clamped, not vanished', () => {
        // Below the floor every dot draws the same size. A dot too small to
        // see is a player the diagram has deleted, and "barely involved" is
        // said in the note underneath instead.
        assert.equal(passMod.nodeRadius(1, 200), passMod.nodeRadius(2, 200));
        assert.ok(passMod.nodeRadius(1, 200) > 1);
    });

    test('twice as thick is twice as many passes', () => {
        assert.ok(Math.abs(
            passMod.edgeWidth(20, 20) / passMod.edgeWidth(10, 20) - 2,
        ) < 1e-9);
    });

    test('a pair that exchanged one pass is not a connection', () => {
        // A hairline between every pair of names turns the diagram into a mesh
        // where nothing stands out.
        assert.ok(passMod.MIN_EDGE >= 2);
    });
});

describe('the sample passing network', () => {
    const built = () => {
        const { byTrack } = sample.samplePassMapping();
        return passing.passingNetwork(sample.samplePassEvents(), {
            byTrack, team: 'team_a',
        });
    };

    test('every player in the shape is placed', () => {
        const net = built();
        assert.equal(net.nodes.length, 11);
        assert.ok(net.nodes.every((n) => n.x != null && n.y != null));
    });

    test('the invented counts match the invented passes', () => {
        // A preview whose figures contradict each other teaches whoever reads
        // it to stop checking whether figures agree.
        const net = built();
        for (const node of net.nodes) {
            assert.ok(node.completed <= node.passes, node.playerId);
        }
    });

    test('it previews the caveats, not just the picture', () => {
        // Two unnamed passers and some passes that went nowhere, on purpose:
        // the note under the diagram is most of what it is for.
        const net = built();
        assert.ok(net.unmapped > 0, 'unnamed passers');
        assert.ok(net.incomplete > 0, 'passes that found nobody');
        assert.equal(net.unplaced, 0, 'the sample run is calibrated');
    });

    test('the left side is the busiest pair, which is the point of it', () => {
        const net = built();
        const top = passing.foldEdges(net.edges)[0];
        assert.deepEqual([top.a, top.b].sort(), ['lb', 'lcm']);
    });

    test('every node sits inside the pitch', () => {
        for (const node of built().nodes) {
            assert.ok(node.x >= 0 && node.x <= 105, `${node.playerId} x`);
            assert.ok(node.y >= 0 && node.y <= 68, `${node.playerId} y`);
        }
    });
});
