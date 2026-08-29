"""Every Laws-of-the-Game dimension typed into the browser, against cv/pitch.py.

`tests/test_pitch_parity.py` proves that `calibrate/pitch-model.js` and
`cv/pitch.py` agree *given* a pitch: hand both the same length, width and
markings and the landmarks land in the same place. What it never asks is where
the browser's numbers came from in the first place. It passes 105 x 68 in
explicitly, so it is blind to every place the site writes 105 down itself.

The site writes it down in seven files.

    assets/pitch-backdrop.js     L, W, PEN_LEN, PEN_W, SIX_LEN, SIX_W, CIRCLE_R
    assets/passing.js            pitchLengthM, pitchWidthM (default arguments)
    assets/sample-report.js      105 / COLS, 68 / ROWS
    coach/coach.js               the 'Halfway' and 'Their goal' axis ticks
    calibrate/pitch-model.js     the marking constants, and three x/y defaults
    calibrate/index.html         the pre-filled length and width inputs
    xg-sandbox/presets.js        WIDTH_M, HALF_LENGTH_M
    xg-sandbox/sandbox.js        the seven it draws its own half-pitch from

Only one of those is a diagram. `pitch-backdrop.js` calls itself decoration in
its own header comment, and it is not: `assets/shot-map.js`, `assets/heatmap.js`
and `assets/pass-map.js` all import `PITCH_LENGTH_M` and `PITCH_WIDTH_M` from it
and use them as the coordinate frame that real event positions in metres are
plotted into. `heatmap.js:164` sizes an occupancy cell as `PITCH_LENGTH_M /
grid.cols`, where the grid came out of `cv/metrics.py` on a `Pitch`. If the two
lengths disagreed, every cell would be the wrong size and every blob would be
drawn somewhere the player never stood, under a plot that is otherwise correct
and gives no sign that anything is wrong.

`assets/passing.js` is worse still, because its 105 and 68 look like fallbacks
and are not: `coach/coach.js:1580` is the only caller and it passes neither, so
those two defaults are the pitch every published pass map is drawn on.

The irony is that `pitchMarkings` already argues this case against itself:

    Two copies of this geometry would be two chances to draw a penalty box in
    the wrong place, and only one of them would be noticed.

There are seven copies, and until this file nothing counted them.

**Both directions, as always.** A literal that drifts from `cv/pitch.py` is a
finding. A pattern here that no longer matches its file is a stale entry to
delete, not a test to loosen -- so every pattern must match exactly once. And
`test_no_unlisted_laws_literal` closes the loop the other way: an *eighth* copy,
added tomorrow, fails this file rather than sitting unnoticed like the seven did.

**The one honest limit.** That last scan can only sweep numbers that are Laws
dimensions and nothing else -- 16.5, 40.32, 18.32, 9.15, 7.32, 52.5. It cannot
sweep 105, 68, 5.5 or 11, which are also shirt numbers, pass counts, minutes and
grid indices; a scan for a bare 11 would report `track_id: 11` forever and get
switched off within a week. Those four are covered by the table below and only
by the table. So a new copy of a distinctive number is caught automatically; a
new copy of 105 has to be added here by whoever writes it.
"""
import io
import re
from dataclasses import fields
from pathlib import Path

import pytest

from cv.pitch import Pitch

from test_free_names import blank

ROOT = Path(__file__).resolve().parents[1]

JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime', 'calibrate',
           'xg-sandbox']

PITCH = Pitch()

# Numbers that mean a pitch marking and never anything else in this repo. 105,
# 68, 5.5 and 11 are deliberately absent -- see the module docstring.
DISTINCTIVE = ['16.5', '40.32', '18.32', '9.15', '7.32', '52.5']

# A numeric literal, not the tail of an identifier or a decimal of its own.
NUMBER = r'(\d+(?:\.\d+)?)'


def _lit(value):
    return re.escape(value)


