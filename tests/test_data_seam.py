"""A value parked on an element, and the code that comes back for it.

An id is looked up and a class is styled. A data attribute is neither. It is a
value written onto an element by one piece of code and picked up later by
another, and in this repo the two halves are routinely in different files, and
in the sharpest case in a different language on a different machine.

Nothing checked that the halves still meet, and both directions can rot:

  * Written and never read. The attribute costs a few bytes and nothing else,
    so it survives every rename around it. `data-mark-id` was written onto
    every tick and every moment button the timeline builds -- two lines in
    `assets/timeline.js` -- and read by nothing, in no file of any type, at any
    point in the history. The click handler on the very next line already
    closes over the whole mark, which is how every other dynamically built
    element here carries its behaviour. Both writes are gone.

  * Read and never written. `el.dataset.thing` on an element nobody wrote
    `data-thing` onto is `undefined`, and `undefined` in a comparison is
    simply false. No error, no log: the tab never switches, the rail never
    highlights, the row never matches. This is the direction that hurts, and
    it is the reason a rename is dangerous -- `dataset.railGroup` and
    `data-rail-group` are the same name written two ways, and only the DOM
    knows it.

Ten attributes, both directions at zero, which is exactly when a check like
this is worth pinning. A gate written over a mess only records the mess.

The scanner
-----------

    markup     data-x="..." in a served .html file
    template   data-x="..." inside a JS template string, the same markup
               written from the other side
    assign     el.dataset.x = ... -- the attribute never appears as text
    read       el.dataset.x in any other position
    selector   [data-x] and [data-x="v"] in a querySelector
    harness    the same two shapes in tests/*.js

Measured, by switching each site off and reading what changed:

    site off        +dead  +ghost   names
      markup            0      2    event, tab
      template          0      1    act
      assign            0      2    signature, target
      read              3      0    signature, tab, target
      selector          2      0    cluster, same
      harness           0      0
      (css)             0      0
      (comments)        0      0

`selector` is worth a word, because the shape it has to survive is the one a
class-name scanner needed a whole extra site for:

    ?.querySelector(`[data-cluster="${clusterId}"] ${control}`)
    control: `[data-same="${row.cluster.cluster_id}"]`

Both are template holes, and both are found anyway -- the attribute's *name*
sits to the left of the hole, so a scan that stops at the `]` or the `=` never
has to guess what the hole contains. `data-cluster` and `data-same` are read
here and nowhere else, so without this site two live attributes are reported
dead.

Two sites were measured and are not here
----------------------------------------

Not overlooked -- measured, found to scan nothing at all, and left out, since
a site with no input is machinery rather than safety:

  * Stylesheets. Eight of them, roughly six thousand lines, and **zero**
    attribute selectors. `test_no_stylesheet_reads_a_data_attribute` below
    holds that measurement, so the day one appears the gate says to put the
    site back rather than quietly calling a live attribute dead.
  * Comment stripping. The only mentions of a data attribute inside a comment
    are in `assets/rail.js`, in backticks, which no site here matches.

The harness site is inert today and stays anyway
------------------------------------------------

`tests/*.js` contributes three reads, and switching it off changes no verdict:
`act` and `event` are both read by the site as well. By the ordinary rule an
inert guard in this direction -- the direction that fails loudly -- is dead
machinery and should go.

It stays, because this is not an ordinary guard. It is the scanner's universe,
and this repo has already paid for getting that wrong once: the id scanner
walked the seven frontend directories only, could not see `tests/smoke.test.js`
reaching into the markup by name, and reported three live calibrate buttons
dead. Their ids were deleted on its word. A false `dead` is acted on, which is
what makes it cost more than no scanner at all -- so the universe is widened
first and justified afterwards, not the other way round.

The vocabulary, and its four copies
-----------------------------------

`data-event` is the one attribute whose value is not free text. It is a member
of a fixed set, and that set is written out four times: nine buttons in
`live-tagging/index.html`, the `EVENTS` map in `assets/events.js`, the
`request.resource.data.type` allowlist in `firestore.rules`, and -- for the
period markers only -- a second copy in `assets/report.js`, which has no
imports by design so that `tests/video.test.js` can cover it.

`assets/events.js` names the duplication in its own comment and calls it
unavoidable, because rules are a separate language and the server has to
validate independently. Unavoidable is not the same as unchecked. A button
whose type the rules reject writes nothing, and it fails at the side of a
pitch, in the second half, with nobody able to read a console.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CSS = ['assets/app.css', 'assets/landing.css', 'coach/coach.css',
       'player/player.css', 'live-tagging/tagging.css', 'halftime/halftime.css',
       'calibrate/calibrate.css', 'xg-sandbox/sandbox.css']
HTML = ['index.html', 'coach/index.html', 'player/index.html',
        'live-tagging/index.html', 'halftime/index.html',
        'calibrate/index.html', 'xg-sandbox/index.html']
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime',
           'calibrate', 'xg-sandbox']

SITES = ('markup', 'template', 'assign', 'read', 'selector', 'harness')

# `data-` is not anchored on a word boundary by itself: `metadata-bearing` in a
# comment in `tests/flow.test.js` is one lookbehind away from entering the
# attribute set as a phantom.
ATTR = re.compile(r'(?<![\w-])data-([a-z][a-z0-9-]*)\s*[=\]"\']')
DATASET_SET = re.compile(r'\.dataset\.([A-Za-z][A-Za-z0-9]*)\s*=(?!=)')
DATASET_ANY = re.compile(r'\.dataset\.([A-Za-z][A-Za-z0-9]*)')
SELECTOR = re.compile(r'\[data-([a-z][a-z0-9-]*)[\]=]')

# Types the client writes that are not a key of any map in `assets/events.js`,
# with the reason and the write site. `test_pinned_extra_types_are_still
# _written` holds each one to it, so a pin cannot outlive the code it excuses.
#
# The file is part of the pin, not decoration on it. `assets/db.js` is the only
# file that writes to the log collection at all, and `type: 'sub'` also appears
# in `assets/report.js`, where it labels a mark the report *derives* from a
# substitution rather than one anybody sends. A pin satisfied by a lookalike in
# another file is a pin that keeps passing after the writer it names is gone.
EXTRA_CLIENT_TYPES = {
    'sub': ('assets/db.js',
            'written as `kind: sub, type: sub` for a substitution; it is not '
            'a tap-able event and has no entry in EVENTS'),
}

# `source` is the field where the rules are deliberately wider than the client.
# `assets/db.js` says so in its own docstring: the log is written with
# `live_tag` today, and the rule already admits the two values the pipeline
# will use on the day it is allowed to append.
FUTURE_SOURCES = {'cv_candidate', 'reviewer_confirmed'}


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def js_files():
    out = []
    for d in JS_DIRS:
        out += sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                      for p in (ROOT / d).glob('*.js'))
    return out


def harness_files():
    return sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                  for p in (ROOT / 'tests').glob('*.js'))


def dashed(camel):
    """`railGroup` -> `rail-group`, the DOM's own spelling rule."""
    return re.sub(r'([A-Z])', lambda m: '-' + m.group(1).lower(), camel)


