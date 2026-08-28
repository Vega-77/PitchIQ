"""One measurement, five copies, and the constant that was supposed to read it.

Every threshold in `cv/framing.py` rests on a single observation: the wide
stadium panorama from the detection spike, where players were 4-8 pixels
**across** and the detector found nothing on the pitch across 300 frames. Two
data points do not make a calibration curve and the module says so; this is the
one that carries the weight.

It was recorded in widths. Every threshold in that module is a height. So one
multiplication stands between the evidence and the numbers it justifies -- and
until now that multiplication was performed in prose, five times, by no code at
all:

* the module docstring's table, `players 4-8 px wide`
* the module docstring's sentence, `12-24 px tall`, and again `9-18 px at
  inference`
* `tests/test_framing.py`'s docstring, `roughly 18 px tall`
* the same file's `person()` helper, building every synthetic player in the
  suite with a bare `/ 3.0`
* `FOOTAGE_DAY.md`, telling somebody at the side of a field `4-8 pixels across`

`PLAYER_ASPECT = 3.0` sat above all of them with a docstring claiming it was
what did the reading, and nothing read it. That is the shape this audit keeps
finding -- a constant that is finished, correct, and connected to nothing -- but
it is worse here than usual, because the thing it was disconnected from is the
only evidence in the repo. Correct the aspect ratio to 2.5, as a standing
player's box arguably wants, and not one of those five numbers moves, the whole
suite stays green, and `PLAYER_FLOOR_PX = 16.0` is left resting on arithmetic
nobody can re-run.

`player_height_px` now does it once, and this file holds the copies to it. Both
directions: a prose figure that stops matching the constant is a stale record of
the measurement, and a constant changed without the prose is a module whose own
argument no longer follows.

Everything here compares two numbers pulled out of text by a regex, and a regex
that stops matching yields nothing to compare. The last class is nothing but
guards against that.
"""
import inspect
import re
from pathlib import Path

from cv import framing
from cv.framing import (
    PLAYER_ASPECT,
    WIDE_CLIP_WIDTHS_PX,
    assess_framing,
    player_height_px,
)

ROOT = Path(__file__).resolve().parents[1]

# `players 4-8 px wide` in the table, and `4-8 px wide is 12-24 px tall` in the
# sentence under it. Both are the same measurement and both have to stay it.
WIDTHS = re.compile(r'(\d+)-(\d+) px wide')

# The conversion, and the worked example that carries it down to what the model
# was actually shown.
HEIGHTS = re.compile(r'(\d+)-(\d+) px tall')
INFERENCE = re.compile(r'(\d+)-(\d+) px at inference')

# The same measurement in the document a person reads at the side of a field.
# En dash there, hyphen in the source: prose typography, one number.
ACROSS = re.compile(r'(\d+)[-–](\d+) pixels across')

# The framing the spike itself ran at, and the frame it ran on. The docstring's
# `9-18 px at inference` is only meaningful against these.
SPIKE_WIDTH, SPIKE_HEIGHT, SPIKE_IMGSZ = 1280, 720, 960


def pairs(pattern, text):
    """Every `lo-hi` pair a pattern finds, as floats."""
    return [(float(lo), float(hi)) for lo, hi in pattern.findall(text)]


def recorded():
    """The measurement as the module records it, low end first."""
    return (min(WIDE_CLIP_WIDTHS_PX), max(WIDE_CLIP_WIDTHS_PX))


def spike_scale():
    """What the spike's run multiplied the frame by, from the code that does it.

    Read off a real `FramingVerdict` rather than written as 960/1280, so the
    worked example in the docstring is tied to the property a caller gets.
    """
    return assess_framing(
        [], SPIKE_WIDTH, SPIKE_HEIGHT, imgsz=SPIKE_IMGSZ
    ).scale


