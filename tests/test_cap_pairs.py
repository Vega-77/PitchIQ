"""A number written twice, in two languages, in two files that never meet.

Three limits in this repo exist in two places at once. `firestore.rules` refuses
a write past them, and somewhere on the client a guard stops the write being
attempted. Neither half can see the other: there is no build step, nothing
carries a constant across Python, JavaScript and the rules language, and the
rules file is deployed by a separate command that is easy to forget.

The failure this catches is not exotic. Somebody raises a cap because a real
match needed more room, changes the end they were looking at, and the other end
keeps the old number. Which way it breaks depends on which end moved. Raise the
client and the write starts failing with a permission error that names no
field. Raise the rule and the client keeps refusing work the backend would now
accept. Both are quiet — the second one completely so.

So each half names the other, in prose, in its own comment syntax:

    cv/publish.py     # Fits under `byEvent.size() <= 1500` in firestore.rules.
    coach/review.js   // Mirrors `missed.size() <= 300` in firestore.rules.
    assets/auth.js    // Mirrors `teamIds.size() <= 10` in firestore.rules.
    firestore.rules   // Client guard: <path>

and this holds the two to each other three ways: the expression the client
quotes must really occur in the rules, the number in the code under the marker
must agree with it, and the rule must point back at the file that quoted it.

**Mirrors** means equal — a guard whose whole job is to refuse exactly what the
rule refuses. **Fits under** means at most — `MAX_EVENTS` bounds a different
document written by a different actor, and lowering it is safe while raising it
past the rule is not. Two verbs because there are two relations; using one for
both would mean either failing a correct change or passing a broken one.

The pairs are discovered by scanning for the markers, not listed here. That is
the whole reason this can fail backwards: a marker added at one end and not the
other is a finding, and so is a marker left behind after its counterpart moved.
There is no allowlist to rot.

Deliberately *not* every cap in `firestore.rules`. There are twenty, and
seventeen of them bound something no client counts — a string length, a key
count, a list the browser cannot make longer than a handful. A gate over all
twenty would need a seventeen-entry allowlist saying "this one is fine", which
is the graveyard `tests/test_call_graph.py` warns about. The three here are the
ones where two pieces of code hold the same number for the same reason.

Nothing is blanked before scanning, unlike most gates here: the markers *are*
comments, and blanking is what removes them.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_cap_pairs.py -q
"""

import re

from test_contrast_floor import JS_DIRS, ROOT

#: The client half. One line, because a marker split across two is a marker
#: half of which can be deleted without the other half noticing.
MARKER = re.compile(
    r'(Mirrors|Fits under) `([A-Za-z_$][\w$]*\.size\(\) <= (\d+))` in firestore\.rules'
)

#: The rules half, on its own line directly above the cap it describes.
GUARD = re.compile(r'^\s*//\s*Client guard:\s*(\S+?)\.?\s*$')

#: Every numeric cap in the rules, for the anti-vacuum count. Both comparators,
#: because `coachUids.size() >= 1` is as much a cap as the ones above it.
RULE_CAP = re.compile(r'\.size\(\)\s*[<>]=\s*(\d+)')

#: How far below a marker its number is allowed to be. Three lines is one
#: statement plus the brace after it; more would start reaching into code the
#: marker is not about.
WINDOW = 3


def rules_text() -> str:
    return (ROOT / 'firestore.rules').read_text(encoding='utf-8')


def client_files() -> list:
    """Everywhere a marker could live: the site's modules and the publisher."""
    return sorted(
        [p for d in JS_DIRS for p in (ROOT / d).glob('*.js')]
        + list((ROOT / 'cv').glob('*.py'))
    )


def rel(path) -> str:
    return path.relative_to(ROOT).as_posix()


def markers(text: str) -> list[dict]:
    """Every client marker in `text`, with the line it sits on."""
    found = []
    for i, line in enumerate(text.splitlines()):
        m = MARKER.search(line)
        if m:
            found.append({
                'verb': m.group(1), 'expr': m.group(2),
                'cap': int(m.group(3)), 'line': i,
            })
    return found


