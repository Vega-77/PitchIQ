"""What a process_match run tells the coach page: the step it is on and the time left.

The run writes one document, `teams/{t}/matches/{m}/cvRun/progress`, through the
Admin SDK; the coach page listens to it and draws the progress bar. The coach
asks for a run with `cvRun/request` (which shirts are ours); `process_match
--requests` lists those, with the command to run for each.

The steps, their labels and how long each took on the processing PC are in
`shared/run-steps.json`, which the coach page reads too, so the estimate before
a run and the time left during one come from the same numbers.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STEPS_FILE = REPO / 'shared' / 'run-steps.json'
WRITE_EVERY_S = 60  # progress within a step is written at most this often
TRUSTED = 0.1  # past this share of a step, its own pace says how long the rest takes


def load_steps() -> list[dict]:
    return json.loads(STEPS_FILE.read_text(encoding='utf-8'))['steps']


def step_of(name: str) -> str | None:
    """The coach page's step for one of process_match's commands (`Run.py` names)."""
    if name == 'halves':
        return 'halves'
    m = re.fullmatch(r'(pano|pass|map|stats)_[a-z]+([12])', name)
    if m:
        return {'pano': 'panorama'}.get(m[1], m[1]) + f'_h{m[2]}'
    if re.match(r'(transfer|pincal|cal_health|camera)_', name):
        return 'calibrate'
    if name in ('merge', 'map_report', 'publish'):
        return 'publish'
    return None


def pass_fraction(log: Path) -> float | None:
    """How far a half's pass is, from its log: the player features over the frames,
    then each piece shipped and bridged. None before the log says how big it is."""
    try:
        lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    sized = re.compile(r'HALF \S+\s+(\d+)\.\.(\d+)\s+(\d+) pieces')
    start = max((i for i, s in enumerate(lines) if sized.search(s)), default=None)
    if start is None:
        return None
    head = sized.search(lines[start])
    lo, hi, n = (int(x) for x in head.groups())
    covered = shipped = bridged = 0
    for s in lines[start + 1 :]:
        if 'HALF DONE' in s:
            return 1.0
        f = re.search(r'players\s+(\d+)\.\.(\d+) features', s)
        if f:
            covered += int(f[2]) - int(f[1]) + 1
        shipped += ' shipped ' in s
        bridged += ' hybrid ' in s
    feats = min(1.0, covered / max(1, hi - lo + 1))
    return min(0.99, 0.2 * feats + 0.4 * min(1.0, shipped / n) + 0.4 * min(1.0, bridged / n))


class Progress:
    """The run's progress on the match, or nothing at all without a site."""

    def __init__(self, client, team: str | None, match: str | None, clock=time.time):
        self.ref = None
        if client is not None and team and match:
            self.ref = (
                (client.collection('teams').document(team).collection('matches').document(match))
                .collection('cvRun')
                .document('progress')
            )
        self.steps = load_steps()
        self.keys = [s['key'] for s in self.steps]
        self.clock = clock
        self.started = clock()
        self.state = 'running'
        self.step = self.keys[0]
        self.step_t0 = self.started
        self.fraction = 0.0
        self.done: list[str] = []
        self.message = ''
        self.pin_halves: list[str] = []
        self.wrote = 0.0

    # ------------------------------------------------------------ what the run says

    def begin(self, key: str) -> None:
        """A step starts: every step before it is done (or was done before this run)."""
        if key == self.step and self.state == 'running':
            return
        i = self.keys.index(key)
        self.done = self.keys[:i]
        self.step, self.step_t0, self.fraction = key, self.clock(), 0.0
        self.state, self.message, self.pin_halves = 'running', '', []
        self.write(force=True)

    def at(self, fraction: float) -> None:
        self.fraction = max(self.fraction, min(0.99, fraction))
        self.write()

    def waiting_for_pins(self, halves: list[str], why: str = '') -> None:
        if self.state != 'pins' or self.message != why:
            self.done = self.keys[: self.keys.index('pins')]
            self.step, self.step_t0, self.fraction = 'pins', self.clock(), 0.0
            self.state, self.message, self.pin_halves = 'pins', why, halves
            self.write(force=True)

    def stopped(self, message: str) -> None:
        self.state, self.message = 'stopped', message
        self.write(force=True)

    def finished(self) -> None:
        self.state, self.done, self.step, self.fraction = 'done', list(self.keys), '', 1.0
        self.write(force=True)

    # ------------------------------------------------------------ the time left

    def expected(self, key: str) -> float:
        lo, hi = next(s['min'] for s in self.steps if s['key'] == key)
        return 60.0 * (lo + hi) / 2

    def left_s(self) -> float:
        """Seconds of work left, the coach's own pinning not counted."""
        if self.state == 'done':
            return 0.0
        i = self.keys.index(self.step)
        later = sum(self.expected(k) for k in self.keys[i + 1 :])
        took = self.clock() - self.step_t0
        if self.fraction >= TRUSTED:
            now = took * (1 - self.fraction) / self.fraction
        else:
            want = self.expected(self.step)
            now = max(want - took, 0.1 * want)
        return later + now

    def doc(self) -> dict:
        left = self.left_s()
        eta = None
        if self.state == 'running':
            eta = datetime.now(timezone.utc) + timedelta(seconds=left)
        return {
            'state': self.state,
            'step': self.step,
            'fraction': round(self.fraction, 3),
            'done': self.done,
            'leftMin': round(left / 60),
            'etaAt': eta,
            'message': self.message,
            'pinHalves': self.pin_halves,
            'startedAt': datetime.fromtimestamp(self.started, timezone.utc),
        }

    def write(self, force: bool = False) -> None:
        if self.ref is None or (not force and self.clock() - self.wrote < WRITE_EVERY_S):
            return
        from google.cloud import firestore

        try:
            self.ref.set(self.doc() | {'updatedAt': firestore.SERVER_TIMESTAMP})
            self.wrote = self.clock()
        except Exception as e:  # the coach's progress bar is never worth a 10-hour run
            print(f'(progress not written to the site: {e})', flush=True)


def read_requests(client) -> list[dict]:
    """Every match a coach has asked to have processed and that is not done."""
    out = []
    for t in client.collection('teams').stream():
        for m in t.reference.collection('matches').stream():
            run = m.reference.collection('cvRun')
            req = run.document('request').get()
            if not req.exists:
                continue
            prog = run.document('progress').get()
            state = (prog.to_dict() or {}).get('state') if prog.exists else None
            if state == 'done':
                continue
            d, r = m.to_dict() or {}, req.to_dict() or {}
            out.append(
                dict(team=t.id, match=m.id, opponent=d.get('opponentName', '?'),
                     date=d.get('date', '?'), video=d.get('videoUrl', ''), ours=r.get('ours'),
                     state=state or 'not started')
            )  # fmt: skip
    return out
