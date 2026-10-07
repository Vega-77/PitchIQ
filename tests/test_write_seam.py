"""Every field the browser writes, against the rule that has to admit it.

`firestore.rules` is the whole backend. Eleven `keys().hasOnly([...])` sets and
eight `changed([...])` allowlists name, exactly, what each document may carry;
anything else is denied. `assets/db.js` and `assets/auth.js` are what actually
writes those documents.

`tests/flow.test.js` already runs the legitimate path against the real
emulator — and it imports nothing from `assets/`. Every document it sends is
hand-typed at the call site: `name: 'South Brunswick', coachUids: [COACH.uid]`
and twenty more like it. So each field set exists in three copies, the rules,
the client, and the mimic, and only the mimic is checked. Add a field to
`db.js` without adding it to the rule and the whole suite stays green while the
write fails in front of a coach, at the one moment they cannot retry it.

This closes that. Both sides are read out of the files rather than listed here:

  forward   every field written at a path is in one of the allowlists that
            path's rules grant for that operation. Firestore ORs its `allow`
            rules, so "one of" is the real semantics, not a shortcut.
  backward  every field an allowlist grants is written by somebody, or is
            named in `UNWRITTEN` with a reason. An allowlist checked in one
            direction only rots into a graveyard nobody dares touch, which is
            the argument `tests/test_call_graph.py` makes at length; and a
            grant with no feature behind it is worth knowing about, because it
            is surface area that exists by accident.

What the backward direction found on the day it was written is written down in
`UNWRITTEN`: the rules were drafted for an editing UI that was never built.
Nothing renames a team, edits a player's name or number, or corrects an
opponent or a date — but the rules let a coach do all of it. That is not a
hole, every one of those fields is validated, and it is not nothing either.

Limits, so a clean run is not read as more than it is:

  * This checks field *names* against allowlists. It says nothing about the
    type and range predicates beside them — `personName`, `shirtNumber`,
    `validPosition` and the rest — which `tests/rules.test.js` covers against
    the emulator and this cannot.
  * `playerReports` deliberately has no `hasOnly` (`firestore.rules:704-723`:
    the document is merged with fields the Admin SDK wrote, which never had to
    be listed). It is checked against the key-count cap the rule uses instead.
  * A write reached through two hops of indirection resolves only because the
    resolver knows the four shapes this repo uses. A fifth shape fails as
    unresolved rather than passing quietly — see
    `test_every_write_site_resolves`.
"""
import io
import re
from pathlib import Path

import pytest

from test_free_names import blank

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / 'firestore.rules'
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime']

# setDoc/batch.set may land on a document that already exists, but every one of
# those paths grants `create, update` together, so the distinction costs
# nothing here and buys precision everywhere else: `finalized` is an update and
# only an update, and reading it as a create would let it through the much
# longer create list.
OPS = {
    'setDoc': 'create',
    'addDoc': 'create',
    'batch.set': 'create',
    'updateDoc': 'update',
    'batch.update': 'update',
}

# Paths whose rule grants no closed field set, with the reason it does not.
# Checked backwards too: a path that grows a `hasOnly` has to leave this list.
NO_ALLOWLIST = {
    'teams/{}/matches/{}/playerReports/{}':
        'merged with the cv-prefixed fields cv/publish.py wrote through the '
        'Admin SDK, so the merged result is not a set this file can know; '
        'capped by key count instead',
}

