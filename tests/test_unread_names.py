"""A name a module declares for itself and then never reads.

`tests/test_free_names.py` is the other half of this: a name *read* with no
declaration anywhere in reach. `tests/test_call_graph.py` is the third: a name
*exported* and imported by nobody. Between them they cover a read with no
writer and a writer with no reader across module boundaries — and neither can
see the thing that sits entirely inside one file, declared and referenced once.

That gap had something in it. `CONFIRMED` sat at the top of `coach/review.js`
for as long as the review tool existed, holding the string `'confirmed'`, read
by nothing. Five hundred lines below it the button that actually writes that
word to Firestore spelled it out by hand. Neither existing gate could say a
word about it: it was never exported, so the call graph had no opinion, and it
was declared, so the free-name scan was satisfied. The constant was not the bug
— the bug was the hand-typed copy — but a constant nothing reads is how a
second copy gets to look harmless. Nobody deletes it, because deleting things
is how you break something; nobody updates it, because nothing points there.

So the rule is the plainest one that means anything: a top-level name has to
occur somewhere other than its own declaration. Counted on blanked source, so a
name kept alive only by a comment about it is still unread, and template
interpolations still count — `blank` keeps `${...}` interiors, which matters
because `CONFIRMED_STATUS` today is read *only* from inside a template literal.

Exported names are deliberately not checked here. `export const X` never
matches the declaration scan, because whether anything reads `X` is a question
about the whole repo and `test_call_graph.py` already answers it in both
directions. Two gates disagreeing about one name is worse than either alone.

What this cannot see, stated rather than hidden — see `TestWhatItCannotSee`:
a shadowed name reads as used, and a property that happens to share a name with
a top-level constant reads as a use of the constant. Both under-report. Neither
can invent a finding, which is the right direction for a check with no parser
behind it.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_unread_names.py -q
"""

import re

from test_contrast_floor import JS_DIRS, ROOT
from test_free_names import blank

#: A top-level declaration, in the four shapes this repo writes them.
#:
#: Anchored at column zero, which is the whole definition of "top-level" here
#: and is why `export` ones fall out for free. `let toastTimer;` carries no
#: initialiser and still counts — `assets/ui.js` and `xg-sandbox/sandbox.js`
#: have five between them.
DECLARED = re.compile(
    r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*[=;]'
    r'|^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\('
    r'|^class\s+([A-Za-z_$][\w$]*)\b',
    re.M,
)

#: Everything between `import` and `from`. Side-effect imports have no `from`
#: and bind nothing, so they are correctly invisible to this.
IMPORT = re.compile(r'^import\s+([^;]*?)\s+from\s', re.M | re.S)


def js_files() -> list:
    return sorted(p for d in JS_DIRS for p in (ROOT / d).glob('*.js'))


def declared_names(code: str) -> list[str]:
    return [g for m in DECLARED.finditer(code) for g in m.groups() if g]


def imported_names(code: str) -> list[str]:
    """Every binding an import statement introduces.

    `{ a, b }` is almost all of them. `{ a as b }` appears once on the whole
    site — `signOut as fbSignOut` in `assets/auth.js`, renamed around the
    wrapper of the same name it exports — and the local name is the one that
    has to be used, so it is the last word of the clause that counts. `* as NS`
    appears nowhere and is handled anyway, because the cost of it is one line
    and the cost of missing it is a whole module's bindings going unchecked.
    """
    names = []
    for clause in IMPORT.findall(code):
        clause = clause.strip()
        braced = re.search(r'\{(.*)\}', clause, re.S)
        if braced:
            for part in braced.group(1).split(','):
                if part.strip():
                    names.append(part.split()[-1])
            clause = clause[:braced.start()]
        names.extend(w for w in re.findall(r'[A-Za-z_$][\w$]*', clause)
                     if w not in ('as', 'default'))
    return names


def occurrences(code: str, name: str) -> int:
    """How many times `name` appears as a whole identifier.

    Whole, or `tot` is read every time `totals` is — the failure that turns
    this gate from a check into a rubber stamp, since almost every short name
    is a substring of some longer one.

    Lookarounds rather than `\\b`, because `$` is an identifier character in
    JavaScript and not a word character to `re`: `\\b$panel\\b` matches
    nothing, so a `$`-prefixed name would be reported dead the moment it was
    used. Nothing on the site is named that way today. It costs one line to be
    right about it and a confusing false positive to be wrong.
    """
    pattern = r'(?<![\w$])%s(?![\w$])' % re.escape(name)
    return len(re.findall(pattern, code))


def unread(source: str) -> dict[str, list[str]]:
    """Names declared or imported at the top of `source` and read nowhere in it.

    One occurrence is the declaration itself. Anything above one is a use, and
    this makes no attempt to tell a read from a write — `let x; x = 1;` counts
    as used. Distinguishing those needs to know which side of an assignment a
    name is on, which needs a parser; the version that guessed from the shape
    of the line called ninety-nine live names dead, most of them by mistaking
    `const total = NS;` for a declaration of `NS` rather than a read of it.
    """
    code = blank(source)
    return {
        'declared': [n for n in declared_names(code) if occurrences(code, n) <= 1],
        'imported': [n for n in imported_names(code) if occurrences(code, n) <= 1],
    }


