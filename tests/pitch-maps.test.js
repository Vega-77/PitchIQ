/**
 * Heatmaps and positions: merging grids, what binning the pitch costs, and
 * where a player played against the line they were picked in.
 *
 * Run:  node --test tests/pitch-maps.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';
import * as heatmap from '../assets/heatmap.js';
import { squad } from './report-fixtures.js';

// ------------------------------------------------------------- the heatmap
//
// The grid was computed per track, never carried across to the cluster, and
// never drawn — declared, published and null on every run. These cover the two
// things that can silently produce a plausible wrong picture: the weighting,
// and which axis is which.

describe('mergeHeatmaps', () => {
    // 2 wide (along the pitch) by 2 deep (across it), column-major.
    const grid = (values) => ({ cols: 2, rows: 2, values });

    const corner = grid([1, 0, 0, 0]);   // one end
    const far = grid([0, 0, 0, 1]);      // the other

    test('one grid comes back normalised', () => {
        const out = heatmap.mergeHeatmaps([{ grid: grid([2, 0, 0, 2]), minutes: 10 }]);
        assert.deepEqual(out.values, [0.5, 0, 0, 0.5]);
    });

    test('minutes decide the weight, not the number of fragments', () => {
        // The failure this prevents: a player tracked cleanly for 45 minutes
        // plus once more for eight seconds at the edge of frame, shown with a
        // hotspot at the edge of frame.
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 45 },
            { grid: far, minutes: 5 },
        ]);
        assert.equal(out.values[0], 0.9);
        assert.equal(out.values[3], 0.1);
    });

    test('a fragment with no minutes still counts, but barely', () => {
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 30 },
            { grid: far, minutes: 0 },
        ]);
        assert.ok(out.values[0] > 0.99);
        assert.ok(out.values[3] > 0);
    });

    test('grids of different shapes are skipped, not stretched', () => {
        // Two bin counts mean two different runs, and resampling one onto the
        // other would invent positions.
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 10 },
            { grid: { cols: 3, rows: 3, values: new Array(9).fill(1) }, minutes: 10 },
        ]);
        assert.equal(out.cols, 2);
        assert.deepEqual(out.values, [1, 0, 0, 0]);
    });

    test('nothing to draw gives null, not an empty pitch', () => {
        // An empty pitch reads as a player who never moved.
        assert.equal(heatmap.mergeHeatmaps([]), null);
        assert.equal(heatmap.mergeHeatmaps(null), null);
        assert.equal(heatmap.mergeHeatmaps([{ grid: null, minutes: 90 }]), null);
        assert.equal(heatmap.mergeHeatmaps([{ grid: grid([0, 0, 0, 0]), minutes: 9 }]), null);
    });

    test('a malformed grid is refused rather than half-read', () => {
        assert.equal(heatmap.isGrid({ cols: 2, rows: 2, values: [1, 2] }), false);
        assert.equal(heatmap.isGrid({ cols: 0, rows: 0, values: [] }), false);
        assert.equal(heatmap.isGrid(undefined), false);
    });
});

describe('cellAt and busiestCell', () => {
    // 3 along the pitch by 2 across it. Column-major, so index = x * rows + y.
    const grid = { cols: 3, rows: 2, values: [0, 0, 0, 0.7, 0.3, 0] };

    test('x runs along the pitch and y across it', () => {
        // Getting this backwards draws a plausible picture of somebody who
        // played sideways, which is the kind of wrong that survives review.
        assert.equal(heatmap.cellAt(grid, 1, 1), 0.7);
        assert.equal(heatmap.cellAt(grid, 2, 0), 0.3);
        assert.equal(heatmap.cellAt(grid, 0, 0), 0);
    });

    test('the busiest cell is found in the same coordinates', () => {
        assert.deepEqual(heatmap.busiestCell(grid), { x: 1, y: 1, share: 0.7 });
    });

    test('an empty grid has no busiest cell', () => {
        assert.equal(heatmap.busiestCell({ cols: 2, rows: 2, values: [0, 0, 0, 0] }), null);
        assert.equal(heatmap.busiestCell(null), null);
    });
});

describe('where a player plays', () => {
  /**
   * The field exists for one reason: a goalkeeper covers a fraction of the
   * ground an outfielder does, and the player table used to rank them against
   * each other down one column with nothing saying so.
   *
   * The behaviour worth pinning is the *absence* case. Most squads will never
   * fill this in, and a coach who has not must get back exactly the table they
   * had — same players, same order, no headings for a field they have never
   * seen.
   */

  const squad = (...positions) => positions.map((position, i) => ({
    id: `p${i}`, playerName: `Player ${i}`, position, goals: 0, minutesPlayed: 90,
  }));

  const titles = (groups) => groups.map((g) => g.title);
  const names = (groups) => groups.flatMap((g) => g.players.map((p) => p.id));

  describe('reading a position', () => {
    test('the four it knows', () => {
      for (const id of ['gk', 'def', 'mid', 'fwd']) {
        assert.equal(report.positionOf(id), id);
      }
    });

    test('anything else is unset, not an error and not a guess', () => {
      // A document written before this field existed, a typo, or a value from
      // some future vocabulary. All of them mean "nobody has said".
      for (const bad of [null, undefined, '', 'goalkeeper', 'GK', 'striker', 7, {}]) {
        assert.equal(report.positionOf(bad), null);
      }
    });

    test('a label only for the ones it knows', () => {
      assert.equal(report.positionLabel('mid'), 'Midfielder');
      assert.equal(report.positionLabel('winger'), null);
      assert.equal(report.positionLabel(null), null);
    });

    test('the keeper test does not fire on an unset player', () => {
      assert.equal(report.isKeeper({ position: 'gk' }), true);
      assert.equal(report.isKeeper({ position: 'def' }), false);
      assert.equal(report.isKeeper({ position: null }), false);
      assert.equal(report.isKeeper({}), false);
      assert.equal(report.isKeeper(null), false);
    });
  });

  describe('grouping a squad', () => {
    test('nobody has a position, so there are no headings at all', () => {
      const groups = report.groupByPosition(squad(null, null, null));
      assert.equal(groups.length, 1);
      assert.equal(groups[0].title, null);
      assert.deepEqual(names(groups), ['p0', 'p1', 'p2']);
    });

    test('an unrecognised value counts as nobody having said', () => {
      // Guards the case a stray string in one document silently switches the
      // whole table into a grouped layout with one bogus heading.
      const groups = report.groupByPosition(squad('striker', 'GK'));
      assert.equal(groups.length, 1);
      assert.equal(groups[0].title, null);
    });

    test('one position set is enough to group the whole table', () => {
      const groups = report.groupByPosition(squad('gk', null, null));
      assert.deepEqual(titles(groups), ['Goalkeepers', 'No position set']);
    });

    test('team-sheet order, keepers first, and empty lines dropped', () => {
      const groups = report.groupByPosition(squad('fwd', 'gk', 'mid'));
      assert.deepEqual(titles(groups), ['Goalkeepers', 'Midfielders', 'Forwards']);
    });

    test('the unset go last, under a heading that is not a line of the team', () => {
      const groups = report.groupByPosition(squad('mid', null));
      assert.equal(groups.at(-1).title, 'No position set');
      assert.deepEqual(groups.at(-1).players.map((p) => p.id), ['p1']);
    });

    test('the involvement order survives inside a line', () => {
      // The point of grouping is to add a heading, not to take the ranking
      // away — a coach still wants the standouts at the top of each line.
      const players = [
        { id: 'quiet', position: 'mid', goals: 0 },
        { id: 'loud', position: 'mid', goals: 3 },
        { id: 'keeper', position: 'gk', goals: 0 },
      ];
      const groups = report.groupByPosition(players, (a, b) => b.goals - a.goals);
      assert.deepEqual(names(groups), ['keeper', 'loud', 'quiet']);
    });

    test('everyone survives the grouping', () => {
      // A dropped player is the failure that would go unnoticed longest: the
      // table still looks like a table.
      const players = squad('gk', 'def', 'def', null, 'fwd', 'mid');
      const groups = report.groupByPosition(players);
      assert.equal(names(groups).length, players.length);
      assert.deepEqual(new Set(names(groups)).size, players.length);
    });

    test('an empty squad, and no squad at all', () => {
      assert.deepEqual(report.groupByPosition([]), [{ id: null, title: null, players: [] }]);
      assert.deepEqual(report.groupByPosition(null), [{ id: null, title: null, players: [] }]);
    });

    test('it does not reorder what it was given', () => {
      // `state.match.stats.players` is held across re-renders and read by the
      // coverage note; sorting it in place would reorder somebody else's list.
      const players = squad('fwd', 'gk');
      const before = players.map((p) => p.id);
      report.groupByPosition(players, (a, b) => a.id.localeCompare(b.id));
      assert.deepEqual(players.map((p) => p.id), before);
    });
  });
});

