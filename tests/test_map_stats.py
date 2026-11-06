"""map_stats: team shape read off the top-down map table."""

import copy
import dataclasses

import numpy as np
import pytest

from pitchiq import map_stats as M


def _table(frames=(0, 15, 30)):
    """Team 0 defends x=0: a back four at x=20, six more at x=40, spread across y 10..60.
    Team 1 mirrored at x=85/65. Plus a keeper each, a referee and a grey ring."""
    rows = []
    for f in frames:
        ys4 = [10, 25, 45, 60]
        ys6 = [12, 22, 30, 40, 48, 58]
        for team, back, mid, gk in ((0, 20, 40, 2), (1, 85, 65, 103)):
            for k, y in enumerate(ys4):
                rows.append((f, team * 100 + k, back, y, True, 1.0, team, M.OUTFIELD))
            for k, y in enumerate(ys6):
                rows.append((f, team * 100 + 10 + k, mid, y, k % 2 == 0, 0.6, team, M.OUTFIELD))
            rows.append((f, team * 100 + 50, gk, 34, True, 1.0, team, M.KEEPER))
        rows.append((f, 900, 52, 1, True, 1.0, 2, M.OUTFIELD))  # grey: in no team
        rows.append((f, 901, 50, 30, True, 1.0, 2, 2))  # referee
    c = list(zip(*rows))
    return M.MapTable(
        f=np.array(c[0]),
        trk=np.array(c[1]),
        x=np.array(c[2], float),
        y=np.array(c[3], float),
        seen=np.array(c[4]),
        alpha=np.array(c[5]),
        team=np.array(c[6]),
        role=np.array(c[7]),
        fps=30.0,
        length_m=105.0,
        width_m=68.0,
    )


def test_defends_left_from_seen_players():
    assert M.defends_left(_table()) == {0: True, 1: False}


def test_shape_counts_outfield_only():
    s = M.shape(_table())
    a, b = s[0], s[1]
    assert list(a.n) == [10, 10, 10]
    assert a.width[0] == 50 and a.depth[0] == 20  # y 10..60, x 20..40: no keeper, no grey
    # line: the deepest round(0.35 * 10) = 4 outfielders, from each team's own goal
    assert a.line[0] == 20 and b.line[0] == 105 - 85
    assert np.isclose(
        a.compact[0],
        np.mean(
            np.hypot(
                np.r_[[20] * 4, [40] * 6] - 32, np.r_[10, 25, 45, 60, 12, 22, 30, 40, 48, 58] - 35
            )
        ),
    )


def test_seen_only_and_trim():
    t = _table()
    s = M.shape(t, seen_only=True)
    assert s[0].n[0] == 4 + 3
    t1 = M.shape(t, trim=1)
    assert t1[0].width[0] == 58 - 12  # second-outermost each side


def test_absent_is_nan_not_zero():
    t = _table()
    few = t.subset((t.team != 0) | (t.trk % 100 < 2) | (t.role != M.OUTFIELD))  # team 0 down to 2
    s = M.shape(few, left={0: True, 1: False})
    assert np.isnan(s[0].width).all() and np.isnan(s[0].line).all()
    assert np.isfinite(s[1].width).all()


def test_bad_team_split_window_is_dropped():
    """Second window: team 0 seen only 4 a frame against team 1's 7 (ratio 0.57)."""
    t = _table(frames=(0, 15, 30, 45))
    t.windows = np.array([[0, 30], [30, 60]])
    late0 = (t.f >= 30) & (t.team == 0) & (t.role == M.OUTFIELD) & (t.trk % 100 >= 10)
    t = t.replace(seen=t.seen & ~late0)
    assert [c['ok'] for c in M.split_checks(t)] == [True, False]
    s = M.shape(t, left={0: True, 1: False})
    assert np.isfinite(s[0].line[:2]).all() and np.isnan(s[0].line[2:]).all()
    assert np.isnan(s[1].width[2:]).all()  # both teams: the split is wrong for both
    assert list(s[0].n) == [10] * 4  # still counted, just no numbers
    kept = M.shape(t, left={0: True, 1: False}, drop_bad_split=False)
    assert np.isfinite(kept[0].line).all()


def test_territory_play_and_height():
    t = _table()
    ts = M.territory(t, left={0: True, 1: False})
    # every outfielder, greys too: 4x20 + 6x40 + 4x85 + 6x65 + one grey at 52
    assert np.allclose(ts.play, 1102 / 21)
    assert np.allclose(ts.height[0], 32) and np.allclose(ts.height[1], 105 - 73)
    assert M.third_shares(ts.play, t.length_m) == {
        'defensive': 0.0,
        'middle': 1.0,
        'attacking': 0.0,
    }
    # on a boundary: the further-forward third, as zones.third does
    assert M.third_shares(np.array([35.0, np.nan]), 105.0)['middle'] == 1.0


def test_heatmap_turns_each_team_to_attack_right():
    t = _table()
    h0 = M.heatmap(t, 0, left={0: True, 1: False})
    h1 = M.heatmap(t, 1, left={0: True, 1: False})
    assert np.isclose(h0.share.sum(), 1) and h0.samples == 30
    assert np.allclose(h0.share, h1.share[:, ::-1])  # mirrored table, half turn: same zones
    assert h0.thirds() == {'defensive': 0.4, 'middle': 0.6, 'attacking': 0.0}
    assert h0.seen[1, 0] == 1.0  # the back four are seen
    assert h0.seen[2, 2] == 0.5  # mids at y 30 (seen) and 40 (guessed) share a zone
    assert np.isnan(h0.seen[5]).all()  # nobody there: no seen share, not 0
    # a half turn keeps wings: a team attacking left sees y = 60 as its right (low y = left)
    assert M._attack_right(t, 100.0, 60.0, False) == (5.0, 8.0)