def numbers_below(text: str, line: int) -> list[int]:
    """The integers in the code under a marker.

    Skips the rest of the marker's own sentence — prose wraps, and the wrapped
    part is still comment — then reads the next few lines of actual code.
    Comments and string literals inside that window drop out too, because both
    are places a number can sit without being the guard: `recordMiss` shows the
    coach a toast with the cap written into the sentence, and a gate that read
    the apology as the guard would pass a file where only the apology moved.

    Stripping quotes here is safe in a way it would not be over the whole file
    — the window is three lines of code by construction, and the marker this is
    checking is itself a comment, which is exactly what a blanking pass removes.
    """
    lines = text.splitlines()
    i = line + 1
    while i < len(lines) and (not lines[i].strip()
                              or lines[i].lstrip().startswith(('#', '//'))):
        i += 1
    out = []
    for row in lines[i:i + WINDOW]:
        if row.lstrip().startswith(('#', '//')):
            continue
        row = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`", ' ', row)
        out.extend(int(n) for n in re.findall(r'(?<![\w.])(\d+)(?![\w.])', row))
    return out


def disagreements(text: str, name: str, rules: str) -> list[str]:
    """Every way one file's markers can fail to describe the rules or itself."""
    out = []
    for mark in markers(text):
        full = 'request.resource.data.' + mark['expr']
        seen = rules.count(full)
        if seen != 1:
            out.append(f'{name}: `{mark["expr"]}` occurs {seen} times in the rules')
            continue

        nums = numbers_below(text, mark['line'])
        if not nums:
            out.append(f'{name}: nothing numeric under the `{mark["expr"]}` marker')
        elif max(nums) > mark['cap']:
            out.append(f'{name}: {max(nums)} sits under a marker claiming'
                       f' {mark["cap"]}')
        elif mark['verb'] == 'Mirrors' and mark['cap'] not in nums:
            out.append(f'{name}: marker claims {mark["cap"]}, the code below it'
                       f' says {nums}')

        rules_line = rules[:rules.index(full)].count('\n')
        above = rules.splitlines()[max(0, rules_line - WINDOW):rules_line]
        pointed = [GUARD.match(row).group(1) for row in above if GUARD.match(row)]
        if name not in pointed:
            out.append(f'{name}: the rule capping `{mark["expr"]}` points at'
                       f' {pointed or "nothing"}')
    return out


def orphan_guards(rules: str, marked: dict) -> list[str]:
    """Rules-side pointers with nothing at the other end.

    The direction that makes this gate more than a comment convention. A cap
    that stops being mirrored, or a path that gets renamed, leaves a line here
    claiming a client guard that is not there.
    """
    out = []
    lines = rules.splitlines()
    for i, line in enumerate(lines):
        m = GUARD.match(line)
        if not m:
            continue
        path = m.group(1)
        below = lines[i + 1] if i + 1 < len(lines) else ''
        cap = RULE_CAP.search(below)
        if not cap:
            out.append(f'firestore.rules:{i + 1}: points at {path}, caps nothing')
        elif path not in marked:
            out.append(f'firestore.rules:{i + 1}: {path} carries no marker')
        elif not any(str(cap.group(1)) == str(c) for c in marked[path]):
            out.append(f'firestore.rules:{i + 1}: {path} quotes {marked[path]},'
                       f' not {cap.group(1)}')
    return out


class TestThePairsAgree:
    def test_every_client_marker_matches_the_rule_it_names(self):
        rules = rules_text()
        found = []
        for path in client_files():
            found += disagreements(path.read_text(encoding='utf-8'), rel(path), rules)
        assert not found

    def test_every_rule_points_at_a_client_that_quotes_it(self):
        rules = rules_text()
        marked = {}
        for path in client_files():
            caps = [m['cap'] for m in markers(path.read_text(encoding='utf-8'))]
            if caps:
                marked[rel(path)] = caps
        assert not orphan_guards(rules, marked)

    def test_both_ends_name_the_same_number_of_pairs(self):
        """A marker added at one end only, stated as its own failure.

        The two tests above would each half-catch it, and each report it as
        something else — a rule pointing nowhere, or a client the rules do not
        point back at. This says the plain thing instead.
        """
        rules = rules_text()
        client = sum(len(markers(p.read_text(encoding='utf-8')))
                     for p in client_files())
        pointers = [line for line in rules.splitlines() if GUARD.match(line)]
        assert client == len(pointers), (
            f'{client} client markers against {len(pointers)} rule pointers')

    def test_the_scan_reaches_both_languages(self):
        """The anti-vacuum guard.

        Three floors, because there are three ways this file can pass by
        looking at nothing: no client files globbed, no markers found in them,
        no caps left in the rules to disagree with.
        """
        files = client_files()
        assert len(files) >= 30, f'only {len(files)} client files found'
        pairs = sum(len(markers(p.read_text(encoding='utf-8'))) for p in files)
        assert pairs >= 3, f'only {pairs} cap markers found'
        caps = RULE_CAP.findall(rules_text())
        assert len(caps) >= 15, f'only {len(caps)} numeric caps in the rules'

    def test_the_three_pairs_are_the_ones_expected(self):
        """Named once, so a fourth pair has to be a deliberate act.

        Not an allowlist — the tests above neither read this nor pass because
        of it. It is here so that adding a pair means editing a test that says
        out loud what the pairs are for, rather than a marker sliding in.
        """
        found = {(rel(p), m['verb'], m['expr'])
                 for p in client_files()
                 for m in markers(p.read_text(encoding='utf-8'))}
        assert found == {
            ('cv/publish.py', 'Fits under', 'byEvent.size() <= 1500'),
            ('coach/review.js', 'Mirrors', 'missed.size() <= 300'),
            ('assets/auth.js', 'Mirrors', 'teamIds.size() <= 10'),
        }


class TestTheScanWorks:
    RULES = (
        'match /x {\n'
        '  allow write: if true\n'
        '    // Client guard: a/b.js\n'
        '    && request.resource.data.things.size() <= 50;\n'
        '}\n'
    )

    def test_it_catches_a_client_that_moved_alone(self):
        src = '// Mirrors `things.size() <= 60` in firestore.rules.\nconst n = 60;\n'
        assert disagreements(src, 'a/b.js', self.RULES)

    def test_it_catches_a_number_the_marker_does_not_claim(self):
        src = '// Mirrors `things.size() <= 50` in firestore.rules.\nconst n = 60;\n'
        out = disagreements(src, 'a/b.js', self.RULES)
        assert out and '60' in out[0]

    def test_it_catches_a_guard_that_was_deleted_under_its_comment(self):
        src = '// Mirrors `things.size() <= 50` in firestore.rules.\nsave(list);\n'
        out = disagreements(src, 'a/b.js', self.RULES)
        assert out and 'nothing numeric' in out[0]

    def test_it_catches_a_rule_pointing_at_someone_else(self):
        src = '// Mirrors `things.size() <= 50` in firestore.rules.\nconst n = 50;\n'
        assert disagreements(src, 'c/d.js', self.RULES)
        assert not disagreements(src, 'a/b.js', self.RULES)

    def test_fits_under_allows_less_and_refuses_more(self):
        low = '# Fits under `things.size() <= 50` in firestore.rules.\nN = 40\n'
        high = '# Fits under `things.size() <= 50` in firestore.rules.\nN = 51\n'
        assert not disagreements(low, 'a/b.js', self.RULES)
        assert disagreements(high, 'a/b.js', self.RULES)

    def test_mirrors_refuses_the_same_slack_fits_under_allows(self):
        """The whole reason there are two verbs.

        A guard that refuses at 40 what the rules refuse at 50 is not mirroring
        it; it is a stricter limit nobody wrote down, and the ten it gives away
        will be found by a coach, not by a test.
        """
        src = '// Mirrors `things.size() <= 50` in firestore.rules.\nconst n = 40;\n'
        assert disagreements(src, 'a/b.js', self.RULES)

    def test_a_number_in_the_window_that_is_only_prose_is_ignored(self):
        """`recordMiss` shows the coach a message with the cap in it."""
        src = ('// Mirrors `things.size() <= 50` in firestore.rules.\n'
               'if (list.length > 50) {\n'
               '    toast("That is 50 of them, which is 900 too few.");\n')
        assert not disagreements(src, 'a/b.js', self.RULES)

    def test_a_marker_wrapped_over_several_lines_still_finds_its_code(self):
        src = ('// Mirrors `things.size() <= 50` in firestore.rules. Refuse here\n'
               '// rather than letting the save fail with a permission error.\n'
               'if (list.length > 50) {\n')
        assert not disagreements(src, 'a/b.js', self.RULES)

    def test_a_rule_pointer_above_no_cap_is_an_orphan(self):
        rules = 'allow write: if true\n  // Client guard: a/b.js\n  && x == 1;\n'
        assert orphan_guards(rules, {'a/b.js': [50]})

    def test_a_rule_pointer_at_an_unmarked_file_is_an_orphan(self):
        assert orphan_guards(self.RULES, {})
        assert not orphan_guards(self.RULES, {'a/b.js': [50]})

    def test_a_rule_pointer_at_a_different_number_is_an_orphan(self):
        out = orphan_guards(self.RULES, {'a/b.js': [60]})
        assert out and 'not 50' in out[0]