# Fields an allowlist grants that nothing in the browser writes. Every one of
# these is a rule drafted ahead of a screen. Checked in both directions: a
# field listed here that someone starts writing fails as a stale entry.
UNWRITTEN = {
    ('teams/{}', 'update', 'name'):
        'no rename-a-team control exists; the create writes it and nothing '
        'edits it afterwards',
    ('teams/{}', 'update', 'taggerUids'):
        'nothing populates it on create either — assets/db.js:44-50 says why: '
        'the tagger-versus-coach split is an open question about people',
    ('teams/{}', 'update', 'archived'):
        'the seat a team-hiding switch will sit in, per assets/db.js:49',
    ('teams/{}/players/{}', 'update', 'name'):
        'the roster editor changes position and active only; renaming a player '
        'is not a control the coach has',
    ('teams/{}/players/{}', 'update', 'jerseyNumber'):
        'same editor, same gap — a number is fixed at create today',
    ('teams/{}/players/{}', 'update', 'emailLower'):
        'firestore.rules:396 anticipates it ("a student changed email") and '
        'there is no screen for it',
    ('teams/{}/matches/{}', 'create', 'videoUrl'):
        'a match is created before anyone has filmed it; the video fields are '
        'attached later by the update rule',
    ('teams/{}/matches/{}', 'create', 'videoOffsetS'): 'see videoUrl above',
    ('teams/{}/matches/{}', 'create', 'secondHalfVideoS'): 'see videoUrl above',
    ('teams/{}/matches/{}', 'create', 'halfTimeClockS'):
        'written by the half-time tap, never at create',
    ('teams/{}/matches/{}', 'update', 'opponentName'):
        'no edit-match-details form; both fields are set once at create',
    ('teams/{}/matches/{}', 'update', 'date'): 'see opponentName above',
}

# Allowlists that guard the interior of a map field rather than a document.
# The function's set and the browser literal that fills it must agree exactly —
# a key on either side alone is a field the rule silently rejects, or a rule
# clause guarding a key nobody sends.
NESTED = {
    'validXgCheck': ('assets/report.js', 'xgTally'),
}


# --------------------------------------------------------------- firestore.rules

def rules_code(src):
    """`//` comments and single-quoted strings blanked, space for space.

    Not `blank`: rules paths are full of slashes and a JS tokenizer reads
    `match /databases/...` as the start of a regex literal.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                out[i] = ' '
                i += 1
        elif c == "'":
            out[i] = ' '
            i += 1
            while i < n and src[i] != "'":
                out[i] = ' '
                i += 1
            if i < n:
                out[i] = ' '
                i += 1
        else:
            i += 1
    return ''.join(out)


MATCH = re.compile(r'\bmatch\s+(\S+)\s*\{')
ALLOW = re.compile(r'\ballow\s+([a-z, ]+?)\s*:')
FUNC = re.compile(r'\bfunction\s+(\w+)\s*\(')
ALLOWLIST = re.compile(r'(?:hasOnly|changed)\s*\(\s*\[')
CAP = re.compile(r'keys\(\)\.size\(\)\s*<=\s*(\d+)')

def normalise(segments):
    """`/teams/{teamId}/matches/{m}` -> `teams/{}/matches/{}`.

    The outermost `match /databases/{db}/documents` is the database itself and
    is not part of any path the browser writes; drop it.
    """
    parts = [p for seg in segments for p in seg.split('/') if p]
    if parts[:1] == ['databases']:
        del parts[:3]
    return '/'.join('{}' if p.startswith('{') else p for p in parts)


def path_stack(code):
    """offset -> the `match` paths enclosing it, outermost first."""
    starts = {m.end() - 1: m.group(1) for m in MATCH.finditer(code)}
    out, stack, depth = [], [], 0
    for i, c in enumerate(code):
        if c == '{':
            if i in starts:
                stack.append((depth, starts[i]))
            depth += 1
        elif c == '}':
            depth -= 1
            while stack and stack[-1][0] >= depth:
                stack.pop()
        out.append(tuple(p for _, p in stack))
    return out


class Grant:
    """One closed field set, and where it applies."""

    def __init__(self, line, path, ops, fields, func):
        self.line = line
        self.path = path
        self.ops = ops
        self.fields = fields
        self.func = func

    def __repr__(self):
        return 'firestore.rules:%d %s %s' % (
            self.line, self.path or ('fn ' + self.func), '/'.join(self.ops))


def grants():
    src = io.open(RULES, encoding='utf-8').read()
    code = rules_code(src)
    stack = path_stack(code)
    allows = {m.start(): m.group(1) for m in ALLOW.finditer(code)}
    funcs = {m.start(): m.group(1) for m in FUNC.finditer(code)}

    out = []
    for m in ALLOWLIST.finditer(code):
        end = code.index(']', m.end())
        # Names come from the raw text: `blank`-style stripping empties the
        # quoted field names but keeps their offsets, which is the point.
        fields = re.findall(r'\w+', src[m.end():end])
        prior_allow = max((o for o in allows if o < m.start()), default=-1)
        prior_func = max((o for o in funcs if o < m.start()), default=-1)
        if prior_func > prior_allow:
            path, ops, func = '', (), funcs[prior_func]
        else:
            path = normalise(stack[m.start()])
            ops = tuple(v.strip() for v in allows[prior_allow].split(','))
            func = ''
        out.append(Grant(code[:m.start()].count('\n') + 1, path, ops,
                         tuple(fields), func))
    return out


def key_cap(path):
    """The `keys().size() <= N` bound a path's rules put on the document."""
    code = rules_code(io.open(RULES, encoding='utf-8').read())
    stack = path_stack(code)
    found = [int(m.group(1)) for m in CAP.finditer(code)
             if normalise(stack[m.start()]) == path]
    assert len(found) == 1, (
        'expected exactly one key-count cap under %s, saw %r' % (path, found))
    return found[0]


