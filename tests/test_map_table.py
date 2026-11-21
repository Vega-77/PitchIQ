"""The players-and-ball table (pitchiq/map_table.py) on a synthetic half."""

import numpy as np
import pytest

from pitchiq import map_table
from pitchiq.calibration import CalibrationError
from pitchiq.map_table import PLAYER_COLS, HalfTable
from pitchiq.panning_camera import PannedCalibration
from pitchiq.pitch import Pitch
from pitchiq.teams import TEAM_A, TEAM_B, UNKNOWN

# OpenCV Lab per window and team: team 0 a light kit, team 1 a dark one
KIT_LAB = np.array([[[200, 128, 128], [100, 160, 140]], [[204, 128, 128], [96, 160, 140]]])


def _cal(t):
    """The table's own calibration without a camera file on disk."""
    pitch = Pitch(length_m=t.length_m, width_m=t.width_m)
    return PannedCalibration(np.eye(3), pitch, CalibrationError(mean_m=0.3, max_m=2.0, p90_m=0.8))


def _ball(track, tag, f, x, y, src):
    """v17 ball files: x, y in StatsBomb units."""
    (track / 'h3d_v17').mkdir(parents=True, exist_ok=True)
    (track / 'evaluation' / 'p2d' / 'v17').mkdir(parents=True, exist_ok=True)
    n = len(f)
    np.savez(
        track / 'h3d_v17' / f'{tag}_ball3dd.npz',
        abs=f,
        x=x,
        y=y,
        z=np.zeros(n),
        state=np.array(['ground'] * n),
    )
    np.savez(
        track / 'evaluation' / 'p2d' / 'v17' / f'{tag}.npz',
        abs=f,
        x=np.zeros(n),
        y=np.zeros(n),
        src=src,
        ring=np.zeros(n),
    )


def _half(tmp_path, ball_x=None, ball_abs=None, role=None, team=None):
    """A 3-frame map with two dots a frame and a 5-frame v17 ball, pitch 110 x 60 m."""
    mp = tmp_path / 'map' / 'zz1.npz'
    mp.parent.mkdir()
    f = np.array([100, 100, 101, 101, 102, 102])
    cols = {
        'f': f,
        'trk': np.array([1, 2, 1, 2, 1, 2]),
        'x': np.array([10.0, 50, 11, 51, 12, 52]),
        'y': np.array([5.0, 30, 5, 30, 5, 30]),
        'seen': np.array([1, 1, 1, 0, 1, 1], bool),
        'alpha': np.ones(6),
        'team': np.array([0, 1, 0, 1, 0, 1]) if team is None else np.asarray(team),
        'role': np.zeros(6, int) if role is None else np.asarray(role),
        'slot': np.full(6, -1),
    }
    np.savez(mp, fps=30.0, length_m=110.0, width_m=60.0, kit_lab=KIT_LAB, **cols)
    track = tmp_path / 'balltrack'
    a = np.arange(99, 104) if ball_abs is None else ball_abs
    x = np.array([60.0, 12.0, np.nan, 24.0, 0.0]) if ball_x is None else ball_x
    _ball(track, 'zz1', a, x, np.array([40.0, 8.0, 8.0, 16.0, 0.0]), np.array([1, 2, 1, 3, 0]))
    return mp, track


def test_the_table_joins_map_and_ball_in_metres(tmp_path):
    mp, track = _half(tmp_path)
    t = HalfTable.load('zz1', mp, track)
    assert (t.fps, t.length_m, t.width_m, t.ball_f0) == (30.0, 110.0, 60.0, 99)
    assert t.frames == range(100, 103)
    assert set(t.players) == set(PLAYER_COLS)
    rows = t.players_at(101)
    assert rows['trk'].tolist() == [1, 2] and rows['seen'].tolist() == [True, False]
    assert len(t.players_at(99)['x']) == 0
    # StatsBomb 120 x 80 -> metres on a 110 x 60 pitch
    assert t.ball_at(99) == pytest.approx((55.0, 30.0, 1))
    assert t.ball_at(100) == pytest.approx((11.0, 6.0, 2))
    x, y, src = t.ball_at(103)  # no ball: src 0 blanks the position whatever the file holds
    assert np.isnan(x) and np.isnan(y) and src == 0
    x, _, src = t.ball_at(500)  # outside the ball arrays
    assert np.isnan(x) and src == 0


