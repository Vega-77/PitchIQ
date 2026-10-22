"""The Pages workflow publishes the site and nothing else.

static.yml copies a fixed list of files and folders into `_site/` and uploads
only that. A page added without being added to the list would deploy as a 404;
a folder on the list that is gone would fail the deploy. Either is caught here,
not on the live site.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stamp_version import MODULE_DIRS, PAGES  # noqa: E402

WORKFLOW = (ROOT / '.github' / 'workflows' / 'static.yml').read_text(encoding='utf-8')


def copied():
    m = re.search(r'^\s*cp -r (.+) _site/\s*$', WORKFLOW, re.M)
    assert m, 'no cp -r ... _site/ line in static.yml'
    return m.group(1).split()


class TestTheWorkflowPublishesOnlyTheSite:
    def test_it_uploads_the_collected_folder_not_the_repo(self):
        assert "path: '_site'" in WORKFLOW
        assert "path: '.'" not in WORKFLOW

    def test_every_page_is_copied(self):
        top = {p.split('/')[0] for p in PAGES}
        assert top - set(copied()) == set(), top - set(copied())

    def test_every_folder_pages_load_from_is_copied(self):
        assert set(MODULE_DIRS) - set(copied()) == set()

    def test_the_not_found_page_is_copied(self):
        assert '404.html' in copied()

    def test_everything_copied_exists(self):
        missing = [p for p in copied() if not (ROOT / p).exists()]
        assert missing == [], missing

    def test_nothing_but_the_site_is_copied(self):
        site = {p.split('/')[0] for p in PAGES} | set(MODULE_DIRS) | {'404.html'}
        assert set(copied()) == site, set(copied()) ^ site
