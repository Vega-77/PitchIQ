"""The coach's pitch pins: the solve, the panorama's trip to the site, and the run's wait.

The video work (gather, loop closure, the colour panorama, the calibration) was
checked against Woodbridge's own files when it moved into the repo: the same
keyframes, rotations, panorama pixels, carried pins and installed registration,
byte for byte. These tests cover what that check cannot: pins made up here.
"""

from __future__ import annotations

import argparse
import base64
import json

import numpy as np
import pytest

from pitchiq import autocal as A
from pitchiq import pins as P
from pitchiq.cli import process_match as pm
from pitchiq.experiments import pincal as PC

F = 2700.0  # px per radian, about Woodbridge's panorama
CX, CY = 3200.0, 900.0
TRUE = dict(C=np.array([6.0, -2.7, 1.3]), L=12.07, W=6.01)  # Woodbridge's camera and pitch, in R


def true_aim():
    """A camera on the near side looking at the centre spot."""
    C, L, W = TRUE['C'], TRUE['L'], TRUE['W']
    z = np.array([L / 2, W / 2, 0.0]) - C
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.stack([x, y, z])  # world -> camera 0


def clicks(names, nudge=None):
    """Where each landmark sits on the panorama, as a coach would pin it."""
    ang = P.project(TRUE['C'], true_aim(), TRUE['L'], TRUE['W'], names)
    xy = np.c_[ang[:, 0] * F + CX, ang[:, 1] * F + CY]
    if nudge:
        name, dx = nudge
        xy[names.index(name), 0] += dx
    return {n: [float(x), float(y)] for n, (x, y) in zip(names, xy)}


SOME = [
    'left corner near', 'left corner far', 'right corner near', 'right corner far',
    'halfway near', 'halfway far', 'centre spot', 'left box near', 'right box far',
    'left spot', 'right spot',
]  # fmt: skip


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(PC, 'WORK', tmp_path)
    for t in ('zz1', 'zz2'):
        (tmp_path / t).mkdir()
        meta = dict(tag=t, F=F, cx=CX, cy=CY, w=6400, h=1800, frames=[])
        (tmp_path / t / 'pano_rgb.json').write_text(json.dumps(meta))
    return tmp_path


def test_the_pin_solve_finds_a_known_camera():
    names = SOME
    xy = np.array([clicks(names)[n] for n in names])
    ang = np.c_[(xy[:, 0] - CX) / F, (xy[:, 1] - CY) / F]
    C, R0, L, W, rms = P.solve(names, ang)
    assert np.allclose(C, TRUE['C'], atol=1e-3)
    assert abs(L - TRUE['L']) < 1e-3 and abs(W - TRUE['W']) < 1e-3
    assert rms * F < 0.01


def test_every_pin_name_is_one_the_site_page_saves():
    # calibrate/landmarks.js maps its landmarks to these names (`pin:`); a name
    # spelled differently there would be dropped here without a word.
    js = open('calibrate/landmarks.js', encoding='utf-8').read()
    for name in P.NAMES:
        assert f"'{name}'" in js, name


def test_a_misplaced_pin_is_named(work):
    pins = clicks(SOME, nudge=('right box far', 300.0))
    (work / 'zz1' / 'pins.json').write_text(json.dumps({'pins': pins}))
    with pytest.raises(PC.BadPins, match='right box far'):
        PC.fit_pins('zz1')
    good = clicks(SOME)
    (work / 'zz1' / 'pins.json').write_text(json.dumps({'pins': good}))
    fit = PC.fit_pins('zz1', log=lambda s: None)
    assert max(fit['worst'].values()) < 1.0


def test_too_few_pins_are_refused(work):
    pins = clicks(SOME[:3])
    (work / 'zz1' / 'pins.json').write_text(json.dumps({'pins': pins}))
    with pytest.raises(PC.BadPins, match='at least 4'):
        PC.fit_pins('zz1', log=lambda s: None)


def test_pins_carry_to_a_half_turned_by_the_transfer(work):
    pins = clicks(SOME)
    (work / 'zz1' / 'pins.json').write_text(json.dumps({'pins': pins}))
    M = A.rodrigues(np.array([[0.0, 0.01, 0.0]]))[0]  # half a degree of pan
    (work / 'zz2' / 'transfer_from_zz1.json').write_text(json.dumps({'ok': True, 'M': M.tolist()}))
    got = PC.carry_pins('zz1', 'zz2')['pins']
    dx = [got[n][0] - pins[n][0] for n in SOME]
    assert np.allclose(dx, dx[0], atol=2.0) and abs(dx[0]) > 10  # all moved alike
    (work / 'zz2' / 'transfer_from_zz1.json').write_text(json.dumps({'ok': False}))
    with pytest.raises(SystemExit, match='own pins'):
        PC.carry_pins('zz1', 'zz2')