def test_seen_ball_is_a_placed_pick(tmp_path):
    """Bridges, frames with no ball and rings the 3D chain could not place are not seen."""
    t = HalfTable.load('zz1', *_half(tmp_path))
    assert t.ball_seen().tolist() == [True, True, False, False, False]
    x, _, src = t.ball_at(101)  # a pick with no 3D position keeps its source, NaN metres
    assert np.isnan(x) and src == 1


def test_the_table_names_what_makes_a_missing_file(tmp_path):
    mp, track = _half(tmp_path)
    (track / 'h3d_v17' / 'zz1_ball3dd.npz').unlink()
    with pytest.raises(FileNotFoundError, match='ball17'):
        HalfTable.load('zz1', mp, track)
    with pytest.raises(FileNotFoundError, match='wholemap'):
        HalfTable.load('zz1', tmp_path / 'nothing.npz', track)


def test_the_table_refuses_a_ball_that_skips_frames(tmp_path):
    mp, track = _half(tmp_path, ball_abs=np.array([99, 100, 101, 103, 104]))
    with pytest.raises(ValueError, match='skips'):
        HalfTable.load('zz1', mp, track)


def test_default_paths_point_at_the_stats_map_and_the_ball_tree():
    assert map_table.MAPS.parts[-3:] == ('PitchIQ-data', 'stats', 'map')
    assert map_table.TRACK.parts[-2:] == ('pitchiq', 'balltrack')


# ---- the stats' view of the table


def test_frames_put_seen_players_on_their_dots_in_metres(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path))
    table = t.frame_table(calibration=_cal(t))
    assert [r.frame_index for r in table.records] == [100, 101, 102]
    assert table.records[1].timestamp_s == pytest.approx(101 / 30)
    first, second = table.records[0].players, table.records[1].players
    # track id = trk * 3 + team; a box PLAYER_HEIGHT_M tall standing on the dot
    h, w = map_table.PLAYER_HEIGHT_M, map_table.BOX_HALF_WIDTH_M
    assert first[0].tolist() == pytest.approx([3, 10 - w, 5 - h, 10 + w, 5, 1])
    assert first[1, 0] == 7
    assert second[:, 0].tolist() == [3]  # trk 2's guess on 101 is not a sighting
    with_guesses = t.frame_table(seen_only=False, calibration=_cal(t))
    assert with_guesses.records[1].players[:, 0].tolist() == [3, 7]


def test_frames_carry_the_ball_where_it_is_placed(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path))
    table = t.frame_table(calibration=_cal(t))
    r100, r101, r102 = table.records
    assert r100.ball_xy == pytest.approx((11.0, 6.0)) and r100.ball_observed  # a gap pick
    assert r101.ball_xy is None  # a pick the 3D chain could not place
    assert r102.ball_xy == pytest.approx((22.0, 12.0)) and not r102.ball_observed  # a bridge
    assert [p.frame_index for p in t.trajectory(table).points] == [100, 102]
    assert [p.frame_index for p in t.trajectory().points] == [99, 100, 102]


def test_teams_follow_the_map_and_ours_is_told_by_kit(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path))
    table = t.frame_table(calibration=_cal(t))
    by_track, colours, separation = t.teams(table, 'lighter')
    assert by_track == {3: TEAM_A, 7: TEAM_B}
    assert colours[3][0].tolist() == [202, 128, 128]  # the windows' median kit
    assert separation == pytest.approx(np.hypot(32, 12))
    by_track, _, _ = t.teams(table, 'darker')
    assert by_track == {3: TEAM_B, 7: TEAM_A}
    with pytest.raises(ValueError, match='lighter'):
        t.teams(table, 'red')


