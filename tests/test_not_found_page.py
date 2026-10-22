"""404.html: the page GitHub Pages serves for an address that has no page.

Pages serves it at the mistyped address itself (`/PitchIQ/no/such/page`), so a
relative link inside it resolves against that address and breaks. That is why
it is self-contained, and why this checks that every reference in it is rooted
at the site or inline, instead of the shared-stylesheet checks the six app
pages get (tests/test_heading_outline.py leaves it out of their list).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / '404.html').read_text(encoding='utf-8')


def test_every_reference_is_rooted_not_relative():
    refs = re.findall(r'(?:href|src)="([^"]*)"', PAGE)
    assert refs, 'the scan found nothing to check'
    for ref in refs:
        assert ref.startswith(('/PitchIQ/', 'https://')), ref


def test_what_every_page_carries():
    assert '<html lang="en">' in PAGE
    assert '<meta name="description"' in PAGE
    assert '<meta name="robots" content="noindex">' in PAGE
    assert 'rel="icon"' in PAGE
    assert len(re.findall(r'<h1\b', PAGE)) == 1


def test_it_links_home():
    assert re.search(r'<a [^>]*id="home"[^>]*href="/PitchIQ/"', PAGE)


def test_the_files_it_names_exist():
    for ref in re.findall(r'(?:href|src)="/PitchIQ/([^"]+)"', PAGE):
        if ref:
            assert (ROOT / ref).is_file(), ref
