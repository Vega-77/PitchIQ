"""From a match video to statistics on the website, in one command.

    python -m pitchiq process_match <video> --game xx --ours lighter --team T --match M
    python -m pitchiq process_match <video> --game xx --ours darker --h1 12:58-55:15 --h2 66:05-111:00 --lines white --team T --match M
    python -m pitchiq process_match <video> --game xx --ours lighter --plan
    python -m pitchiq process_match --list-matches
    python -m pitchiq process_match --requests

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
                page by hand and save `pins.json` beside it. `--pins-from wb`
                carries an earlier game's pins at the same field instead (the
                camera must have stood in the same place). `--no-pins` uses
                the pin-free calibration (`experiments/autocal_run.py`) instead
  3. pass       the ball and the players over each half (`balltrack/half.py`)
  4. ball, map  at the same time: the look #17 ball over both halves
                (`balltrack/ball17.py`, 3-6 h, mostly the GPU) and, beside it,
                the top-down map of each half (`experiments/wholemap.py`, the
                CPU), which needs the pass's players and camera but not the ball.
                Then the 3D ball checks the camera (`checks.flight_flags`): a half
                with almost no ball in the air, or flights that miss, stops the run
                (`--trust-cal` carries on)
  5. stats     the pipeline report of each half from its map and ball, in
                pitch metres, with no video read (`experiments/cached_report.py
                half`, `map_table.py`) but for the players' pictures, cut from
                a few hundred chosen frames (`table_pictures.py`, about 20 s)
  6. publish    both halves joined, the map's figures put in, and the report
                written to the match on the site (`cached_report merge`,
                `map_report --into`, `publish_report`); then the match viewer's
                map and moments (`viewer_export`, `publish_viewer`), which only
                cost the coach the viewer if they fail

A coach asks for a match on the site (the match page's "Video processing"
card, which also says which shirts are ours); `--requests` lists those, each
with the command to run once the video is downloaded. With --team/--match the
run keeps that card's progress bar up to date (`pitchiq/run_progress.py`):
the step, how far it is, the time left, a call to pin the pitch, and done.

Which kit is ours (`--ours`) the video cannot say: the coach's request says it,
or the command line. The end we attack in the first half comes from the map
(where each team's keeper and players stand) unless `--attacks-first` says it;
the run stops if the two halves' maps do not show the teams changing ends.

About 5 to 10 hours a game on this PC, plus however long the coach takes to
pin; each step's time is in `shared/run-steps.json`, which the coach page's
estimate reads too. Woodbridge's measured steps (2026-11-19) add up to about
8 h 40 min in this order, against 10 h 52 min before the maps ran beside the
ball and the stats came from the table. Everything else runs one step at a time.

Publishing needs PITCHIQ_SA_KEY (the service account key, kept outside the
repo) and the team and match ids; `--list-matches` prints them. Both are
checked before anything long starts, with the video, every model and cache,
the disk, memory and GPU, and that no other run is going (`preflight.py`). `--tag-log` takes the coach page's log
download for this match and reruns only the stats and the publish.

Before publishing, the finished report is checked against itself (`pitchiq/run_checks.py`):
the goals found against the score the coach logged (or `--score`), the two halves alike,
possession spells of a believable length and the ball's share in the air. A flag stops
nothing: it goes to run.log, notify.log and the top of the report's warnings, which the
coach's match page shows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from pitchiq.run_progress import Progress, pass_fraction, read_requests, step_of

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
# At most this many map processes, fewer when memory is short (MAP_GB each, see map_one).
# 8 ran this 32 GB PC out of memory on wb1 (2026-11-19) while each held its own copy of the
# half's player features; they now share one memory-mapped copy (player_lab.feature_cache).
MAP_PROCS = int(os.environ.get('PIQ_MAP_PROCS', '8'))
MAP_GB = 3.0  # measured on wb1 2026-11-21: 2.5 GB working set, 3.6 GB committed a process
# What the maps leave free for the ball step running beside them (step_ball_and_maps).
# Estimated, not measured: the fit holds about 110k 40 x 40 x 7 crops (1.2 GB) plus torch
# and CUDA, and TAPNext one half's frames. Each step's peak now goes in run.log ("peak").
BALL_GB = float(os.environ.get('PIQ_BALL_GB', '6'))
SITE = os.environ.get('PIQ_SITE', 'https://vega-77.github.io/PitchIQ/')
PIN_POLL_S = 60  # how often the run looks for the coach's pins
PIN_REMIND_S = 1800  # and how often it says it is still waiting
TICK_S = 30  # how often a running step's progress is looked at
STEPS = ('halves', 'camera', 'pass', 'ball beside map', 'stats', 'publish')
NOTE_CHARS = 1500  # of a phone note (ntfy takes 4 KB; the full text is in run.log)
RESUME_WAIT_S = 1800  # a run restarted after a reboot waits this long for the checks to pass
RESUME_POLL_S = 120


class Stop(Exception):
    """A step failed or a check refused: the message says what to do next.

    `coach` is the same news in plain words for the coach's Video processing card (the
    message itself names files and flags); left out, the card says only which step
    stopped."""

    def __init__(self, text: str, coach: str = ''):
        super().__init__(text)
        self.coach = coach


FAILED_TWICE = 'A step failed twice on the processing computer.'


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


def memory_gb(pid: int) -> tuple[float, float]:
    """(in use, committed) GB of a process and everything it started; (0, 0) once it is gone.
    Committed is what counts against Windows' commit limit (the pagefile's error 1455)."""
    import psutil

    try:
        p = psutil.Process(pid)
        procs = [p, *p.children(recursive=True)]
    except psutil.Error:
        return 0.0, 0.0
    use = commit = 0
    for q in procs:
        try:
            m = q.memory_info()
        except psutil.Error:  # it ended between the listing and the look
            continue
        use += m.rss
        commit += getattr(m, 'private', 0)  # Windows only
    return use / 2**30, commit / 2**30


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
            PIQ_GIANT='50000',  # the look #17 ball's finder rule (balltrack/blob_candidates.py), in the pass
            PYTHONPATH=str(REPO),
            PYTHONIOENCODING='utf8',
        )
        self.progress = Progress(None, None, None)  # main() points it at the match
        self.video_s = None  # the video's length in seconds, from check_video
        self.db = None  # the site's Firestore client, from check_site
        # set in a thread whose steps run beside another (the maps beside the ball): they
        # leave the coach's progress bar to the one in front
        self.beside = threading.local()

    def say(self, text: str) -> None:
        line = f'{now()}  {text}'
        print(line, flush=True)
        if not self.a.plan:
            self.logs.mkdir(parents=True, exist_ok=True)
            with open(self.out / 'run.log', 'a', encoding='utf-8') as f:
                f.write(f'{date.today()} {line}\n')

    def tell(self, what: str, text: str, priority: str = 'default') -> None:
        """A note to whoever runs this PC: in run/notify.log, and on their phone once
        `python -m pitchiq notify --setup` has been run. Nothing in --plan."""
        if self.a.plan:
            return
        from pitchiq.cli import notify

        notify.send(self.out, f'PitchIQ {self.g}: {what}', text[:NOTE_CHARS], priority)

    def py(self, name: str, *args, check=True, fewer=None) -> int:
        """Run `python -m <module> args` from the repo root, its output to logs/<name>.log.
        While it runs, the coach page's progress bar is kept up to date.

        With `check`, a step that fails is tried once more before the run stops; when its
        log says it ran out of memory, the second try runs `fewer(args)` (fewer processes).
        Steps resume from what they finished, so a retry redoes only the unfinished part.
        """
        from pitchiq.cli import preflight

        rc = self.launch(name, args)
        if rc and check:
            log = self.logs / f'{name}.log'
            tail = '\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-60:])
            oom = preflight.out_of_memory(tail)
            again = tuple(fewer(args)) if oom and fewer else args
            how = f' with {" ".join(map(str, again[1:]))}' if again != args else ''
            self.say(f'{name} failed (rc {rc}{", out of memory" if oom else ""}): once more{how}')
            rc = self.launch(name, again)
            if rc:
                tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-15:]
                raise Stop(
                    f'{name} failed twice (rc {rc}). The end of {log}:\n  ' + '\n  '.join(tail),
                    coach=FAILED_TWICE,
                )
        return rc

    def launch(self, name: str, args) -> int:
        """One try of a step (see py); its exit code."""
        cmd = [sys.executable, '-W', 'ignore', '-m', *map(str, args)]
        log = self.logs / f'{name}.log'
        key = None if getattr(self.beside, 'on', False) else step_of(name)
        if key:
            self.progress.begin(key)
        self.say(f'start {name}  (log: {log})')
        t0 = time.time()
        peak = (0.0, 0.0)
        with open(log, 'a', encoding='utf-8') as f:
            f.write(f'\n=== {time.ctime()}  {" ".join(cmd)}\n')
            f.flush()
            proc = subprocess.Popen(cmd, cwd=REPO, env=self.env, stdout=f, stderr=subprocess.STDOUT)
            while True:
                try:
                    rc = proc.wait(timeout=TICK_S)
                    break
                except subprocess.TimeoutExpired:
                    peak = tuple(map(max, peak, memory_gb(proc.pid)))
                    if key:
                        self.tick(name, key)
        took = (time.time() - t0) / 60
        held = f', peak {peak[0]:.1f} GB in use, {peak[1]:.1f} GB committed' if peak[0] else ''
        self.say(f'end {name}  rc {rc}  ({took:.0f} min{held})')
        return rc

    def tick(self, name: str, key: str) -> None:
        """How far the running step is: the pass from its log, the rest from the clock."""
        p = self.progress
        f = None
        if key.startswith('pass_'):
            f = pass_fraction(TRACK / 'half' / name.split('_', 1)[1] / 'log.txt')
        if f is None:
            f = min(0.95, (time.time() - p.step_t0) / p.expected(key))
        p.at(f)


# ---------------------------------------------------------------- 0. checks before anything long


def check_video(path: Path) -> float:
    """The video opens, has the size and rate everything was tuned on, and decodes to its
    end (a cut-short download fails here, not hours in). Its length in seconds."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise Stop(f'cannot open {path}')
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if abs(fps - FPS) > 0.1 or size != SIZE:
        cap.release()
        raise Stop(
            f'{path.name} is {size[0]}x{size[1]} at {fps:.2f} fps. Everything here was built '
            f'and tuned on {SIZE[0]}x{SIZE[1]} at 30 fps (the Veo "Tactical" export).',
            coach='The video is not the Veo “Tactical” export (1080p, 30 fps) processing needs.',
        )
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 60))
    ok = cap.read()[0]
    cap.release()
    if not ok:
        raise Stop(
            f'{path.name} says it has {n} frames but its last two seconds do not decode: '
            'the download may be cut short. Download it again.',
            coach='The video file looks cut short: it needs downloading again.',
        )
    return n / FPS


