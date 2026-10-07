# -*- coding: utf-8 -*-
"""Every page hands a screen reader a list of regions. This checks the list.

Landmarks are the table of contents a screen reader offers when someone asks
for one: banner, navigation, main, complementary, contentinfo. Nobody sighted
ever sees this list, which is exactly why it rots -- a page can fall out of it
entirely and every pixel still looks right.

Three things had to be measured rather than assumed, and each one changed the
answer:

**A page with no `<main>` puts everything outside every landmark.** Six of
these seven pages wrap their views in `<main class="shell">`. `live-tagging`
wrapped them in nothing, so the tool a tagger holds for ninety minutes offered
one region -- the tally rail -- and the entire tagging surface was in none of
them. That is the page where this matters most and the only page where it was
missing.

**Two landmarks of one type are ambiguous only when both are on screen.**
Counting `<aside>` per file says the coach page has three unnamed
complementary landmarks and wants names for all three. They are one per tab --
`#tab-matches`, `#tab-roster`, `#tab-staff` -- and `showTab` swaps them, so a
screen reader announces one "complementary" and there is nothing to confuse it
with. Naming them would have been work done against a measurement that was
wrong. Counting per *screen* found the one real case instead: the tab strip,
the only `<nav>` on the site with no name, sharing `view-main` with the site
nav in the footer. So a screen here is a view, and on one page a view
subdivided by its tabs -- two levels of switching, not one.

**`<header>` is a banner only when nothing sectioning is above it.** Most
`<header>` elements on this site are section headers nested in `<main>`, and a
tag count would report half a dozen duplicate banners that do not exist.

The one thing here that is not about screen readers: `.tag-main` carries a
height because `.view` is `height: 100%`, and a percentage resolves against
its parent. Adding the wrapper without the height collapses all three views to
nothing. Neither file can see that coupling, so it is pinned from both ends.

The walk comes from `test_heading_outline` rather than being copied. That gate
argues the point itself: two copies of a walk means the ways it can lie are
proved against a scan that is not the one running.
"""
import re
import unittest

from test_contrast_floor import HTML, read
from test_heading_outline import VIEWS, decomment, spans

# The one view that splits further. `coach/coach.js:3741` `showTab` hides two
# of these three whenever it shows the other, independently of which view is
# up, so `view-main` is three screens rather than one.
TABS = {'coach/index.html': {'view-main': ['tab-matches', 'tab-roster',
                                           'tab-staff']}}

# The full-bleed tool, which has no site chrome and correctly no banner.
FULLSCREEN = 'live-tagging/index.html'
SHELLED = [rel for rel in HTML if rel != FULLSCREEN]

IMPLICIT = {'main': 'main', 'nav': 'navigation', 'aside': 'complementary'}
SECTIONING = ('article', 'aside', 'nav', 'section', 'main')
ROLES = {'banner', 'navigation', 'main', 'complementary', 'contentinfo',
         'region', 'form', 'search'}

# Unique per page, so there is nothing for a name to tell them apart from.
# Naming these is noise a screen reader reads out on every page.
UNIQUE = ('main', 'banner', 'contentinfo')

# What the scan finds today, so that a scan which quietly stops finding things
# fails instead of passing everything.
TOTALS = {'index.html': 5, 'coach/index.html': 10, 'player/index.html': 6,
          'live-tagging/index.html': 2, 'halftime/index.html': 4}


def whole(tag, attrs):
    """The key that turns `spans` into a plain walk over every element."""
    return tag, attrs


def name_of(attrs, ids):
    """The accessible name, or None -- which is a real answer, not a gap."""
    m = re.search(r'\baria-label="([^"]*)"', attrs)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r'\baria-labelledby="([^"]+)"', attrs)
    if m:
        # A pointer at an id that is not on the page names nothing. There is
        # no fallback -- the landmark is simply unnamed, which is why this
        # resolves the pointer rather than trusting that it was written.
        hit = [i for i in m.group(1).split() if i in ids]
        return ' '.join(hit) or None
    return None


def role_of(tag, attrs, start, sect):
    """The landmark role of one element, or None if it is not a landmark."""
    m = re.search(r'\brole="([^"]+)"', attrs)
    if m:
        return m.group(1) if m.group(1) in ROLES else None
    if tag in IMPLICIT:
        return IMPLICIT[tag]
    if tag in ('header', 'footer'):
        # Nested inside anything sectioning, this is that section's header
        # and not the page's. Neither tag is itself sectioning, so an element
        # cannot match its own span here.
        if any(lo <= start < hi for lo, hi in sect):
            return None
        return 'banner' if tag == 'header' else 'contentinfo'
    if tag in ('section', 'form'):
        named = 'aria-label' in attrs or 'aria-labelledby' in attrs
        return ('region' if tag == 'section' else 'form') if named else None
    return None