# ------------------------------------------------------------------- javascript

OPEN = {'(': ')', '[': ']', '{': '}'}
CLOSE = {')': '(', ']': '[', '}': '{'}
QUOTES = ('"', "'", '`')


def group(code, start):
    """Offset just past the bracket group opening at `start`."""
    stack, i = [code[start]], start + 1
    while i < len(code) and stack:
        c = code[i]
        if c in OPEN:
            stack.append(c)
        elif c in CLOSE:
            if stack[-1] != CLOSE[c]:
                break
            stack.pop()
        i += 1
    return i


def items(code, lb, raw=None):
    """Top-level comma-separated (start, end) spans inside the group at `lb`.

    `raw` decides whether a final segment is real or a trailing comma: `blank`
    empties a string literal, so `['a', 'b']` ends in a run of spaces that is
    indistinguishable from `[a, b,]` unless the unblanked text is consulted.
    A blanked quote mark is the evidence — a blanked *comment* after a trailing
    comma leaves plenty of text behind and must not count as a segment.
    """
    end = group(code, lb) - 1
    out, depth, start = [], 0, lb + 1
    for i in range(lb + 1, end):
        c = code[i]
        if c in OPEN:
            depth += 1
        elif c in CLOSE:
            depth -= 1
        elif c == ',' and depth == 0:
            out.append((start, i))
            start = i + 1
    if code[start:end].strip() or blanked_string(code, raw, start, end):
        out.append((start, end))
    return out


def blanked_string(code, raw, start, end):
    """True when `blank` emptied a string literal out of this span."""
    if raw is None:
        return False
    return any(raw[i] in QUOTES and code[i] == ' ' for i in range(start, end))


KEY = re.compile(r'^\s*(\w+)\s*:')
SHORT = re.compile(r'^\s*(\w+)\s*$')
SPREAD = re.compile(r'^\s*\.\.\.\s*(.*)$', re.S)


def literal_keys(code, lb, raw=None):
    """(named keys, spread expressions) of the object literal at `lb`."""
    named, spreads = [], []
    for s, e in items(code, lb, raw):
        text = code[s:e]
        m = KEY.match(text) or SHORT.match(text)
        if m:
            named.append(m.group(1))
            continue
        m = SPREAD.match(text)
        if m:
            spreads.append(m.group(1).strip())
            continue
        named.append('?? ' + ' '.join(text.split())[:48])
    return named, spreads


def object_at(code, start, end):
    """The object literal that is the whole of `code[start:end]`, or None."""
    text = code[start:end]
    lb = text.find('{')
    if lb < 0 or text[:lb].strip():
        return None
    return start + lb


class Source:
    def __init__(self, rel):
        self.rel = rel
        self.raw = io.open(ROOT / rel, encoding='utf-8',
                           errors='replace').read()
        # `blank` preserves length, so an offset found in one is valid in the
        # other. Structure is read from the blanked text; string contents — the
        # collection names in a `doc()` call — only from the raw text.
        self.code = blank(self.raw)

    def line(self, offset):
        return self.code[:offset].count('\n') + 1

    def at(self, offset):
        return '%s:%d' % (self.rel, self.line(offset))


def sources():
    out = {}
    for d in JS_DIRS:
        for path in sorted((ROOT / d).rglob('*.js')):
            rel = str(path.relative_to(ROOT)).replace('\\', '/')
            out[rel] = Source(rel)
    return out


