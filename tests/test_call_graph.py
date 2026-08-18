"""Nothing under `cv/` is finished, tested, and connected to nothing.

The audit that produced this file found `cv/publish.py` — the last mile of the
whole project, the code that puts numbers on a coach's screen — fully written,
fully guarded and fully tested, with no way to run it. No `main`, no
`__main__`, no caller outside `tests/`. The suite was green the entire time,
and green was the problem: every piece worked, and nothing called them. A test
per function cannot see that shape, because the shape is the absence of an edge
between two functions that each pass their own tests.

So this walks the call graph instead of the coverage, and fails when a public
name defined at the top level of a `cv/` module is unreachable from anything
that actually runs. Reachable means: reachable from module-level code, from a
`main()` in `cv/experiments/`, or from `analyse_match`. Tests are deliberately
not roots — "called only by its own test" is precisely the thing being looked
for, so counting a test as a caller would hide every finding.

    What this check is, and what it is not.

It is conservative on purpose. Names propagate by bare match: anything calling
`run` marks every `run` in `cv/` reachable, because resolving attributes back to
types would need a type checker and would start being wrong in ways nobody
could audit. So it over-approximates what is reachable, and therefore
under-reports what is dead. A failure here is strong evidence — that name has
no plausible caller at all. A pass is weak evidence; there is more dead code
than this can see, and finding it needs a person.

Three things it cannot see, all in the same direction:

* dispatch through `getattr`, a string, or a dict of callables
* anything reached from the browser, the command line, or a JSON key
* the browser modules themselves, which are not walked at all — an export
  there is reached from `<script type="module">` and from event handlers that
  no Python AST is going to find

    Why there is an allowlist, and why it carries reasons.

Not everything unreachable is a mistake. This project builds the primitive
before the caller more often than not, because the primitive is the part that
can be tested without footage. `nearest_player` and `zone_grid` are finished,
correct and waiting for a feature that has not been written; deleting them to
make a check pass would be the check making the codebase worse.

Failing on everything unreachable would therefore be dishonest, and an
allowlist of bare names would rot into a graveyard nobody dares touch. So each
entry states why it has no caller yet, and the check fails in *both*
directions: an unlisted orphan is a finding, and a listed name that has since
been wired up or deleted is a stale entry to remove. The list is the record,
and the test is what stops the record drifting from the code.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_call_graph.py -q
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

CV = Path(__file__).resolve().parent.parent / 'cv'

# Every public top-level name under `cv/` with no caller, and the reason it has
# none yet. Written the day the audit ran, so that the next audit does not have
# to work out any of this a second time.
#
# The bar for adding an entry is that the name is finished and correct and the
# only thing missing is a feature to call it from. Anything that is unreachable
# because it was superseded gets deleted instead — three were, the day this
# file was written, and one of those had a bug in it that no test could ever
# have hit because no test could reach it.
PENDING_BY_DESIGN = {
    # --- geometry primitives, built with the pitch model, waiting on features
    'in_goal_area': 'Six-yard box test. Built with the rest of the pitch '
                    'geometry; the goalkeeper stats that would ask it are '
                    'currently derived from distance to goal instead.',
    'channel': 'Which lateral channel a point is in, five by default. The '
               'territory block splits the pitch by third, along the other '
               'axis; this is the primitive a wing-play breakdown would need.',
    'zone_grid': 'The full grid of pitch zones. The heatmap bins positions '
                 'itself because it needs its own resolution; this exists for '
                 'a zone-by-zone table that has not been designed.',

    # --- tracking and shape primitives
    'nearest_player': 'Closest tracked figure to the ball, in pixels. '
                      '`cv/touches.py` runs its own search because it also '
                      'needs the runner-up distance to know whether the '
                      'assignment was a coin flip; this returns the winner '
                      'alone, which is what a one-off question wants.',
    'pinned_back': 'Whether a team is camped in its own third. A phrase for a '
                   'summary nobody writes yet — the territory numbers are '
                   'shown raw, and turning them into words is a decision about '
                   'tone, not code.',

    # --- identity
    'cluster_of_track': 'Reverse lookup from track to cluster. Everything '
                        'published walks clusters forward to their tracks; the '
                        'reverse direction is what a per-track debug view '
                        'would want.',

    # --- diagnostics that print rather than publish
    'drift_notes': 'English sentences for whichever shape figures actually '
                   'moved. The pipeline publishes `shape_drift` as numbers and '
                   '`assets/report.js` writes its own wording from them, so '
                   'these are the sentences a Python-side diagnostic would '
                   'print if one existed.',

    # --- xG, deliberately not on the pipeline path
    'predict_xg': 'The single-shot entry point. The pipeline calls '
                  '`xg_for_shots`, which batches; this is the one-shot version '
                  'the browser sandbox mirrors and the tests exercise '
                  'directly.',
    'validate_against_noise': 'Measures how xG degrades on noisy positions. '
                              'Called by `tests/test_xg_noise.py`, which is '
                              'the only place it makes sense to call it — the '
                              'measurement is the deliverable, and it is '
                              'pinned in the roadmap.',
}


def _module_files() -> list[Path]:
    return sorted(
        p for p in CV.rglob('*.py') if '__pycache__' not in p.parts
    )


def _names_used(node: ast.AST) -> set[str]:
    """Every identifier this node mentions, however it mentions it.

    Attributes by their final name only (`x.publish` contributes `publish`),
    which is what makes the whole graph conservative: it cannot tell one
    module's `run` from another's, so it treats a call to either as a call to
    both.
    """
    used: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            used.add(child.id)
        elif isinstance(child, ast.Attribute):
            used.add(child.attr)
        elif isinstance(child, ast.alias):
            used.add(child.name.rsplit('.', 1)[-1])
            if child.asname:
                used.add(child.asname)
    return used


def _shallow_uses(node: ast.AST) -> set[str]:
    """Identifiers mentioned by a node but not inside a nested def or class.

    Without this, every name a method mentions would be credited to the
    enclosing class as well, and a class reached for one reason would drag in
    everything all of its methods touch.
    """
    used: set[str] = set()
    bodies = [node]
    while bodies:
        current = bodies.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                continue
            used |= _names_used(child)
            bodies.append(child)
    return used


DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def build_graph():
    """(name of each def, what it uses, who its children are, the roots)."""
    uses: dict[int, set[str]] = {}
    name_of: dict[int, str] = {}
    children: dict[int, list[int]] = {}
    by_name: dict[str, list[int]] = {}
    top_level: dict[str, tuple[int, Path]] = {}
    roots: set[str] = set()

    counter = itertools.count()

    def visit(node, parent_id, path, depth):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, DEF_TYPES):
                continue
            # Numbered rather than keyed on `id(child)`: the trees are dropped
            # as the loop moves on, and CPython reuses the addresses of freed
            # objects, so `id` silently merges unrelated defs.
            cid = next(counter)
            name_of[cid] = child.name
            uses[cid] = _shallow_uses(child)
            by_name.setdefault(child.name, []).append(cid)
            if parent_id is not None:
                children.setdefault(parent_id, []).append(cid)
            if depth == 0 and not child.name.startswith('_'):
                top_level.setdefault(child.name, (cid, path))
            visit(child, cid, path, depth + 1)

    for path in _module_files():
        tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        visit(tree, None, path, 0)

        # Module-level code runs on import, so whatever it mentions is live.
        for stmt in tree.body:
            if not isinstance(stmt, DEF_TYPES):
                roots |= _names_used(stmt)
            else:
                # Decorators sit outside the def they wrap and run at import.
                for dec in stmt.decorator_list:
                    roots |= _names_used(dec)

        # A tool is entered through its `main`, which nothing in the repo calls.
        if path.parent.name == 'experiments':
            roots.add('main')

    # The pipeline's entry point, called from every experiment and from tests.
    roots.add('analyse_match')
    return uses, name_of, children, by_name, top_level, roots


def reachable_names() -> set[str]:
    uses, name_of, children, by_name, _top, roots = build_graph()

    stack = [nid for name in roots for nid in by_name.get(name, ())]
    seen = set(stack)
    while stack:
        nid = stack.pop()
        for name in uses[nid]:
            for target in by_name.get(name, ()):
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        # Reaching a class reaches its methods: `Pass(...)` runs `__post_init__`
        # and everything a caller then calls on the instance.
        for child in children.get(nid, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)

    return {name_of[nid] for nid in seen}


def orphans() -> dict[str, Path]:
    _uses, _name_of, _children, _by_name, top_level, _roots = build_graph()
    live = reachable_names()
    return {
        name: path for name, (_cid, path) in top_level.items()
        if name not in live
    }


class TestNothingIsOrphaned:
    def test_every_public_name_has_a_caller_or_a_recorded_reason(self):
        """The check itself.

        A name here is finished code nothing runs. Two honest ways out: wire it
        up, or add it to `PENDING_BY_DESIGN` with the reason it has no caller
        yet. A third — delete it — is right whenever it was superseded rather
        than merely early.
        """
        found = orphans()
        unrecorded = sorted(set(found) - set(PENDING_BY_DESIGN))
        detail = '\n'.join(
            f'  {name}  ({found[name].relative_to(CV.parent)})'
            for name in unrecorded
        )
        assert not unrecorded, (
            'public names under cv/ that nothing calls:\n' + detail +
            '\n\nWire it up, delete it, or record why it is waiting in '
            'PENDING_BY_DESIGN.'
        )

    def test_the_record_has_not_gone_stale(self):
        """The other direction, which is what stops the list becoming a
        graveyard: a name that has since been wired up or deleted is an entry
        to remove, not a reason to leave a lie in the file."""
        stale = sorted(set(PENDING_BY_DESIGN) - set(orphans()))
        assert not stale, (
            'PENDING_BY_DESIGN entries that are no longer orphaned (wired up, '
            'renamed or deleted):\n' + '\n'.join(f'  {n}' for n in stale)
        )

    def test_every_reason_says_something(self):
        """A bare name in the list would defeat the point of the list."""
        for name, reason in PENDING_BY_DESIGN.items():
            assert len(reason) > 40, f'{name}: give the reason, not a label'


class TestTheGraphItself:
    """The check is only worth its failures if the walk is right, and a
    call-graph walk fails quietly — it says "all clear" either way.
    """

    def test_the_last_mile_would_have_been_caught(self):
        """`publish` is the function whose orphaning started all of this. It
        has a caller now, in `cv/experiments/publish_report.py`, and this
        asserts the walk actually finds it — otherwise the check would have
        missed the one case it was built for."""
        assert 'publish' in reachable_names()

    def test_a_method_of_a_reached_class_is_reached(self):
        """Without this the check reports most of the codebase. Nothing calls
        `Pitch.to_statsbomb` by that name through a module attribute — it is
        reached by having a `Pitch`."""
        assert 'to_statsbomb' in reachable_names()

    def test_the_pipeline_entry_point_is_a_root(self):
        assert 'analyse_match' in reachable_names()

    def test_orphans_are_public_and_top_level_only(self):
        """A private helper with no caller is pyflakes' business, and a method
        with no caller cannot be told from one the graph cannot resolve."""
        assert not any(name.startswith('_') for name in orphans())