describe('what binning the pitch into cells costs', () => {
  // The grid arrives 12 x 8 over a 105 x 68 m pitch, so a cell is 8.75 m along
  // the pitch — twice the gap between a defender and a midfielder standing
  // apart. Every figure in the shape block is read off that grid, so the size
  // of the error it introduces is not a detail; it decides whether any of it
  // can be reported in metres at all.
  //
  // These numbers are the ones written into `assets/heatmap.js`. They are here
  // so that a change to the binning, the correction, or the grid's default
  // shape fails a test rather than quietly widening the error under a docstring
  // that still claims the old one.

  const L = 105;

  /**
   * Occupancy laid down as a Gaussian along the pitch and then binned, next to
   * the same distribution measured unbinned.
   *
   * Quadrature rather than sampled points, so the comparison isolates the one
   * thing being measured — the binning — instead of mixing in the noise of a
   * random draw. Truncated at the goal lines, because a real player's occupancy
   * is, and the truncation is half of what the tails of this table are about.
   */
  const laid = (meanM, spreadM, cols = 12, rows = 8) => {
    const values = new Array(cols * rows).fill(0);
    const cellW = L / cols;
    const steps = cols * 800;
    let total = 0;
    let sumX = 0;
    let sumXX = 0;

    for (let i = 0; i < steps; i += 1) {
      const xM = ((i + 0.5) / steps) * L;
      const share = Math.exp(-0.5 * ((xM - meanM) / spreadM) ** 2);
      // One lane across the pitch: the y axis plays no part in either figure.
      values[Math.min(cols - 1, Math.floor(xM / cellW)) * rows + 3] += share;
      total += share;
      sumX += share * xM;
      sumXX += share * xM * xM;
    }

    const trueMeanM = sumX / total;
    return {
      grid: { cols, rows, values: values.map((v) => v / total) },
      trueMeanM,
      trueSpreadM: Math.sqrt(sumXX / total - trueMeanM ** 2),
    };
  };

  const sweep = (spreads, from, to) => {
    let worst = 0;
    const all = [];
    for (const spreadM of spreads) {
      for (let meanM = from; meanM <= to; meanM += 0.25) {
        const { grid, trueMeanM } = laid(meanM, spreadM);
        const off = Math.abs(heatmap.gridCentroid(grid).xM - trueMeanM);
        all.push(off);
        worst = Math.max(worst, off);
      }
    }
    all.sort((a, b) => a - b);
    return { worst, median: all[all.length >> 1] };
  };

  test('out on the pitch the binned middle is the middle', () => {
    // Spreads from a full-back who holds a line to a midfielder who covers
    // half the pitch, everywhere between the two penalty areas.
    const { worst, median } = sweep([4, 5, 6, 8, 10, 12, 16, 20], 8, 97);
    assert.ok(worst < 0.25, `worst ${worst}`);
    assert.ok(median < 0.02, `median ${median}`);
  });

  test('the error is worst on the one row no verdict is drawn from', () => {
    // Against a goal line the pitch truncates the distribution and the cell it
    // is truncated in stops being symmetric, which is where the whole error
    // lives. Twice the outfield figure — and the only player who averages four
    // metres off their own goal line is the keeper, who appears in the rows and
    // never in a remark.
    const { worst } = sweep([4, 5, 6, 8], 4, 7);
    assert.ok(worst > 0.3 && worst < 0.5, `worst ${worst}`);
  });

  test('somebody who never moved is the case it cannot do', () => {
    // 2.3 m out at a 1 m spread. A footballer does not have a 1 m spread; a
    // corner flag does. Recorded because the failure is real and because it
    // shrinks fast — by 3 m of spread it is back under a third of a metre.
    const tightest = (spreadM) => {
      let worst = 0;
      for (let meanM = 20; meanM <= 85; meanM += 0.25) {
        const { grid, trueMeanM } = laid(meanM, spreadM);
        worst = Math.max(worst, Math.abs(heatmap.gridCentroid(grid).xM - trueMeanM));
      }
      return worst;
    };
    assert.ok(tightest(1) > 2.2 && tightest(1) < 2.4);
    assert.ok(tightest(2) < 1.0);
    assert.ok(tightest(3) < 0.3);
  });

  test('Sheppard\'s correction gives the band back', () => {
    // Averaged over where the cell edges fall, which is what the correction is
    // derived for. Without it every one of these reads high, and a tight player
    // would be handed a wider uncertainty than they earned.
    const across = (spreadM) => {
      let sum = 0;
      let n = 0;
      for (let meanM = 48.125; meanM < 48.125 + 8.75; meanM += 0.125) {
        sum += heatmap.gridSpread(laid(meanM, spreadM).grid);
        n += 1;
      }
      return sum / n;
    };
    for (const spreadM of [4, 5, 6, 8, 12]) {
      assert.ok(Math.abs(across(spreadM) - spreadM) < 0.01, `${spreadM} -> ${across(spreadM)}`);
    }
  });

  test('one player at one phase does worse than the average of all of them', () => {
    // The figure the docstring used to give was the phase-averaged one, and a
    // single player is not an average over phases. A true 4 m reads anywhere in
    // 3.85-4.14 depending on where the grid's lines happen to fall, and a true
    // 3 m - a third of a cell - breaks down completely.
    const at = (meanM, spreadM) => heatmap.gridSpread(laid(meanM, spreadM).grid);
    assert.ok(Math.abs(at(48.125, 4) - 3.85) < 0.02);
    assert.ok(Math.abs(at(52.5, 4) - 4.14) < 0.02);
    assert.ok(at(48.125, 3) < 2.3);
    assert.ok(at(52.5, 3) > 3.6);
  });
});

