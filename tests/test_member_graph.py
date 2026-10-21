"""No method, property or class field under `pitchiq/` is read by nothing but tests.

The sibling of `tests/test_call_graph.py`, which walks top-level names only and
says why: a method with no caller cannot be told from one the graph cannot
resolve. This check takes the cruder route that makes the question answerable.
A member counts as read when its name appears anywhere in non-test code under
`pitchiq/` as an attribute being read (`x.name`, `x.name += 1`) or as a string
(`getattr(x, 'name')`, a dict key). Assignments do not count: a field that is
only ever written is the finding.

Like the call graph it matches bare names, so it over-approximates what is
read and under-reports what is dead. A `slot` read on one class marks every
`slot` read. A failure is strong evidence; a pass is weak.

The record works the same way as `PENDING_BY_DESIGN`: every member here has a
reason, and the check fails in both directions, so an entry that has since
been wired up or deleted is a stale line to remove.

Run:  .venv/Scripts/python.exe -m pytest tests/test_member_graph.py -q
"""

from __future__ import annotations

import ast
from pathlib import Path

CV = Path(__file__).resolve().parent.parent / 'pitchiq'

# 'Class.member' -> why nothing outside tests reads it.
UNREAD_BY_DESIGN = {
    # --- conversions kept beside their inverse
    'Pitch.from_statsbomb': 'The inverse of `to_statsbomb`. Nothing reads '
    'StatsBomb coordinates back in yet; the round trip is pinned in '
    '`tests/test_calibration.py`, which is what keeps the two directions honest.',
    'VideoClock.to_clock': 'The inverse of `to_video`. The pipeline only ever goes '
    'from match clock to footage; `tests/test_phases.py` round-trips through both, '
    'and the frozen reading during the break is defined here.',
    # --- a correction hook waiting on its button
    'TeamAssignment.swap': 'Which cluster is "us" is a human fact. This lets a '
    'reviewer correct the mapping without refitting; the review page that would '
    'offer the button has not been built.',
    # --- the funnel, as Python states it
    'PhaseOfPlay.share_reaching': 'One reading of the phase-of-play funnel. '
    '`shared/report.js` renders the funnel from the JSON counts and words it '
    'itself; this is the Python statement of the same rule, pinned by '
    '`tests/test_possession_sequences.py`.',
    'PhaseOfPlay.accuracy_in': 'Pass accuracy by third from the funnel counts. '
    '`shared/report.js` computes it from `passes`/`passes_completed`; this is '
    'the Python statement of the rule its tests pin.',
    'PhaseOfPlay.out_of_defence': 'Possessions that began in the defensive third '
    'and how many escaped. Published as `escaped_defence`, read by '
    '`shared/report.js`; this pairs it with its own denominator for Python callers.',
    # --- read only by tests, which pin them as the contract
    'FrameState.holder_track': 'Who held the ball on the frame. The tests pin '
    'possession frame by frame through it; the pipeline only needs the team.',
    'FrameState.distance_px': 'How far the holder was from the ball. The tests pin '
    'the claim radius through it; nothing downstream needs the distance itself.',
    'MovementStats.discarded_frames': 'How many implausible jumps were dropped. '
    'The tests pin that the speed cap bites through it; no report shows it yet.',
    'Pass.length_ph': 'Pass length in player heights, the unit the pass threshold '
    'is set in. `tests/test_events.py` pins it; the report publishes metres.',
    'Touch.speed_before_ph_s': 'Ball speed into the touch. Kept with '
    '`speed_after_ph_s` and `turn_deg` so a touch records the motion change that '
    'made it one; the touch tests read it.',
    'Touch.turn_deg': 'How far the ball turned at the touch. The motion-change '
    'score is built from it; the touch tests read it to pin that the turn was seen.',
    # --- top-down map diagnostics
    'TopDown.slots': "Each slot's chained visits. The map draws `dots`; this is "
    'what the map diagnostics (kept outside the repo) inspect when a dot jumps slot.',
    'TopDown.extras': 'Visits left outside every slot. Read by the map diagnostics '
    'kept outside the repo, to see who the slot cap squeezed out.',
    'TopDown.slot_of': 'Per track, its slot or -1. Read by the map diagnostics '
    'kept outside the repo; the renderer only needs `group`.',
    'Dot.slot': "The dot's slot, or -1. Lets the map diagnostics kept outside the "
    'repo tie a drawn dot back to `TopDown.slots`.',
    'LinkResult.team_counts': 'People per team the linker settled on. Read by the '
    'linking diagnostics kept outside the repo; the map takes the cap from settings.',
}


def _local_only(p: Path) -> bool:
    """The same gitignored trees `tests/test_call_graph.py` skips."""
    return any(part == 'tapnet' or part.startswith('h3d_') for part in p.parts)


def _module_files() -> list[Path]:
    return sorted(
        p
        for p in CV.rglob('*.py')
        if '__pycache__' not in p.parts and not _local_only(p.relative_to(CV))
    )


def scan() -> tuple[set[str], dict[str, Path]]:
    """(every name read anywhere, each public 'Class.member' -> its file)."""
    read: set[str] = set()
    members: dict[str, Path] = {}
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Store):
                read.add(node.attr)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Attribute):
                read.add(node.target.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.isidentifier():
                    read.add(node.value)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = item.name
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        name = item.target.id
                    else:
                        continue
                    if not name.startswith('_'):
                        members[f'{node.name}.{name}'] = path
    return read, members


def unread() -> dict[str, Path]:
    read, members = scan()
    return {key: path for key, path in members.items() if key.split('.', 1)[1] not in read}


class TestNoMemberIsReadOnlyByTests:
    def test_every_member_is_read_or_has_a_recorded_reason(self):
        """Wire it up, delete it, or record why it is waiting in UNREAD_BY_DESIGN."""
        found = unread()
        unrecorded = sorted(set(found) - set(UNREAD_BY_DESIGN))
        detail = '\n'.join(f'  {key}  ({found[key].relative_to(CV.parent)})' for key in unrecorded)
        assert not unrecorded, (
            'members under pitchiq/ that nothing outside tests reads:\n'
            + detail
            + '\n\nWire it up, delete it, or record why in UNREAD_BY_DESIGN.'
        )

    def test_the_record_has_not_gone_stale(self):
        stale = sorted(set(UNREAD_BY_DESIGN) - set(unread()))
        assert not stale, (
            'UNREAD_BY_DESIGN entries that are now read, renamed or deleted:\n'
            + '\n'.join(f'  {key}' for key in stale)
        )

    def test_every_reason_says_something(self):
        for key, reason in UNREAD_BY_DESIGN.items():
            assert len(reason) > 40, f'{key}: give the reason, not a label'


class TestTheScanItself:
    def test_a_field_read_through_to_json_counts(self):
        """`to_json` reads `self.saves`; a field serialised that way is live."""
        assert 'KeeperReport.saves' in scan()[1]
        assert 'KeeperReport.saves' not in unread()

    def test_a_write_alone_does_not_count(self):
        """The finding this exists for: written, never read."""
        tree = ast.parse('class A:\n    x: int\n\na = A()\na.x = 1\n')
        stores = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        assert all(isinstance(n.ctx, ast.Store) for n in stores)

    def test_private_members_are_left_to_pyflakes(self):
        assert not any(key.split('.', 1)[1].startswith('_') for key in unread())
