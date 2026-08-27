"""Every rule has an element, and every element has a rule.

The five payload gates beside this file ask one question in both directions:
is every figure the pipeline publishes read by some page, and is every figure
a page reads actually published. This file asks the same question one layer
out, of the stylesheet, which until now nothing checked at all.

Roughly four thousand lines of CSS reach every visitor of this site. Both
halves of the seam can rot, and they rot differently:

  * A rule with no element is bytes every visitor downloads and no visitor
    ever sees. Harmless in ones, and it accumulates: the class gets renamed,
    the component gets rewritten, the rule stays because deleting CSS without
    a way to prove it is unused feels like tempting fate.

  * An element with no rule is the loud one. It renders with the browser's
    defaults, and on a dark theme the browser's defaults are usually black
    text, which is to say invisible rather than merely plain.

The audit this file grew out of found five real defects, and it is worth
writing down what they were, because they are the shape of what it will catch
next time:

  1. `.list` had no rule at all, on four containers in `coach/index.html`.
     Every list of `.list-item` rows on the match view stacked flush, so
     adjacent 1px borders met as one 2px seam and twenty shots read as a
     single ruled block. Its sibling `.card-list` had had `gap: 8px` all along.
  2. `.cluster-swatch` -- three rules and a named grid area -- was superseded
     when `clusterFace()` started building a `.cluster-thumb` with the kit
     colour as a strip inside it. Its own docstring says so.
  3. `.wrap` appeared in exactly one selector, in the print sheet, and nowhere
     else in the repo.
  4. `.sub-row.is-bare` was written in `coach/coach.js` and styled nowhere: a
     substitution with nothing measurable either side of it was meant to read
     differently and did not.
  5. `.pass-node`, `.rail-links`, `.shot-log` and `.welcome-main`: four names
     on real elements that no selector ever mentioned.

Both directions now hold at zero, which is exactly when a check is worth
pinning. A gate written over a mess only records the mess.

The scanner
-----------

Finding the declared names is easy and finding the used ones is not, because a
class name in this repo is frequently not a string anybody wrote:

    el.className = `tick is-${type}`;                 // `.is-shot` is alive
    el.className = `sub-row${row.scored ? '' : ' is-bare'}`;
    const classes = ['chart-bar'];                    // and pushed to, later
    svgEl('rect', { class: classes.join(' ') });      // SVG has no className

A scan that reads only quoted strings calls all forty-one `is-*` rules dead.
A scan that reads `className` and `classList` but not attribute bags calls
every SVG rule dead -- seventeen of them, since an SVG element has no writable
`className` and every chart here goes through `el(tag, {class: ...})`.

So there are six sites, and each one is proved load-bearing by a test below
rather than asserted to be useful:

    markup     class="a b ${x}" inside a template string, at any depth
    classname  className = <anything>, including `'stat ' + kind`
    classlist  classList.add / remove / toggle / contains / replace
    query      querySelector('.foo'), closest('.foo'), matches('.foo')
    bag        el('g', {class: 'pass-node'}) -- the SVG shape
    join       class: classes.join(' ') -- the array built up a push at a time

Which way to be wrong
---------------------

The two ways this scanner can be wrong are not symmetrical, and the asymmetry
decides how greedy it should be:

  * Miss a use, and the dead-rule list grows with false alarms while the
    unstyled list quietly shrinks. Noisy, and noise is survivable.
  * Invent a use, and the dead-rule list quietly shrinks -- a real dead rule
    stops being reported and nobody ever learns it was there.

That was not theoretical. Adding the two SVG sites cut the dead list from
thirty names to thirteen: seventeen of the thirty "dead rules" were the
scanner's blindness reported as the repo's defect, exactly the loud direction.

So the scan is greedy up to the point where being greedier would mean guessing,
and past that point it says so instead. `WEAK` below is that boundary.

Holes are filled by substitution, not by reading
------------------------------------------------

    `is-${done ? 'good' : 'todo'}`

is two classes called `is-good` and `is-todo`. Reading the literals out of the
hole on their own gets both names wrong in both directions at once: it invents
elements called `good` and `todo`, and it leaves `.is-good` looking like a rule
with no element. Each literal is substituted back into its template and the
result re-tokenised -- the same move the payload scanners make. Follow the
value, not the thing holding it.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CSS = ['assets/app.css', 'assets/landing.css', 'coach/coach.css',
       'player/player.css', 'live-tagging/tagging.css', 'halftime/halftime.css',
       'calibrate/calibrate.css', 'xg-sandbox/sandbox.css']
HTML = ['index.html', 'coach/index.html', 'player/index.html',
        'live-tagging/index.html', 'halftime/index.html',
        'calibrate/index.html', 'xg-sandbox/index.html']
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime',
           'calibrate', 'xg-sandbox']

HOLE = '\x00'                             # one `${...}` stands here
NAME = r'-?[A-Za-z_][A-Za-z0-9_-]*'
SITES = frozenset(
    {'markup', 'classname', 'classlist', 'query', 'bag', 'join'})

# Names the scanner will not claim either way, and why. Past the six mechanical
# shapes the question becomes real string-valued dataflow across a module
# boundary, and a regex cannot follow it without either inventing reachability
# or denying it. Both of those are worse than saying which of the two it is.
#
# A name in here is not proven used. It is proven *written down* as a bare
# string in a served file, which is enough that nobody should delete its rule
# on this file's say-so. The test below holds the list to that standard in both
# directions: an entry that stops being written down anywhere is dead after all
# and should go, and an entry that becomes provably reachable is a stale excuse
# and should also go.
WEAK = {
    'cm-label': "labelled('cm-label', label) in coach.js -- the class is a "
                "positional argument to a helper that builds the element",
    'cm-time': "labelled('cm-time', clockText(s)), same helper",
    'dim': "figure(..., 'dim') in landing.js -- a tone passed as an argument",
    'est': "decision('est', ...) in halftime.js -- likewise",
    'info': "decision('info', ...) in halftime.js",
    'ours-bad': "report.js returns the string and a caller in another module "
                "applies it; the docstring names both halves",
    'ours-good': "the other arm of the same return",
    'period': "an event tone in db.js, applied by classList.add(tone) after "
              "travelling through a lookup table",
    'red': "a card colour in db.js and ui.js, same shape",
    'second_yellow': "a card colour in db.js; unlike red and yellow it has no "
                     "second home in ui.js, so it is thinner evidence still",
    'yellow': "a card colour in db.js and ui.js",
}


def js_files():
    out = []
    for d in JS_DIRS:
        out += sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                      for p in (ROOT / d).glob('*.js'))
    return out


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


# --------------------------------------------------------------- the sheets

NESTS = re.compile(r'@(?:media|supports|container|layer|scope|document)\b')


def selectors(css, *, nested=True, comments=True):
    """Every selector prelude in the sheet, at any nesting depth.

    An earlier draft peeled innermost `{...}` blocks repeatedly. That works on
    flat rules and quietly deletes every rule inside an `@media` block: once
    the declarations inside it are gone, the block holds no braces of its own
    and vanishes with them. This sheet keeps its whole print stylesheet and
    every responsive override inside such blocks, so that draft could not see
    a fifth of the file.
    """
    if comments:
        css = re.sub(r'/\*.*?\*/', ' ', css, flags=re.S)
    out = []

    def walk(lo, hi):
        i = start = lo
        while i < hi:
            c = css[i]
            if c == '{':
                prelude = css[start:i].strip()
                depth, j = 1, i + 1
                while j < hi and depth:
                    if css[j] == '{':
                        depth += 1
                    elif css[j] == '}':
                        depth -= 1
                    j += 1
                if not prelude.startswith('@'):
                    out.append(prelude)
                elif nested and NESTS.match(prelude):
                    walk(i + 1, j - 1)
                i = start = j
            elif c in '};':
                i += 1
                start = i
            else:
                i += 1

    walk(0, len(css))
    return out


def all_declared(**kw):
    """{class name: [where it was declared, ...]} across all eight sheets."""
    out = {}
    for rel in CSS:
        for sel in selectors(read(rel), **kw):
            flat = ' '.join(sel.split())
            for name in re.findall(r'\.(' + NAME + ')', sel):
                out.setdefault(name, []).append('%s: %s' % (rel, flat[:64]))
    return out


# --------------------------------------------------------- the served files

def literals(src, *, comments=True):
    """Every string in `src`, as (quote, text, holes, start, end).

    Template holes collapse to one HOLE byte and their source comes back
    separately, so `${a ? 'x' : 'y'}` can still yield x and y. Comments are
    skipped: a commented-out `class="..."` is not markup on anybody's page.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if comments and c == '/' and i + 1 < n and src[i + 1] == '/':
            i = src.find('\n', i)
            if i < 0:
                break
            continue
        if comments and c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c not in '\'"`':
            i += 1
            continue
        start, quote, i = i, c, i + 1
        text, holes = [], []
        while i < n:
            ch = src[i]
            if ch == '\\':
                text.append(src[i:i + 2])
                i += 2
                continue
            if ch == quote:
                i += 1
                break
            if quote != '`' and ch == '\n':
                break                  # unterminated: not a string at all
            if quote == '`' and ch == '$' and i + 1 < n and src[i + 1] == '{':
                depth, j = 1, i + 2
                while j < n and depth:
                    if src[j] == '{':
                        depth += 1
                    elif src[j] == '}':
                        depth -= 1
                    j += 1
                holes.append(src[i + 2:j - 1])
                text.append(HOLE)
                i = j
                continue
            text.append(ch)
            i += 1
        out.append((quote, ''.join(text), holes, start, i))
    return out