describe('turning a grid into a place on the pitch', () => {
  const grid = (cols, rows, at) => {
    const values = new Array(cols * rows).fill(0);
    values[at[0] * rows + at[1]] = 1;
    return { cols, rows, values };
  };

  test('a player attacking right is measured from the left goal', () => {
    // Column 1 of 12 is 8.75-17.5 m along, centred at 13.125.
    const out = heatmap.orientedCentroid(grid(12, 8, [1, 1]), 'right');
    assert.ok(Math.abs(out.forwardM - 13.125) < 0.001);
    assert.ok(Math.abs(out.lateralM - 12.75) < 0.001);
  });

  test('attacking left mirrors both axes, not just the one', () => {
    // Flip only x and a player who lived on the left wing in the second half
    // comes out on the right — the same reason cv/report_json.py::shot_marks
    // mirrors both.
    const out = heatmap.orientedCentroid(grid(12, 8, [1, 1]), 'left');
    assert.ok(Math.abs(out.forwardM - (105 - 13.125)) < 0.001);
    assert.ok(Math.abs(out.lateralM - (68 - 12.75)) < 0.001);
  });

  test('without an attacking end there is no forward', () => {
    // "They stayed high" and "they sat deep" are the same picture flipped, so
    // there is no half-answer to give here.
    for (const end of [null, undefined, '', 'up', 0]) {
      assert.equal(heatmap.orientedCentroid(grid(12, 8, [1, 1]), end), null);
    }
    assert.equal(heatmap.orientedCentroid(null, 'right'), null);
    assert.equal(heatmap.orientedCentroid({ cols: 2, rows: 2, values: [0, 0, 0, 0] }, 'right'), null);
  });

  test('the spread is the same figure whichever way they were kicking', () => {
    const wide = { cols: 12, rows: 8, values: new Array(96).fill(1 / 96) };
    const right = heatmap.orientedCentroid(wide, 'right');
    const left = heatmap.orientedCentroid(wide, 'left');
    assert.equal(right.spreadM, left.spreadM);
    assert.ok(right.spreadM > 0);
  });

  test('an empty grid is nowhere, not the centre spot', () => {
    // The centre spot is a real place a defensive midfielder averages, so it
    // could never have been the sentinel even if this project allowed one.
    assert.equal(heatmap.gridCentroid({ cols: 2, rows: 2, values: [0, 0, 0, 0] }), null);
    assert.equal(heatmap.gridCentroid(null), null);
    assert.equal(heatmap.gridSpread(null), null);
  });
});