def test_summary_rounds_and_covers():
    t = _table()
    out = M.summary(M.shape(t), t.fps)
    assert out[0]['half']['width'] == 50.0
    assert out[0]['cover']['line'] == 1.0
    assert out[0]['seen_share'] == 0.7


def test_report_block_for_the_teams_block():
    """Keyed by which side is ours; NaN becomes null; width and depth held back."""
    t = _table()
    b = M.report_block(t, ours=1)
    a, other = b['team_a'], b['team_b']
    assert a['shape'] == {
        'width_m': None,
        'depth_m': None,
        'compactness_m': a['shape']['compactness_m'],
        'line_m': 20.0,
    }
    assert other['shape']['line_m'] == 20.0
    fp = a['field_position']
    assert fp['height_m'] == 32.0
    # team 1 (ours) in its own direction: back four in the first third
    assert fp['third_share'] == {'defensive': 0.4, 'middle': 0.6, 'attacking': 0.0}
    assert fp['play_third_share'] == {'defensive': 0.0, 'middle': 1.0, 'attacking': 0.0}
    assert fp['seen_share'] == 0.7 and fp['kept_share'] == 1.0
    hm = fp['heatmap']
    assert (hm['cols'], hm['rows']) == M.REPORT_GRID
    assert len(hm['values']) == len(hm['seen']) == 21 * 14
    assert abs(sum(hm['values']) - 1) < 1e-3
    assert None in hm['seen'] and not any(isinstance(v, float) and v != v for v in hm['seen'])
    assert a['attacking_end'] == 'left' and other['attacking_end'] == 'right'


def test_report_block_dropped_half_is_null_not_zero():
    """Every window's split fails: team figures are null, the heatmap is gone."""
    t = _table()
    t.windows = np.array([[0, 60]])
    mids0 = (t.team == 0) & (t.role == M.OUTFIELD) & (t.trk % 100 >= 10)
    t = t.replace(seen=t.seen & ~mids0)  # team 0 seen 4 a frame against 7: ratio 0.57
    b = M.report_block(t, ours=0)
    fp = b['team_a']['field_position']
    assert b['team_a']['shape']['line_m'] is None and fp['height_m'] is None
    assert fp['heatmap'] is None and fp['third_share'] is None and fp['seen_share'] is None
    assert fp['kept_share'] == 0.0
    assert fp['play_third_share'] is not None  # play needs no team split


def test_report_block_needs_to_be_told_which_side_is_ours():
    import pytest

    with pytest.raises(ValueError):
        M.report_block(_table(), ours=2)


def _mirrored(t):
    """The same half with the ends changed, as after half time: the pitch turned round."""
    return dataclasses.replace(t, x=t.length_m - t.x, y=t.width_m - t.y)


def test_match_block_joins_halves_that_changed_ends():
    h1 = M.report_block(_table(), ours=0)
    h2 = M.report_block(_mirrored(_table()), ours=0)
    assert h1['team_a']['attacking_end'] == 'right' and h2['team_a']['attacking_end'] == 'left'
    m = M.match_block([h1, h2], [2400.0, 2400.0])
    for team in ('team_a', 'team_b'):
        # every figure is in the team's own direction, so the change of ends is no change
        assert m[team]['field_position'] == h1[team]['field_position']
        assert m[team]['shape'] == h1[team]['shape']
        assert m[team]['attacking_end'] is None  # the ends changed at half time


def test_match_block_weights_by_kept_time_and_skips_missing_figures():
    a = M.report_block(_table(), ours=0)
    b = copy.deepcopy(a)
    b['team_a']['field_position']['height_m'] = 50.0
    b['team_a']['shape']['line_m'] = None  # not measured in that half: no say
    m = M.match_block([a, b], [3000.0, 1000.0])['team_a']
    assert m['field_position']['height_m'] == round((32.0 * 3 + 50.0) / 4, 1)
    assert m['shape']['line_m'] == a['team_a']['shape']['line_m']
    b['team_a']['field_position']['kept_share'] = 0.0  # its split never held
    m = M.match_block([a, b], [3000.0, 1000.0])['team_a']
    assert m['field_position']['height_m'] == 32.0
    assert m['field_position']['kept_share'] == 0.75


def test_match_block_seen_per_cell_by_time_in_the_cell():
    a = M.report_block(_table(), ours=0)
    b = copy.deepcopy(a)
    ha, hb = a['team_a']['field_position']['heatmap'], b['team_a']['field_position']['heatmap']
    i = max(range(len(ha['values'])), key=lambda k: ha['values'][k])
    ha['seen'][i], hb['seen'][i] = 1.0, 0.0
    hb['values'] = [0.0] * len(hb['values'])  # half b never stood in any cell
    hb['values'][i] = 1.0
    m = M.match_block([a, b], [1000.0, 1000.0])['team_a']['field_position']['heatmap']
    va = ha['values'][i]
    assert m['seen'][i] == round(va / (va + 1.0), 2)
    assert abs(sum(m['values']) - 1.0) < 1e-3


def test_match_block_needs_a_duration_per_half():
    b = M.report_block(_table(), ours=0)
    with pytest.raises(ValueError):
        M.match_block([b, b], [100.0])