def test_the_panorama_goes_up_in_parts_that_fit_a_document(work):
    raw = bytes(range(256)) * 9000  # 2.3 MB
    (work / 'zz1' / 'pano_rgb.jpg').write_bytes(raw)
    head, parts = PC.pano_parts('zz1')
    assert head['parts'] == len(parts) == 4 and head['w'] == 6400
    assert max(len(p) for p in parts) < 1_000_000  # Firestore holds 1 MiB a document
    assert b''.join(base64.b64decode(p) for p in parts) == raw


class FakeDb:
    """Just enough of the Firestore client for read_pins."""

    def __init__(self, docs):
        self.docs = docs

    def collection(self, name):
        return _Ref(self.docs, (name,))


class _Ref:
    def __init__(self, docs, path):
        self.docs, self.path = docs, path

    def document(self, name):
        return _Ref(self.docs, self.path + (name,))

    collection = document

    def get(self):
        d = self.docs.get('/'.join(self.path))
        return argparse.Namespace(exists=d is not None, to_dict=lambda: d)


def test_read_pins_keeps_only_landmarks_it_knows():
    db = FakeDb({
        'teams/T/matches/M/cvPins/h1': {
            'pins': {'centre spot': [10, 20], 'flag post': [1, 2], 'halfway far': [3]},
            'updatedAt': 'then',
        },
    })  # fmt: skip
    assert PC.read_pins(db, 'T', 'M', 1) == {'pins': {'centre spot': [10.0, 20.0]}, 'at': 'then'}
    assert PC.read_pins(db, 'T', 'M', 2) is None


def run_for(tmp_path, **over):
    a = argparse.Namespace(
        game='zz', video=tmp_path / 'v.mp4', lines='gold', plan=False, team='T', match='M',
        no_wait=False,
    )  # fmt: skip
    vars(a).update(over)
    r = pm.Run(a)
    r.out = tmp_path / 'run'
    r.logs = r.out / 'logs'
    return r


def test_the_run_waits_past_pins_it_turned_down(work, tmp_path, monkeypatch):
    saved = [
        {'pins': clicks(SOME), 'at': 'first'},  # turned down earlier: ignored
        None,
        {'pins': clicks(SOME), 'at': 'second'},
    ]
    (work / 'zz1' / 'pins_rejected.json').write_text(json.dumps({'pins': {}, 'at': 'first'}))
    monkeypatch.setattr(PC, 'read_pins', lambda db, t, m, h: saved.pop(0))
    monkeypatch.setattr(pm.time, 'sleep', lambda s: None)
    pm.wait_for_pins(run_for(tmp_path), object(), 'zz1')
    assert json.loads((work / 'zz1' / 'pins.json').read_text())['at'] == 'second'
    assert not saved


def test_without_waiting_the_run_stops_and_says_where_to_pin(work, tmp_path, monkeypatch):
    monkeypatch.setattr(PC, 'read_pins', lambda db, t, m, h: None)
    with pytest.raises(pm.Stop, match='calibrate/\\?team=T&match=M&half=h2'):
        pm.wait_for_pins(run_for(tmp_path, no_wait=True), object(), 'zz2')
    with pytest.raises(pm.Stop, match='pins.json'):
        pm.wait_for_pins(run_for(tmp_path, team=None, match=None), None, 'zz2')


def test_turned_down_pins_are_set_aside_with_what_they_carried(work, tmp_path, monkeypatch):
    marks = []
    monkeypatch.setattr(PC, 'mark_problem', lambda db, t, m, h, why: marks.append((h, why)))
    (work / 'zz1' / 'pins.json').write_text(json.dumps({'pins': {}, 'at': 'x'}))
    (work / 'zz2' / 'pins.json').write_text(json.dumps({'pins': {}, 'from': 'zz1'}))
    pm.reject(run_for(tmp_path), object(), 'zz1', 'the other pins disagree with centre spot')
    assert (work / 'zz1' / 'pins_rejected.json').exists()
    assert not (work / 'zz1' / 'pins.json').exists() and not (work / 'zz2' / 'pins.json').exists()
    assert marks == [(1, 'the other pins disagree with centre spot')]