describe('where they played against the line they were picked in', () => {
  /**
   * The shape of this feature is that the rows are a measurement and the
   * remarks are a verdict, and the two have to be able to come apart. Almost
   * every test here is about a case where the measurement stands and the
   * verdict is withheld, because that is the common case on real footage and
   * the one a careless version gets wrong by reporting a finding anyway.
   */

  const EVEN = { thirds: { left: 0.92, middle: 0.95, right: 0.9 } };

  // 5 m of spread over a full match: a band of 1.83 m, well inside MAX_BAND_M.
  const at = (playerId, position, forwardM, extra = {}) => ({
    playerId,
    name: playerId,
    position,
    forwardM,
    lateralM: 34,
    spreadM: 5,
    minutesTracked: 60,
    ...extra,
  });

  const LINES = [
    at('d1', 'def', 25), at('d2', 'def', 27),
    at('m1', 'mid', 45), at('m2', 'mid', 48),
    at('f1', 'fwd', 75),
  ];

  const play = (rows, coverage = EVEN) => report.positionalPlay(rows, { coverage });

  test('a side that lined up where it was picked gets no remarks and no complaint', () => {
    const out = play(LINES);
    assert.equal(out.withheld, null);
    assert.deepEqual(out.remarks, []);
    assert.equal(out.note, null);
    assert.equal(out.rows.length, 5);
  });

  test('the rows come back up the pitch, whatever order they went in', () => {
    const out = play([...LINES].reverse());
    assert.deepEqual(out.rows.map((r) => r.playerId), ['d1', 'd2', 'm1', 'm2', 'f1']);
  });

  test('a defender ahead of every midfielder is the remark', () => {
    const out = play([at('d1', 'def', 25), at('d2', 'def', 60), ...LINES.slice(2)]);
    assert.equal(out.remarks.length, 1);
    const [only] = out.remarks;
    assert.equal(only.playerId, 'd2');
    assert.equal(only.direction, 'ahead');
    assert.equal(only.line, 'mid');
    assert.match(only.text, /ahead of every one of the midfielders/);
    // The margin over the nearest of them, which is what "by how much" means.
    assert.equal(only.gapM, 12);
  });

  test('ahead of most of a line is not ahead of the line', () => {
    // 60 clears the midfielder at 45 by 15 m and the one at 58 by 2. A rule
    // that took the line's average would fire here, and a coach told twice
    // about nothing stops reading the third time.
    const out = play([
      at('d1', 'def', 25), at('d2', 'def', 60),
      at('m1', 'mid', 45), at('m2', 'mid', 58), at('f1', 'fwd', 75),
    ]);
    assert.deepEqual(out.remarks, []);
    assert.equal(out.withheld, null);
  });

  test('one player, one remark: the furthest line they cleared', () => {
    const out = play([at('d1', 'def', 25), at('d2', 'def', 90), ...LINES.slice(2)]);
    assert.equal(out.remarks.length, 1);
    assert.equal(out.remarks[0].line, 'fwd');
    assert.match(out.remarks[0].text, /ahead of every one of the forwards/);
    assert.equal(out.remarks[0].gapM, 15);
  });

  test('a forward behind the whole defence reads the other way round', () => {
    const out = play([...LINES.slice(0, 4), at('f1', 'fwd', 15)]);
    assert.equal(out.remarks.length, 1);
    assert.equal(out.remarks[0].playerId, 'f1');
    assert.equal(out.remarks[0].direction, 'behind');
    assert.match(out.remarks[0].text, /behind every one of the defenders/);
  });

  test('two lines that swapped over are one finding, not five', () => {
    // The arithmetic is symmetric and would otherwise flag both sides: the lone
    // striker who dropped in, and every defender he dropped behind. Said from
    // the smaller side only, because it is the same sentence either way.
    const lone = play([...LINES.slice(0, 4), at('f1', 'fwd', 15)]);
    assert.deepEqual(lone.remarks.map((r) => r.playerId), ['f1']);

    // The other way round, with one defender behind two forwards, the defender
    // is the smaller side and the one spoken about.
    const solo = play([at('d1', 'def', 25), at('f1', 'fwd', 15), at('f2', 'fwd', 17)]);
    assert.deepEqual(solo.remarks.map((r) => r.playerId), ['d1']);
    assert.equal(solo.remarks[0].direction, 'ahead');

    // Two against two leaves nothing to choose between them, and the honest
    // answer is to say it from both ends rather than pick one.
    const even = play([
      at('d1', 'def', 25), at('d2', 'def', 27),
      at('f1', 'fwd', 15), at('f2', 'fwd', 17),
    ]);
    assert.deepEqual(
      even.remarks.map((r) => r.playerId).sort(), ['d1', 'd2', 'f1', 'f2'],
    );
  });

  test('one unjudgeable player takes their whole line out of the comparison', () => {
    // "Ahead of the whole midfield" is a claim about the whole midfield and
    // cannot be made from two of the three.
    const roamer = at('m3', 'mid', 50, { spreadM: 20, minutesTracked: 30 });
    const line = [at('d1', 'def', 25), at('d2', 'def', 60), ...LINES.slice(2)];

    // Without the roamer, d2 at 60 is ahead of a midfield sitting at 45 and 48.
    assert.deepEqual(play(line).remarks.map((r) => r.playerId), ['d2']);

    // With them, there is no longer a whole midfield to be ahead of.
    const out = play([...line, roamer]);
    assert.deepEqual(out.remarks, []);
    // And they are still on the screen with everyone else.
    assert.equal(out.rows.length, 6);
    assert.equal(out.rows.find((r) => r.playerId === 'm3').judgeable, false);
  });

  test('a keeper is measured and never judged', () => {
    // A keeper covers a third the ground of a midfielder. Ranking them on this
    // axis would put every keeper last and call it a finding.
    const out = play([at('g1', 'gk', 8), ...LINES]);
    assert.equal(out.rows[0].playerId, 'g1');
    assert.deepEqual(out.remarks, []);
    // Even one playing absurdly high, which is a real thing a keeper does.
    const sweeper = play([at('g1', 'gk', 62), ...LINES]);
    assert.deepEqual(sweeper.remarks.map((r) => r.playerId), []);
  });

  test('an uneven camera withholds the verdict and keeps the measurement', () => {
    const out = play(LINES, { thirds: { left: 0.95, middle: 0.8, right: 0.5 } });
    assert.equal(out.withheld, 'uneven');
    assert.equal(out.rows.length, 5);
    assert.deepEqual(out.remarks, []);
    assert.match(out.note, /would mostly compare the camera/);
  });

  test('a run that never measured its framing is withheld too', () => {
    const out = play(LINES, null);
    assert.equal(out.withheld, 'unknown');
    assert.match(out.note, /did not record how much of the pitch/);
  });

  test('a squad with no positions set is asked for them, not judged', () => {
    const out = play(LINES.map((r) => ({ ...r, position: null })));
    assert.equal(out.withheld, 'no-lines');
    assert.equal(out.rows.length, 5);
    assert.match(out.note, /Give these players a position/);
  });

  test('one comparable player is nobody to compare with', () => {
    const out = play([
      at('d1', 'def', 25),
      at('m1', 'mid', 45, { spreadM: 20, minutesTracked: 30 }),
    ]);
    assert.equal(out.withheld, 'too-thin');
    assert.equal(out.rows.length, 2);
  });

  test('a row with no position at all is not a row', () => {
    const out = play([...LINES, at('x', 'mid', null), at('y', 'mid', NaN), null]);
    assert.equal(out.rows.length, 5);
  });

  test('nothing to draw is not an error', () => {
    for (const empty of [[], null, undefined]) {
      const out = report.positionalPlay(empty, { coverage: EVEN });
      assert.deepEqual(out.rows, []);
      assert.deepEqual(out.remarks, []);
    }
  });
});
