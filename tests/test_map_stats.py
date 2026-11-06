"""map_stats: team shape read off the top-down map table."""

import numpy as np

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


def test_summary_rounds_and_covers():
    t = _table()
    out = M.summary(M.shape(t), t.fps)
    assert out[0]['half']['width'] == 50.0
    assert out[0]['cover']['line'] == 1.0
    assert out[0]['seen_share'] == 0.7
