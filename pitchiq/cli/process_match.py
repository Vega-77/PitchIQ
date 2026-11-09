"""From a match video to statistics on the website, in one command.

    python -m pitchiq process_match <video> --game xx --ours lighter --team T --match M
    python -m pitchiq process_match <video> --game xx --ours darker --h1 12:58-55:15 --h2 66:05-111:00 --lines white --team T --match M
    python -m pitchiq process_match <video> --game xx --ours lighter --plan
    python -m pitchiq process_match --list-matches

It runs the same steps that made the published Woodbridge report, one after
another, each skipped when its output is already on disk. A crash or a
reboot costs only the step that was running: run the same command again. A
step's own output goes to `PitchIQ-data/stats/<game>/run/logs/<step>.log`;
this command prints when each one starts and ends.

  1. halves     where each half starts and ends in the video (`--h1/--h2`, or
                `experiments/halves.py`'s guess, to the minute), written into
                `pitchiq/balltrack/halves.json` as game <xx>
  2. camera     the coach's pitch pins (`experiments/pincal.py`): each half
                stitched into one colour panorama of the camera's view, the
                first half's put on the match as soon as it exists; the coach
                pins it on the site (the match page links to the calibrate
                page) and the run waits for the pins (`--no-wait` stops
                instead). If the camera stayed put between the halves the
                pins carry to the second half; if not, it is put up too. A pin
                the others disagree with is named on the pin page and the run
                waits for new ones. Then both halves are calibrated from the
                pins together, and `experiments/cal_health.py` checks each; the
                run stops if a half looks wrong (`--trust-cal` carries on).
                Without --team/--match, pin `pano_rgb.jpg` on the calibrate
                page by hand and save `pins.json` beside it. `--no-pins` uses
                the pin-free calibration (`experiments/autocal_run.py`) instead
  3. pass       the ball and the players over each half (`balltrack/half.py`)
  4. map        the top-down map of each half (`experiments/wholemap.py`)
  5. stats      the pipeline report of each half from the pass's player
                detections (`experiments/cached_report.py half`)
  6. publish    both halves joined, the map's figures put in, and the report
                written to the match on the site (`cached_report merge`,
                `map_report --into`, `publish_report`)

Which kit is ours (`--ours`) the video cannot say. The end we attack in the
first half comes from the map (where each team's keeper and players stand)
unless `--attacks-first` says it; the run stops if the two halves' maps do not
show the teams changing ends.

Roughly 10 hours a game on this PC, plus however long the coach takes to pin:
the panoramas about 25 min a half, the calibration 5 min a half, the pass
2-3 h a half (longer at night), the map 15-30 min a half, the stats about 1 h
a half. Everything runs one step at a time.

Publishing needs PITCHIQ_SA_KEY (the service account key, kept outside the
repo) and the team and match ids; `--list-matches` prints them. Both are
checked before anything long starts. `--tag-log` takes the coach page's log
download for this match and reruns only the stats and the publish.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO.parent / 'PitchIQ-data'
TRACK = REPO / 'pitchiq' / 'balltrack'
HALVES = TRACK / 'halves.json'
HOMOG = TRACK / 'pitch_registration'
H3D = TRACK / 'h3d_hyb90'
DETS = TRACK / 'plab' / 'out'
MAPS = DATA / 'stats' / 'map'
AUTOCAL = DATA / 'autocal'
DOWNLOADS = Path(os.environ.get('PIQ_DOWNLOADS', Path.home() / 'Downloads'))
HELD_OUT = {'nb2', 'pi1', 'wh1', 'co1', 'wb2'}  # as experiments/wholemap.py

FPS = 30.0
SIZE = (1920, 1080)
CAMERA_FRAMES = 1800  # a minute of the first half for the camera alone
CAMERA_TRIES = (0.25, 0.5, 0.75, 0.1, 0.9)  # where in the half to try that minute
MIN_MINUTES = 1.0  # per-player rows shorter than this are left out (Firestore's 1 MB a document)
MAP_PROCS = 8
SITE = os.environ.get('PIQ_SITE', 'https://vega-77.github.io/PitchIQ/')
PIN_POLL_S = 60  # how often the run looks for the coach's pins
PIN_REMIND_S = 1800  # and how often it says it is still waiting
STEPS = ('halves', 'camera', 'pass', 'map', 'stats', 'publish')


class Stop(Exception):
    """A step failed or a check refused: the message says what to do next."""


# ---------------------------------------------------------------- small pieces


def clock(text: str) -> float:
    """'12:58', '1:05:30' or '778' -> seconds."""
    parts = [float(p) for p in text.strip().split(':')]
    s = 0.0
    for p in parts:
        s = 60 * s + p
    return s


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def span(text: str) -> tuple[float, float]:
    """'12:58-55:15' -> (778.0, 3315.0)."""
    m = re.fullmatch(r'\s*([\d:.]+)\s*-\s*([\d:.]+)\s*', text)
    if not m:
        raise argparse.ArgumentTypeError(f'{text!r}: expected start-end, like 12:58-55:15')
    a, b = clock(m.group(1)), clock(m.group(2))
    if b <= a:
        raise argparse.ArgumentTypeError(f'{text!r}: the end is not after the start')
    return a, b


def game_code(text: str) -> str:
    if not re.fullmatch(r'[a-z]{2,8}', text) or text == 'scrim':
        raise argparse.ArgumentTypeError(
            f'{text!r}: 2-8 lowercase letters, no digits (the halves become {text}1 and {text}2)'
        )
    return text


def now() -> str:
    return time.strftime('%H:%M')


class Run:
    """One game's run: where its files go and how its steps are launched."""

    def __init__(self, a):
        self.a = a
        self.g = a.game
        self.tags = (f'{self.g}1', f'{self.g}2')
        self.out = DATA / 'stats' / self.g / 'run'
        self.logs = self.out / 'logs'
        self.env = dict(
            os.environ,
            PIQ_VIDEO=str(a.video),
            PIQ_LINES=a.lines,
            PYTHONPATH=str(REPO),
            PYTHONIOENCODING='utf8',
        )

    def say(self, text: str) -> None:
        line = f'{now()}  {text}'
        print(line, flush=True)
        if not self.a.plan:
            self.logs.mkdir(parents=True, exist_ok=True)
            with open(self.out / 'run.log', 'a', encoding='utf-8') as f:
                f.write(f'{date.today()} {line}\n')

    def py(self, name: str, *args, check=True) -> int:
        """Run `python -m <module> args` from the repo root, its output to logs/<name>.log."""
        cmd = [sys.executable, '-W', 'ignore', '-m', *map(str, args)]
        log = self.logs / f'{name}.log'
        self.say(f'start {name}  (log: {log})')
        t0 = time.time()
        with open(log, 'a', encoding='utf-8') as f:
            f.write(f'\n=== {time.ctime()}  {" ".join(cmd)}\n')
            f.flush()
            rc = subprocess.call(cmd, cwd=REPO, env=self.env, stdout=f, stderr=subprocess.STDOUT)
        self.say(f'end {name}  rc {rc}  ({(time.time() - t0) / 60:.0f} min)')
        if rc and check:
            tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-15:]
            raise Stop(f'{name} failed (rc {rc}). The end of {log}:\n  ' + '\n  '.join(tail))
        return rc