def landmarks(src):
    """(role, name, start, end) for every landmark, in document order."""
    els = spans(src, whole)
    sect = [(lo, hi) for (tag, _), lo, hi in els if tag in SECTIONING]
    ids = set(re.findall(r'\bid="([^"]+)"', src))
    out = [(role_of(tag, attrs, lo, sect), name_of(attrs, ids), lo, hi)
           for (tag, attrs), lo, hi in els]
    return sorted([r for r in out if r[0]], key=lambda r: r[2])


def switched(rel):
    """Every id on the page that something hides."""
    out = list(VIEWS[rel])
    for ids in TABS.get(rel, {}).values():
        out += ids
    return out


def screens(rel):
    """(label, ids showing) for each distinct thing a person can look at."""
    tabs = TABS.get(rel, {})
    for view in VIEWS[rel] or [None]:
        for tab in tabs.get(view, [None]):
            here = [i for i in (view, tab) if i]
            yield '/'.join(here) or '(page)', here


def on_screen(rel, src, showing):
    """The landmarks a person has while looking at `showing`.

    A landmark inside a box that is not showing is not on screen. A landmark
    inside no box at all -- the shell, the site nav, the footer -- is on
    screen in every one of them.
    """
    hidden = [(lo, hi) for k, lo, hi in spans(src, ident)
              if k in switched(rel) and k not in showing]
    return [(role, name) for role, name, lo, _ in landmarks(src)
            if not any(lo_ <= lo < hi for lo_, hi in hidden)]


def ident(_tag, attrs):
    m = re.search(r'\bid="([^"]+)"', attrs)
    return m.group(1) if m else None


def page(rel):
    return decomment(read(rel))


class TestEveryPageHasOneMain(unittest.TestCase):
    """Zero is the defect that was found; two would be a defect as well."""

    def test_every_page_has_exactly_one(self):
        for rel in HTML:
            roles = [r for r, _, _, _ in landmarks(page(rel))]
            self.assertEqual(roles.count('main'), 1,
                             '%s has %d <main>' % (rel, roles.count('main')))

    def test_the_fullscreen_tool_is_the_one_that_had_none(self):
        """It is full-bleed rather than shelled; that is the whole
        difference, and it is not a reason to sit outside every landmark."""
        self.assertIn('<main class="tag-main">', read(FULLSCREEN))


class TestNoScreenIsAmbiguous(unittest.TestCase):
    """A name is needed exactly when two of a type share a screen."""

    def test_duplicates_on_one_screen_are_named_and_distinct(self):
        for rel in HTML:
            src = page(rel)
            for label, showing in screens(rel):
                by = {}
                for role, name in on_screen(rel, src, showing):
                    by.setdefault(role, []).append(name)
                for role, names in by.items():
                    if len(names) < 2:
                        continue
                    where = '%s / %s / %s: %s' % (rel, label, role, names)
                    self.assertNotIn(None, names, where)
                    self.assertEqual(len(set(names)), len(names), where)

    def test_the_landmarks_that_are_unique_carry_no_name(self):
        for rel in HTML:
            for role, name, _, _ in landmarks(page(rel)):
                if role in UNIQUE:
                    self.assertIsNone(name, '%s %s named %r'
                                      % (rel, role, name))

    def test_the_coach_asides_are_one_per_tab_not_three_at_once(self):
        """The near miss. Per file they look like three unnamed landmarks
        wanting names; per screen there is never more than one."""
        rel, src = 'coach/index.html', page('coach/index.html')
        every = [r for r, _, _, _ in landmarks(src)]
        self.assertEqual(every.count('complementary'), 3, every)
        for label, showing in screens(rel):
            here = [r for r, _ in on_screen(rel, src, showing)]
            self.assertLessEqual(here.count('complementary'), 1, label)


class TestTheTabsAreASecondLevelOfSwitching(unittest.TestCase):
    """`screens` claims a view can subdivide. That is a claim about running
    code, so it is checked against the running code in both directions."""

    def test_each_named_panel_is_in_the_markup(self):
        for rel, views in TABS.items():
            src = read(rel)
            for ids in views.values():
                for i in ids:
                    self.assertIn('id="%s"' % i, src, (rel, i))

    def test_the_javascript_switches_exactly_these(self):
        js = read('coach/coach.js')
        m = re.search(r'const TABS = \[([^\]]*)\]', js)
        self.assertIsNotNone(m, 'coach.js no longer declares TABS')
        named = re.findall(r"'([^']+)'", m.group(1))
        self.assertEqual(['tab-%s' % n for n in named],
                         TABS['coach/index.html']['view-main'])

    def test_it_hides_the_ones_it_is_not_showing(self):
        """Without this line the panels would all be on screen together and
        the three asides really would need names."""
        js = read('coach/coach.js')
        self.assertIn("classList.toggle('hidden', name !== wanted)", js)

    def test_all_but_one_start_hidden(self):
        src = read('coach/index.html')
        starts = [bool(re.search(r'id="%s"[^>]*\bclass="[^"]*hidden' % i, src))
                  for i in TABS['coach/index.html']['view-main']]
        self.assertEqual(starts.count(False), 1, starts)