def test_grey_tracks_belong_to_no_team(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path, team=[0, 2, 0, 2, 0, 2]))
    by_track, colours, _ = t.teams(t.frame_table(calibration=_cal(t)), 'lighter')
    assert by_track == {3: TEAM_A, 8: UNKNOWN} and 8 not in colours


def test_participants_come_from_the_map_roles(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path, role=[1, 2, 1, 2, 1, 2]))
    report = t.participants(t.frame_table(calibration=_cal(t)))
    keeper, ref = report.by_track[3], report.by_track[7]
    assert (keeper.role, keeper.reason, keeper.sightings) == ('player', 'the map (keeper)', 3)
    assert (ref.role, ref.reason, ref.sightings) == ('official', 'the map (referee)', 2)
    # as on the video path: an official is flagged but kept (with no team), only off-field
    # figures are left out of the counts
    assert report.officials == {7} and not report.excluded and report.is_player(7)


def test_first_cuts_the_table_to_a_test_window(tmp_path):
    t = HalfTable.load('zz1', *_half(tmp_path)).first(2 / 30)
    assert t.frames == range(100, 102)
    assert t.players_at(102)['x'].size == 0


# ---- the stats on the table

SPOTS = {1: (40.0, 30.0), 2: (60.0, 20.0), 3: (75.0, 40.0), 4: (30.0, 45.0), 5: (55.0, 50.0)}
TEAM = {1: 0, 2: 0, 3: 1, 4: 1, 5: 2}  # 1, 2 ours (the light kit); 3, 4 theirs; 5 the referee


def _match(tmp_path):
    """Twenty seconds, everyone standing still: 1 passes to 2, 2 gives it to 3, 3 keeps it."""
    mp = tmp_path / 'map' / 'zz1.npz'
    mp.parent.mkdir()
    f0, n = 1000, 600
    rows = np.array(
        [(fr, k, *SPOTS[k], TEAM[k], 2 if k == 5 else 0) for fr in range(f0, f0 + n) for k in SPOTS]
    )
    cols = {
        'f': rows[:, 0].astype(int),
        'trk': rows[:, 1].astype(int),
        'x': rows[:, 2],
        'y': rows[:, 3],
        'seen': np.ones(len(rows), bool),
        'alpha': np.ones(len(rows)),
        'team': rows[:, 4].astype(int),
        'role': rows[:, 5].astype(int),
        'slot': np.full(len(rows), -1),
    }
    np.savez(mp, fps=30.0, length_m=110.0, width_m=60.0, kit_lab=KIT_LAB, **cols)
    pts = []
    for k, m in ((1, 150), ((1, 2), 60), (2, 150), ((2, 3), 60), (3, 180)):
        if isinstance(k, int):
            pts += [SPOTS[k]] * m  # at the player's feet
        else:  # rolling from one to the other
            a, b = np.array(SPOTS[k[0]]), np.array(SPOTS[k[1]])
            pts += [tuple(a + (b - a) * (j + 1) / (m + 1)) for j in range(m)]
    p = np.array(pts)
    track = tmp_path / 'balltrack'
    f = np.arange(f0, f0 + n)
    _ball(track, 'zz1', f, p[:, 0] * 120 / 110, p[:, 1] * 80 / 60, np.ones(n, int))
    return mp, track


def test_analyse_table_counts_a_synthetic_spell_with_no_video(tmp_path, monkeypatch):
    from pitchiq.pipeline import MatchContext, analyse_table

    monkeypatch.setattr(HalfTable, 'calibration', _cal)
    half = HalfTable.load('zz1', *_match(tmp_path))
    report = analyse_table(half, context=MatchContext(ours_kit='lighter'))
    assert report.duration_s == pytest.approx(20.0)
    assert report.participants.by_track[5 * 3 + 2].role == 'official'
    teams = report.to_json()['teams']
    ours, theirs = teams['team_a'], teams['team_b']
    assert ours['possession_pct'] > theirs['possession_pct'] > 0
    assert ours['passes_attempted'] >= 1 and ours['passes_completed'] >= 1