def scan(drop=()):
    """Every data attribute, where it is written and where it is read."""
    on = lambda s: s not in drop  # noqa: E731
    written, got = {}, {}

    def note(bag, name, where):
        bag.setdefault(name, []).append(where)

    if on('markup'):
        for rel in HTML:
            for m in ATTR.finditer(read(rel)):
                note(written, m.group(1), rel)

    def reads_of(rel, src, label):
        for m in SELECTOR.finditer(src):
            note(got, m.group(1), '%s (%s)' % (rel, label))
        assigned = {m.start() for m in DATASET_SET.finditer(src)}
        for m in DATASET_ANY.finditer(src):
            if m.start() not in assigned:
                note(got, dashed(m.group(1)), '%s (%s)' % (rel, label))

    for rel in js_files():
        src = read(rel)
        if on('template'):
            for m in ATTR.finditer(src):
                note(written, m.group(1), rel)
        if on('assign'):
            for m in DATASET_SET.finditer(src):
                note(written, dashed(m.group(1)), rel)
        if on('read') and on('selector'):
            reads_of(rel, src, 'js')
        elif on('read'):
            assigned = {m.start() for m in DATASET_SET.finditer(src)}
            for m in DATASET_ANY.finditer(src):
                if m.start() not in assigned:
                    note(got, dashed(m.group(1)), rel)
        elif on('selector'):
            for m in SELECTOR.finditer(src):
                note(got, m.group(1), rel)

    if on('harness'):
        for rel in harness_files():
            reads_of(rel, read(rel), 'harness')

    return written, got


