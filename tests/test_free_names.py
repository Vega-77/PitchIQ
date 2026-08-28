"""Identifiers a browser module uses that nothing in it binds.

There is no build step here, no bundler and no linter, so a name a module
references but never binds is caught by nothing. `node --check` reads syntax
and a free identifier is perfectly good syntax. The Python suite never
evaluates browser JS. The JS suite does not import `calibrate/` at all. The
failure is a `ReferenceError` at runtime, on whatever code path a person
happens to reach by hand -- which is to say, it ships.

It has shipped twice.

`calibrate/calibrate.js` drew the pitch overlay from `L` and `W`, and d7d25f5
deleted `const { length_m: L, width_m: W } = pitchDims();` while leaving eight
references to them behind. For eight days every redraw past the fourth placed
landmark threw, and because `draw()` calls the overlay before `drawPoints`,
the throw also erased every point the coach had clicked.

`coach/review.js` called `seekReview(...)` on the goal-disagreement rows. The
function is `reviewSeek`; the name was transposed when the review block was
lifted out of `coach.js`. Clicking a row -- the one place two independent
records of the same match visibly conflict -- threw instead of seeking.

Neither is a subtle mistake. Both are invisible without a gate.

## Scope-blind, on purpose

Every binding occurrence anywhere in a file goes into one flat set, and scope
is not modelled at all. A name bound in some other function counts as bound
here. That is deliberate: over-approximating the bindings means the scan can
only ever miss a free name, never invent one. It cannot report a name that is
merely out of scope, because it does not know what scope is. It can only
report a name bound *nowhere in the file* -- which is exactly what `L`, `W`
and `seekReview` were.

So a clean run is not proof the modules are sound. A dirty run is proof they
are not.
"""
import io
import re

import pytest

from test_contrast_floor import JS_DIRS, ROOT

# Words that are never a reference to a binding.
KEYWORDS = set("""
await break case catch class const continue debugger default delete do else
export extends finally for function if import in instanceof let new of return
static super switch this throw try typeof var void while with yield async get
set true false null undefined arguments constructor from as
""".split())

# What the browser hands every module. This describes the platform, not this
# repo, so unlike every other allowlist here it is NOT checked backwards: a
# module that stops calling `fetch` has not thereby made this list stale.
GLOBALS = set("""
globalThis window document console navigator location history screen self top
parent Math JSON Object Array Number String Boolean Symbol BigInt Function
Promise Set Map WeakMap WeakSet Date RegExp Error TypeError RangeError Proxy
Reflect Infinity NaN isNaN isFinite parseFloat parseInt encodeURIComponent
decodeURIComponent encodeURI decodeURI atob btoa structuredClone queueMicrotask
setTimeout clearTimeout setInterval clearInterval requestAnimationFrame
cancelAnimationFrame fetch Request Response Headers AbortController FormData
URL URLSearchParams Blob File FileReader Image Audio Video CustomEvent Event
KeyboardEvent MouseEvent PointerEvent DragEvent Node Element HTMLElement
HTMLCanvasElement SVGElement DOMParser XMLHttpRequest MutationObserver
IntersectionObserver ResizeObserver performance crypto localStorage
sessionStorage getComputedStyle matchMedia alert confirm prompt Intl
TextEncoder TextDecoder Uint8Array Uint8ClampedArray Int32Array Float32Array
Float64Array ArrayBuffer DataView OffscreenCanvas Path2D CanvasGradient
ImageData MediaRecorder ClipboardItem devicePixelRatio innerWidth innerHeight
scrollY scrollX
""".split())

# Globals a *page* puts there with a script tag, which is a fact about this
# repo and is therefore checked in both directions: the name must still be
# referenced by some module, and the tag must still be on the page named here.
PAGE_GLOBALS = {
    'ort': ('xg-sandbox/index.html', 'onnxruntime-web'),
}

# `name(` after one of these opens a condition, not a parameter list.
BLOCK_HEADS = {'if', 'while', 'for', 'switch', 'catch', 'return', 'typeof',
               'else', 'do', 'function', 'new', 'delete', 'void', 'await',
               'yield', 'in', 'of', 'instanceof'}

# After one of these a `/` opens a regex; after a value it is division.
REGEX_AFTER = {'return', 'typeof', 'case', 'in', 'of', 'do', 'else', 'new',
               'delete', 'void', 'instanceof', 'yield', 'await', 'throw'}