CALL = re.compile(r'\b(setDoc|updateDoc|addDoc|batch\.set|batch\.update)\s*\(')
ASSIGN = r'\b(?:const|let|var)\s+%s\s*='
PARAM = r'\bfunction\s+\w+\s*\([^)]*\b%s\b[^)]*\)'
FOR_OF = r'\bfor\s*\(\s*(?:const|let)\s+%s\s+of\s+'


def assignment(src, name, before):
    """(start, end) of the expression assigned to `name` before `before`."""
    found = [m for m in re.finditer(ASSIGN % re.escape(name), src.code)
             if m.end() < before]
    if not found:
        return None
    start = found[-1].end()
    semi = src.code.find(';', start)
    return (start, semi if semi > 0 else len(src.code))


def ref_path(src, start, end, seen=()):
    """Reduce a document-reference expression to `teams/{}/matches/{}`."""
    parts = ref_parts(src, start, end, seen)
    if parts is None:
        return None
    # A `collection()` names a collection; the document written inside it adds
    # the id segment that `doc(collection(db, 'teams'))` leaves implicit.
    if len(parts) % 2:
        parts = parts + ['{}']
    return '/'.join(parts)


def ref_parts(src, start, end, seen=()):
    """Path segments of a reference expression, `{}` for anything computed."""
    text = src.code[start:end]
    lead = len(text) - len(text.lstrip())
    start, text = start + lead, text.strip()
    if not text:
        return None

    if text.startswith('await '):
        return ref_parts(src, start + 6, end, seen)

    call = re.match(r'(?:doc|collection)\s*\(', text)
    if call:
        parts, arglist = [], items(src.code, start + call.end() - 1, src.raw)
        for i, (a, b) in enumerate(arglist):
            seg = src.raw[a:b].strip()
            if seg.startswith("'") and seg.endswith("'"):
                parts.append(seg[1:-1])
            elif seg == 'db':
                continue
            elif i == 0:
                # `doc(collection(db, 'teams'))` — the first argument is
                # itself a reference, and the rest hang off it.
                inner = ref_parts(src, a, b, seen)
                if inner is None:
                    return None
                parts.extend(inner)
            else:
                parts.append('{}')
        return parts

    wrap = re.match(r'getDocs?\s*\(', text)
    if wrap:
        inner = items(src.code, start + wrap.end() - 1, src.raw)
        return ref_parts(src, *inner[0], seen=seen) if inner else None

    member = re.match(r'(\w+)\.(?:ref|docs)\s*$', text)
    if member:
        return ref_parts_of_name(src, member.group(1), start, seen)

    named = re.match(r'(\w+)\s*[($]?', text)
    if named and re.match(r'\w+\s*(\(|$)', text):
        return ref_parts_of_name(src, named.group(1), start, seen)

    return None


def ref_parts_of_name(src, name, before, seen=()):
    if name in seen:
        return None
    seen = seen + (name,)
    span = assignment(src, name, before)
    if span:
        body = src.code[span[0]:span[1]]
        arrow = body.find('=>')
        if arrow >= 0 and '{' not in body[:arrow]:
            return ref_parts(src, span[0] + arrow + 2, span[1], seen)
        return ref_parts(src, *span, seen=seen)
    loop = [m for m in re.finditer(FOR_OF % re.escape(name), src.code)
            if m.end() < before]
    if loop:
        m = loop[-1]
        end = src.code.find(')', m.end())
        return ref_parts(src, m.end(), end, seen)
    return None


PASSTHROUGH = 'passthrough'