class TestTheMeasurementItself:
    """Every place that states the widths states the recorded ones."""

    def test_the_module_docstring_quotes_the_constant(self):
        """The table and the sentence under it are the same observation.

        Two mentions, and they have to agree with each other as well as with
        `WIDE_CLIP_WIDTHS_PX` -- a band edited in one and not the other is the
        module arguing from two different clips in consecutive paragraphs.
        """
        found = pairs(WIDTHS, inspect.getdoc(framing))

        assert found, 'no width measurement found in the module docstring'
        assert set(found) == {recorded()}

    def test_the_field_guide_quotes_the_same_measurement(self):
        """`FOOTAGE_DAY.md` is the copy that gets acted on.

        It is read on a phone beside a pitch by somebody deciding whether the
        camera is close enough, which makes it the copy where a stale number
        costs a match rather than a code review.
        """
        guide = (ROOT / 'FOOTAGE_DAY.md').read_text(encoding='utf-8')
        found = pairs(ACROSS, guide)

        assert found, 'no width measurement found in FOOTAGE_DAY.md'
        assert set(found) == {recorded()}


class TestTheConversion:
    """The prose arithmetic, done by the function that now owns it."""

    def test_the_stated_heights_are_the_recorded_widths_converted(self):
        """`4-8 px wide is 12-24 px tall` has to survive a corrected aspect.

        This is the assertion the whole file exists for. Change `PLAYER_ASPECT`
        and the sentence that justifies `PLAYER_FLOOR_PX` either moves with it
        or fails here, which are the only two honest outcomes.
        """
        found = pairs(HEIGHTS, inspect.getdoc(framing))
        lo, hi = recorded()

        assert found, 'no height conversion found in the module docstring'
        assert set(found) == {(player_height_px(lo), player_height_px(hi))}

    def test_the_worked_example_reaches_the_scale_the_code_applies(self):
        """`9-18 px at inference` is the last step, and the one that decides.

        Native height is what the camera made possible; inference height is what
        the model was shown, and it is the number compared against
        `PLAYER_FLOOR_PX`. The docstring carries it down by hand, so it is held
        to `FramingVerdict.scale` rather than to a second written-out ratio.
        """
        found = pairs(INFERENCE, inspect.getdoc(framing))
        lo, hi = recorded()
        scale = spike_scale()

        assert found, 'no inference figure found in the module docstring'
        assert set(found) == {
            (player_height_px(lo) * scale, player_height_px(hi) * scale)
        }


class TestTheReadersStillRead:
    """Four regexes and a scale, none of which announce it when they stop."""

    def test_every_reader_found_something(self):
        """The anti-vacuum guard.

        Each check above compares a set built by a regex against a set built
        from the constants. Blind the regex and the first set is empty, the
        comparison is between an empty set and a full one -- which is why every
        check asserts `found` first, and why this restates it in one place that
        fails loudly if the docstring is ever restructured.
        """
        doc = inspect.getdoc(framing)
        guide = (ROOT / 'FOOTAGE_DAY.md').read_text(encoding='utf-8')

        assert len(pairs(WIDTHS, doc)) >= 2
        assert len(pairs(HEIGHTS, doc)) >= 1
        assert len(pairs(INFERENCE, doc)) >= 1
        assert len(pairs(ACROSS, guide)) >= 1

    def test_the_conversion_actually_converts(self):
        """A conversion that returns its argument would satisfy everything else.

        `PLAYER_ASPECT` at 1.0 makes widths and heights the same number, at
        which point the docstring could state one band for both and every check
        above would agree. A player is taller than they are wide by some real
        margin, and that is the claim.
        """
        assert PLAYER_ASPECT > 1.5
        assert player_height_px(10.0) == 10.0 * PLAYER_ASPECT

    def test_the_recorded_band_is_a_band(self):
        """A measurement collapsed to one number stops being a range.

        Both ends are used -- the low end is the median player the spike saw,
        the high end is the one that clears `PLAYER_FLOOR_PX` and gets called
        marginal. A pair whose ends are equal quietly makes those the same test.
        """
        lo, hi = recorded()

        assert len(WIDE_CLIP_WIDTHS_PX) == 2
        assert 0.0 < lo < hi

    def test_the_spike_frame_still_downscales(self):
        """`9-18 px at inference` is only below `12-24` because of a downscale.

        At `imgsz` equal to the long edge the scale is 1.0, the two bands in the
        docstring become the same numbers, and the sentence stops making its
        point while both checks above still pass.
        """
        assert 0.0 < spike_scale() < 1.0