# (file, what it is, pattern, the Pitch value every captured group must equal)
#
# Patterns are matched against the file as written, so a row here reads like the
# line it pins and stays easy to check by eye. Each must match exactly once, so
# a pattern that also caught a mention in a comment would fail loudly rather
# than pass quietly. The sweep at the bottom is the one that has to be careful
# about prose, and it runs over `blank`ed source instead.
SITES = [
    # --- assets/pitch-backdrop.js: the frame the maps plot into -----------
    ('assets/pitch-backdrop.js', 'L', r'\bconst L = ' + NUMBER,
     lambda p: p.length_m),
    ('assets/pitch-backdrop.js', 'W', r'\bconst W = ' + NUMBER,
     lambda p: p.width_m),
    ('assets/pitch-backdrop.js', 'PEN_LEN', r'\bconst PEN_LEN = ' + NUMBER,
     lambda p: p.penalty_area_length_m),
    ('assets/pitch-backdrop.js', 'PEN_W', r'\bconst PEN_W = ' + NUMBER,
     lambda p: p.penalty_area_width_m),
    ('assets/pitch-backdrop.js', 'SIX_LEN', r'\bconst SIX_LEN = ' + NUMBER,
     lambda p: p.goal_area_length_m),
    ('assets/pitch-backdrop.js', 'SIX_W', r'\bconst SIX_W = ' + NUMBER,
     lambda p: p.goal_area_width_m),
    ('assets/pitch-backdrop.js', 'CIRCLE_R', r'\bconst CIRCLE_R = ' + NUMBER,
     lambda p: p.centre_circle_radius_m),
    # The penalty spots, written inline at both ends of the same ternary.
    ('assets/pitch-backdrop.js', 'the penalty spots',
     r'cx: side \? L - ' + NUMBER + r' : ' + NUMBER,
     lambda p: p.penalty_spot_m),

    # --- assets/passing.js: not fallbacks, the actual pitch ---------------
    ('assets/passing.js', 'pitchLengthM', r'pitchLengthM = ' + NUMBER,
     lambda p: p.length_m),
    ('assets/passing.js', 'pitchWidthM', r'pitchWidthM = ' + NUMBER,
     lambda p: p.width_m),

    # --- assets/sample-report.js: the preview heatmap's cell size ---------
    ('assets/sample-report.js', 'CELL_LENGTH_M',
     r'\bconst CELL_LENGTH_M = ' + NUMBER + r' / COLS', lambda p: p.length_m),
    ('assets/sample-report.js', 'CELL_WIDTH_M',
     r'\bconst CELL_WIDTH_M = ' + NUMBER + r' / ROWS', lambda p: p.width_m),

    # --- coach/coach.js: the shape scale a coach reads the bars against ---
    ('coach/coach.js', "the 'Halfway' tick", r"'Halfway', " + NUMBER,
     lambda p: p.length_m / 2),
    ('coach/coach.js', "the 'Their goal' tick", r"'Their goal', " + NUMBER,
     lambda p: p.length_m),

    # --- calibrate/pitch-model.js ----------------------------------------
    # The seven marking constants are also proved live by test_pitch_parity's
    # test_default_markings_match, which runs them through node. Listed here
    # anyway: this file's claim is about the literal on the page, and an
    # exemption list is one more thing that would quietly rot.
    ('calibrate/pitch-model.js', 'GOAL_WIDTH_M',
     r'GOAL_WIDTH_M = ' + NUMBER, lambda p: p.goal_width_m),
    ('calibrate/pitch-model.js', 'GOAL_AREA_LENGTH_M',
     r'GOAL_AREA_LENGTH_M = ' + NUMBER, lambda p: p.goal_area_length_m),
    ('calibrate/pitch-model.js', 'GOAL_AREA_WIDTH_M',
     r'GOAL_AREA_WIDTH_M = ' + NUMBER, lambda p: p.goal_area_width_m),
    ('calibrate/pitch-model.js', 'PENALTY_AREA_LENGTH_M',
     r'PENALTY_AREA_LENGTH_M = ' + NUMBER, lambda p: p.penalty_area_length_m),
    ('calibrate/pitch-model.js', 'PENALTY_AREA_WIDTH_M',
     r'PENALTY_AREA_WIDTH_M = ' + NUMBER, lambda p: p.penalty_area_width_m),
    ('calibrate/pitch-model.js', 'PENALTY_SPOT_M',
     r'PENALTY_SPOT_M = ' + NUMBER, lambda p: p.penalty_spot_m),
    ('calibrate/pitch-model.js', 'CENTRE_CIRCLE_RADIUS_M',
     r'CENTRE_CIRCLE_RADIUS_M = ' + NUMBER, lambda p: p.centre_circle_radius_m),
    # The x/y defaults, which test_pitch_parity never sees because it always
    # passes a length and a width in.
    ('calibrate/pitch-model.js', "landmarks()'s defaults",
     r'function landmarks\(lengthM = ' + NUMBER + r', widthM = ' + NUMBER,
     lambda p: (p.length_m, p.width_m)),
    ('calibrate/pitch-model.js', "measureMarkings()'s defaults",
     r'function measureMarkings\(points, lengthM = ' + NUMBER
     + r', widthM = ' + NUMBER, lambda p: (p.length_m, p.width_m)),
    ('calibrate/pitch-model.js', "the fit's starting guess",
     r'best = \{ lengthM: ' + NUMBER + r', widthM: ' + NUMBER,
     lambda p: (p.length_m, p.width_m)),

    # --- calibrate/calibrate.js: what an empty box falls back to ----------
    ('calibrate/calibrate.js', "length_m's fallback",
     r"length_m: parseFloat\(byId\('input-length'\)\.value\) \|\| " + NUMBER,
     lambda p: p.length_m),
    ('calibrate/calibrate.js', "width_m's fallback",
     r"width_m: parseFloat\(byId\('input-width'\)\.value\) \|\| " + NUMBER,
     lambda p: p.width_m),

    # --- calibrate/index.html: what the coach sees pre-filled -------------
    ('calibrate/index.html', 'the length input',
     r'id="input-length"[^>]*value="' + NUMBER + r'"', lambda p: p.length_m),
    ('calibrate/index.html', 'the width input',
     r'id="input-width"[^>]*value="' + NUMBER + r'"', lambda p: p.width_m),

    # --- xg-sandbox/presets.js: the half-pitch the presets are written in -
    ('xg-sandbox/presets.js', 'WIDTH_M', r'WIDTH_M = ' + NUMBER,
     lambda p: p.width_m),
    ('xg-sandbox/presets.js', 'HALF_LENGTH_M', r'HALF_LENGTH_M = ' + NUMBER,
     lambda p: p.length_m / 2),

    # --- xg-sandbox/sandbox.js: feeds the browser's xG features -----------
    ('xg-sandbox/sandbox.js', 'GOAL_WIDTH_M',
     r'\bconst GOAL_WIDTH_M = ' + NUMBER, lambda p: p.goal_width_m),
    ('xg-sandbox/sandbox.js', 'PENALTY_AREA_WIDTH_M',
     r'\bconst PENALTY_AREA_WIDTH_M = ' + NUMBER,
     lambda p: p.penalty_area_width_m),
    ('xg-sandbox/sandbox.js', 'PENALTY_AREA_DEPTH_M',
     r'\bconst PENALTY_AREA_DEPTH_M = ' + NUMBER,
     lambda p: p.penalty_area_length_m),
    ('xg-sandbox/sandbox.js', 'GOAL_AREA_WIDTH_M',
     r'\bconst GOAL_AREA_WIDTH_M = ' + NUMBER, lambda p: p.goal_area_width_m),
    ('xg-sandbox/sandbox.js', 'GOAL_AREA_DEPTH_M',
     r'\bconst GOAL_AREA_DEPTH_M = ' + NUMBER, lambda p: p.goal_area_length_m),
    ('xg-sandbox/sandbox.js', 'PENALTY_SPOT_M',
     r'\bconst PENALTY_SPOT_M = ' + NUMBER, lambda p: p.penalty_spot_m),
    ('xg-sandbox/sandbox.js', 'CENTRE_CIRCLE_RADIUS_M',
     r'\bconst CENTRE_CIRCLE_RADIUS_M = ' + NUMBER,
     lambda p: p.centre_circle_radius_m),
]