# ---------------------------------------------------------------- 0. checks before anything long


def check_video(path: Path) -> None:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise Stop(f'cannot open {path}')
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    if abs(fps - FPS) > 0.1 or size != SIZE:
        raise Stop(
            f'{path.name} is {size[0]}x{size[1]} at {fps:.2f} fps. Everything here was built '
            f'and tuned on {SIZE[0]}x{SIZE[1]} at 30 fps (the Veo "Tactical" export).'
        )


def check_site(a) -> None:
    """The key and the match exist, read-only, so a 10-hour run cannot fail at its last step."""
    if a.plan:
        return
    if not (a.team and a.match):
        if a.no_publish:
            return  # no site at all: the pins come from a file (see offer)
        raise Stop(
            'publishing needs --team and --match (--list-matches prints them), or pass --no-publish'
        )
    from pitchiq.publish_to_firestore import PublishError, _client

    try:
        db = _client()
    except PublishError as e:
        raise Stop(str(e)) from None
    m = db.collection('teams').document(a.team).collection('matches').document(a.match).get()
    if not m.exists:
        raise Stop(f'no match {a.match} under team {a.team} (--list-matches prints them)')
    d = m.to_dict() or {}
    print(f'publishing to: {d.get("opponentName") or "?"} on {d.get("date") or "?"}', flush=True)