def expand(src, all_src, expr, before, seen=()):
    """Keys contributed by a spread or a payload identifier.

    Returns a set of names, or `PASSTHROUGH` when the value is a parameter of
    the enclosing function — the keys are then whatever the callers pass, and
    the caller is a write site in its own right.
    """
    expr = expr.strip().rstrip(',')
    if expr in seen:
        return set()
    seen = seen + (expr,)

    bare = re.match(r'(\w+)\s*$', expr)
    if bare:
        name = bare.group(1)
        span = assignment(src, name, before)
        if span:
            lb = object_at(src.code, *span)
            if lb is None:
                return None
            named, spreads = literal_keys(src.code, lb, src.raw)
            found = set(named)
            # `const patch = { status }; if (...) patch.halfTimeClockS = ...`
            # — the conditional key is as real as the literal one.
            for m in re.finditer(r'\b%s\.(\w+)\s*=(?!=)' % re.escape(name),
                                 src.code[span[1]:before]):
                found.add(m.group(1))
            for s in spreads:
                more = expand(src, all_src, s, before, seen)
                if more in (None, PASSTHROUGH):
                    return more
                found |= more
            return found
        if re.search(PARAM % re.escape(name), src.code[:before]):
            return PASSTHROUGH
        return None

    called = re.match(r'(\w+)\s*\(', expr)
    if called:
        return returned_keys(all_src, called.group(1))

    return None


RETURN = re.compile(r'\breturn\s*\{')


def function_body(all_src, name):
    """(source, start, end) of the body of the function called `name`."""
    for src in all_src.values():
        m = re.search(r'\bfunction\s+%s\s*\(' % re.escape(name), src.code)
        if not m:
            continue
        lb = src.code.find('{', group(src.code, m.end() - 1) - 1)
        return src, lb, group(src.code, lb)
    return None


def returned_keys(all_src, name):
    """Union of the keys of every object literal the function returns."""
    body = function_body(all_src, name)
    if body is None:
        return None
    src, start, end = body
    found = set()
    for m in RETURN.finditer(src.code[start:end]):
        lb = start + m.end() - 1
        named, spreads = literal_keys(src.code, lb, src.raw)
        found |= set(named)
        for s in spreads:
            more = expand(src, all_src, s, lb)
            if more in (None, PASSTHROUGH):
                return more
            found |= more
    return found or None


class Write:
    def __init__(self, where, call, path, fields, note=''):
        self.where = where
        self.call = call
        self.path = path
        self.op = OPS[call]
        self.fields = fields
        self.note = note

    def __repr__(self):
        return '%s %s -> %s %s' % (self.where, self.call, self.path, self.op)


def callers(all_src, name):
    """Object literals passed to `name(...)` anywhere in the site sources."""
    out = []
    for src in all_src.values():
        for m in re.finditer(r'\b%s\s*\(' % re.escape(name), src.code):
            head = src.code.rfind('\n', 0, m.start())
            if re.search(r'\b(function|import)\b', src.code[head:m.start()]):
                continue
            arglist = items(src.code, m.end() - 1, src.raw)
            if not arglist:
                continue
            lb = object_at(src.code, *arglist[-1])
            if lb is None:
                continue
            named, spreads = literal_keys(src.code, lb, src.raw)
            out.append((src.at(m.start()), set(named), spreads))
    return out


def writes(all_src):
    """Every client Firestore write, resolved."""
    out = []
    for src in all_src.values():
        for m in CALL.finditer(src.code):
            lp = m.end() - 1
            arglist = items(src.code, lp, src.raw)
            if len(arglist) < 2:
                out.append(Write(src.at(m.start()), m.group(1), None, None,
                                 'no payload argument'))
                continue
            path = ref_path(src, *arglist[0])
            lb = object_at(src.code, *arglist[1])
            if lb is None:
                found = expand(src, all_src, src.code[arglist[1][0]:
                                                      arglist[1][1]], lp)
                if found is PASSTHROUGH:
                    for where, keys, spreads in callers(
                            all_src, enclosing(src, m.start())):
                        out.append(Write(where, m.group(1), path, keys,
                                         'through ' +
                                         enclosing(src, m.start())))
                    continue
                out.append(Write(src.at(m.start()), m.group(1), path, found))
                continue
            named, spreads = literal_keys(src.code, lb, src.raw)
            fields = set(named)
            note = ''
            broken = False
            for s in spreads:
                more = expand(src, all_src, s, lb)
                if more is PASSTHROUGH:
                    base = set(fields)
                    for where, keys, _ in callers(
                            all_src, enclosing(src, m.start())):
                        out.append(Write(where, m.group(1), path,
                                         base | keys, 'through ' +
                                         enclosing(src, m.start())))
                    broken = True
                    break
                if more is None:
                    fields.add('?? ...' + s.split('(')[0])
                    note = 'unresolved spread'
                else:
                    fields |= more
            if broken:
                continue
            out.append(Write(src.at(m.start()), m.group(1), path, fields, note))
    return out