def check_inputs(r: Run) -> None:
    """Everything the steps still to run will read, plus the disk, memory, GPU and no
    other run beside this one (`preflight.py`), and existing halves that look wrong."""
    from pitchiq.balltrack import ball17
    from pitchiq.cli import preflight

    passes = all(passed(t) for t in r.tags)
    balls = passes and all(ball17.version(t) == ball17.VER for t in r.tags)
    maps = all((MAPS / f'{t}.npz').exists() for t in r.tags)
    need = dict(
        disk_gb=preflight.DISK_GB_LATE if passes else preflight.DISK_GB, ram=not maps, gpu=not balls
    )
    found = preflight.problems(need)
    if found and os.environ.get('PIQ_RESUMED') and not r.a.plan:
        # Started again after a restart (resume.py) with nobody there to start it once more:
        # the desktop may still be taking memory or the GPU as it comes up, so wait for it.
        r.say(f'checks: not ready ({found[0]}); waiting up to {RESUME_WAIT_S // 60} min')
        until = time.time() + RESUME_WAIT_S
        while found and time.time() < until:
            time.sleep(RESUME_POLL_S)
            found = preflight.problems(need)
    found += pins_from_problems(r)
    H = load_halves()
    if r.g in H and H[r.g].get('video') == r.a.video.name and not r.a.trust_halves:
        spans = [(lo / FPS, hi / FPS) for lo, hi in half_frames(r.g)]
        found += [
            f'halves.json, game {r.g}: {p}. Fix the entry by hand, or pass --trust-halves'
            for p in preflight.halves_problems(spans, r.video_s)
        ]
    if not found:
        r.say('checks: inputs, disk, memory, GPU and halves are fine')
        return
    text = '\n  - '.join(found)
    if r.a.plan:
        r.say(f'checks: these would stop the run:\n  - {text}')
        return
    raise Stop(
        f'before anything long:\n  - {text}',
        coach='The processing computer is not ready (space, memory or another run); '
        'nothing long had started.',
    )