def variants(text, holes, *, fill=True):
    """Concrete class values for one template, one hole filled at a time.

    Only one hole is filled per variant. Filling every combination would be
    exponential and would buy nothing: no class token in this repo spans two
    holes, and a hole left unfilled still contributes the prefix in front of
    it, which is what makes `is-${type}` cover forty-one rules.
    """
    out = [text]
    if not fill:
        return out
    for index, hole in enumerate(holes):
        for option in {t for _, t, _, _, _ in literals(hole)}:
            parts = text.split(HOLE)
            if len(parts) <= index + 1:
                continue
            # A splice, not a join. Putting the parts back together with HOLE
            # would drop the hole straight back into the gap that was just
            # resolved, turning the finished name into a prefix again.
            out.append(HOLE.join(parts[:index]
                                 + [parts[index] + option + parts[index + 1]]
                                 + parts[index + 2:]))
    return out


def _split(value):
    """A class attribute value -> (literal names, prefixes before a hole)."""
    names, prefixes = [], []
    for tok in value.split():
        if HOLE not in tok:
            names.append(tok)
        else:
            prefixes.append(tok.split(HOLE, 1)[0])   # '' means "any name"
    return names, prefixes


CLASS_ATTR = re.compile(r'class\s*=\s*(["\'])(.*?)\1', re.S)
CLASS_IN_TEXT = re.compile(r'class\s*=\s*(["\'])([^"\']*)\1')
CLASS_NAME_LHS = re.compile(r'className\s*\+?=\s*')
CLASS_LIST_LHS = re.compile(
    r'classList\s*\.\s*(add|remove|toggle|contains|replace)\s*\(')