def enclosing(src, offset):
    """Name of the function the offset sits in."""
    found = list(re.finditer(r'\bfunction\s+(\w+)\s*\(', src.code[:offset]))
    return found[-1].group(1) if found else ''


@pytest.fixture(scope='module')
def all_src():
    return sources()


@pytest.fixture(scope='module')
def rules():
    return grants()


@pytest.fixture(scope='module')
def sites(all_src, rules):
    known = {g.path for g in rules if g.path}
    found = writes(all_src)
    for w in found:
        w.path = canonical(w.path, known)
    return found


def path_matches(client, rule):
    """A rule wildcard matches any one client segment; literals must agree."""
    a, b = client.split('/'), rule.split('/')
    return len(a) == len(b) and all(y == '{}' or x == y for x, y in zip(a, b))


def canonical(path, rule_paths):
    """The rule path that governs a client path, or the path unchanged.

    The client writes literal document ids where the rules declare a wildcard:
    `cvMapping/players` against `match /cvMapping/{doc}`. Anything no rule
    matches is left alone, so an ungoverned write is still reported as one
    rather than being quietly rounded to the nearest rule.
    """
    if path is None or path in rule_paths:
        return path
    hits = sorted(r for r in rule_paths if path_matches(path, r))
    assert len(hits) < 2, 'more than one rule path matches %r: %r' % (
        path, hits)
    return hits[0] if hits else path


def permitted(rules, path, op):
    """The allowlists that could admit a write, and the paths that have none."""
    return [g for g in rules if g.path == path and op in g.ops]


# ------------------------------------------------------------------------ tests

def test_every_write_site_resolves(sites):
    """A shape the resolver cannot read has to say so, not pass quietly."""
    broken = ['%s: %s' % (w.where, w.note or w.fields) for w in sites
              if w.path is None or w.fields is None
              or any(f.startswith('??') for f in w.fields)]
    assert not broken, (
        'these writes could not be reduced to a path and a field set; extend '
        'the resolver rather than dropping them:\n  ' + '\n  '.join(broken))


def test_every_write_lands_on_a_governed_path(sites, rules):
    """A write to a path no rule names is denied, and nothing here says so."""
    known = {g.path for g in rules if g.path}
    stray = sorted({'%s -> %s' % (w.where, w.path) for w in sites
                    if w.path not in known and w.path not in NO_ALLOWLIST})
    assert not stray, (
        'no allowlist governs these paths; either the rule is missing or the '
        'path belongs in NO_ALLOWLIST with a reason:\n  ' + '\n  '.join(stray))


def test_every_written_field_is_permitted(sites, rules):
    """Forward. Add a field to db.js without the rule and this fails."""
    bad = []
    for w in sites:
        if w.path in NO_ALLOWLIST:
            continue
        options = permitted(rules, w.path, w.op)
        if not options:
            bad.append('%s: nothing grants %s on %s' % (w.where, w.op, w.path))
            continue
        # Firestore ORs its allow rules: the write needs one that covers it.
        if any(w.fields <= set(g.fields) for g in options):
            continue
        best = min(options, key=lambda g: len(w.fields - set(g.fields)))
        bad.append('%s writes %s to %s, which no rule grants (%s is the '
                   'closest and is missing %s)' % (
                       w.where, sorted(w.fields), w.path, best,
                       sorted(w.fields - set(best.fields))))
    assert not bad, '\n  '.join([''] + bad)


def test_every_granted_field_is_written(sites, rules):
    """Backward. A grant with no writer is documented or it is a finding."""
    written = {}
    for w in sites:
        written.setdefault((w.path, w.op), set()).update(w.fields)

    bad = []
    for g in rules:
        if not g.path or g.path in NO_ALLOWLIST:
            continue
        # One grant covering `create, update` is satisfied by a writer under
        # either: `setDoc` on a document that may or may not exist yet is the
        # reason both verbs are on the same line in the first place.
        have = set()
        for op in g.ops:
            have |= written.get((g.path, op), set())
        for field in g.fields:
            if field in have or any((g.path, op, field) in UNWRITTEN
                                    for op in g.ops):
                continue
            bad.append('%s grants %r, and nothing writes it' % (g, field))
    assert not bad, (
        '\n  '.join([''] + bad) +
        '\n\nEither wire it up, or add it to UNWRITTEN with the reason.')