def list_matches() -> int:
    from pitchiq.publish_to_firestore import PublishError, _client

    try:
        db = _client()
    except PublishError as e:
        print(e, file=sys.stderr)
        return 1
    for t in db.collection('teams').stream():
        print(f'team {t.id}  {(t.to_dict() or {}).get("name", "")}')
        for m in t.reference.collection('matches').stream():
            d = m.to_dict() or {}
            print(
                f'  match {m.id}  {d.get("date", "?")}  v {d.get("opponentName", "?")}  {d.get("status", "")}'
            )
    return 0


# ---------------------------------------------------------------- 1. halves


def load_halves() -> dict:
    return json.loads(HALVES.read_text(encoding='utf-8'))


def half_frames(game: str) -> list[tuple[int, int]]:
    entry = load_halves()[game]
    return [
        tuple(int(x) for x in next(v for k, v in entry.items() if k.startswith(f'H{h}'))[2:4])
        for h in (1, 2)
    ]


def add_game(game: str, video: Path, halves: list[tuple[float, float]], how: str) -> None:
    """Append the game to halves.json as text, so the hand-kept layout (and CRLF) stays."""
    entry = {'video': video.name}
    if video.resolve().parent != DOWNLOADS.resolve():
        entry['path'] = video.resolve().as_posix()
    for h, (s, e) in enumerate(halves, 1):
        entry[f'H{h}'] = [mmss(s), mmss(e), int(round(s * FPS)), int(round(e * FPS))]
    entry['approx'] = f'Added by process_match {date.today()}: {how}.'
    raw = HALVES.read_bytes().decode('utf-8')
    nl = '\r\n' if '\r\n' in raw else '\n'
    body = raw.rstrip()
    if not body.endswith('}'):
        raise Stop(f'{HALVES} does not end in }}: fix it by hand')
    lines = [f'"{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in entry.items()]
    text = f'{{{lines[0]},' + nl + ''.join(f'         {s},{nl}' for s in lines[1:-1])
    text += f'         {lines[-1]}}}'
    new = body[:-1].rstrip() + ',' + nl + f'  "{game}": ' + text + nl + '}' + nl
    json.loads(new)  # never leave a broken file behind
    HALVES.write_bytes(new.encode('utf-8'))


def step_halves(r: Run) -> None:
    a, H = r.a, load_halves()
    if r.g in H:
        if H[r.g].get('video') != a.video.name:
            raise Stop(
                f'game {r.g} is already {H[r.g].get("video")!r} in halves.json: pick another --game'
            )
        (l1, h1), (l2, h2) = half_frames(r.g)
        r.say(
            f'halves: from halves.json, H1 {mmss(l1 / FPS)}-{mmss(h1 / FPS)}, H2 {mmss(l2 / FPS)}-{mmss(h2 / FPS)}'
        )
        return
    if a.plan:
        r.say(
            'halves: to do (' + ('--h1/--h2' if a.h1 else "halves.py's guess, to the minute") + ')'
        )
        return
    if a.h1 and a.h2:
        halves, how = [a.h1, a.h2], 'halves given on the command line'
    else:
        r.py('halves', 'pitchiq.experiments.halves', r.g)
        import numpy as np

        os.environ['PIQ_VIDEO'] = str(a.video)
        from pitchiq.experiments import halves as finder

        z = np.load(DETS / f'{r.g}_halves.npz')
        halves, _, _ = finder.guess(z)
        if len(halves) < 2:
            raise Stop(
                f'the halves finder found {len(halves)} half: give --h1 and --h2 (video clock)'
            )
        import cv2

        cap = cv2.VideoCapture(str(a.video))
        last = (cap.get(cv2.CAP_PROP_FRAME_COUNT) - 2) / FPS  # a guess ends on a whole minute
        cap.release()
        halves = [(float(s), min(float(e), last)) for s, e in halves[:2]]
        how = f"halves.py's guess, to the minute; check {DETS / (r.g + '_halves.png')}"
    add_game(r.g, a.video, halves, how)
    r.say(
        f'halves: H1 {mmss(halves[0][0])}-{mmss(halves[0][1])}, H2 {mmss(halves[1][0])}-{mmss(halves[1][1])} ({how})'
    )


# ---------------------------------------------------------------- 2. camera


def registered(tag: str) -> bool:
    return (HOMOG / f'{tag}_reg.npz').exists() and (HOMOG / f'{tag}_venue.npz').exists()


def step_camera(r: Run) -> None:
    halves = half_frames(r.g) if r.g in load_halves() else None
    todo = [t for t in r.tags if not registered(t)]
    if not todo:
        r.say('camera: both halves already calibrated')
    elif r.a.no_pins:
        step_camera_auto(r, halves, todo)
    else:
        step_pins(r, halves)
    if r.a.plan and todo:
        return
    for t in r.tags:
        health(r, t)


def step_camera_auto(r: Run, halves, todo) -> None:
    """--no-pins: the pin-free calibration (autocal), for a field it can read alone."""
    if r.a.plan:
        r.say(f'camera (no pins): to do for {", ".join(todo)} (about 1 h a half)')
        return
    cam = camera_minute(r, halves[0])
    for t, (lo, hi) in zip(r.tags, halves):
        if t in todo:
            r.py(
                f'camera_{t}', 'pitchiq.experiments.autocal_run', t, lo, hi,
                '--lines', r.a.lines, '--camera', cam,
            )  # fmt: skip
            if not registered(t):
                raise Stop(f'autocal finished but wrote no {t}_reg.npz / {t}_venue.npz')


# The coach's pins (experiments/pincal.py and the calibrate page). Each half gets
# autocal's gather, a loop closure and a colour panorama (about 25 min). The first
# half's panorama goes on the match as soon as it exists, so the coach can pin while
# the second half is prepared. If the tripod stayed put, the first half's pins carry
# over and the coach pins once; otherwise the second half needs its own.


def pin_url(r: Run, tag: str) -> str:
    return f'{SITE}calibrate/?team={r.a.team}&match={r.a.match}&half=h{tag[-1]}'


def others_of(r: Run, tag: str, moved: bool) -> list[str]:
    return [] if moved else [t for t in r.tags if t != tag]


def step_pins(r: Run, halves) -> None:
    from pitchiq.experiments import pincal as PC

    h1, h2 = r.tags
    if r.a.plan:
        for t in r.tags:
            if (PC.here(t) / 'pins.json').exists():
                what = 'pins in, calibration to do (5 min)'
            elif PC.prepped(t):
                what = 'panorama ready, waiting for pins'
            else:
                what = 'panorama to do (about 25 min), then the coach pins it'
            r.say(f'camera {t}: {what}')
        return
    db = None
    if r.a.team and r.a.match:
        from pitchiq.publish_to_firestore import _client

        db = _client()
    for t, (lo, hi) in zip(r.tags, halves):
        if not PC.prepped(t):
            r.py(f'pano_{t}', 'pitchiq.experiments.pincal', 'prep', t, lo, hi, '--lines', r.a.lines)
        if t == h1:
            offer(r, db, h1)
    xfer = PC.here(h2) / f'transfer_from_{h1}.json'
    if not xfer.exists():
        r.py(f'transfer_{h2}', 'pitchiq.experiments.pincal', 'transfer', h1, h2, check=False)
    moved = not json.loads(xfer.read_text())['ok']
    if moved:
        r.say(f'camera: it moved between the halves, so {h2} needs pins of its own')
        offer(r, db, h2)
    else:
        r.say(f"camera: it stayed put, so {h1}'s pins will carry to {h2}")

    need = [h1, h2] if moved else [h1]
    while not pins_fit(r, db, moved):
        pass  # the pins were turned down and the coach told why: wait for new ones
    if db is not None:
        for t in need:
            if (PC.here(t) / 'pins_rejected.json').exists():
                PC.mark_problem(db, r.a.team, r.a.match, int(t[-1]), '')
    for t in r.tags:
        if not registered(t):
            other = others_of(r, t, moved)
            args = ['--with', *other] if other else []
            r.py(f'pincal_{t}', 'pitchiq.experiments.pincal', 'cal', t, *args, '--install')
            if not registered(t):
                raise Stop(f'pincal finished but installed no {t}_reg.npz / {t}_venue.npz')


def offer(r: Run, db, tag: str) -> None:
    """Put a half's panorama on the match (once) for the coach to pin."""
    from pitchiq.experiments import pincal as PC

    if (PC.here(tag) / 'pins.json').exists():
        return
    if db is None:
        r.say(
            f'camera {tag}: pin {PC.here(tag) / "pano_rgb.jpg"} on the calibrate page '
            f'(the panorama of a panning camera) and save its pins.json beside it'
        )
        return
    sent = r.out / f'pano_sent_{tag}.json'
    if not sent.exists():
        PC.upload_pano(db, r.a.team, r.a.match, int(tag[-1]), tag)
        sent.write_text(json.dumps({'at': time.ctime()}))
    r.say(f'camera {tag}: the panorama is on the match; the coach pins it at {pin_url(r, tag)}')


def wait_for_pins(r: Run, db, tag: str) -> None:
    """Until pins.json exists: fetched from the match, or saved there by hand."""
    from pitchiq.experiments import pincal as PC

    path = PC.here(tag) / 'pins.json'
    rejected = PC.here(tag) / 'pins_rejected.json'
    told = 0.0
    while not path.exists():
        got = PC.read_pins(db, r.a.team, r.a.match, int(tag[-1])) if db is not None else None
        if got and rejected.exists() and json.loads(rejected.read_text()).get('at') == got['at']:
            got = None  # the pins already turned down: wait for the coach to save new ones
        if got:
            path.write_text(json.dumps(got, indent=1))
            r.say(f'camera {tag}: {len(got["pins"])} pins from the match (saved {got["at"]})')
            return
        if r.a.no_wait or db is None:
            where = pin_url(r, tag) if db is not None else str(path)
            raise Stop(f'{tag} has no pins yet ({where}). Run the same command once they are in.')
        if time.time() - told > PIN_REMIND_S:
            r.say(f'camera {tag}: waiting for the coach to pin {pin_url(r, tag)}')
            told = time.time()
        time.sleep(PIN_POLL_S)


def pins_fit(r: Run, db, moved: bool) -> bool:
    """Wait for the pins each half needs, carry the first half's over if the camera
    stayed put, and check every pin against the rest (seconds, before the 5-minute
    calibration). False when a set was turned down: it is set aside and the pin page
    names the pin, and the caller waits for new ones."""
    from pitchiq.experiments import pincal as PC

    h1, h2 = r.tags
    for t in [h1, h2] if moved else [h1]:
        wait_for_pins(r, db, t)
    if not moved:
        carried = PC.carry_pins(h1, h2)
        (PC.here(h2) / 'pins.json').write_text(json.dumps(carried, indent=1))
    for t in r.tags:
        try:
            fit = PC.fit_pins(t, others_of(r, t, moved), log=lambda s: None)
        except PC.BadPins as e:
            reject(r, db, t if moved else h1, str(e))  # carried pins are the first half's
            return False
        worst = max(fit['worst'].items(), key=lambda kv: kv[1], default=('none', 0.0))
        r.say(
            f'camera {t}: {len(fit["names"])} pins; the worst, left out, is '
            f'{worst[1]:.0f} px off ({worst[0]})'
        )
    return True


def reject(r: Run, db, tag: str, why: str) -> None:
    """Pins the solve cannot trust: set aside and the coach told why. Stops the run
    when nobody is there to fix them (--no-wait, or no site)."""
    from pitchiq.experiments import pincal as PC

    (PC.here(tag) / 'pins.json').replace(PC.here(tag) / 'pins_rejected.json')
    for t in r.tags:
        carried = PC.here(t) / 'pins.json'
        if t != tag and carried.exists() and json.loads(carried.read_text()).get('from') == tag:
            carried.unlink()
    r.say(f'camera {tag}: the pins do not fit ({why}); set aside as pins_rejected.json')
    if db is not None:
        PC.mark_problem(db, r.a.team, r.a.match, int(tag[-1]), why)
    if db is None or r.a.no_wait:
        where = pin_url(r, tag) if db is not None else str(PC.here(tag))
        raise Stop(
            f'the pins for {tag} do not fit: {why} ({where}). '
            'Run the same command once they are saved again.'
        )


def camera_minute(r: Run, h1: tuple[int, int]) -> str:
    """Calibrate one minute of the first half alone; the halves then share its camera."""
    lo, hi = h1
    for k, at in enumerate(CAMERA_TRIES, 1):
        tag = f'{r.g}cam{k}'
        if (AUTOCAL / tag / 'full.pkl').exists():
            return tag
        a = lo + int((hi - lo - CAMERA_FRAMES) * at)
        rc = r.py(
            f'camera_{tag}', 'pitchiq.experiments.autocal_run', tag, a, a + CAMERA_FRAMES,
            '--lines', r.a.lines, '--homog', r.out / 'camera', check=False,
        )  # fmt: skip
        if rc == 0 and (AUTOCAL / tag / 'full.pkl').exists():
            r.say(
                f'camera: calibrated on {mmss(a / FPS)}-{mmss((a + CAMERA_FRAMES) / FPS)} ({tag})'
            )
            return tag
    raise Stop(
        f'the pin-free calibration found no pitch in {len(CAMERA_TRIES)} stretches of the first half '
        f'(logs/camera_{r.g}cam*.log). Wrong --lines colour? Otherwise this field needs pins: '
        'run without --no-pins and the coach pins it on the site.'
    )


def health(r: Run, tag: str) -> None:
    """The calibration's own check: far/near player size and height over the half."""
    path = r.out / f'cal_health_{tag}.json'
    if r.a.plan:
        r.say(f'camera check {tag}: ' + ('done' if path.exists() else 'to do'))
        return
    if not path.exists():
        r.py(f'cal_health_{tag}', 'pitchiq.experiments.cal_health', tag, '--json', path)
    from pitchiq.checks import cal_flags

    total = json.loads(path.read_text(encoding='utf-8'))['total']
    flags = cal_flags(total) if total else ['no frames read']
    if not flags:
        r.say(f'camera check {tag}: ok')
    elif r.a.trust_cal:
        r.say(f'camera check {tag}: {", ".join(flags)}; carrying on (--trust-cal)')
    else:
        raise Stop(
            f'the calibration of {tag} looks wrong: {", ".join(flags)} ({path}). Every position '
            'after this would be off. Pin the field, or pass --trust-cal to carry on anyway.'
        )


# ---------------------------------------------------------------- 3. pass, 4. map


def passed(tag: str) -> bool:
    log = TRACK / 'half' / tag / 'log.txt'
    done = log.exists() and 'HALF DONE' in log.read_text(encoding='utf-8', errors='replace')
    return done and (H3D / f'{tag}_smooth2.npz').exists() and (DETS / f'{tag}_dets.npz').exists()


def step_pass(r: Run) -> None:
    for t, (lo, hi) in zip(r.tags, half_frames(r.g) if r.g in load_halves() else [(0, 0)] * 2):
        if passed(t):
            r.say(f'pass {t}: done')
        elif r.a.plan:
            r.say(f'pass {t}: to do (2-3 h)')
        else:
            r.py(f'pass_{t}', 'pitchiq.balltrack.half', t, lo, hi)
            if not passed(t):
                raise Stop(
                    f'the pass of {t} ended without HALF DONE ({TRACK / "half" / t / "log.txt"})'
                )


def step_map(r: Run) -> None:
    for t in r.tags:
        if (MAPS / f'{t}.npz').exists():
            r.say(f'map {t}: done')
        elif r.a.plan:
            r.say(f'map {t}: to do (15-30 min)')
        else:
            extra = ['--final'] if t in HELD_OUT and r.a.final else []
            if t in HELD_OUT and not extra:
                raise Stop(f'{t} is held out: its map is made once, with --final')
            r.py(f'map_{t}', 'pitchiq.experiments.wholemap', t, '--procs', MAP_PROCS, *extra)


# ---------------------------------------------------------------- 5. stats


def attacks_first(r: Run) -> str:
    """The end ours attack in the first half, from where each team stands on the map."""
    if r.a.attacks_first:
        return r.a.attacks_first
    from pitchiq.map_stats import MapTable, defends_left, lighter_team

    ends = []
    for t in r.tags:
        path = MAPS / f'{t}.npz'
        light = lighter_team(path)
        ours = light if r.a.ours == 'lighter' else 1 - light
        ends.append('right' if defends_left(MapTable.load(path))[ours] else 'left')
    if ends[0] == ends[1]:
        raise Stop(
            f'the maps put ours attacking {ends[0]} in both halves, so which team is which is in '
            'doubt. Check --ours, and give --attacks-first left|right (the end ours attack first).'
        )
    r.say(f'attacking: ours attack {ends[0]} in the first half (from the map)')
    return ends[0]


def step_stats(r: Run) -> None:
    if r.a.plan:
        if all((MAPS / f'{t}.npz').exists() for t in r.tags):
            attacks_first(r)  # read-only: shows what the stats would be told
        for t in r.tags:
            r.say(
                f'stats {t}: '
                + ('done' if (r.out / f'pipe_{t}.json').exists() else 'to do (about 1 h)')
            )
        return
    side = attacks_first(r)
    tag_log = Path(r.a.tag_log).resolve() if r.a.tag_log else None
    key = dict(ours=r.a.ours, attacks_first=side, tag_log=str(tag_log) if tag_log else None,
               tag_log_mtime=tag_log.stat().st_mtime if tag_log else None)  # fmt: skip
    for t in r.tags:
        out, kfile = r.out / f'pipe_{t}.json', r.out / f'pipe_{t}.key.json'
        if out.exists() and kfile.exists() and json.loads(kfile.read_text()) == key:
            r.say(f'stats {t}: done')
            continue
        args = ['half', t, '--ours', r.a.ours, '--attacks-first', side, '--out', out]
        if tag_log:
            args += ['--tag-log', tag_log]
        r.py(f'stats_{t}', 'pitchiq.experiments.cached_report', *args)
        kfile.write_text(json.dumps(key))


# ---------------------------------------------------------------- 6. publish


def step_publish(r: Run) -> None:
    pipe, full = r.out / f'pipe_{r.g}.json', r.out / f'{r.g}_report.json'
    if r.a.plan:
        if r.a.no_publish:
            r.say('publish: --no-publish, the report file only')
        elif r.a.team and r.a.match:
            r.say(f'publish: to team {r.a.team}, match {r.a.match} (checked when the run starts)')
        else:
            r.say('publish: needs --team and --match (--list-matches), or --no-publish')
        return
    halves = [r.out / f'pipe_{t}.json' for t in r.tags]
    r.py(
        'merge',
        'pitchiq.experiments.cached_report',
        'merge',
        *halves,
        '--out',
        pipe,
        '--min-minutes',
        MIN_MINUTES,
    )
    maps = [MAPS / f'{t}.npz' for t in r.tags]
    r.py(
        'map_report',
        'pitchiq',
        'map_report',
        *maps,
        '--ours',
        r.a.ours,
        '--into',
        pipe,
        '--out',
        full,
    )
    if r.a.no_publish:
        r.say(f'report written, not published: {full}')
        return
    r.py('publish', 'pitchiq', 'publish_report', full, '--team', r.a.team, '--match', r.a.match)
    r.say(f'published {full.name} to match {r.a.match}')


def footage_note(r: Run) -> None:
    """What the coach page's footage form wants, for tagging this video later."""
    if r.g not in load_halves():
        return
    (l1, _), (l2, _) = half_frames(r.g)
    print(
        f'For tagging this video on the live tagger: the halves in halves.json start at '
        f'{mmss(l1 / FPS)} and {mmss(l2 / FPS)}. The footage form wants the kick-offs, which '
        'can be a few seconds later (a guessed half starts on the minute).'
    )


# ---------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m pitchiq process_match', description=__doc__.split('\n')[0]
    )
    p.add_argument(
        'video', type=Path, nargs='?', help='the match video (Veo Tactical, 1920x1080, 30 fps)'
    )
    p.add_argument(
        '--game',
        type=game_code,
        help='short code for this game, e.g. ma; its halves are ma1 and ma2',
    )
    p.add_argument(
        '--ours', choices=('lighter', 'darker'), help="which shirts are the coach's side"
    )
    p.add_argument('--team', help='team document id (see --list-matches)')
    p.add_argument('--match', help='match document id (see --list-matches)')
    p.add_argument(
        '--h1', type=span, help='first half on the video clock, e.g. 12:58-55:15 (default: guessed)'
    )
    p.add_argument('--h2', type=span, help='second half on the video clock, e.g. 66:05-111:00')
    p.add_argument(
        '--lines',
        choices=('gold', 'white'),
        default='gold',
        help='paint colour of the pitch lines (default gold, the home field)',
    )
    p.add_argument(
        '--attacks-first',
        choices=('left', 'right'),
        help='the end ours attack in the first half (default: from the map)',
    )
    p.add_argument(
        '--tag-log',
        help="the coach page's log download for this match: reruns stats and publish with it",
    )
    p.add_argument(
        '--no-publish', action='store_true', help='write the report file but not to the site'
    )
    p.add_argument(
        '--plan', action='store_true', help='say what is done and what would run; run nothing'
    )
    p.add_argument(
        '--trust-cal', action='store_true', help='carry on when the calibration check fails'
    )
    p.add_argument(
        '--no-wait', action='store_true', help='stop, rather than wait, while pins are missing'
    )
    p.add_argument(
        '--no-pins',
        action='store_true',
        help='calibrate without pins (autocal: only for a field it can read alone)',
    )
    p.add_argument(
        '--final', action='store_true', help='allow the map of a held out half (scored once)'
    )
    p.add_argument(
        '--list-matches',
        action='store_true',
        help='print team and match ids from the site, and stop',
    )
    return p  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.list_matches:
        return list_matches()
    if not (a.video and a.game and a.ours):
        p.error('a video, --game and --ours are needed')
    if bool(a.h1) != bool(a.h2):
        p.error('give both --h1 and --h2, or neither')
    a.video = a.video.resolve()
    r = Run(a)
    try:
        check_video(a.video)
        check_site(a)
        r.say(
            f'{"plan for" if a.plan else "processing"} {a.video.name} as {r.g} (ours: {a.ours}, lines: {a.lines})'
        )
        for step in (step_halves, step_camera, step_pass, step_map, step_stats, step_publish):
            step(r)
    except Stop as e:
        r.say(f'STOPPED: {e}')
        print('Run the same command again once that is fixed: finished steps are skipped.')
        return 1
    footage_note(r)
    r.say('plan only: nothing was run' if a.plan else 'ALL DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