class TestTheWrapperCarriesItsHeight(unittest.TestCase):
    """`.view` is a percentage height, so the wrapper added between it and
    `<body>` has to have a height or all three views collapse. Nothing else
    in the suite would notice that being deleted."""

    def test_tag_main_has_a_height(self):
        css = read('live-tagging/tagging.css')
        m = re.search(r'\.tag-main\s*\{([^}]*)\}', css)
        self.assertIsNotNone(m, 'tagging.css has no .tag-main rule')
        self.assertIn('height', m.group(1))

    def test_the_views_still_depend_on_it(self):
        """If `.view` stops being a percentage height, the rule above stops
        being load-bearing and should be revisited rather than left as
        something nobody can explain."""
        css = read('live-tagging/tagging.css')
        m = re.search(r'\n\.view\s*\{([^}]*)\}', css)
        self.assertIsNotNone(m, 'tagging.css has no .view rule')
        self.assertIn('height: 100%', m.group(1))


class TestTheTopLevelPair(unittest.TestCase):
    """Banner and contentinfo, which a tag count gets wrong."""

    def test_the_shelled_pages_have_one_of_each(self):
        for rel in SHELLED:
            roles = [r for r, _, _, _ in landmarks(page(rel))]
            self.assertEqual(roles.count('banner'), 1, rel)
            self.assertEqual(roles.count('contentinfo'), 1, rel)

    def test_the_fullscreen_tool_has_neither(self):
        """No site chrome, so no site banner. A borrowed one would announce a
        header that is not there."""
        roles = [r for r, _, _, _ in landmarks(page(FULLSCREEN))]
        self.assertNotIn('banner', roles)
        self.assertNotIn('contentinfo', roles)

    def test_a_nested_header_is_not_a_banner(self):
        """Measured, so the rule is known to change the answer rather than
        never firing: twenty-one `<header>`/`<footer>` tags across these
        pages, twelve of them landmarks. The other nine are section headers
        inside `<main>`, and a scan counting tags would announce every one of
        them as a duplicate banner."""
        tags = landmark = 0
        for rel in HTML:
            src = page(rel)
            tags += len([1 for (tag, _), _, _ in spans(src, whole)
                         if tag in ('header', 'footer')])
            landmark += len([1 for r, _, _, _ in landmarks(src)
                             if r in ('banner', 'contentinfo')])
        self.assertGreater(tags, landmark, (tags, landmark))


class TestTheScanCouldBeLying(unittest.TestCase):
    """Every assertion above is worth what this scan is worth."""

    def test_it_finds_the_landmarks_that_are_there(self):
        got = {rel: len(landmarks(page(rel))) for rel in HTML}
        self.assertEqual(got, TOTALS)

    def test_a_commented_out_landmark_does_not_count(self):
        src = '<body><!-- <nav aria-label="Old"></nav> --><main></main></body>'
        self.assertEqual([r for r, _, _, _ in landmarks(decomment(src))],
                         ['main'])

    def test_the_real_pages_reach_the_scan_with_the_comments_gone(self):
        """The case above decomments by hand, so it stays green even if the
        pages stop being decommented on the way in -- which is a real way to
        break this: live-tagging's wrapper comment quotes
        `<main class="shell">`, markup that appears nowhere on that page.
        The second assertion is the guard: if no page has a comment left to
        strip, the first one is checking nothing.
        """
        for rel in HTML:
            self.assertNotIn('<!--', page(rel), rel)
        self.assertIn('<!--', read(FULLSCREEN))

    def test_it_sees_a_landmark_declared_by_role(self):
        """No element on the site does this today, which is the reason to
        check it: the scan must not be reading tag names alone."""
        src = '<div role="navigation" aria-label="Sideways"></div>'
        self.assertEqual(landmarks(src)[0][:2], ('navigation', 'Sideways'))

    def test_a_role_that_is_not_a_landmark_is_not_one(self):
        self.assertEqual(landmarks('<div role="dialog"></div>'), [])

    def test_hiding_a_view_takes_its_landmarks_with_it(self):
        """The tally rail lives in `view-live` and nowhere else."""
        rel, src = FULLSCREEN, page(FULLSCREEN)
        live = on_screen(rel, src, ['view-live'])
        setup = on_screen(rel, src, ['view-setup'])
        self.assertIn(('complementary', 'What has been tagged so far'), live)
        self.assertNotIn('complementary', [r for r, _ in setup])

    def test_an_empty_name_is_not_a_name(self):
        self.assertIsNone(landmarks('<nav aria-label="  "></nav>')[0][1])

    def test_a_name_pointing_nowhere_is_not_a_name(self):
        src = '<h2 id="here">Here</h2><nav aria-labelledby="gone"></nav>'
        self.assertIsNone(landmarks(src)[0][1])
        src = src.replace('gone', 'here')
        self.assertEqual(landmarks(src)[0][1], 'here')


if __name__ == '__main__':
    unittest.main()