def test_unwritten_entries_are_still_unwritten(sites, rules):
    """The other half of the backward check: no stale excuses."""
    written = {}
    for w in sites:
        written.setdefault((w.path, w.op), set()).update(w.fields)
    granted = {(g.path, op, f) for g in rules for op in g.ops
               for f in g.fields if g.path}

    stale = []
    for key in sorted(UNWRITTEN):
        path, op, field = key
        if key not in granted:
            stale.append('%s/%s/%s: no rule grants it any more' % key)
        elif field in written.get((path, op), set()):
            stale.append('%s/%s/%s: something writes it now' % key)
    assert not stale, (
        '\n  '.join([''] + stale) +
        '\n\nDelete the entry. An allowlist of excuses that is never pruned is '
        'the graveyard this check exists to avoid.')


def test_paths_without_an_allowlist_are_the_documented_ones(sites, rules):
    """A path that grows a `hasOnly` has to leave NO_ALLOWLIST."""
    used = {w.path for w in sites}
    granted = {g.path for g in rules if g.path}
    stale = sorted(p for p in NO_ALLOWLIST if p in granted or p not in used)
    assert not stale, (
        'these no longer need an exemption — either a rule now names their '
        'fields, or nothing writes them: %s' % stale)


def test_the_uncapped_document_stays_under_its_cap(sites, all_src):
    """`playerReports` has no allowlist, so the cap is the only bound."""
    path = 'teams/{}/matches/{}/playerReports/{}'
    cap = key_cap(path)
    reports = [w for w in sites if w.path == path]
    assert reports, 'nothing writes a player report any more'
    for w in reports:
        assert len(w.fields) <= cap, (
            '%s writes %d keys and firestore.rules caps the document at %d'
            % (w.where, len(w.fields), cap))
    # The pipeline writes its own on top of these through the Admin SDK, and
    # the cap counts the merged result. Leave it room.
    biggest = max(len(w.fields) for w in reports)
    assert biggest <= cap - 20, (
        'the largest player-report write is %d keys against a cap of %d. The '
        'pipeline merges roughly twenty more on top; raise the cap in '
        'firestore.rules before this gets closer.' % (biggest, cap))


def test_nested_allowlists_match_their_literal_exactly(rules, all_src):
    """A map field's interior, where subset is not good enough.

    `xgCheck` is written whole, so a key the rule does not grant is a rejected
    write and a key nothing sends is a clause guarding nothing.
    """
    by_func = {g.func: g for g in rules if g.func}
    for func, (rel, js) in NESTED.items():
        g = by_func.get(func)
        assert g is not None, '%s no longer holds a closed field set' % func
        keys = returned_keys(all_src, js)
        assert keys, '%s in %s returns no object literal' % (js, rel)
        assert keys == set(g.fields), (
            '%s grants %s and %s writes %s' % (
                g, sorted(g.fields), js, sorted(keys)))


# --------------------------------------------------- the merge-clearing branch

CV_KEYS = re.compile(r'const CV_REPORT_KEYS = \[')
NO_STATS = re.compile(r'if \(!stats\) return Object\.fromEntries\(\s*'
                      r'CV_REPORT_KEYS')


def cv_branches(all_src):
    """(nulled-when-absent, written-when-present) for `cvReportFields`."""
    src = all_src['assets/report.js']
    m = CV_KEYS.search(src.code)
    assert m, 'CV_REPORT_KEYS is gone from assets/report.js'
    lb = src.code.index('[', m.end() - 1)
    # Read the names off the raw text — `blank` empties string interiors — but
    # only the quoted runs, so the comment inside the array stays out of it.
    nulled = set()
    for s, e in items(src.code, lb, src.raw):
        nulled.update(re.findall(r"'([^'\n]+)'", src.raw[s:e]))

    body = function_body(all_src, 'cvReportFields')
    assert body, 'cvReportFields is gone from assets/report.js'
    body_src, start, end = body
    assert NO_STATS.search(' '.join(body_src.code[start:end].split())), (
        'the no-stats branch of cvReportFields no longer nulls every '
        'CV_REPORT_KEYS entry; this check reads that shape')
    written = set()
    for r in RETURN.finditer(body_src.code[start:end]):
        named, _ = literal_keys(body_src.code, start + r.end() - 1, body_src.raw)
        written |= set(named)
    return nulled, written