def pins_from_problems(r: Run) -> list[str]:
    """--pins-from: the earlier game's first half has the pins and the panorama they are
    carried from (its second half's are needed only if this camera moved, and checked then)."""
    if not r.a.pins_from or all(registered(t) for t in r.tags):
        return []
    from pitchiq.experiments import pincal as PC

    src = f'{r.a.pins_from}1'
    d = PC.here(src)
    if PC.prepped(src) and (d / 'pins.json').exists() and (d / 'keys').is_dir():
        return []
    return [f'--pins-from {r.a.pins_from}: {d} has no pins.json, panorama or keyframes']


def check_site(a):
    """The key and the match exist, read-only, so a 10-hour run cannot fail at its last step.
    The Firestore client, or None without a site; `--ours` from the coach's request when
    it was left out."""
    if a.plan:
        return None
    if not (a.team and a.match):
        if a.no_publish:
            return None  # no site at all: the pins come from a file (see offer)
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
    if not a.ours:
        req = m.reference.collection('cvRun').document('request').get()
        a.ours = (req.to_dict() or {}).get('ours') if req.exists else None
        if not a.ours:
            raise Stop('--ours is needed: the coach has not asked for this match on the site')
        print(f"ours: {a.ours} (from the coach's request)", flush=True)
    return db


