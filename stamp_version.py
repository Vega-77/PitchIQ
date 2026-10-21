"""Stamp a cache-busting version onto every local asset reference.

    python stamp_version.py 5

There is no build step, so nothing gets a content hash in its filename and
browsers will happily serve yesterday's JavaScript. Adding `?v=N` by hand to the
<link> and <script> tags is only half a fix: a versioned entry point does *not*
version what it imports, so `coach.js?v=4` loads fresh and then pulls
`../shared/auth.js` straight from cache. That failure looks exactly like a
missing export, and it has cost real debugging time more than once.

This walks the whole frontend and stamps both:

  * href/src attributes in the HTML pages,
  * relative import specifiers inside the ES modules themselves, and
  * `@import url('...')` inside the stylesheets (shared/app.css imports its
    parts from shared/styles/, and the same rule applies to a sheet).

Run it before committing a frontend change; bump the number every time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent

PAGES = [
    'index.html',
    'coach/index.html',
    'player/index.html',
    'live-tagging/index.html',
    'halftime/index.html',
    'calibrate/index.html',
]

MODULE_DIRS = [
    'shared',
    'coach',
    'player',
    'live-tagging',
    'halftime',
    'calibrate',
]

# href="shared/app.css?v=3" / src="coach.js?v=3" — local files only, so a CDN
# or Google Fonts URL is left alone.
ASSET_REF = re.compile(r'((?:href|src)=")(?!https?:|//)([^"?]+\.(?:css|js))(?:\?v=\d+)?(")')

# import ... from './x.js'  /  import('./x.js')  — relative specifiers only.
IMPORT_SPEC = re.compile(r"((?:from|import)\s*\(?\s*')(\.{1,2}/[^']+\.js)(?:\?v=\d+)?(')")

# @import url('styles/tokens.css')  — single-quoted, local stylesheets only.
CSS_IMPORT = re.compile(r"(@import\s+url\(\s*')(?!https?:|//)([^'?]+\.css)(?:\?v=\d+)?(')")


def stamp(version: int) -> int:
    changed = 0

    for page in PAGES:
        path = ROOT / page
        text = path.read_text(encoding='utf-8')
        stamped = ASSET_REF.sub(rf'\1\2?v={version}\3', text)
        if stamped != text:
            path.write_text(stamped, encoding='utf-8')
            changed += 1

    for directory in MODULE_DIRS:
        for path in sorted((ROOT / directory).glob('*.js')):
            text = path.read_text(encoding='utf-8')
            stamped = IMPORT_SPEC.sub(rf'\1\2?v={version}\3', text)
            if stamped != text:
                path.write_text(stamped, encoding='utf-8')
                changed += 1
        for path in sorted((ROOT / directory).rglob('*.css')):
            text = path.read_text(encoding='utf-8')
            stamped = CSS_IMPORT.sub(rf'\1\2?v={version}\3', text)
            if stamped != text:
                path.write_text(stamped, encoding='utf-8')
                changed += 1

    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print(__doc__)
        return 2

    version = int(argv[1])
    changed = stamp(version)
    print(f'stamped ?v={version} across {changed} file(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
