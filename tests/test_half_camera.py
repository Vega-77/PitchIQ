"""Each half's camera file: stitched from the pass's pieces at its seams, no ball involved."""

import json
from pathlib import Path

import numpy as np
import pytest

from pitchiq.balltrack import camera

S2 = Path(camera.S2)


@pytest.fixture
def where(tmp_path, monkeypatch):
    hom = tmp_path / 'pitch_registration'
    hom.mkdir()
    monkeypatch.setattr(camera, 'S2', str(tmp_path))
    monkeypatch.setattr(camera, 'HOM', str(hom))
    return tmp_path


def piece(where, ptag, lo, hi):
    """A piece's smoothed camera: H holds its own frame number, so a cut shows."""
    f = np.arange(lo, hi + 1)
    H = np.tile(np.eye(3), (len(f), 1, 1))
    H[:, 0, 2] = f
    H[:, 1, 2] = int(ptag[-1])  # which piece the frame came from
    np.savez(where / 'pitch_registration' / f'{ptag}_smooth.npz', abs=f, H=H, x=f * 0.0)


def passed_half(where, tag, windows, cuts):
    d = where / 'half' / tag
    d.mkdir(parents=True)
    ptags = [f'{tag}_q00{k}' for k in range(len(windows))]
    for p, (lo, hi) in zip(ptags, windows):
        piece(where, p, lo, hi)
        (d / f'{p}.shipped.done').touch()
    seams = [
        dict(left=a, right=b, edge=c, cut=c, agreed=True) for a, b, c in zip(ptags, ptags[1:], cuts)
    ]
    (d / 'seams.json').write_text(json.dumps(seams))


def test_the_pieces_are_cut_at_the_seams(where):
    passed_half(where, 'zz1', [(100, 260), (200, 400)], [230])
    assert camera.backfill('zz1') == 'stitched from the pieces'
    f, H = camera.load('zz1')
    assert np.array_equal(f, np.arange(100, 401))
    assert np.array_equal(H[:, 0, 2], f)
    assert (H[f < 230, 1, 2] == 0).all() and (H[f >= 230, 1, 2] == 1).all()


def test_a_gap_between_pieces_is_refused(where):
    piece(where, 'zz1_q000', 100, 200)
    piece(where, 'zz1_q001', 250, 400)
    with pytest.raises(ValueError, match='gaps'):
        camera.stitch('zz1', ['zz1_q000', 'zz1_q001'], [100, 230, 401])


def test_without_pieces_the_old_camera_is_copied(where):
    (where / 'h3d_hyb90').mkdir()
    old = dict(abs=np.arange(5, 9), H=np.tile(np.eye(3), (4, 1, 1)))
    np.savez(where / 'h3d_hyb90' / 'zz1_smooth2.npz', **old)
    assert camera.backfill('zz1').startswith('copied from h3d_hyb90')
    f, H = camera.load('zz1')
    assert np.array_equal(f, old['abs']) and np.array_equal(H, old['H'])


def test_a_half_with_nothing_says_how_to_make_it(where):
    with pytest.raises(FileNotFoundError, match='python -m pitchiq.balltrack.camera zz9'):
        camera.load('zz9')


@pytest.mark.skipif(
    not (S2 / 'h3d_hyb90' / 'wb1_smooth2.npz').exists(), reason="needs Woodbridge's pass"
)
@pytest.mark.parametrize('tag', ['wb1', 'wb2'])
def test_woodbridge_rebuilt_from_its_pieces_equals_the_camera_the_map_used(
    tag, tmp_path, monkeypatch
):
    monkeypatch.setattr(camera, 'path', lambda t: str(tmp_path / f'{t}_camera.npz'))
    camera.stitch(tag, *camera.pieces_of(tag))
    f, H = camera.load(tag)
    with np.load(S2 / 'h3d_hyb90' / f'{tag}_smooth2.npz') as old:
        assert np.array_equal(f, old['abs']) and np.array_equal(H, old['H'])


def test_the_pass_cuts_its_seams_where_the_shipped_ball_agrees(where, monkeypatch):
    """Two pieces whose balls part for five frames at the chunk edge: the cut moves to the
    nearest frame with five agreeing frames each side, and the picks, the 3D ball and the
    camera are all cut there."""
    from pitchiq.balltrack import half

    (where / 'evaluation' / 'p2d' / 'cls_B').mkdir(parents=True)
    monkeypatch.setattr(half, 'S2', str(where))
    monkeypatch.setattr(half, 'AT', str(where / 'evaluation'))
    monkeypatch.setattr(half, 'HOM', str(where / 'pitch_registration'))
    h = half.Half('zz1', 0, 2 * 9000 - 1, 6, 300)
    h.say = lambda msg: None
    assert [p['win'] for p in h.pieces] == [(0, 9299), (8700, 17999)]
    for k, p in enumerate(h.pieces):
        f = np.arange(p['win'][0], p['win'][1] + 1)
        x = f.astype(float)
        if k == 1:
            x[(f >= 8998) & (f <= 9002)] += 5  # the right piece sees the ball elsewhere here
        two = dict(abs=f, x=x, y=x * 0 + k, src=np.ones(len(f), np.int8))
        np.savez(where / 'evaluation' / 'p2d' / 'cls_B' / f'{p["ptag"]}.npz', **two)
        three = dict(abs=f, x=x, y=x * 0, z=x * 0, state=np.zeros(len(f), np.int8))
        np.savez(where / 'pitch_registration' / f'{p["ptag"]}_ball3dd.npz', **three)
        piece(where, p['ptag'], *p['win'])
    h.stitch()
    seams = json.loads((where / 'half' / 'zz1' / 'seams.json').read_text())
    assert [(s['edge'], s['cut'], s['agreed']) for s in seams] == [(9000, 8992, True)]
    with np.load(where / 'evaluation' / 'p2d' / 'cls_B' / 'zz1.npz') as z:
        assert np.array_equal(z['abs'], np.arange(18000))
        assert (z['y'][:8992] == 0).all() and (z['y'][8992:] == 1).all()
    with np.load(where / 'pitch_registration' / 'zz1_ball3dd.npz') as z:
        assert np.array_equal(z['abs'], np.arange(18000)) and z['x'][9000] == 9005
    f, H = camera.load('zz1')
    assert np.array_equal(f, np.arange(18000))
    assert (H[:8992, 1, 2] == 0).all() and (H[8992:, 1, 2] == 1).all()