def list_requests() -> int:
    """The matches coaches have asked to have processed, with the command for each."""
    from pitchiq.publish_to_firestore import PublishError, _client

    try:
        db = _client()
    except PublishError as e:
        print(e, file=sys.stderr)
        return 1
    reqs = read_requests(db)
    if not reqs:
        print('no coach is waiting for a match to be processed')
    taken = set(load_halves())
    for q in reqs:
        print(f'{q["date"]}  v {q["opponent"]}  ({q["state"]})')
        print(f'  video: {q["video"] or "(no link on the match yet)"}')
        code = suggest_code(q['opponent'], taken)
        taken.add(code)
        print(
            f'  python -m pitchiq process_match <downloaded video> --game {code} '
            f'--team {q["team"]} --match {q["match"]}'
        )
    return 0


def suggest_code(opponent: str, taken: set[str]) -> str:
    """A free game code from the opponent's name: 'Woodbridge' -> 'wo', then 'woo'..."""
    letters = re.sub(r'[^a-z]', '', opponent.lower()) or 'game'
    for n in range(2, 9):
        code = (letters + 'zzzzzzzz')[:n]
        if code not in taken and code != 'scrim':
            return code
    return next(f'g{c}' for c in 'abcdefghijklmnopqrstuvwxyz' if f'g{c}' not in taken)


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
        r.say('halves: to do (' + ('--h1/--h2' if a.h1 else "halves.py's activity fit") + ')')
        return
    if a.h1 and a.h2:
        halves, how = [a.h1, a.h2], 'halves given on the command line'
    else:
        r.py('halves', 'pitchiq.experiments.halves', r.g)
        import numpy as np

        os.environ['PIQ_VIDEO'] = str(a.video)
        from pitchiq.experiments import halves as finder

        z = np.load(DETS / f'{r.g}_halves.npz')
        halves, info = finder.fit(z)
        plot = finder.plot(r.g, z, halves)
        bad = finder.problems(halves, info)
        if bad:
            raise Stop(
                'the halves finder is not sure: ' + '; '.join(bad) + f'. Look at {plot}, '
                'then give --h1 and --h2 (video clock, m:ss-m:ss)',
                coach='Processing could not tell where the halves start and end; whoever runs '
                'it gives the times by hand.',
            )
        import cv2

        cap = cv2.VideoCapture(str(a.video))
        last = (
            cap.get(cv2.CAP_PROP_FRAME_COUNT) - 2
        ) / FPS  # a fit can end one sample past the video
        cap.release()
        halves = [(float(s), min(float(e), last)) for s, e in halves[:2]]
        how = (
            "halves.py's activity fit, within about a minute (2.6 min at worst on 7 known "
            f'games); the plot is {plot}'
        )
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
    src = r.a.pins_from
    if r.a.plan:
        for t in r.tags:
            if (PC.here(t) / 'pins.json').exists():
                what = 'pins in, calibration to do (5 min)'
            else:
                pano = 'panorama ready' if PC.prepped(t) else 'panorama to do (about 25 min)'
                if t == h2:
                    who = f"{h1}'s pins carried over if the camera stayed put, else " + (
                        f"{src}2's" if src else 'the coach pins it'
                    )
                elif src:
                    who = f"{src}1's pins carried over (--pins-from)"
                else:
                    who = 'the coach pins it'
                what = f'{pano}, then {who}'
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
            if src:
                pins_from_game(r, h1)
            offer(r, db, h1)
    moved = not transfer(r, h1, h2)['ok']
    if moved:
        r.say(f'camera: it moved between the halves, so {h2} needs pins of its own')
        if src:
            pins_from_game(r, h2)
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


def transfer(r: Run, src: str, dst: str) -> dict:
    """Whether `src`'s camera stood where `dst`'s did, and the turn between them
    (`pincal transfer`, kept as dst's transfer_from_<src>.json). It exits 1 when the camera
    moved, so its exit code says nothing; a run that wrote no answer stops."""
    from pitchiq.experiments import pincal as PC

    path = PC.here(dst) / f'transfer_from_{src}.json'
    if not path.exists():
        r.py(f'transfer_{dst}', 'pitchiq.experiments.pincal', 'transfer', src, dst, check=False)
    if not path.exists():
        raise Stop(
            f'comparing the cameras of {src} and {dst} failed (logs/transfer_{dst}.log)',
            coach='A step failed on the processing computer.',
        )
    return json.loads(path.read_text())