WRITTEN, GOT = scan()


# --------------------------------------------------------------- both ways

class TestAttributeSeam(unittest.TestCase):
    """Every attribute written is read, and every attribute read is written."""

    def test_no_attribute_is_written_and_never_read(self):
        dead = sorted(n for n in WRITTEN if n not in GOT)
        self.assertEqual(dead, [], (
            'these data attributes are written and read by nothing: %s. '
            'Either something stopped reading them, or they were never read '
            '-- `data-mark-id` in assets/timeline.js was the latter, on every '
            'tick and every moment button, for its whole life.'
            % ', '.join('data-' + n + ' (' + ', '.join(sorted(set(
                WRITTEN[n]))) + ')' for n in dead)))

    def test_no_attribute_is_read_and_never_written(self):
        ghost = sorted(n for n in GOT if n not in WRITTEN)
        self.assertEqual(ghost, [], (
            'these data attributes are read and written by nothing: %s. '
            'A read of an attribute nobody wrote is `undefined`, and a '
            'comparison against `undefined` is quietly false -- the branch '
            'never fires and nothing is logged.'
            % ', '.join('data-' + n + ' (' + ', '.join(sorted(set(
                GOT[n]))) + ')' for n in ghost)))

    def test_every_name_survives_the_round_trip(self):
        """`data-rail-group` and `railGroup` have to agree, or both halves
        of this scan are talking about different attributes."""
        for name in sorted(set(WRITTEN) | set(GOT)):
            camel = re.sub(r'-([a-z])', lambda m: m.group(1).upper(), name)
            self.assertEqual(dashed(camel), name, name)
            self.assertRegex(name, r'^[a-z][a-z0-9-]*$', name)

    def test_the_scan_found_something(self):
        """The anti-vacuum guard. Every assertion above passes trivially if
        the scan returns nothing, which is what a broken regex looks like."""
        self.assertGreaterEqual(len(WRITTEN), 8, sorted(WRITTEN))
        self.assertGreaterEqual(len(GOT), 8, sorted(GOT))
        self.assertGreaterEqual(sum(len(v) for v in WRITTEN.values()), 60)


# ------------------------------------------------------------------- sites