CLASS_KEY = re.compile(r'(?<![\w-])[\'"]?class[\'"]?\s*:\s*')
JOINED = re.compile(r'\s*(' + NAME + r')\s*\.\s*join\s*\(')
QUERY = re.compile(
    r'(?:querySelector|querySelectorAll|closest|matches|'
    r'getElementsByClassName)\s*\(\s*')

# `toggle(cls, force)` and `contains(cls)` take one class and then something
# that is not one. Reading every argument turned the `'link'` in
# `classList.toggle('is-link', placement === 'link')` into an element with no
# rule, which is a scanner defect reported as a UI defect.
FIRST_ARG_ONLY = ('toggle', 'contains')


def _spans(lits):
    return {s: e for _, _, _, s, e in lits}


def _statement_end(src, i, lits):
    """Where the expression starting at `i` stops: `;` or end of its line."""
    spans = _spans(lits)
    j = i
    while j < len(src):
        if j in spans:
            j = spans[j]
            continue
        if src[j] in ';\n':
            return j
        j += 1
    return len(src)


def _call_end(src, i, lits):
    """The `)` closing a call whose `(` has already been passed."""
    spans = _spans(lits)
    depth, j = 1, i
    while j < len(src):
        if j in spans:
            j = spans[j]
            continue
        if src[j] == '(':
            depth += 1
        elif src[j] == ')':
            depth -= 1
            if not depth:
                return j
        j += 1
    return len(src)


def _first_comma(src, lo, hi, lits):
    """The first top-level comma in `src[lo:hi]`, or `hi`."""
    spans = _spans(lits)
    depth, j = 0, lo
    while j < hi:
        if j in spans:
            j = spans[j]
            continue
        if src[j] in '([{':
            depth += 1
        elif src[j] in ')]}':
            depth -= 1
        elif src[j] == ',' and not depth:
            return j
        j += 1
    return hi