# The lookbehind matters: without it the `e` of `1e-9` reads as an identifier,
# and every exponent in the repo becomes a finding.
WORD = re.compile(r'(?<![\w$])[A-Za-z_$][\w$]*')
TOKEN = re.compile(r'[\w$]+')


def blank(src):
    """Comments, strings and regex literals out; `${...}` interiors kept.

    Template literals nest -- a `<li>${x}</li>` built inside another one is
    ordinary here -- so this keeps a stack rather than scanning for the next
    backtick. A flat scan reported forty words of English prose as free names.
    Regex literals have to go too, or `/^pen_(left|right)_/` contributes
    `pen_`, `left` and `right`. Length is preserved so line numbers stay true.
    """
    out = []
    i, n = 0, len(src)
    stack = [['code', 0]]          # ['code', brace depth] | ['tpl']
    last, lastword = '', ''

    def hush(text):
        return ''.join(ch if ch == '\n' else ' ' for ch in text)

    while i < n:
        top = stack[-1]
        c = src[i]

        if top[0] == 'tpl':
            if c == '\\':
                out.append(hush(src[i:i + 2]))
                i += 2
            elif src[i:i + 2] == '${':
                out.append('  ')
                stack.append(['code', 0])
                last, lastword = '(', ''
                i += 2
            elif c == '`':
                out.append(' ')
                stack.pop()
                last, lastword = ')', ''
                i += 1
            else:
                out.append('\n' if c == '\n' else ' ')
                i += 1
            continue

        two = src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(hush(src[i:j]))
            i = j
        elif two == '/*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(hush(src[i:j]))
            i = j
        elif c in '"\'':
            j = i + 1
            while j < n and src[j] != c and src[j] != '\n':
                j += 2 if src[j] == '\\' else 1
            out.append(hush(src[i:min(j + 1, n)]))
            last, lastword = ')', ''
            i = min(j + 1, n)
        elif c == '`':
            out.append(' ')
            stack.append(['tpl'])
            i += 1
        elif c == '/' and opens_regex(last, lastword):
            j, klass = i + 1, False
            while j < n:
                ch = src[j]
                if ch == '\\':
                    j += 2
                    continue
                if ch == '\n':
                    break
                if ch == '[':
                    klass = True
                elif ch == ']':
                    klass = False
                elif ch == '/' and not klass:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            out.append(hush(src[i:min(j, n)]))
            last, lastword = ')', ''
            i = min(j, n)
        elif c.isalnum() or c in '_$':
            word = TOKEN.match(src, i).group(0)
            out.append(word)
            last, lastword = word[-1], word
            i += len(word)
        else:
            if c == '{':
                top[1] += 1
            elif c == '}':
                if top[1] == 0 and len(stack) > 1:
                    out.append(' ')
                    stack.pop()
                    last, lastword = ')', ''
                    i += 1
                    continue
                top[1] -= 1
            out.append(c)
            if not c.isspace():
                last, lastword = c, ''
            i += 1

    return ''.join(out)


def opens_regex(last, lastword):
    """Is the `/` about to be read the start of a regex rather than a divide?"""
    if last == '':
        return True
    if lastword:
        return lastword in REGEX_AFTER
    return last not in ')]}'


