"""What `process_match` checks in its first seconds, before anything long starts.

A missing model, a full disk or a second run beside this one used to show up hours
in: the ball step reads its fit data about 6 hours into a new game. Each check here
returns the problems it finds as sentences that say what to fix. `process_match`
stops on any of them; with `--plan` it only lists them.

    from pitchiq.cli import preflight
    preflight.problems(video, halves, need)   # [] when the run can start
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO.parent / 'PitchIQ-data'
TRACK = REPO / 'pitchiq' / 'balltrack'
FIT = DATA / 'pipeline' / 'ballfit'

# A game keeps about 12 GB (9 GB of it player features), the map step unpacks one half's
# features (about 5 GB, removed after), and Windows wants about 10 GB of room for the
# pagefile: the silent crashes of October were a full disk.
DISK_GB = 35
DISK_GB_LATE = 5  # once both passes are done: maps, stats and the report
RAM_GB = 12  # free at the start (this 32 GB PC shows about 19 with the desktop open)
GPU_MB = 3000  # in use already (the desktop alone uses about 1.2 GB of the 8 GB card)

# Minutes. High-school halves are 40 minutes plus stoppage: the games here ran
# 41.7-49.8, with breaks of 7.8-11.2. A finder guess outside these is wrong.
HALF_MIN = (35.0, 55.0)
BREAK_MIN = (4.0, 25.0)

# (file, what it is, the step that reads it)
INPUTS = [
    (REPO / 'pitchiq' / 'weights' / 'yolov8n.pt', 'the person detector', 'halves, pass'),
    (TRACK / 'rank7.model.json', 'the ball candidate ranker', 'pass'),
    (TRACK / 'rank7.model.json.affine.json', "the ranker's score scale", 'pass'),
    (TRACK / 'still4.npz', "the ranker's ball-size table", 'pass'),
    (TRACK / 'cls_C.pt', 'the ball crop classifier', 'pass'),
    (
        TRACK / 'tapnext_bridge' / 'tapnextpp_512.ckpt',
        'TAPNext++, the ball gap filler',
        'pass, ball',
    ),
    (TRACK / 'pitch_registration' / 'vpitch.npz', 'the default pitch template', 'pass'),
    (TRACK / 'pitch_registration' / 'vtop.npz', 'the default pitch template', 'pass'),
    (TRACK / 'crop_labels' / 'crops.jsonl', 'the hand ball labels', 'ball'),
    (FIT / 'devpass' / 'MANIFEST.json', "the ball fit's frozen dev pieces", 'ball'),
    (FIT / 'labels' / 'oof_k3.npz', "the ball fit's dev labels", 'ball'),
    (FIT / 'models' / 'resnet18-f37072fd.pth', "the ball fit's starting weights", 'ball'),
    (FIT / 'halfvideo.json', "the ball fit's video list", 'ball'),
    (FIT / 'ckLlabels.json', "the ball fit's click labels", 'ball'),
    (FIT / 'clicks' / 'items_all.json', "the ball fit's click answers", 'ball'),
    (REPO / 'pitchiq' / 'xg_model8.onnx', 'the xG model', 'stats'),
    (REPO / 'shared' / 'run-steps.json', "the steps' expected minutes", 'progress'),
]
FOLDERS = [  # each must hold files
    (FIT / 'cache', "the ball fit's crop cache", 'ball'),
    (FIT / 'tcache', "the ball fit's neighbour-frame cache", 'ball'),
    (FIT / 'crops', "the ball fit's labelled crops", 'ball'),
    (FIT / 'oof_cls', "the ball fit's dev scores", 'ball'),
    (FIT / 'devpass', "the ball fit's frozen dev pieces", 'ball'),
]
# a process whose command line holds one of these is a pipeline run
RUN_MODULES = (
    'process_match',
    'balltrack.half',
    'ball17',
    'detect_candidates',
    'tn_engine',
    'wholemap',
    'cached_report',
    'player_lab',
    'evaluation.mk3d',
)


def missing_inputs() -> list[str]:
    """The fixed inputs that are not on disk."""
    out = [
        f'{what} ({step}) is missing: {path}'
        for path, what, step in INPUTS
        if not path.is_file() or path.stat().st_size == 0
    ]
    out += [
        f'{what} ({step}) is missing or empty: {path}'
        for path, what, step in FOLDERS
        if not path.is_dir() or not any(path.iterdir())
    ]
    from pitchiq.extras import MissingExtra, check

    try:
        check('balltrack')
    except MissingExtra as e:
        out.append(str(e).replace('\n', ' '))
    return out


def disk_problems(need_gb: float, where: Path = REPO) -> list[str]:
    free = shutil.disk_usage(where).free / 2**30
    if free >= need_gb:
        return []
    return [
        f'{where.anchor or where} has {free:.0f} GB free and this run needs about {need_gb:.0f} GB '
        '(its files plus room for the pagefile). Free some space, then run again.'
    ]


def _top_memory(n: int = 4) -> str:
    import psutil

    use: dict[str, float] = {}
    for p in psutil.process_iter(['name', 'memory_info']):
        mi = p.info.get('memory_info')
        if mi:
            use[p.info['name']] = use.get(p.info['name'], 0.0) + mi.rss / 2**30
    top = sorted(use.items(), key=lambda kv: -kv[1])[:n]
    return ', '.join(f'{name} {gb:.1f} GB' for name, gb in top)


def memory_problems(need_gb: float = RAM_GB) -> list[str]:
    import psutil

    free = psutil.virtual_memory().available / 2**30
    if free >= need_gb:
        return []
    return [
        f'only {free:.0f} GB of memory is free and the run needs about {need_gb:.0f} GB to start. '
        f'Close what is using it (the most now: {_top_memory()}), then run again.'
    ]


def free_gb() -> float:
    import psutil

    return psutil.virtual_memory().available / 2**30


def procs_for(want: int, each_gb: float, reserve_gb: float = 3.0) -> int:
    """How many processes of `each_gb` fit in the memory free now, keeping `reserve_gb`
    for the rest of the machine: at most `want`, at least 1."""
    return max(1, min(want, int((free_gb() - reserve_gb) // each_gb)))


# The ends of a log that mean a step ran out of memory, not that it is wrong.
OUT_OF_MEMORY = (
    'MemoryError',
    'Unable to allocate',
    'paging file is too small',  # Windows' commit limit (error 1455)
    'CUDA out of memory',
    'OutOfMemoryError',
    'DefaultCPUAllocator: not enough memory',
)


def out_of_memory(log_tail: str) -> bool:
    return any(s in log_tail for s in OUT_OF_MEMORY)


def gpu_problems(max_used_mb: float = GPU_MB) -> list[str]:
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
        used, total = (float(x) for x in out.strip().splitlines()[0].split(','))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return ['no NVIDIA GPU answered (nvidia-smi): the pass and the ball step need it']
    if used > max_used_mb:
        return [
            f'the GPU already has {used:.0f} of {total:.0f} MB in use. Close what is using it '
            '(a game, a video, another run), then run again.'
        ]
    import torch

    if not torch.cuda.is_available():
        return ['torch sees no CUDA GPU: the pass and the ball step need it']
    return []


def _is_run(cmdline: list[str]) -> bool:
    return any(m in arg for arg in cmdline for m in RUN_MODULES) and any(
        'python' in arg.lower() for arg in cmdline[:1]
    )


def other_runs() -> list[str]:
    """Pipeline processes other than this one and its parents: two runs at once page the
    machine to a crawl and can write the same files."""
    import psutil

    me = psutil.Process()
    mine = {me.pid} | {p.pid for p in me.parents()} | {p.pid for p in me.children(recursive=True)}
    runs = {}
    for p in psutil.process_iter(['pid', 'ppid', 'cmdline', 'create_time']):
        cmd = p.info.get('cmdline') or []
        if p.info['pid'] not in mine and _is_run(cmd):
            runs[p.info['pid']] = p.info
    out = []
    for pid, info in runs.items():
        if info['ppid'] in runs:  # the venv's python.exe is a launcher: name the run once
            continue
        cmd = info['cmdline']
        since = time.strftime('%d %b %H:%M', time.localtime(info['create_time']))
        shown = ' '.join(Path(cmd[0]).name if i == 0 else a for i, a in enumerate(cmd))[:120]
        out.append(
            f'another pipeline process is running: PID {pid}, since {since} ({shown}). '
            'Let it finish or stop it, then run again.'
        )
    return out


def halves_problems(halves: list[tuple[float, float]], video_s: float | None = None) -> list[str]:
    """What is implausible about two halves given in seconds on the video clock."""
    out = []
    if len(halves) < 2:
        return [f'{len(halves)} half found; a game has two']
    (s1, e1), (s2, e2) = halves[:2]
    for n, (s, e) in enumerate(halves[:2], 1):
        m = (e - s) / 60
        if not HALF_MIN[0] <= m <= HALF_MIN[1]:
            out.append(
                f'half {n} is {m:.0f} min long; a half runs {HALF_MIN[0]:.0f}-{HALF_MIN[1]:.0f} min'
            )
    gap = (s2 - e1) / 60
    if not BREAK_MIN[0] <= gap <= BREAK_MIN[1]:
        out.append(
            f'the break between the halves is {gap:.0f} min; '
            f'it runs {BREAK_MIN[0]:.0f}-{BREAK_MIN[1]:.0f} min'
        )
    if video_s is not None and e2 > video_s + 1:
        out.append(f'half 2 ends at {e2 / 60:.1f} min but the video is {video_s / 60:.1f} min long')
    return out


def problems(need: dict) -> list[str]:
    """Every check the run's remaining steps need. `need` says which: `disk_gb`, and
    whether `ram` and `gpu` are needed (False once the steps using them are done)."""
    out = missing_inputs() + other_runs() + disk_problems(need['disk_gb'])
    if need.get('ram'):
        out += memory_problems()
    if need.get('gpu'):
        out += gpu_problems()
    return out


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog='python -m pitchiq preflight',
        description='Check this PC can start a pipeline run: inputs, disk, memory, GPU, '
        'and no other run going.',
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    found = problems(dict(disk_gb=DISK_GB, ram=True, gpu=True))
    print('\n'.join(found) or 'all checks pass')
    return 1 if found else 0


if __name__ == '__main__':
    raise SystemExit(main())