class TestSites(unittest.TestCase):
    """Each site earns its place by being switched off."""

    def deltas(self, site):
        written, got = scan(drop=(site,))
        dead = sorted(n for n in written if n not in got)
        ghost = sorted(n for n in got if n not in written)
        return dead, ghost

    def test_markup_site_is_load_bearing(self):
        dead, ghost = self.deltas('markup')
        self.assertEqual(ghost, ['event', 'tab'], ghost)
        self.assertEqual(dead, [])

    def test_template_site_is_load_bearing(self):
        dead, ghost = self.deltas('template')
        self.assertEqual(ghost, ['act'], ghost)
        self.assertEqual(dead, [])

    def test_assign_site_is_load_bearing(self):
        """`rail.js` writes `dataset.signature` and `dataset.target` and the
        strings `data-signature` and `data-target` appear nowhere at all."""
        dead, ghost = self.deltas('assign')
        self.assertEqual(ghost, ['signature', 'target'], ghost)

    def test_read_site_is_load_bearing(self):
        dead, ghost = self.deltas('read')
        self.assertEqual(dead, ['signature', 'tab', 'target'], dead)

    def test_selector_site_is_load_bearing(self):
        """And specifically: it is what keeps the two template-hole selectors
        in `coach/coach.js` from reading as dead attributes."""
        dead, ghost = self.deltas('selector')
        self.assertEqual(dead, ['cluster', 'same'], dead)

    def test_the_hole_shape_is_matched_by_name_not_by_value(self):
        coach = read('coach/coach.js')
        self.assertIn('[data-cluster="${', coach)
        self.assertIn('[data-same="${', coach)
        for name in ('cluster', 'same'):
            self.assertIn(name, [m.group(1) for m
                                 in SELECTOR.finditer(coach)])

    def test_harness_site_has_input_even_though_it_is_inert(self):
        """It changes no verdict today. It is kept because it is the
        scanner's universe, and the id scanner's narrower one cost three
        live buttons -- the docstring above tells that story."""
        found = []
        for rel in harness_files():
            src = read(rel)
            found += [m.group(1) for m in SELECTOR.finditer(src)]
            assigned = {m.start() for m in DATASET_SET.finditer(src)}
            found += [dashed(m.group(1)) for m in DATASET_ANY.finditer(src)
                      if m.start() not in assigned]
        self.assertGreaterEqual(len(found), 3, found)
        for name in found:
            self.assertIn(name, WRITTEN, name)

    def test_no_stylesheet_reads_a_data_attribute(self):
        """The measurement that justifies leaving the CSS site out. When this
        fails it is not a defect in the sheet -- it means the site has input
        now and belongs in `scan()`, before the next attribute is called dead
        on a scanner that cannot see it."""
        for rel in CSS:
            hits = sorted({m.group(1) for m in SELECTOR.finditer(read(rel))})
            self.assertEqual(hits, [], (
                '%s selects on %s. Add a `css` site to scan() -- otherwise '
                'an attribute read only by the stylesheet reads as dead.'
                % (rel, ', '.join(hits))))


# -------------------------------------------------------- the vocabulary

def object_keys(src, decl):
    """The top-level keys of the object literal that `decl` opens."""
    assert src.count(decl) == 1, decl
    start = src.index(decl) + len(decl)
    depth, i = 1, start
    while depth:
        if src[i] in '{[(':
            depth += 1
        elif src[i] in '}])':
            depth -= 1
        i += 1
    flat = re.sub(r'\{[^{}]*\}', '', src[start:i - 1])
    return [m.group(1) for m
            in re.finditer(r'^\s*([a-z][a-z0-9_]*)\s*:', flat, re.M)]


def quoted(text):
    return re.findall(r"'([a-z][a-z0-9_]*)'", text)


def rules_list(field):
    """The allowlist `firestore.rules` pins `field` to.

    The list wraps onto the next line whenever the field name is long enough,
    and `cardColor` sits inside a ternary besides, so the gap between `in` and
    the bracket is whitespace of any kind and cannot be a literal.
    """
    src = read('firestore.rules')
    hits = re.findall(r'request\.resource\.data\.%s\s+in\s*\[([^\]]*)\]'
                      % field, src)
    assert len(hits) == 1, '%s: %d allowlists in firestore.rules' % (
        field, len(hits))
    return quoted(hits[0])


EVENTS_JS = read('assets/events.js')
EVENTS = object_keys(EVENTS_JS, 'export const EVENTS = {')
PERIODS = object_keys(EVENTS_JS, 'export const PERIOD_LABELS = {')
CARDS = object_keys(EVENTS_JS, 'export const CARD_COLOURS = {')
BUTTONS = re.findall(r'data-event="([a-z_]+)"', read('live-tagging/index.html'))