class TestNothingUnread:
    def test_no_module_declares_a_name_it_never_reads(self):
        """The shape `CONFIRMED` had, checked across every module on the site."""
        found = {}
        for path in js_files():
            names = unread(path.read_text(encoding='utf-8'))['declared']
            if names:
                found[path.relative_to(ROOT).as_posix()] = names
        assert not found

    def test_no_module_imports_a_name_it_never_uses(self):
        """The same finding one line higher up.

        An import nothing uses is worse than a constant nothing reads: it says
        this file depends on that one, to every person and every tool that goes
        looking, and it does not.
        """
        found = {}
        for path in js_files():
            names = unread(path.read_text(encoding='utf-8'))['imported']
            if names:
                found[path.relative_to(ROOT).as_posix()] = names
        assert not found

    def test_the_scan_reaches_the_whole_site(self):
        """The anti-vacuum guard, and the reason the two zeros above mean
        anything.

        A directory dropped from `JS_DIRS`, a glob that stops matching, a
        `blank` that starts returning nothing — each of those turns this file
        into two tests that pass by looking at no code at all. Floors rather
        than exact counts, so the repo is allowed to grow.
        """
        files = js_files()
        assert len(files) >= 30, f'only {len(files)} site modules found'
        total = 0
        for path in files:
            code = blank(path.read_text(encoding='utf-8'))
            total += len(declared_names(code)) + len(imported_names(code))
        assert total >= 900, f'only {total} top-level names found'


class TestTheScanWorks:
    def test_it_finds_a_declaration_nothing_reads(self):
        assert unread("const DEAD = 'x';\nexport const n = 1;\n")['declared'] == ['DEAD']

    def test_a_shorter_name_is_not_found_inside_a_longer_one(self):
        """Without whole-identifier boundaries this gate reports nothing, ever.

        `tot` appears inside `totals`, `id` inside `videoId`, `n` inside every
        other name on the site. A scan matching bare substrings finds a use for
        almost anything and passes on a repo full of dead constants.
        """
        source = 'const tot = 1;\nexport const n = totals.length;\n'
        assert unread(source)['declared'] == ['tot']

    def test_it_finds_an_import_nothing_uses(self):
        source = "import { alpha, beta } from './x.js';\nexport const n = beta;\n"
        assert unread(source)['imported'] == ['alpha']

    def test_an_aliased_import_binds_the_local_name(self):
        """`assets/auth.js` is the only place this happens, and it is the worst
        possible place to get it wrong.

        It imports `signOut as fbSignOut` and exports a wrapper of its own
        called `signOut`. A scan that took the first word of the clause would
        look for `signOut`, find the wrapper, and count the import as used —
        while `fbSignOut`, the binding that actually has to be there, goes
        unchecked for as long as the file exists.
        """
        source = "import { signOut as fbSignOut } from './x.js';\nexport const n = signOut;\n"
        assert unread(source)['imported'] == ['fbSignOut']

    def test_a_namespace_import_binds_the_namespace(self):
        source = "import * as NS from './x.js';\nexport const n = 1;\n"
        assert unread(source)['imported'] == ['NS']

    def test_a_name_read_only_inside_a_template_literal_counts(self):
        """The precondition the whole scan rests on.

        `blank` strips string literals but keeps `${...}` interiors, and
        `CONFIRMED_STATUS` is read from exactly one place in `coach/review.js`
        — inside the template that builds the verdict buttons. If blanking
        swallowed interpolations this gate would report the fix for the bug it
        was written about as a fresh instance of the same bug.
        """
        source = "const TAG = 'a';\nexport const html = `<b data-x=\"${TAG}\"></b>`;\n"
        assert unread(source)['declared'] == []

    def test_a_name_left_only_in_a_comment_is_unread(self):
        """Comments are where a deleted use goes to look like a live one."""
        source = "const GONE = 1;\n// GONE is still used by the chart code.\n"
        assert unread(source)['declared'] == ['GONE']

    def test_exported_names_are_left_to_the_call_graph(self):
        """Checked, because silence here has to be a decision and not an
        accident of where the regex happens to anchor."""
        assert unread("export const LOOSE = 1;\n")['declared'] == []
        assert unread("const LOOSE = 1;\n")['declared'] == ['LOOSE']


class TestWhatItCannotSee:
    def test_a_shadowed_name_reads_as_used(self):
        """A limit, pinned so it is a known one rather than a surprise.

        The inner `helper` is a different variable that happens to share a
        name, and counting identifiers cannot tell. Fixing it means parsing
        JavaScript in a Python test suite with no build step, for a false
        negative that hides a finding rather than inventing one.
        """
        source = 'const helper = 1;\nexport function f() { const helper = 2; return helper; }\n'
        assert unread(source)['declared'] == []

    def test_a_property_of_the_same_name_reads_as_a_use(self):
        """The other under-report, and the more likely of the two.

        `total` as a top-level constant and `summary.total` as a field are
        indistinguishable to a scan that does not know what a dot means.
        """
        source = 'const total = 1;\nexport const n = summary.total;\n'
        assert unread(source)['declared'] == []
