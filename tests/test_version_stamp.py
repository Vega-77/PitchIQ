"""Every local asset reference carries the same cache-busting stamp.

`stamp_version.py` exists because there is no build step: nothing gets a content
hash in its filename, so a browser will happily serve yesterday's JavaScript. Its
own docstring says what happens when the stamping is only half done — *"a
versioned entry point does not version what it imports, so `coach.js?v=4` loads
fresh and then pulls `../assets/auth.js` straight from cache. That failure looks
exactly like a missing export, and it has cost real debugging time more than
once."*

Nothing checked that the stamper had actually run, run over everything, or run
with one number. That last one is the nasty case: a page on v=102 loading a
module that still says v=97 is a *mixed* module graph, which is the precise
failure the tool was written to prevent, arriving with a symptom that points at
the wrong file.

So this reads the frontend the way the browser does and asserts three things:
every local reference is stamped, every stamp agrees, and every stamped path
still exists.

It also checks the stamper's own reach, in both directions. `PAGES` and
`MODULE_DIRS` are hardcoded lists, and its two regexes match only double-quoted
attributes and only single-quoted import specifiers. A new page, a new module
directory, or a quote-style drift does not make the stamper fail — it makes the
stamper quietly skip a file, and the first sign of that is a coach staring at a
stale screen. The same standard `tests/test_field_commands.py` sets for its
document list applies here: a renamed page should fail this test rather than
drop out of it.

No browser, no network, no version bump. This is text.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import stamp_version

REPO = Path(__file__).resolve().parent.parent

# Directories that are not the frontend, kept explicit so the walk below can
# discover a *new* page directory without also wandering into the virtualenv —
# matplotlib and torch both ship HTML and JavaScript of their own.
NOT_BROWSER = {
    '.git', '.github', 'node_modules', '__pycache__', 'PitchIQHelper',
    'cv', 'tests', 'baselines', 'runs', 'scratch_frames',
}

# The same shapes stamp_version.py looks for, but blind to quoting style. What
# the stamper can see minus what is actually there is the drift this catches.
LOOSE_ASSET = re.compile(
    r'(?:href|src)\s*=\s*(["\'])([^"\']+)\1'
)
LOOSE_SPEC = re.compile(
    r'(?:from|import)\s*\(?\s*(["\'])(\.{1,2}/[^"\']+\.js(?:\?v=\d+)?)\1'
)

STAMP = re.compile(r'\?v=(\d+)$')


def _is_local_asset(value: str) -> bool:
    """A reference the stamper is supposed to own: a local .css or .js."""
    if value.startswith(('http:', 'https:', '//', 'data:', 'mailto:', '#')):
        return False
    return value.split('?')[0].endswith(('.css', '.js'))


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def page_paths() -> list[Path]:
    return [REPO / page for page in stamp_version.PAGES]


def module_paths() -> list[Path]:
    out: list[Path] = []
    for directory in stamp_version.MODULE_DIRS:
        out.extend(sorted((REPO / directory).glob('*.js')))
    return out


def browser_dirs_on_disk() -> set[str]:
    """Top-level directories that hold a page or a module, found by looking."""
    found = set()
    for child in sorted(REPO.iterdir()):
        if not child.is_dir() or child.name in NOT_BROWSER:
            continue
        if (child / 'index.html').exists() or any(child.glob('*.js')):
            found.add(child.name)
    return found


def pages_on_disk() -> set[str]:
    found = {'index.html'} if (REPO / 'index.html').exists() else set()
    for name in browser_dirs_on_disk():
        if (REPO / name / 'index.html').exists():
            found.add(f'{name}/index.html')
    return found


def references() -> list[tuple[str, str]]:
    """(file, reference) for every local asset reference in the frontend.

    Both halves of the job in one list — attributes in the pages and relative
    specifiers in the modules — because the whole point is that they have to
    agree with each other, not merely each with itself.
    """
    out = []
    for path in page_paths():
        rel = path.relative_to(REPO).as_posix()
        for _quote, value in LOOSE_ASSET.findall(_read(path)):
            if _is_local_asset(value):
                out.append((rel, value))
    for path in module_paths():
        rel = path.relative_to(REPO).as_posix()
        for _quote, value in LOOSE_SPEC.findall(_read(path)):
            out.append((rel, value))
    return out


class TestEveryReferenceIsStamped(unittest.TestCase):
    def test_nothing_local_is_missing_its_stamp(self):
        bare = [f'{where}: {ref}'
                for where, ref in references() if not STAMP.search(ref)]
        self.assertEqual(bare, [], 'unstamped local reference')

    def test_every_stamp_is_the_same_number(self):
        seen: dict[str, list[str]] = {}
        for where, ref in references():
            match = STAMP.search(ref)
            if match:
                seen.setdefault(match.group(1), []).append(f'{where}: {ref}')
        self.assertEqual(
            len(seen), 1,
            'the frontend is on more than one version at once — a page loading '
            'a module stamped with a different number is a mixed module graph, '
            'which is the failure the stamper exists to prevent: '
            + '; '.join(f'v={v} ({len(w)} refs, e.g. {w[0]})'
                        for v, w in sorted(seen.items())))

    def test_every_stamped_path_still_exists(self):
        missing = []
        for where, ref in references():
            target = (REPO / where).parent / ref.split('?')[0]
            if not target.exists():
                missing.append(f'{where}: {ref}')
        self.assertEqual(missing, [], 'reference to a file that is not there')

    def test_this_check_is_looking_at_something(self):
        # A regex that silently matches nothing would make every assertion
        # above pass. Pin the one edge the stamper's docstring is about: the
        # coach page's entry point, and that entry point's import of auth.js.
        refs = dict(references())
        self.assertIn('coach/index.html', refs, 'the coach page found no refs')
        found = [ref for where, ref in references()
                 if where == 'coach/coach.js' and 'auth.js' in ref]
        self.assertEqual(len(found), 1, 'coach.js -> auth.js was not seen')
        self.assertTrue(STAMP.search(found[0]), found[0])


class TestTheStamperStillReachesEverything(unittest.TestCase):
    def test_the_page_list_matches_what_is_on_disk(self):
        self.assertEqual(set(stamp_version.PAGES), pages_on_disk())

    def test_the_module_directory_list_matches_what_is_on_disk(self):
        self.assertEqual(
            set(stamp_version.MODULE_DIRS), browser_dirs_on_disk())

    def test_no_module_is_hiding_in_a_subdirectory(self):
        # The stamper globs '*.js', which does not recurse.
        nested = []
        for directory in stamp_version.MODULE_DIRS:
            for path in (REPO / directory).rglob('*.js'):
                if path.parent != REPO / directory:
                    nested.append(path.relative_to(REPO).as_posix())
        self.assertEqual(nested, [], 'nested module the stamper never walks')

    def test_the_stamper_sees_every_asset_reference_the_pages_make(self):
        # ASSET_REF matches double-quoted attributes only. A single-quoted one
        # is valid HTML, renders fine, and never gets stamped again.
        for path in page_paths():
            text = _read(path)
            rel = path.relative_to(REPO).as_posix()
            loose = {value.split('?')[0]
                     for _q, value in LOOSE_ASSET.findall(text)
                     if _is_local_asset(value)}
            seen = {m.group(2) for m in stamp_version.ASSET_REF.finditer(text)}
            self.assertEqual(loose - seen, set(), f'{rel}: stamper blind spot')

    def test_the_stamper_sees_every_import_the_modules_make(self):
        # IMPORT_SPEC matches single-quoted specifiers only, the mirror trap.
        for path in module_paths():
            text = _read(path)
            rel = path.relative_to(REPO).as_posix()
            loose = {value.split('?')[0]
                     for _q, value in LOOSE_SPEC.findall(text)}
            seen = {m.group(2) for m in stamp_version.IMPORT_SPEC.finditer(text)}
            self.assertEqual(loose - seen, set(), f'{rel}: stamper blind spot')

    def test_no_page_imports_from_an_inline_module_script(self):
        # The stamper walks .js files for imports and HTML only for attributes,
        # so a relative import written inside <script type="module"> in a page
        # is stamped by nothing at all.
        inline = re.compile(r'<script[^>]*type="module"[^>]*>(.*?)</script>',
                            re.S | re.I)
        offenders = []
        for path in page_paths():
            for body in inline.findall(_read(path)):
                if LOOSE_SPEC.search(body):
                    offenders.append(path.relative_to(REPO).as_posix())
        self.assertEqual(offenders, [], 'inline module import goes unstamped')


class TestTheStamperItself(unittest.TestCase):
    """The detectors above are only worth having if they can fail."""

    def test_an_unstamped_reference_is_reported(self):
        self.assertIsNone(STAMP.search('assets/app.css'))
        self.assertTrue(STAMP.search('assets/app.css?v=7'))

    def test_a_single_quoted_attribute_escapes_the_stamper(self):
        text = "<link rel=\"stylesheet\" href='assets/app.css'>"
        self.assertEqual(list(stamp_version.ASSET_REF.finditer(text)), [])
        self.assertEqual(
            [v for _q, v in LOOSE_ASSET.findall(text)], ['assets/app.css'])

    def test_a_double_quoted_specifier_escapes_the_stamper(self):
        text = 'import { onUser } from "../assets/auth.js";'
        self.assertEqual(list(stamp_version.IMPORT_SPEC.finditer(text)), [])
        self.assertEqual(
            [v for _q, v in LOOSE_SPEC.findall(text)], ['../assets/auth.js'])

    def test_restamping_replaces_rather_than_appends(self):
        # The bump is run over already-stamped files every time, so the regex
        # has to consume the old query string instead of growing a second one.
        page = '<script type="module" src="coach.js?v=41"></script>'
        once = stamp_version.ASSET_REF.sub(r'\1\2?v=42\3', page)
        self.assertEqual(
            once, '<script type="module" src="coach.js?v=42"></script>')
        self.assertEqual(stamp_version.ASSET_REF.sub(r'\1\2?v=42\3', once), once)

        spec = "import { getTeam } from '../assets/db.js?v=41';"
        once = stamp_version.IMPORT_SPEC.sub(r'\1\2?v=42\3', spec)
        self.assertEqual(once, "import { getTeam } from '../assets/db.js?v=42';")
        self.assertEqual(
            stamp_version.IMPORT_SPEC.sub(r'\1\2?v=42\3', once), once)

    def test_a_remote_url_is_left_alone(self):
        text = '<link href="https://fonts.googleapis.com/css2?family=X">'
        self.assertEqual(list(stamp_version.ASSET_REF.finditer(text)), [])
        self.assertEqual(
            [v for _q, v in LOOSE_ASSET.findall(text) if _is_local_asset(v)],
            [])


if __name__ == '__main__':
    unittest.main()
