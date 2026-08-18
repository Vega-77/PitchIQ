"""Two figures, one student: both sides have to add them up the same way.

The tracker loses people when they leave frame, and `cv/identity.py` only
rejoins fragments a couple of seconds apart. Anyone who went off and came back
stays split, so the cluster picker lets a coach map several tracked figures to
one player deliberately — a wrong automatic merge would credit one student with
another's work and could not be undone.

That makes the merge arithmetic real, and it exists twice:

    assets/report.js   cvStatsByPlayer()  — the coach's match view
    cv/publish.py      merge_tracks()     — the student's own report

Neither reads the other. They are the same player's afternoon rendered on two
screens, and until 2026-08-17 the Python side did not merge at all: it wrote one
document per cluster, so the second write replaced the first and the student saw
whichever fragment came last in the mapping — 24 touches of 54, 3.1 km of 7.3,
with the coach's screen showing the totals.

Fixing that made two implementations where there had been one and a half, which
is what this file is for. The failure it guards against is quiet: both sides
keep producing plausible numbers, and only a coach who happened to compare their
own screen against a student's would ever see it.

Skipped automatically when Node isn't installed.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_player_merge_parity.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cv.publish import merge_tracks, player_report_fields

REPO = Path(__file__).resolve().parents[1]
JS_REPORT = REPO / "assets" / "report.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)

# Three fragments of one player, chosen so every rule is exercised by the data
# rather than only by the assertions: the fastest run is not in the longest
# fragment, the worst wobble is not in the fastest one, and one fragment has no
# answer at all for two of the questions.
FRAGMENTS = [
    {
        "cluster_id": 0, "team": "team_a", "touches": 30,
        "passes_attempted": 20, "passes_completed": 16, "pass_accuracy": 0.8,
        "carries": 5, "tackles": 2, "interceptions": 1, "recoveries": 3,
        "shots": 2, "goals": 1, "xg": 0.3, "distance_m": 4200.0,
        "top_speed_kmh": 27.0, "sprint_count": 9, "sprint_distance_m": 310.0,
        "accelerations": 12, "position_noise_m": 0.4, "minutes_tracked": 28.0,
        "touch_times_s": [90.0, 30.0],
    },
    {
        "cluster_id": 1, "team": "team_a", "touches": 24,
        "passes_attempted": 15, "passes_completed": 10, "pass_accuracy": 0.667,
        "carries": 4, "tackles": 1, "interceptions": 0, "recoveries": 2,
        "shots": 1, "goals": 0, "xg": 0.1, "distance_m": 3100.0,
        "top_speed_kmh": 29.5, "sprint_count": 7, "sprint_distance_m": 240.0,
        "accelerations": None, "position_noise_m": 1.9, "minutes_tracked": 21.0,
        "touch_times_s": [60.0],
    },
    {
        "cluster_id": 2, "team": "team_a", "touches": 3,
        "passes_attempted": 2, "passes_completed": 1, "pass_accuracy": 0.5,
        "carries": 0, "tackles": 0, "interceptions": 0, "recoveries": 0,
        "shots": 0, "goals": 0, "xg": 0.0, "distance_m": 190.0,
        "top_speed_kmh": 18.0, "sprint_count": 0, "sprint_distance_m": 0.0,
        "accelerations": 1, "position_noise_m": None, "minutes_tracked": 2.0,
        "touch_times_s": [1200.0],
    },
]

MAPPING = {"0": "playerA", "1": "playerA", "2": "playerA"}

# Every field the two sides both produce. `heatmap` is not among them on
# purpose: `cvStatsByPlayer` never carries one, because a coach's heatmap is
# drawn from `cvStats/identity` directly while a student's arrives on their
# report — so there is nothing to compare, and pretending otherwise would be a
# test of a thing neither side does.
COMPARED = [
    "touches", "passes_attempted", "passes_completed", "carries", "tackles",
    "interceptions", "recoveries", "shots", "goals", "xg", "distance_m",
    "sprint_count", "sprint_distance_m", "minutes_tracked", "accelerations",
    "top_speed_kmh", "position_noise_m",
]


def from_browser(tracks: list[dict], mapping: dict[str, str]) -> dict:
    script = (
        f"import {{ cvStatsByPlayer }} from {json.dumps(JS_REPORT.as_uri())};"
        f"const data = {json.dumps({'tracks': tracks, 'mapping': mapping})};"
        "const out = cvStatsByPlayer(data.tracks, data.mapping);"
        "console.log(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["playerA"]


@pytest.fixture(scope="module")
def both():
    return merge_tracks(FRAGMENTS), from_browser(FRAGMENTS, MAPPING)


@pytest.mark.parametrize("field", COMPARED)
def test_the_two_sides_agree_field_for_field(field, both):
    mine, theirs = both
    assert mine[field] == pytest.approx(theirs[field]), (
        f"{field}: the student's report says {mine[field]} and the coach's "
        f"screen says {theirs[field]} about the same afternoon"
    )


def test_they_agree_about_pass_accuracy(both):
    """Named differently on the two sides — `pass_accuracy` against
    `passAccuracy` — which is exactly the kind of seam a shared test is for."""
    mine, theirs = both
    assert mine["pass_accuracy"] == pytest.approx(theirs["passAccuracy"])
    # 27 of 37, not the mean of 0.8, 0.667 and 0.5.
    assert mine["pass_accuracy"] == pytest.approx(27 / 37)


def test_they_agree_about_the_touch_timeline(both):
    mine, theirs = both
    assert mine["touch_times_s"] == theirs["touchTimes"] == [30.0, 60.0, 90.0, 1200.0]


def test_a_question_no_fragment_answered_is_unanswered_on_both():
    """None on one side and 0 on the other would read as a player who never
    accelerated, rather than one nothing could measure.

    The two say it differently and that is fine: Python writes the key as null,
    the browser never sets the key at all. Both reach a screen through `??`, so
    both print a dash. What would matter — and what this checks — is either of
    them reaching for a zero.
    """
    fragments = [dict(f, accelerations=None) for f in FRAGMENTS]
    assert merge_tracks(fragments)["accelerations"] is None
    assert from_browser(fragments, MAPPING).get("accelerations") is None


def test_the_pipeline_writes_nothing_the_browser_cannot_clear():
    """Every field this side writes must be one `cvReportFields` nulls.

    The browser re-publishes a player's report with every video field
    explicitly nulled when they have no mapped cluster — that is what un-naming
    a tracked figure has to mean. A field the pipeline writes and that list
    does not know about survives the un-naming and sits on the report as a
    measurement of a player nobody claims was measured.

    `cvPassAccuracy` was exactly that until 2026-08-17, and got away with it
    because nothing read it. The next one might not be so harmless.

    The other direction is fine and deliberate: the browser knows things the
    pipeline cannot — how many of a player's own minutes were filmed, how many
    clusters they were assembled from, whether a human has reviewed any of it.
    """
    written = set(player_report_fields({}))
    cleared = set(json.loads(subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import {{ cvReportFields }} from {json.dumps(JS_REPORT.as_uri())};"
         "console.log(JSON.stringify(Object.keys(cvReportFields(null))));"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout))

    assert written <= cleared, (
        "the pipeline writes fields the browser never clears, so un-naming a "
        f"tracked figure leaves them standing: {sorted(written - cleared)}"
    )


def test_neither_side_averages_the_worst_fragment_away(both):
    """The wobble decides whether a burst was worth counting, so a player
    assembled from a clean track and a jittery one is only as trustworthy as
    the jittery one."""
    mine, theirs = both
    assert mine["position_noise_m"] == theirs["position_noise_m"] == 1.9