def pins_from_game(r: Run, tag: str) -> None:
    """--pins-from: an earlier game's pins for the same half, carried onto this one when
    its camera stood in the same place (the check that carries a first half's pins to the
    second, `pincal transfer`). Nobody is asked to pin, so pins that cannot carry stop the
    run, and so do carried pins that were turned down (see reject)."""
    from pitchiq.experiments import pincal as PC

    if (PC.here(tag) / 'pins.json').exists():
        return
    src = f'{r.a.pins_from}{tag[-1]}'
    rejected = PC.here(tag) / 'pins_rejected.json'
    if rejected.exists() and json.loads(rejected.read_text()).get('from') == src:
        why = json.loads(rejected.read_text()).get('why', '')
        raise Stop(
            f'the pins carried from {src} did not fit {tag} ({why}). '
            'Leave out --pins-from and pin the field.'
        )
    t = transfer(r, src, tag)
    if not t['ok']:
        raise Stop(
            f"--pins-from {r.a.pins_from}: {src}'s camera stood somewhere else ({t.get('why')}), "
            f'so its pins cannot carry to {tag}. Leave out --pins-from and pin the field.'
        )
    got = PC.carry_pins(src, tag)
    (PC.here(tag) / 'pins.json').write_text(json.dumps(got, indent=1))
    r.say(f'camera {tag}: {len(got["pins"])} pins carried from {src} (--pins-from)')


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
        why = ''
        if rejected.exists():
            why = json.loads(rejected.read_text()).get('why', '')
        r.progress.waiting_for_pins([f'h{tag[-1]}'], why)
        if not told:  # once a wait: the coach's card says the rest
            bad = f' (the last pins did not fit: {why})' if why else ''
            r.tell('waiting for pins', f'{tag} waits for the coach to pin {pin_url(r, tag)}{bad}')
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
    when nobody is there to fix them (--no-wait, or no site), and when they were carried
    from another game (--pins-from): the coach never placed them."""
    from pitchiq.experiments import pincal as PC

    bad = PC.here(tag) / 'pins_rejected.json'
    (PC.here(tag) / 'pins.json').replace(bad)
    bad.write_text(json.dumps(json.loads(bad.read_text()) | {'why': why}, indent=1))
    for t in r.tags:
        carried = PC.here(t) / 'pins.json'
        if t != tag and carried.exists() and json.loads(carried.read_text()).get('from') == tag:
            carried.unlink()
    r.say(f'camera {tag}: the pins do not fit ({why}); set aside as pins_rejected.json')
    if r.a.pins_from:
        raise Stop(
            f'the pins carried from {r.a.pins_from} do not fit {tag}: {why}. '
            'Leave out --pins-from and pin the field.'
        )
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
            'after this would be off. Pin the field, or pass --trust-cal to carry on anyway.',
            coach='The camera’s view of the pitch did not line up, so every position would be off.',
        )


# ---------------------------------------------------------------- 3. pass, 4. map


def passed(tag: str) -> bool:
    """The half's pass finished: HALF DONE, its players and its camera file. A half passed
    before 2026-11-21 may have only the Hybrid 90 folder's copy of the camera; the map makes
    the file from it (`balltrack/camera.py`)."""
    log = TRACK / 'half' / tag / 'log.txt'
    done = log.exists() and 'HALF DONE' in log.read_text(encoding='utf-8', errors='replace')
    cam = (HOMOG / f'{tag}_camera.npz').exists() or (H3D / f'{tag}_smooth2.npz').exists()
    return done and cam and (DETS / f'{tag}_dets.npz').exists()


def step_pass(r: Run) -> None:
    for t, (lo, hi) in zip(r.tags, half_frames(r.g) if r.g in load_halves() else [(0, 0)] * 2):
        if passed(t):
            r.say(f'pass {t}: done')
        elif r.a.plan:
            r.say(f'pass {t}: to do (1.5-2 h)')
        else:
            r.py(f'pass_{t}', 'pitchiq.balltrack.half', t, lo, hi, fewer=fewer_pass)
            if not passed(t):
                raise Stop(
                    f'the pass of {t} ended without HALF DONE ({TRACK / "half" / t / "log.txt"})'
                )


def ball_done(r: Run) -> bool:
    from pitchiq.balltrack import ball17

    return all(ball17.version(t) == ball17.VER for t in r.tags)


def flight_check(r: Run) -> None:
    """The 3D ball's own check of the camera (`checks.flight_flags`): a half where next to
    no ball fits as a flight, or the chains miss, was seen through a camera solved at the
    wrong height, or with flight units that do not match it (Woodbridge and
    Wardlaw-Hartridge before 2026-11-22). The stats would put every kick on the grass.
    `--trust-cal` carries on."""
    import numpy as np

    from pitchiq.balltrack import ball17
    from pitchiq.checks import flight_flags

    for t in r.tags:
        d = TRACK / f'h3d_{ball17.version(t)}'
        chains, ball = d / f'{t}_chains2.npy', d / f'{t}_ball3d2.npz'
        if not (chains.exists() and ball.exists()):
            if r.a.plan:
                r.say(f'flight check {t}: to do')
            continue
        flags = flight_flags(np.load(chains, allow_pickle=True), np.load(ball)['state'])
        if not flags:
            r.say(f'flight check {t}: ok')
        elif r.a.trust_cal or r.a.plan:
            why = ' (--trust-cal)' if r.a.trust_cal else ''
            r.say(f'flight check {t}: {", ".join(flags)}; carrying on{why}')
        else:
            raise Stop(
                f'the 3D ball of {t} says its camera is wrong: {", ".join(flags)} ({chains}). '
                'Every kick would be put on the grass. Check the camera height and pins, '
                'or pass --trust-cal to carry on anyway.',
                coach='The ball’s flights did not fit this camera, so the stats would treat '
                'every kick as a ground ball.',
            )


def step_ball_and_maps(r: Run) -> None:
    """The ball step and both halves' maps at the same time.

    The ball (the look #17 ball over both halves at once, self-trained on this game's own
    tracks) needs both passes. The maps need only each pass's players and camera, not the
    ball, so they run beside it. The ball is the long one, 3-6 h and mostly the GPU, and it
    keeps the coach's progress bar; the maps (15-40 min a half, the CPU) leave BALL_GB free
    for it and are done long before its 3D stage, the part that needs the most memory.
    A map that fails is said at once; the run stops when the ball has finished (its work is
    kept either way, and a rerun skips it).
    """
    todo = [t for t in r.tags if not (MAPS / f'{t}.npz').exists()]
    ball = not ball_done(r)
    if r.a.plan:
        work = 'to do (about 3-6 h: 30 min of training, TAPNext bridges, 3D per half)'
        r.say(f'ball {r.g}: ' + (work if ball else 'done'))
        beside = ', beside the ball' if ball else ''
        for t in r.tags:
            r.say(f'map {t}: ' + (f'to do (15-30 min{beside})' if t in todo else 'done'))
        flight_check(r)
        return
    for t in todo:
        if t in HELD_OUT and not r.a.final:
            raise Stop(f'{t} is held out: its map is made once, with --final')
    # The features need the GPU and a video read, so they go first, alone. The pass makes
    # them; only a half passed before 2026-11-21 lacks them (deleted that day).
    for t in todo:
        if not (DETS / f'{t}_feat.npz').exists():  # the map links worse without
            r.py(f'features_{t}', 'pitchiq.experiments.player_lab', 'features', '--tag', t)
    failed: list[BaseException] = []
    mapping = [None]  # the half the maps are on

    def maps():
        r.beside.on = ball
        try:
            for t in todo:
                mapping[0] = t
                map_one(r, t, BALL_GB if ball else 0.0)
        except BaseException as e:  # Stop, or a bug: raised in the main thread after the ball
            failed.append(e)
            if ball:
                why = 'The ball step goes on; the run stops when it ends.'
                r.say(f'map stopped: {e}. {why}')
                r.tell('map stopped', f'{e}\n\n{why}', 'high')

    for t in r.tags:
        if t not in todo:
            r.say(f'map {t}: done')

    def after_maps(args):
        """A ball that ran out of memory beside the maps tries again once they are done."""
        if side.is_alive():
            r.say(f'ball {r.g}: out of memory beside the maps; trying again once they finish')
            side.join()
        return args

    side = threading.Thread(target=maps, name='maps', daemon=True)
    side.start()
    follow = False  # whether the coach's bar follows the maps once the ball is done
    try:
        if ball:
            tags = ','.join(r.tags)
            r.py(f'ball17_{r.g}', 'pitchiq.balltrack.ball17', r.g, tags, fewer=after_maps)
            follow = True
        else:
            r.say(f'ball {r.g}: done')
    finally:
        # never leave a map running behind a stopped run: its processes would go on alone
        shown = None
        while side.is_alive():
            key = step_of(f'map_{mapping[0]}') if follow and mapping[0] else None
            if key and key != shown:  # the bar moves on from the ball to the map
                r.progress.begin(key)
                shown = key
            elif key:
                r.tick(f'map_{mapping[0]}', key)
            side.join(timeout=TICK_S)
    if ball:
        from pitchiq.balltrack import ball17

        missing = [t for t in r.tags if ball17.version(t) != ball17.VER]
        if missing:
            log = DATA / 'ball17' / r.g / 'log.txt'
            raise Stop(f'the ball step left no v17 ball for {", ".join(missing)} ({log})')
    if failed:
        raise failed[0]
    flight_check(r)


def map_one(r: Run, t: str, reserve_gb: float) -> None:
    """One half's map, with as many processes as the memory free now allows after
    `reserve_gb` for whatever runs beside it."""
    from pitchiq.cli import preflight

    extra = ['--final'] if t in HELD_OUT else []
    n = preflight.procs_for(MAP_PROCS, MAP_GB, reserve_gb=3.0 + reserve_gb)
    if n < MAP_PROCS:
        why = f', {reserve_gb:.0f} GB kept for the ball' if reserve_gb else ''
        r.say(f'map {t}: {n} processes, not {MAP_PROCS}: {preflight.free_gb():.0f} GB free{why}')
    r.py(f'map_{t}', 'pitchiq.experiments.wholemap', t, '--procs', n, *extra, fewer=fewer_map)
    (DETS / f'{t}_feat.npy').unlink(missing_ok=True)  # the map's unpacked copy (5 GB)


def fewer_map(args):
    """A map that ran out of memory tries again with half the processes."""
    a = list(args)
    i = a.index('--procs') + 1
    a[i] = max(1, int(a[i]) // 2)
    return a


def fewer_pass(args):
    """A pass that ran out of memory tries again with half the detector processes and one
    ball lane. It resumes from the chunks it finished."""
    from pitchiq.balltrack import config

    return [*args, '--workers', max(1, config.HALF_WORKERS // 2), '--lanes', 1]


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
                + ('done' if (r.out / f'pipe_{t}.json').exists() else 'to do (a few minutes)')
            )
        return
    side = attacks_first(r)
    tag_log = Path(r.a.tag_log).resolve() if r.a.tag_log else None
    # `source`: a report from the old video path (no `source` in its key) is counted again, and
    # `pictures`: one from the table made before the players' pictures were cut is made again
    key = dict(ours=r.a.ours, attacks_first=side, tag_log=str(tag_log) if tag_log else None,
               tag_log_mtime=tag_log.stat().st_mtime if tag_log else None, source='table',
               pictures=True)  # fmt: skip
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
    self_check(r, full)
    if r.a.no_publish:
        r.say(f'report written, not published: {full}')
        return
    r.py('publish', 'pitchiq', 'publish_report', full, '--team', r.a.team, '--match', r.a.match)
    r.say(f'published {full.name} to match {r.a.match}')
    publish_viewer(r, full)


def publish_viewer(r: Run, full: Path) -> None:
    """The match viewer's map and moments (viewer_export, then publish_viewer), after the stats.

    Never stops the run and is never tried twice: the stats are already on the site, and a
    viewer that fails costs the coach the viewer and nothing else. It says so in run.log and
    notify.log, with the log to read.
    """
    view = r.out / 'view'
    for name, args in (
        (
            'viewer',
            (
                'pitchiq',
                'viewer_export',
                r.g,
                '--ours',
                r.a.ours,
                '--report',
                full,
                '--run-dir',
                r.out,
                '--out',
                view,
            ),
        ),
        (
            'publish_viewer',
            ('pitchiq', 'publish_viewer', view, '--team', r.a.team, '--match', r.a.match),
        ),
    ):
        if r.py(name, *args, check=False):
            text = (
                f'{name} failed; the stats are published without the match viewer. '
                f'See {r.logs / (name + ".log")}'
            )
            r.say(text)
            r.tell('no match viewer', text)
            return
    r.say(f'match viewer published to match {r.a.match}')


def final_score(text: str) -> tuple[int, int]:
    m = re.fullmatch(r'\s*(\d+)\s*[-:]\s*(\d+)\s*', text)
    if not m:
        raise argparse.ArgumentTypeError(f'{text!r} is not a score like 2-3 (ours-theirs)')
    return int(m.group(1)), int(m.group(2))


def score_of(r: Run) -> tuple[tuple[int, int] | None, str]:
    """The final score for the goals check and where it came from: `--score`, else the goals
    in the coach's log (the --tag-log file, else the match's log on the site)."""
    from pitchiq import run_checks
    from pitchiq.cli.event_report import load_tag_log

    if r.a.score:
        return r.a.score, 'the final score given'
    if r.a.tag_log:
        return run_checks.logged_score(load_tag_log(r.a.tag_log)), 'the coach’s log'
    if r.db is None:
        return None, ''
    try:
        match = r.db.collection('teams').document(r.a.team).collection('matches')
        entries = [d.to_dict() for d in match.document(r.a.match).collection('log').stream()]
    except Exception as e:  # a check is never worth a finished run
        r.say(f'self-check: the match log could not be read ({e})')
        return None, ''
    return run_checks.logged_score(entries), 'the coach’s log'


def self_check(r: Run, full: Path) -> None:
    """The finished report checked against itself (run_checks); each flag goes to run.log,
    notify.log and the top of the report's warnings. Nothing stops."""
    import numpy as np

    from pitchiq import run_checks
    from pitchiq.balltrack import ball17

    report = json.loads(full.read_text(encoding='utf-8'))
    halves = [json.loads((r.out / f'pipe_{t}.json').read_text(encoding='utf-8')) for t in r.tags]
    air = {}
    for t in r.tags:
        path = TRACK / f'h3d_{ball17.version(t)}' / f'{t}_ball3d2.npz'
        air[t] = run_checks.air_share(np.load(path)['state']) if path.exists() else None
    score, source = score_of(r)
    checks = run_checks.self_checks(report, halves, score, air, source)
    flags = run_checks.flags(checks)
    report['self_checks'] = run_checks.as_json(checks)
    report['warnings'] = flags + [w for w in report.get('warnings') or [] if w not in flags]
    report['trustworthy'] = bool(report.get('trustworthy')) and not flags
    full.write_text(json.dumps(report, indent=1), encoding='utf-8')
    for c in checks:
        verdict = {True: 'ok', False: 'FLAG', None: 'not checked'}[c.ok]
        r.say(f'self-check {c.key}: {verdict}: {c.text}')
    if flags:
        r.tell('self-checks', '\n'.join(flags))


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
        '--score',
        type=final_score,
        help="the final score, ours-theirs (e.g. 2-3), for the goals check when the coach's "
        'log has none',
    )
    p.add_argument(
        '--no-publish', action='store_true', help='write the report file but not to the site'
    )
    p.add_argument(
        '--plan', action='store_true', help='say what is done and what would run; run nothing'
    )
    p.add_argument(
        '--trust-cal',
        action='store_true',
        help='carry on when the calibration or flight check fails',
    )
    p.add_argument(
        '--trust-halves',
        action='store_true',
        help="carry on when the halves' lengths or the break look wrong",
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
        '--pins-from',
        type=game_code,
        metavar='GAME',
        help="carry an earlier game's pins instead of asking the coach (the same field, the "
        'camera in the same place: checked, and the run stops if not)',
    )
    p.add_argument(
        '--final', action='store_true', help='allow the map of a held out half (scored once)'
    )
    p.add_argument(
        '--list-matches',
        action='store_true',
        help='print team and match ids from the site, and stop',
    )
    p.add_argument(
        '--requests',
        action='store_true',
        help='print the matches coaches have asked to have processed, and stop',
    )
    return p  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.list_matches:
        return list_matches()
    if a.requests:
        return list_requests()
    if not (a.video and a.game):
        p.error('a video and --game are needed')
    if not a.ours and not (a.team and a.match and not a.plan):
        p.error("--ours is needed (it comes from the coach's request with --team and --match)")
    if bool(a.h1) != bool(a.h2):
        p.error('give both --h1 and --h2, or neither')
    if a.pins_from and (a.no_pins or a.pins_from == a.game):
        p.error('--pins-from takes an earlier game, and not with --no-pins')
    a.video = a.video.resolve()
    r = Run(a)
    if a.plan:
        return run_steps(r)
    from pitchiq.cli import resume

    # a reboot kills the run but leaves this mark, and the logon task (resume.py) starts it again
    resume.mark(r.out, sys.argv[1:] if argv is None else argv)
    resume.keep_awake()
    if os.environ.get('PIQ_RESUMED'):
        r.say(
            f'resumed after a restart (time {os.environ["PIQ_RESUMED"]}): finished steps are skipped'
        )
    try:
        return run_steps(r)
    finally:
        resume.clear(r.out)


