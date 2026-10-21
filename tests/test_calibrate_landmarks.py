"""The calibrate page's landmarks, against the two calibrators that read its pins.

`calibrate/landmarks.js` names every landmark twice: once as `pitchiq/pitch.py`
does (what `python -m pitchiq.experiments.calibrate` fits a fixed camera from) and
once as the panning camera's pin solve does. A name the reader does not know
is a pin it silently drops, and a position that disagrees with
`Pitch.landmarks()` is a homography the page checks against one pitch and
Python fits to another. Both are held here.

The pin solve measures in centre-circle radii with the box sizes in
`pitchiq/autocal.py` (rounded to a tenth of a radius), so its names are held to the
same points to within that rounding.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pitchiq import autocal as A
from pitchiq.pitch import CENTRE_CIRCLE_RADIUS_M, Pitch

ROOT = Path(__file__).resolve().parents[1]
LANDMARKS_JS = ROOT / 'calibrate' / 'landmarks.js'
ROW = re.compile(r"\['(\w+)', (?:'([\w ]+)'|null), '[^']+', (?:true|false)\]")


def rows():
    return ROW.findall(LANDMARKS_JS.read_text(encoding='utf-8'))


def pin_solve_landmarks(L, W):
    """The pin solve's names and positions in radii (`lc/pins.py:landmarks`)."""
    c = W / 2
    out = {
        'centre spot': (L / 2, c),
        'halfway near': (L / 2, 0),
        'halfway far': (L / 2, W),
        'circle near': (L / 2, c - 1),
        'circle far': (L / 2, c + 1),
    }
    for side, g, s in (('left', 0.0, 1.0), ('right', L, -1.0)):
        out[f'{side} corner near'] = (g, 0)
        out[f'{side} corner far'] = (g, W)
        out[f'{side} box near'] = (g + s * A.BOX_D, c - A.BOX_HW)
        out[f'{side} box far'] = (g + s * A.BOX_D, c + A.BOX_HW)
        out[f'{side} box line near'] = (g, c - A.BOX_HW)
        out[f'{side} box line far'] = (g, c + A.BOX_HW)
        out[f'{side} goal area near'] = (g + s * A.GOAL_AREA_D, c - A.GOAL_AREA_HW)
        out[f'{side} goal area far'] = (g + s * A.GOAL_AREA_D, c + A.GOAL_AREA_HW)
        out[f'{side} spot'] = (g + s * A.SPOT_D, c)
    return out


def test_the_table_is_read_at_all():
    assert len(rows()) == len(Pitch().landmarks())


def test_every_python_landmark_is_on_the_page_once():
    names = [name for name, _ in rows()]
    assert sorted(names) == sorted(Pitch().landmarks())


def test_every_pin_solve_name_is_on_the_page_once():
    pins = [pin for _, pin in rows() if pin]
    assert sorted(pins) == sorted(pin_solve_landmarks(12.0, 7.0))


def test_both_names_of_a_row_are_the_same_point():
    pitch = Pitch()
    R = CENTRE_CIRCLE_RADIUS_M
    theirs = pin_solve_landmarks(pitch.length_m / R, pitch.width_m / R)
    ours = pitch.landmarks()
    for name, pin in rows():
        if not pin:
            continue
        u, v = theirs[pin]
        x, y = ours[name]
        # 0.1 R is the rounding in autocal's box sizes (16.5 m is 1.80 R, 1.8 is 16.47 m)
        assert abs(x / R - u) < 0.1 and abs(y / R - v) < 0.1, (name, pin)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is not installed')
@pytest.mark.parametrize('length, width', [(105.0, 68.0), (96.0, 59.5)])
def test_the_page_puts_each_landmark_where_python_does(length, width):
    script = (
        f'import {{ positions }} from {json.dumps(LANDMARKS_JS.as_uri())};'
        f'console.log(JSON.stringify(positions({length}, {width})));'
    )
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    page = json.loads(result.stdout)
    python = Pitch(length_m=length, width_m=width).landmarks()
    assert sorted(page) == sorted(python)
    for name, (x, y) in python.items():
        assert page[name] == pytest.approx([x, y], abs=1e-9), name


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is not installed')
def test_the_saved_points_load_into_a_calibration(tmp_path):
    """Pins at a known homography's pixels, saved the page's way, fit back to it."""
    import numpy as np

    from pitchiq.calibration import Calibration

    pitch = Pitch(length_m=100.0, width_m=64.0)
    H = np.array([[11.0, 2.0, 300.0], [0.4, -5.5, 800.0], [0.0003, 0.002, 1.0]])
    script = (
        f'import {{ assess }} from {json.dumps((ROOT / "calibrate" / "homography.js").as_uri())};'
        'const pins = JSON.parse(process.argv[1]);'
        'const f = assess(pins);'
        'console.log(JSON.stringify({mean: f.mean, loo: f.looMean}));'
    )
    points, pins = [], []
    for name, (x, y) in pitch.landmarks().items():
        q = H @ [x, y, 1.0]
        px = [q[0] / q[2], q[1] / q[2]]
        points.append({'landmark': name, 'x': px[0], 'y': px[1]})
        pins.append({'name': name, 'px': px, 'm': [x, y]})
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script, json.dumps(pins)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    fit = json.loads(result.stdout)
    assert fit['mean'] < 1e-6 and fit['loo'] < 1e-6

    path = tmp_path / 'calibration-points.json'
    path.write_text(
        json.dumps(
            {
                'points': points,
                'image_size': [1920, 1080],
                'pitch': {'length_m': 100.0, 'width_m': 64.0},
            }
        )
    )
    cal = Calibration.from_picker_export(path)
    assert cal.pitch.length_m == 100.0
    x, y = cal.to_pitch(points[0]['x'], points[0]['y'])
    assert (x, y) == pytest.approx(pitch.landmarks()[points[0]['landmark']], abs=1e-4)
