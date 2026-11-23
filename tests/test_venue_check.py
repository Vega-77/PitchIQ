"""Where a game's camera stood, against earlier games filmed from the same spot (3.8).

Synthetic keyframes through the real matching: points on a flat pitch seen by a pinhole
camera that turns (the same spot) or moves 6 m (paint still matches by a homography, but no
pure turn explains it), and the calibrations' camera positions in the installed format."""

from __future__ import annotations

import pickle

import numpy as np
import pytest

from pitchiq import run_checks as rc
from pitchiq.experiments import venue_check as V
from pitchiq.pins import M_PER_R

F = 1500.0
RNG = np.random.default_rng(7)
GROUND = np.c_[RNG.uniform(35, 75, 400), RNG.uniform(5, 40, 400), np.zeros(400)]
DESC = RNG.uniform(0, 255, (400, 128)).astype(np.float32)  # one look per point, both views


def look_at(C, T):
    fwd = (T - C) / np.linalg.norm(T - C)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    return np.array([right, down, fwd])


def view(C, T):
    """Pixels and descriptors of the ground points a camera at C looking at T sees."""
    x = (GROUND - C) @ look_at(C, T).T
    px = F * x[:, :2] / x[:, 2:3] + [960, 540]
    keep = (x[:, 2] > 0) & (px[:, 0] > 0) & (px[:, 0] < 1920) & (px[:, 1] > 0) & (px[:, 1] < 1080)
    return px[keep].astype(np.float32), DESC[keep]


def half(root, tag, C, targets):
    d = root / tag
    (d / 'keys').mkdir(parents=True)
    for k, T in enumerate(targets):
        xy, desc = view(np.asarray(C, float), np.asarray(T, float))
        np.savez(d / 'keys' / ('k%05d.npz' % k), xy=xy, d=desc)
    with open(d / 'keys.pkl', 'wb') as fh:
        pickle.dump(dict(keys=list(range(len(targets)))), fh)
    with open(d / 'solve.pkl', 'wb') as fh:
        pickle.dump(dict(f=np.full(len(targets), F)), fh)


def installed(homog, tag, C, L=110.0, W=68.0):
    np.savez(homog / f'{tag}_venue.npz', C=np.asarray(C) / M_PER_R, L=L / M_PER_R, W=W / M_PER_R)


SPOT = (55.0, -6.5, 5.2)
TARGETS = [(50, 20, 0), (55, 22, 0), (60, 20, 0)]


@pytest.fixture
def disk(tmp_path):
    root, homog = tmp_path / 'keys', tmp_path / 'homog'
    root.mkdir()
    homog.mkdir()
    half(root, 'aa1', SPOT, TARGETS)
    half(root, 'bb1', SPOT, [(52, 18, 0), (57, 21, 0), (58, 24, 0)])  # turned, not moved
    half(root, 'cc1', (61.0, -8.5, 5.2), TARGETS)  # the tripod 6 m along
    for t, C in (('aa1', SPOT), ('bb1', SPOT), ('cc1', (61.0, -8.5, 5.2))):
        installed(homog, t, C)
    return (root,), homog


def test_a_camera_that_only_turned_is_the_same_spot(disk):
    roots, _ = disk
    c = V.compare('aa1', 'bb1', roots)
    assert c['pairs'] == 9 and c['spot_pairs'] == 9 and c['same_spot']
    assert max(c['turn_px']) < 1.0


def test_paint_seen_from_6_m_away_matches_but_is_not_the_same_spot(disk):
    roots, _ = disk
    c = V.compare('aa1', 'cc1', roots)
    assert c['pairs'] >= 3  # the flat pitch still matches by a homography
    assert c['spot_pairs'] == 0 and not c['same_spot']
    assert min(c['turn_px']) > 2 * V.SPOT_PX


def test_check_compares_other_games_and_measures_the_calibrations(disk):
    roots, homog = disk
    doc = V.check('aa1', roots, homog, log=lambda *_: None)
    got = {c['other']: c for c in doc['compared']}
    assert set(got) == {'bb1', 'cc1'}
    assert got['bb1']['apart_m'] == pytest.approx(0.0, abs=1e-6)
    assert got['cc1']['apart_m'] == pytest.approx(np.hypot(6, 2))
    check = rc.camera([doc])
    assert check.ok is True and 'bb1' in check.text and 'cc1' not in check.text


def test_a_calibration_6_m_off_on_the_same_spot_is_flagged(disk):
    roots, homog = disk
    installed(homog, 'bb1', (55.0, -12.5, 5.2))  # bb1's pins put the camera 6 m further back
    doc = V.check('aa1', roots, homog, log=lambda *_: None)
    check = rc.camera([doc])
    assert check.ok is False
    assert 'same spot as bb1' in check.text and '6.0 m apart' in check.text
    assert '6 m behind the touchline, 5 m up, against 12 m behind' in check.text


def test_3_m_on_the_same_spot_is_within_the_limit(disk):
    roots, homog = disk
    installed(homog, 'bb1', (55.0, -9.5, 5.2))
    assert rc.camera([V.check('aa1', roots, homog, log=lambda *_: None)]).ok is True


def test_nothing_to_compare_is_not_a_flag():
    assert rc.camera(None).ok is None
    assert rc.camera([]).ok is None
    lone = dict(tag='aa1', camera={}, compared=[dict(other='cc1', same_spot=False, apart_m=6.3)])
    c = rc.camera([lone])
    assert c.ok is None and '1 compared' in c.text
    c = rc.camera([dict(tag='aa1', camera={}, compared=[])])
    assert c.ok is None and 'none to compare with' in c.text


def test_one_half_per_camera_and_never_the_game_itself(tmp_path):
    root, homog = tmp_path / 'keys', tmp_path / 'homog'
    root.mkdir()
    homog.mkdir()
    for t in ('aa1', 'aa2', 'bb1', 'bb2', 'dd1', 'dd2', 'x01'):
        half(root, t, SPOT, TARGETS[:1])
    for t in ('aa1', 'aa2', 'bb1', 'bb2', 'dd1', 'x01'):
        installed(homog, t, SPOT)
    installed(homog, 'bb2', (75.0, -6.5, 5.2))  # bb's camera stood elsewhere after the break
    # dd2 has keyframes but no installed calibration; x01 is a clip, not a game's half
    assert V.earlier('aa1', (root,), homog) == ['bb1', 'bb2', 'dd1']
    assert V.tags_to_check(['bb1', 'bb2'], homog) == ['bb1', 'bb2']
    assert V.tags_to_check(['aa1', 'aa2'], homog) == ['aa1']


def test_camera_is_in_metres_from_halfway():
    assert V.game_of('wbr1') == 'wbr' and V.game_of('ww01') is None and V.game_of('wb') is None
    assert V.spread(list(range(40)), 4) == [0, 13, 26, 39]
    assert V.spread([3, 5], 4) == [3, 5]


def test_camera_reads_the_installed_calibration(tmp_path):
    installed(tmp_path, 'aa1', (57.0, -6.5, 5.2), L=110.0, W=68.0)
    cam = V.camera('aa1', tmp_path)
    assert cam == pytest.approx(dict(along=2.0, back=6.5, up=5.2, length_m=110.0, width_m=68.0))
    assert V.camera('zz1', tmp_path) is None