def _value_end(src, i, lits):
    """Where an object-literal property value stops: a `,` or the closing `}`."""
    spans = _spans(lits)
    depth, j = 0, i
    while j < len(src):
        if j in spans:
            j = spans[j]
            continue
        c = src[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return j
            depth -= 1
        elif c == ',' and not depth:
            return j
        j += 1
    return len(src)


def _close(src, i, lits):
    """The bracket closing the one already passed, of any kind."""
    spans = _spans(lits)
    depth, j = 1, i
    while j < len(src):
        if j in spans:
            j = spans[j]
            continue
        if src[j] in '([{':
            depth += 1
        elif src[j] in ')]}':
            depth -= 1
            if not depth:
                return j
        j += 1
    return len(src)


def _array_parts(src, name, lits):
    """Every string put into the array called `name`, wherever it went in.

    `const classes = ['chart-bar']; if (...) classes.push('scored');` is one
    class value spelled across three statements. The declaration and the pushes
    are the only two ways anything enters these arrays in this repo, and both
    are read here rather than guessed at.
    """
    out = []
    pat = re.compile(r'(?:(?:const|let|var)\s+' + re.escape(name) +
                     r'\s*=\s*\[)|(?:' + re.escape(name) +
                     r'\s*\.\s*(?:push|unshift)\s*\()')
    for m in pat.finditer(src):
        end = _close(src, m.end(), lits)
        for _, text, holes, s, _e in lits:
            if m.end() <= s < end:
                out.append((text, holes))
    return out


def used(rel, *, sites=SITES, fill=True, first_arg_only=True, comments=True):
    """(literal names -> where, prefixes -> where) for one served file."""
    src = read(rel)
    names, prefixes = {}, {}

    def take(text, holes, why):
        for value in variants(text, holes, fill=fill):
            n, p = _split(value)
            for x in n:
                names.setdefault(x, []).append('%s (%s)' % (rel, why))
            for x in p:
                prefixes.setdefault(x, []).append('%s (%s)' % (rel, why))

    if rel.endswith('.html'):
        for _, body in CLASS_ATTR.findall(src):
            take(body, [], 'class=')
        return names, prefixes

    lits = literals(src, comments=comments)

    if 'markup' in sites:
        # Markup inside strings, at any nesting depth. The attribute value is
        # matched against the template *text*, so a hole inside it survives as
        # a HOLE byte and gets expanded like any other.
        def walk_markup(items, why):
            for _, text, holes, _, _ in items:
                for _, body in CLASS_IN_TEXT.findall(text):
                    take(body, holes, why)
                for hole in holes:
                    walk_markup(literals(hole, comments=comments),
                                why + ' in a hole')

        walk_markup(lits, 'class=')

    if 'classname' in sites:
        # The whole right-hand side is read, so both arms of a ternary and
        # every piece of a concatenation are counted.
        for m in CLASS_NAME_LHS.finditer(src):
            end = _statement_end(src, m.end(), lits)
            for _, text, holes, s, e in lits:
                if not (m.end() <= s < end):
                    continue
                take(text, holes, 'className')
                # `'stat ' + kind` is a hole with no braces drawn around it.
                if src[e:end].lstrip().startswith('+'):
                    tail = ('' if not text or text[-1].isspace()
                            else text.split()[-1])
                    take(tail + HOLE, [], 'className concatenated')

    if 'classlist' in sites:
        for m in CLASS_LIST_LHS.finditer(src):
            end = _call_end(src, m.end(), lits)
            if first_arg_only and m.group(1) in FIRST_ARG_ONLY:
                end = _first_comma(src, m.end(), end, lits)
            for _, text, holes, s, _e in lits:
                if m.end() <= s < end:
                    take(text, holes, 'classList')

    if 'bag' in sites:
        # `class` as an object key rather than an attribute: SVG elements have
        # no writable className, so every chart in this repo goes through an
        # attribute bag instead.
        for m in CLASS_KEY.finditer(src):
            end = _value_end(src, m.end(), lits)
            for _, text, holes, s, _e in lits:
                if m.end() <= s < end:
                    take(text, holes, 'class:')
            if 'join' in sites:
                hop = JOINED.match(src, m.end(), end)
                if hop:
                    for text, holes in _array_parts(src, hop.group(1), lits):
                        take(text, holes, 'class: %s.join()' % hop.group(1))

    if 'query' in sites:
        starts = {s: text for _, text, _, s, _ in lits}
        for m in QUERY.finditer(src):
            text = starts.get(m.end())
            if text is None:
                continue
            for name in re.findall(r'\.(' + NAME + ')', text):
                names.setdefault(name, []).append('%s (querySelector)' % rel)

    return names, prefixes


def all_used(**kw):
    names, prefixes = {}, {}
    for rel in HTML + js_files():
        n, p = used(rel, **kw)
        for k, v in n.items():
            names.setdefault(k, []).extend(v)
        for k, v in p.items():
            prefixes.setdefault(k, []).extend(v)
    return names, prefixes


def bare_strings():
    """Every string in a served file that is exactly one class-shaped name."""
    out = {}
    for rel in js_files():
        for _, text, _, _, _ in literals(read(rel)):
            token = text.strip()
            if token and HOLE not in token and re.fullmatch(NAME, token):
                out.setdefault(token, []).append(rel)
    return out


def reachable(name, names, prefixes):
    """Is `name` asked for by any served file, literally or through a hole?"""
    if name in names:
        return 'literal'
    for pre in prefixes:
        if pre and name.startswith(pre):
            return 'built: %s${...}' % pre
    return None


def unreached(rules, uses):
    names, prefixes = uses
    return {n for n in rules if not reachable(n, names, prefixes)}


def hooks(names):
    """Names some file passes to querySelector.

    A class JavaScript looks the element back up by is not an unstyled
    element, it is a handle. `<button class="btn tiny shot-header-btn">` is
    styled head to toe by `btn tiny`; the third name exists so that
    `item.querySelector('.shot-header-btn')` can find that one button among
    the four in the row. Demanding a rule for it would be demanding decoration
    for a name whose whole job is to be unique.

    The two are told apart by evidence rather than by how the name reads.
    """
    return {n for n, where in names.items()
            if any(w.endswith('(querySelector)') for w in where)}


@pytest.fixture(scope='module')
def rules():
    return all_declared()


@pytest.fixture(scope='module')
def uses():
    return all_used()


@pytest.fixture(scope='module')
def bare():
    return bare_strings()


class TestTheScannerCanSeeTheRepo:
    """Anti-vacuum. Every assertion below is of the form "this set is empty",
    and a scanner that reads nothing satisfies all of them at once."""

    def test_every_file_it_claims_to_read_exists(self):
        for rel in CSS + HTML:
            assert (ROOT / rel).exists(), rel
        assert len(js_files()) > 25, len(js_files())

    def test_the_sheets_yield_rules(self, rules):
        assert len(rules) > 500, len(rules)

    def test_the_pages_yield_uses(self, uses):
        names, prefixes = uses
        assert len(names) > 450, len(names)
        assert len(prefixes) > 3, sorted(prefixes)

    def test_the_runtime_prefixes_are_still_there(self, uses):
        # `is-` alone stands in for forty-one rules. If it stops being found,
        # the dead-rule test starts failing loudly rather than quietly, which
        # is the right way round -- but it would be failing about the scanner.
        _names, prefixes = uses
        assert 'is-' in prefixes


class TestNoRuleIsDead:

    def test_every_declared_name_reaches_an_element(self, rules, uses, bare):
        orphans = sorted(n for n in unreached(rules, uses) if n not in bare)
        assert not orphans, '\n'.join(
            '%-24s %s' % (n, rules[n][0]) for n in orphans)


class TestNoElementIsUnstyled:

    def test_every_applied_name_has_a_rule(self, rules, uses):
        names, _prefixes = uses
        handles = hooks(names)
        orphans = sorted(n for n in names
                         if n not in rules and n not in handles)
        assert not orphans, '\n'.join(
            '%-24s %s' % (n, names[n][0]) for n in orphans)

    def test_the_hooks_are_still_only_hooks(self, rules, uses):
        # The excuse is "JavaScript finds this element by name". Six today.
        # A hook that grows a rule stops being a hook and simply passes the
        # test above; a name that stops being queried stops being excused.
        names, _prefixes = uses
        handles = sorted(n for n in hooks(names) if n not in rules)
        assert handles == ['edit-save', 'edit-type', 'edit-who', 'rc-detail',
                           'shot-header-btn', 'team-card-body'], handles


class TestTheWeakListIsCurrent:
    """The list of names this file will not claim either way, held to the same
    standard in both directions as the payload gates hold their allowlists.
    An allowlist nobody can remove from rots into a graveyard nobody dares
    touch."""

    def test_it_names_exactly_the_unproven(self, rules, uses, bare):
        # Every unreached rule that is at least written down somewhere, and
        # nothing else. A name that becomes provably reachable is a stale
        # excuse; a name that goes unreached without this list noticing is a
        # hole in the gate.
        excused = {n for n in unreached(rules, uses) if n in bare}
        assert excused == set(WEAK), {
            'no longer weak': sorted(set(WEAK) - excused),
            'newly weak': sorted(excused - set(WEAK)),
        }

    def test_every_entry_still_has_a_rule(self, rules):
        # The other direction: an entry whose rule was deleted is an excuse
        # for nothing.
        assert not [n for n in WEAK if n not in rules]

    def test_every_entry_says_why(self):
        for name, why in WEAK.items():
            assert len(why) > 30, name


class TestEachScanSiteIsLoadBearing:
    """Six sites, and a guard that cannot fire is dead machinery. Each test
    switches one off and pins what goes wrong, so the cost of the site is paid
    against a measured benefit rather than a plausible story."""

    def _lost(self, rules, uses, **kw):
        """Rules that go dark when one site is switched off, and only those.

        The baseline is subtracted rather than compared against zero: eleven
        names are unreached whatever the settings, and a test that forgot to
        take them off would pass on their account instead of the site's.
        """
        return unreached(rules, all_used(**kw)) - unreached(rules, uses)

    def test_the_classname_site_is_load_bearing(self, rules, uses):
        # Much the largest of the six: 105 rules are reachable only because
        # somebody assigned to className. A threshold rather than a set,
        # because that number moves with every component anybody writes.
        lost = self._lost(rules, uses, sites=SITES - {'classname'})
        assert len(lost) > 80, len(lost)

    def test_the_attribute_bag_is_load_bearing(self, rules, uses):
        # SVG elements have no writable className, so every chart here goes
        # through `el(tag, {class: ...})`. Without this site the shot map, the
        # pass map, the form chart and the heat map all read as unstyled at
        # once -- 18 rules, which was 17 of the 30 "dead rules" the first draft
        # of this scanner reported.
        lost = self._lost(rules, uses, sites=SITES - {'bag'})
        assert len(lost) > 14, sorted(lost)
        for name in ('pass-dot', 'pass-edge', 'form-svg', 'heatmap-cells'):
            assert name in lost, name

    def test_the_markup_site_is_load_bearing(self, rules, uses):
        # 27, and smaller than it looks it should be only because the seven
        # HTML pages are scanned whatever the sites setting says.
        lost = self._lost(rules, uses, sites=SITES - {'markup'})
        assert len(lost) > 20, len(lost)

    def test_the_classlist_site_is_load_bearing(self, rules, uses):
        # 23, and almost all of them state changes: .live, .stalled, .flash,
        # .working -- the classes that only ever exist while something is
        # happening, which is exactly the set hardest to notice by eye.
        lost = self._lost(rules, uses, sites=SITES - {'classlist'})
        assert len(lost) > 15, len(lost)

    def test_the_array_join_is_load_bearing(self, rules, uses):
        # `const classes = ['chart-bar']; ... classes.push('scored')`. One
        # class value spelled across three statements, and small enough to pin
        # exactly.
        lost = self._lost(rules, uses, sites=SITES - {'join'})
        assert lost == {'chart-bar', 'scored', 'unknown'}, sorted(lost)

    def test_the_query_site_is_load_bearing(self, rules, uses):
        # Measured, and it turns out this site contributes nothing at all to
        # the dead-rule direction: every name reached by a `.foo` selector is
        # reached some other way too. Its whole value is the other direction.
        # Without it the six handles below have no evidence of being handles,
        # and each is reported as an element rendering unstyled. They are not:
        # every one sits on an element already styled by a sibling class and
        # exists so that querySelector can find it again.
        assert not self._lost(rules, uses, sites=SITES - {'query'})
        names, _p = all_used(sites=SITES - {'query'})
        blind = sorted(n for n in names
                       if n not in rules and n not in hooks(names))
        assert blind == ['edit-save', 'edit-type', 'edit-who', 'rc-detail',
                         'shot-header-btn', 'team-card-body'], blind

    def test_filling_the_holes_is_load_bearing(self, rules, uses):
        # Three, and the small number is the interesting part. `is-${type}`
        # does not need filling to be reachable: the token still carries the
        # prefix `is-` in front of the hole, and the prefix alone covers all
        # forty-one rules. What filling buys is the cases where the hole is at
        # the *front* of the token, where the prefix is empty and the name is
        # only knowable by substituting -- `${tone}` and its kind.
        lost = self._lost(rules, uses, fill=False)
        assert lost == {'accent', 'off', 'resumable'}, sorted(lost)


class TestEachGuardIsLoadBearing:
    """The four quieter guards, measured the same way. Three of them fire.
    The fourth is inert today and is kept anyway, and the reason it is kept
    without a test is written where it is kept."""

    def test_the_first_argument_rule_is_load_bearing(self, rules, uses):
        # `classList.toggle(cls, force)` takes one class and then a boolean.
        # Reading past the comma invents elements, which is the *silent*
        # direction: an invented use can cover a genuinely dead rule and stop
        # it ever being reported.
        #
        # Three today, and they are their own evidence. `link` comes from
        # `toggle('is-link', placement === 'link')`, where the second argument
        # happens to be a string that looks exactly like a class. The other two
        # are `contains(...)` compared against prose -- 'Everything' is a word
        # off a button label, and no stylesheet will ever have a rule for it.
        names, _p = all_used(first_arg_only=False)
        live, _p2 = uses
        assert set(names) - set(live) == {'Everything', 'link', 'ok'}, sorted(
            set(names) - set(live))

    def test_descending_into_media_blocks_is_load_bearing(self, rules):
        # The print stylesheet and every responsive override live inside
        # `@media`. Skipping them loses twelve rules outright, which sends the
        # names that only ever had a rule there into the unstyled list -- the
        # loud direction, so this guard would at least announce its own
        # failure. `.pass-node` is one of the twelve: the hover affordance this
        # audit added is inside `@media (hover: hover)`, because a hover state
        # on a touch screen is a state you cannot leave.
        flat = all_declared(nested=False)
        lost = set(rules) - set(flat)
        assert len(lost) >= 10, sorted(lost)
        assert {'no-print', 'pass-node'} <= lost, sorted(lost)

    def test_stripping_css_comments_is_load_bearing(self, rules):
        # This one was written expecting it to be inert and it is not, and the
        # way it fails is worth keeping the note for. The guess was that an
        # unstripped comment would invent declarations. It does -- seven of
        # them, and they are not commented-out rules at all but ordinary prose:
        # a comment naming `report.js` or `app.css` reads as a declaration of
        # `.js` and `.css`, and each one becomes a permanent false entry on the
        # dead-rule list that no amount of searching the repo can explain.
        #
        # The larger half was not guessed. Leaving comments in *deletes* seven
        # real rules, because the brace walk decides whether to descend by
        # asking if the prelude starts with `@`. A comment sitting above an
        # `@media` line is part of that prelude, so the prelude starts with `/`
        # instead, the block is taken for an ordinary selector, and every rule
        # inside it is skipped. Four of the seven are the `area-*` names that
        # place the whole wide-screen report grid.
        uncommented = set(all_declared(comments=False))
        assert len(set(rules) - uncommented) >= 5, sorted(
            set(rules) - uncommented)
        assert len(uncommented - set(rules)) >= 5, sorted(
            uncommented - set(rules))

    def test_stripping_javascript_comments_is_inert_today_and_stays(self, uses):
        # Kept deliberately without a test that it changes anything, because
        # measured against this repo it does not: no comment in thirty-one
        # served files holds a `class="..."` or a `.selector` string that is
        # not also live somewhere real. That is a fact about how this repo's
        # authors comment, not a property of JavaScript.
        #
        # It stays because of which way it would be wrong. A comment that
        # invents a use is the silent direction: the invented use covers a
        # genuinely dead rule, the rule stops being reported, and nobody ever
        # learns it was there -- and commenting a component out rather than
        # deleting it is the single most likely way for that to start being
        # true. A guard that is inert in the silent direction is exactly the
        # kind worth paying for, so this assertion records only that it is
        # currently inert, so the day it stops being inert is visible.
        names, _p = all_used(comments=False)
        live, _p2 = uses
        assert set(names) == set(live)
