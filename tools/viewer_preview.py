"""Preview the coach's match viewer on this PC, from a viewer export and the game's video.

    python tools/viewer_preview.py <export dir> <video> [port]

Then open http://localhost:8120/tools/viewer-preview.html. The page mounts
`shared/match-viewer.js` as the coach site will, with the export's files in place of
Firestore and the video served from disk.

The server serves the repo (the site's own modules), the export at /view/ and the video at
/video. It answers Range requests, which a browser needs to seek in a video;
Python's own http.server does not. It listens on localhost only and caches nothing.
"""

from __future__ import annotations

import http.server
import mimetypes
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHUNK = 1 << 20


def handler(view: Path, video: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(REPO), **k)

        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/video':
                return self.send_range(video)
            if path.startswith('/view/'):
                name = path[len('/view/') :]
                target = (view / name).resolve()
                if target.parent != view.resolve() or not target.is_file():
                    return self.send_error(404)
                return self.send_range(target)
            return super().do_GET()

        def send_range(self, path: Path):
            size = path.stat().st_size
            start, end = 0, size - 1
            m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    return self.end_headers()
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            else:
                self.send_response(200)
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_header('Content-Type', kind)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            with path.open('rb') as fh:
                fh.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        data = fh.read(min(CHUNK, left))
                        if not data:
                            break
                        self.wfile.write(data)
                        left -= len(data)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    pass  # the browser seeks by dropping a request mid-way

    return Handler


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    view, video = Path(argv[0]), Path(argv[1])
    port = int(argv[2]) if len(argv) > 2 else 8120
    for p in (view / 'index.json', video):
        if not p.exists():
            print(f'{p} is missing', file=sys.stderr)
            return 1
    os.chdir(REPO)
    server = http.server.ThreadingHTTPServer(('localhost', port), handler(view, video))
    print(f'http://localhost:{port}/tools/viewer-preview.html', flush=True)
    server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