class TestVocabulary(unittest.TestCase):
    """The four copies of the tagging vocabulary, held against each other."""

    def test_every_button_is_an_event_the_app_knows(self):
        unknown = sorted(set(BUTTONS) - set(EVENTS))
        self.assertEqual(unknown, [], (
            'live-tagging/index.html has a button for %s, and EVENTS in '
            'assets/events.js has no entry for it -- the sheet opens with no '
            'label, no side wording and no player prompt.' % unknown))

    def test_every_event_the_app_knows_has_a_button(self):
        unreachable = sorted(set(EVENTS) - set(BUTTONS))
        self.assertEqual(unreachable, [], (
            'EVENTS describes %s and no button in live-tagging/index.html '
            'carries it, so nobody can tag one.' % unreachable))

    def test_every_type_the_client_writes_is_allowed_by_the_rules(self):
        client = set(EVENTS) | set(PERIODS) | set(EXTRA_CLIENT_TYPES)
        rejected = sorted(client - set(rules_list('type')))
        self.assertEqual(rejected, [], (
            'the app can write type %s and firestore.rules rejects it. The '
            'tap fails at the side of a pitch, with nobody able to read a '
            'console.' % rejected))

    def test_the_rules_allow_no_type_the_client_cannot_write(self):
        client = set(EVENTS) | set(PERIODS) | set(EXTRA_CLIENT_TYPES)
        stale = sorted(set(rules_list('type')) - client)
        self.assertEqual(stale, [], (
            'firestore.rules admits type %s and nothing in the app writes '
            'one. Either a feature was removed and the rule was not, or a '
            'writer moved somewhere this test cannot see it.' % stale))

    def test_pinned_extra_types_are_still_written(self):
        """A pin has to keep proving itself, or the list rots into a
        graveyard nobody dares touch."""
        for name, (rel, why) in EXTRA_CLIENT_TYPES.items():
            self.assertIn("type: '%s'" % name, read(rel), (
                '%s is pinned as a client type -- %s -- and %s does not write '
                'it any more. Either follow the writer, or drop the pin and '
                'let the rules drop the type with it.' % (name, why, rel)))

    def test_card_colours_agree_with_the_rules(self):
        self.assertEqual(sorted(CARDS), sorted(rules_list('cardColor')),
                         'CARD_COLOURS: %s' % CARDS)

    def test_the_kinds_db_writes_are_exactly_the_kinds_allowed(self):
        kinds = sorted(set(re.findall(r"kind: '([a-z_]+)'", read('assets/db.js'))))
        self.assertEqual(kinds, sorted(rules_list('kind')), kinds)

    def test_the_only_source_written_is_live_tag(self):
        """The one place the rules are deliberately wider than the client,
        and `assets/db.js` says why in its own docstring."""
        db = read('assets/db.js')
        written = sorted(set(re.findall(r"source: '([a-z_]+)'", db)))
        self.assertEqual(written, ['live_tag'], written)
        allowed = set(rules_list('source'))
        self.assertEqual(allowed - {'live_tag'}, FUTURE_SOURCES, allowed)
        for name in FUTURE_SOURCES:
            self.assertIn(name, db, (
                '%s is allowed by the rules and assets/db.js no longer '
                'explains why it is not written.' % name))

    def test_the_period_labels_report_js_keeps_its_own_copy_of_still_match(self):
        """`assets/report.js` imports nothing by design, so it carries a
        second copy of the period names. Unavoidable is not unchecked."""
        report = read('assets/report.js')
        text = object_keys(report, 'const PERIOD_TEXT = {')
        self.assertEqual(text, PERIODS, text)
        for key in PERIODS:
            shared = re.search(r"^\s*%s: '([^']*)'" % key, EVENTS_JS, re.M)
            mine = re.search(r"^\s*%s: '([^']*)'" % key,
                             report[report.index('const PERIOD_TEXT = {'):],
                             re.M)
            self.assertEqual(shared.group(1), mine.group(1), key)

    def test_the_vocabulary_scan_found_something(self):
        """The anti-vacuum guard: every comparison above is trivially true
        between two empty sets."""
        self.assertEqual(len(BUTTONS), 9, BUTTONS)
        self.assertEqual(len(EVENTS), 9, EVENTS)
        self.assertEqual(len(PERIODS), 4, PERIODS)
        self.assertEqual(len(CARDS), 3, CARDS)
        self.assertEqual(len(rules_list('type')), 14)


if __name__ == '__main__':
    unittest.main()
