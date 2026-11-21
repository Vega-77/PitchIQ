"""Tell whoever runs the pipeline, on their phone, when a run stops, waits or finishes.

    python -m pitchiq notify --setup      once: pick a private topic, send a test push
    python -m pitchiq notify --test       send a test push to the topic set up
    python -m pitchiq notify "text"       send one by hand

A run's stops, its wait for the coach's pins, a restart after a reboot and the end
of a run each call `send`. Every message goes to the run's `run/notify.log`, and,
once `--setup` has been run, to the phone as a push through ntfy (https://ntfy.sh):
a free app, no account, that shows whatever is posted to a topic you subscribe to.
The topic name is the only secret, so `--setup` makes a long random one and keeps it
in `PitchIQ-data/notify.json` (outside the repo). Nothing is sent before that file
exists. A push that fails never stops a run.

The coach gets their own plain-words line on the site's Video processing card
(`run_progress.Progress.stopped`); an email to the coach is for later.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO.parent / 'PitchIQ-data' / 'notify.json'
SERVER = 'https://ntfy.sh'
PRIORITY = {'low': '2', 'default': '3', 'high': '4'}


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def push(title: str, text: str, priority: str = 'default', cfg: dict | None = None) -> str:
    """Post to the phone; '' when sent, else why not (never raises)."""
    cfg = config() if cfg is None else cfg
    if not cfg.get('topic'):
        return 'no phone set up (python -m pitchiq notify --setup)'
    url = f'{cfg.get("server", SERVER).rstrip("/")}/{cfg["topic"]}'
    req = urllib.request.Request(
        url,
        data=text.encode('utf-8'),
        method='POST',
        headers={
            'Title': title.encode('ascii', 'replace').decode(),
            'Priority': PRIORITY.get(priority, '3'),
            'Tags': {'high': 'warning', 'low': 'white_check_mark'}.get(priority, 'soccer'),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return '' if 200 <= r.status < 300 else f'HTTP {r.status}'
    except Exception as e:  # noqa: BLE001  a push must never stop a run
        return f'{type(e).__name__}: {e}'


def send(run_dir: Path | None, title: str, text: str, priority: str = 'default') -> None:
    """Log the message in the run's notify.log and push it to the phone if one is set up."""
    why = push(title, text, priority)
    if run_dir is not None:
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            with open(run_dir / 'notify.log', 'a', encoding='utf-8') as f:
                sent = 'pushed' if not why else f'not pushed: {why}'
                f.write(
                    f'{time.strftime("%Y-%m-%d %H:%M")}  [{priority}] {title}: {text}  ({sent})\n'
                )
        except OSError:
            pass


def setup() -> int:
    cfg = config()
    if not cfg.get('topic'):
        cfg = {'server': SERVER, 'topic': 'pitchiq-' + secrets.token_urlsafe(18)}
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=1), encoding='utf-8')
    print(
        f'In the ntfy app on your phone, subscribe to this topic (Settings: server {cfg["server"]}):\n\n'
        f'    {cfg["topic"]}\n\nKeep it private: anyone with the name can read and post to it.'
    )
    why = push('PitchIQ', 'Test: runs will tell you here when they stop, wait or finish.', cfg=cfg)
    print('test push sent' if not why else f'test push failed: {why}')
    return 1 if why else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq notify',
        description='Phone pushes when a pipeline run stops, waits or finishes (ntfy).',
    )
    ap.add_argument('text', nargs='?', help='a message to send by hand')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--setup', action='store_true', help='pick a private topic, send a test push')
    g.add_argument('--test', action='store_true', help='send a test push')
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.setup:
        return setup()
    text = 'Test from python -m pitchiq notify.' if a.test or not a.text else a.text
    why = push('PitchIQ', text)
    print('sent' if not why else f'not sent: {why}')
    return 1 if why else 0


if __name__ == '__main__':
    raise SystemExit(main())