def test_the_cleared_cv_fields_cover_the_written_ones(all_src):
    """Under a merge, a key that is never nulled is never cleared.

    `publishReports` writes the player report with `merge: true`. A field in
    the with-stats branch that `CV_REPORT_KEYS` does not list survives a coach
    un-mapping the cluster it came from — the player goes on being shown last
    week's numbers under this week's match. `assets/db.js:836-845` records
    being bitten by exactly this once already, and nothing has checked it
    since.
    """
    nulled, written = cv_branches(all_src)
    missing = sorted(written - nulled)
    assert not missing, (
        'cvReportFields writes %s but CV_REPORT_KEYS does not list them, so '
        'un-mapping a player never clears them' % missing)


def test_the_cleared_cv_fields_are_all_real(all_src):
    """And the other way: a name nobody writes is a null nobody needs.

    Three are deliberate — the heatmap, the attacking end and the calibration
    error are written by `cv/publish.py` and cleared only here. Every other
    entry has to be a field this function actually produces.
    """
    nulled, written = cv_branches(all_src)
    pipeline = {'cvHeatmap', 'cvAttackingEnd', 'cvCalibrationErrorM'}
    stray = sorted(nulled - written - pipeline)
    assert not stray, (
        'CV_REPORT_KEYS lists %s, which cvReportFields never writes and '
        'cv/publish.py does not either' % stray)


# ------------------------------------------------------------------ anti-vacuum

def test_the_scan_actually_scanned_something(all_src, sites, rules):
    """A gate that reads nothing passes everything."""
    assert len(all_src) >= 25, 'only %d site modules' % len(all_src)
    assert all(len(s.raw) > 200 for s in all_src.values())

    assert len(sites) >= 25, 'only %d write sites' % len(sites)
    assert len({w.path for w in sites}) >= 8
    assert len({w.where.split(':')[0] for w in sites}) >= 2
    assert all(w.fields for w in sites)

    assert len(rules) >= 19, 'only %d allowlists in firestore.rules' % len(rules)
    assert len({g.path for g in rules if g.path}) >= 8
    assert sum(len(g.fields) for g in rules) >= 100


def test_the_backward_check_has_something_to_check(sites, rules):
    """UNWRITTEN is the finding, not a formality — it must stay live."""
    assert UNWRITTEN, 'the day this empties, delete the mechanism too'
    granted = {(g.path, op, f) for g in rules for op in g.ops
               for f in g.fields if g.path}
    assert set(UNWRITTEN) <= granted


# ------------------------------------------------------------- negative control

def test_the_forward_check_can_fail(sites, rules):
    invented = Write('nowhere:1', 'setDoc', 'teams/{}',
                     {'name', 'coachUids', 'createdAt', 'createdBy',
                      'taggerUids', 'archived', 'sponsor'})
    options = permitted(rules, 'teams/{}', 'create')
    assert options
    assert not any(invented.fields <= set(g.fields) for g in options)


def test_the_path_resolver_can_fail():
    src = Source('assets/db.js')
    assert ref_path(src, 0, 0) is None
    assert ref_parts_of_name(src, 'noSuchReference', len(src.code)) is None


def test_the_literal_reader_can_fail():
    code = "x({ a: 1, ...spread, [dynamic]: 2 })"
    named, spreads = literal_keys(code, code.index('{'))
    assert 'a' in named
    assert spreads == ['spread']
    assert any(n.startswith('??') for n in named)


def test_the_rules_reader_ignores_commented_out_grants():
    code = rules_code("allow create: if x.keys().hasOnly(['a']);\n"
                      "// allow update: if y.keys().hasOnly(['b']);\n")
    assert 'a' in code
    assert 'hasOnly' in code.split('\n')[0]
    assert 'hasOnly' not in code.split('\n')[1]