def read(rel):
    """A site file exactly as written."""
    return io.open(ROOT / rel, encoding='utf-8', errors='replace').read()


def code_of(path):
    """The same file with comments, strings and regex literals blanked out.

    `blank` replaces them space for space rather than deleting them, so an
    offset in here is the same offset in the raw text -- which is what lets the
    sweep compare its hits against spans the table matched raw.
    """
    return blank(io.open(path, encoding='utf-8', errors='replace').read())


def matches(text, pattern):
    return list(re.finditer(pattern, text))


def expected(site):
    """The Pitch value(s) a site's captured groups must equal, as a tuple."""
    want = site[3](PITCH)
    return want if isinstance(want, tuple) else (want,)


@pytest.fixture(scope='module')
def sources():
    return {rel: read(rel) for rel, *_ in SITES}


# --------------------------------------------------------------- forwards


def test_every_literal_matches_the_python_default(sources):
    """The point of the file: nothing in the browser has drifted."""

    wrong = []
    for site in SITES:
        rel, label, pattern, _ = site
        found = matches(sources[rel], pattern)
        if len(found) != 1:
            continue                       # the backwards test reports this
        want = expected(site)
        got = tuple(float(g) for g in found[0].groups())
        for i, (a, b) in enumerate(zip(got, want)):
            if a != pytest.approx(b, abs=1e-9):
                wrong.append('%s %s%s: js=%g python=%g' % (
                    rel, label, '' if len(got) == 1 else ' #%d' % (i + 1), a, b))
    assert not wrong, (
        'pitch dimensions in the browser disagree with cv/pitch.py:\n  '
        + '\n  '.join(wrong))


# -------------------------------------------------------------- backwards


def test_every_site_is_still_there(sources):
    """A pattern that no longer matches is a stale entry, not a loose test.

    Deleting or renaming one of these constants should mean deleting its row
    above. If a miss were tolerated the table would fill with rows that check
    nothing, and the whole file would go quietly green.
    """

    broken = []
    for rel, label, pattern, _ in SITES:
        found = matches(sources[rel], pattern)
        if len(found) != 1:
            broken.append('%s %s: matched %d times, expected 1'
                          % (rel, label, len(found)))
    assert not broken, (
        'stale rows in SITES -- delete or fix them:\n  ' + '\n  '.join(broken))