def closer(code, start):
    """Index of the `)` matching the `(` at `start`, or -1."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == '(':
            depth += 1
        elif code[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def before_initialiser(chunk):
    """The pattern half of one declarator, up to its top-level `=`.

    Splitting at the *first* `=` would stop at a destructuring default and
    lose every name after it: `const { a = 1, b } = opts` would bind `a`
    alone and report `b` as free. Four real option bags in this repo are
    written that way.
    """
    depth = 0
    for i, ch in enumerate(chunk):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif (depth == 0 and ch == '=' and chunk[i + 1:i + 2] != '='
                and (i == 0 or chunk[i - 1] not in '=!<>')):
            return chunk[:i]
    return chunk


def declarator_names(code):
    """Names bound by const/let/var, splitting the list at top-level commas."""
    found = set()
    for m in re.finditer(r'\b(?:const|let|var)\s', code):
        i = m.end()
        depth, start, chunks = 0, i, []
        while i < len(code):
            ch = code[i]
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch in ';\n':
                break
            elif depth == 0 and ch == ',':
                chunks.append(code[start:i])
                start = i + 1
            i += 1
        chunks.append(code[start:i])
        for chunk in chunks:
            head = before_initialiser(chunk)
            head = re.sub(r'\b(?:of|in)\b.*', '', head, flags=re.S)
            # `{ a: b }` binds b, not a; `{ a }` binds a.
            head = re.sub(r'[A-Za-z_$][\w$]*\s*:', ' ', head)
            found.update(WORD.findall(head))
    return found


def param_names(code):
    """Every identifier inside a parameter list, over-approximating freely.

    Defaults are swept up with the parameters -- `(a = FALLBACK)` binds
    `FALLBACK` as far as this is concerned. That is the over-approximation
    working as intended: it costs a missed finding, never a false one.
    """
    found = set()
    for m in re.finditer(r'\)\s*=>', code):
        end, depth = m.start(), 0
        for i in range(end, -1, -1):
            if code[i] == ')':
                depth += 1
            elif code[i] == '(':
                depth -= 1
                if depth == 0:
                    found.update(WORD.findall(code[i:end]))
                    break
    for m in re.finditer(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*=>', code):
        found.add(m.group(1))
    for m in re.finditer(r'\b(?:function\s*\*?\s*([A-Za-z_$][\w$]*)?|'
                         r'(?<![.\w$])([A-Za-z_$][\w$]*))\s*\(', code):
        name = m.group(1) or m.group(2)
        if name in BLOCK_HEADS and not m.group(1):
            continue
        close = closer(code, m.end() - 1)
        if close < 0:
            continue
        tail = code[close + 1:close + 40].lstrip()
        if not (tail.startswith('{') or m.group(1) is not None
                or tail.startswith('=>')):
            continue
        if name:
            found.add(name)
        found.update(WORD.findall(code[m.end():close]))
    for m in re.finditer(r'\bcatch\s*\(([^)]*)\)', code):
        found.update(WORD.findall(m.group(1)))
    return found


def import_names(code):
    """Names an import clause or a class declaration brings into the file."""
    found = set()
    # Both halves of `{ mount as mountVideo }`. The local binding is the
    # second; the first is not a reference either and would read as free.
    for m in re.finditer(r'\b(?:import|export)\s*\{([^}]*)\}', code):
        found.update(WORD.findall(m.group(1)))
    for m in re.finditer(r'\bimport\s+(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)', code):
        found.add(m.group(1))
    for m in re.finditer(r'\bclass\s+([A-Za-z_$][\w$]*)', code):
        found.add(m.group(1))
    return found


def references(code):
    """Identifiers read as values.

    A property access, an object key, a statement label and the target of a
    labelled `break` are all identifiers that are not reads of a binding. No
    module here uses a label today; the case is handled because the first one
    that does would otherwise be reported, and a gate that cries wolf on
    correct code gets deleted rather than fixed.

    Maps each name to the first line it is read on, so a failure says where
    to look rather than only what to look for.
    """
    out = {}
    line, pos = 1, 0
    for m in WORD.finditer(code):
        line += code.count('\n', pos, m.start())
        pos = m.start()
        j = m.start() - 1
        while j >= 0 and code[j] in ' \t\r\n':
            j -= 1
        if j >= 0 and code[j] == '.':
            continue                      # property access, or `?.`
        end = j + 1
        while j >= 0 and (code[j].isalnum() or code[j] in '_$'):
            j -= 1
        if code[j + 1:end] in ('break', 'continue'):
            continue                      # a label, not a value
        k = m.end()
        while k < len(code) and code[k] in ' \t\r\n':
            k += 1
        if code[k:k + 1] == ':' and code[k:k + 2] != '::':
            continue                      # object key, or a label
        out.setdefault(m.group(0), line)
    return out


def bindings(code):
    return declarator_names(code) | param_names(code) | import_names(code)


def free_names(src, allow=()):
    """[(name, line)] for every identifier this source reads and never binds."""
    code = blank(src)
    bound = bindings(code) | KEYWORDS | GLOBALS | set(allow)
    refs = references(code)
    return sorted((n, refs[n]) for n in refs if n not in bound)


def modules():
    out = []
    for d in JS_DIRS:
        out.extend(sorted((ROOT / d).rglob('*.js')))
    return out


def source(path):
    return io.open(path, encoding='utf-8', newline='').read()


def rel(path):
    return str(path.relative_to(ROOT)).replace('\\', '/')


def test_no_free_identifiers():
    """Every name a site module reads is bound somewhere in that module."""
    bad = []
    for path in modules():
        for name, line in free_names(source(path), PAGE_GLOBALS):
            bad.append('%s:%d  %s' % (rel(path), line, name))
    assert not bad, (
        'these names are read but bound nowhere in their own file, and will '
        'throw ReferenceError the moment the line runs:\n  '
        + '\n  '.join(bad))


def test_page_globals_are_still_real():
    """Each allowed page global is still used, and still loaded by its page.

    The other direction of the same gate. An allowlist entry for a global
    nobody references any more is a hole held open for no reason, and one
    whose script tag has gone is a module about to throw.
    """
    used = set()
    for path in modules():
        used.update(name for name, _ in free_names(source(path)))

    stale = sorted(n for n in PAGE_GLOBALS if n not in used)
    assert not stale, (
        'allowed as page globals but referenced by no module any more: %s'
        % ', '.join(stale))

    for name, (page, marker) in sorted(PAGE_GLOBALS.items()):
        html = io.open(ROOT / page, encoding='utf-8', newline='').read()
        assert marker in html, (
            '%s is allowed because %s loads %s, and that script tag is gone'
            % (name, page, marker))


def test_the_scan_actually_scanned_something():
    """The two-empty-sets trap: no modules, or no references, passes vacuously."""
    found = modules()
    assert len(found) >= 25, 'only %d site modules found' % len(found)

    bound = refs = 0
    for path in found:
        code = blank(source(path))
        assert code.strip(), '%s blanked away to nothing' % rel(path)
        bound += len(bindings(code))
        refs += len(references(code))
    assert bound > 500, 'only %d bindings across the site' % bound
    assert refs > 500, 'only %d references across the site' % refs


def test_a_planted_free_name_is_found():
    """A test that cannot fail proves nothing. This is the shape of both bugs."""
    src = 'const a = 1;\nfunction draw() { return a + ghost; }\n'
    assert free_names(src) == [('ghost', 2)]


def test_the_shipped_bug_would_have_been_caught():
    src = ('function drawPitchOverlay(ctx) {\n'
           '    const poly = (pts) => pts.forEach((pt) => ctx.lineTo(...pt));\n'
           '    poly([[0, 0], [L, 0], [L, W], [0, W]]);\n'
           '}\n')
    assert [n for n, _ in free_names(src)] == ['L', 'W']

    src = ('function reviewSeek(s) { return s; }\n'
           'row.addEventListener("click", () => seekReview(1));\n')
    assert [n for n, _ in free_names(src)] == ['row', 'seekReview']


def test_prose_is_not_a_reference():
    """Comments, strings, regexes and template text are text, not code."""
    src = '\n'.join([
        '// The ghost in this comment is prose.',
        '/* And the phantom in this one. */',
        'const s = "a spectre in a string";',
        "const t = 'a wraith in another';",
        'const r = /^pen_(left|right)_corner$/;',
        'const u = `a poltergeist in a template`;',
        'const v = 1e-9 / 2;',
    ])
    assert free_names(src) == []


def test_nested_templates_do_not_leak_their_words():
    """A template built inside a `${...}` hole is still a template."""
    src = ('const rows = [1];\n'
           'const html = `<ul>${rows.map((x) => `<li>Your ${x} thing</li>`)'
           '.join("")}</ul>`;\n')
    assert free_names(src) == []


def test_destructuring_defaults_bind_what_follows_them():
    """`const { a = 1, b } = opts` binds `b` too. Four option bags rely on it."""
    src = ('const opts = {};\n'
           'const { first = 1, second = null, third } = opts;\n'
           'use(first, second, third);\n')
    assert [n for n, _ in free_names(src)] == ['use']


def test_import_aliases_bind_both_halves():
    src = 'import { mount as mountVideo } from "./video.js";\nmountVideo();\n'
    assert free_names(src) == []


@pytest.mark.parametrize('src', [
    'const a = 1; label: for (;;) { break label; }',
    'const o = { key: 1, other: 2 }; use(o.key, o.other);',
    'try { risky(); } catch (err) { report(err); }',
    'for (const [k, v] of Object.entries({})) use(k, v);',
    'export function f(a, { b, c: renamed }) { return a + b + renamed; }',
])
def test_shapes_that_bind_without_looking_like_it(src):
    """Every construct here binds names. None of them may read as free."""
    allowed = {'use', 'risky', 'report'}
    assert [n for n, _ in free_names(src) if n not in allowed] == []