def run_steps(r: Run) -> int:
    """The checks, then every step; 1 when one stopped."""
    a = r.a
    try:
        r.video_s = check_video(a.video)
        check_inputs(r)
        db = r.db = check_site(a)
        if db is not None:
            r.progress = Progress(db, a.team, a.match)
        r.say(
            f'{"plan for" if a.plan else "processing"} {a.video.name} as {r.g} (ours: {a.ours}, lines: {a.lines})'
        )
        steps = (step_halves, step_camera, step_pass, step_ball_and_maps, step_stats, step_publish)
        for step in steps:
            step(r)
    except Stop as e:
        r.say(f'STOPPED: {e}')
        r.progress.stopped(e.coach)
        r.tell('stopped', f'{e}\n\nRun the same command again once it is fixed.', 'high')
        print('Run the same command again once that is fixed: finished steps are skipped.')
        return 1
    except KeyboardInterrupt:  # someone pressed Ctrl+C: they know
        r.say('STOPPED: interrupted')
        r.progress.stopped('')
        return 1
    except Exception as e:  # a bug, not a check: say so, then show the traceback
        r.say(f'STOPPED: process_match itself failed: {type(e).__name__}: {e}')
        r.progress.stopped('')
        r.tell('crashed', f'process_match failed: {type(e).__name__}: {e}. See run.log.', 'high')
        raise
    footage_note(r)
    if not a.plan:
        r.progress.finished()
        where = 'kept here (--no-publish)' if a.no_publish else 'on the site'
        r.tell('done', f'{a.video.name}: every step finished; the stats are {where}.', 'low')
    r.say('plan only: nothing was run' if a.plan else 'ALL DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