def test_no_unlisted_laws_literal():
    """An eighth copy of the geometry, wherever someone puts it.

    Only the six numbers that cannot be anything else. See the docstring for
    why 105, 68, 5.5 and 11 are out of reach of this particular sweep.
    """

    listed = {}
    for rel, _, pattern, _ in SITES:
        if rel.endswith('.html'):
            continue
        for m in matches(read(rel), pattern):
            listed.setdefault(rel, []).append(m.span())

    stray = []
    for path in modules():
        rel = str(path.relative_to(ROOT)).replace('\\', '/')
        code = code_of(path)
        spans = listed.get(rel, [])
        for value in DISTINCTIVE:
            for m in re.finditer(r'(?<![\w$.])%s(?![\w$.])' % _lit(value), code):
                if any(a <= m.start() and m.end() <= b for a, b in spans):
                    continue
                stray.append('%s:%d  %s' % (
                    rel, code[:m.start()].count('\n') + 1, value))
    assert not stray, (
        'pitch dimensions written down somewhere this file does not check.\n'
        'Add a row to SITES so the number is held to cv/pitch.py:\n  '
        + '\n  '.join(stray))


def test_every_pitch_field_is_mirrored_somewhere():
    """Nine fields on `Pitch`, nine covered here.

    Not a formality: `Pitch` is where a new marking would be added, and the
    browser draws all of them. A tenth field arriving with no row here is a
    decision to make -- give it a copy, or say in a comment why the browser
    never needs one -- not something to discover from a wrong-looking diagram.
    """

    seen = set()
    for site in SITES:
        want = expected(site)
        for field in fields(Pitch):
            value = getattr(PITCH, field.name)
            if any(v == pytest.approx(value, abs=1e-9) for v in want):
                seen.add(field.name)
    missing = {f.name for f in fields(Pitch)} - seen
    assert not missing, (
        'no browser copy of %s is checked here' % ', '.join(sorted(missing)))


# ------------------------------------------------------------- anti-vacuum


def modules():
    out = []
    for d in JS_DIRS:
        out.extend(sorted((ROOT / d).rglob('*.js')))
    return out


def test_the_scan_actually_scanned_something(sources):
    """A gate that reads no files passes every one of the tests above."""

    assert len(SITES) >= 30
    assert len({rel for rel, *_ in SITES}) >= 7
    assert len(modules()) >= 25
    assert all(len(text) > 200 for text in sources.values())
    total = sum(len(matches(sources[rel], pattern))
                for rel, _, pattern, _ in SITES)
    assert total == len(SITES), 'every row should match exactly once'


# --------------------------------------------------- the checks, checked


def test_a_drifted_literal_is_found():
    """The failure this file exists to catch, on a source it cannot damage."""

    text = 'const PEN_LEN = 18.0;'
    found = matches(text, r'\bconst PEN_LEN = ' + NUMBER)
    assert len(found) == 1
    assert float(found[0].group(1)) != PITCH.penalty_area_length_m


def test_a_renamed_constant_is_a_miss_not_a_pass():
    """Rename it and the pattern matches nothing -- which must not read as ok."""

    text = 'const PENALTY_DEPTH = 16.5;'
    assert matches(text, r'\bconst PEN_LEN = ' + NUMBER) == []


def test_a_number_in_prose_is_not_a_copy():
    """The reason the sweep runs over `blank`ed source and not raw text."""

    src = '// fitted against a 16.5m box in Python\nconst k = 1;\n'
    code = blank(src)
    assert '16.5' not in code
    assert 'const k = 1;' in code


def test_a_number_in_a_string_is_not_a_copy():
    src = "const msg = 'the box is 16.5 m deep';\n"
    assert '16.5' not in blank(src)


@pytest.mark.parametrize('value', DISTINCTIVE)
def test_every_distinctive_number_is_one_the_laws_specify(value):
    """The sweep list, held to the same source as everything else.

    52.5 is half a pitch rather than a marking, which is why it is compared
    against length/2 and not against a field.
    """

    known = [getattr(PITCH, f.name) for f in fields(Pitch)]
    known.append(PITCH.length_m / 2)
    assert any(float(value) == pytest.approx(k, abs=1e-9) for k in known), (
        '%s is in DISTINCTIVE but is not a dimension cv/pitch.py has' % value)


def test_the_number_pattern_does_not_read_a_version_query():
    """`?v=115` is stamped onto every import specifier in this repo."""

    text = "import { x } from './y.js?v=115';"
    assert matches(blank(text), r'WIDTH_M = ' + NUMBER) == []
    assert re.search(r'(?<![\w$.])105(?![\w$.])', blank(text)) is None
