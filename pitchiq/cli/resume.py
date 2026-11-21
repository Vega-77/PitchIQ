"""Restart a pipeline run that a reboot, or a crash of Python itself, cut off.

    python -m pitchiq resume              what the logon task runs (as pitchiq.cli.resume)
    python -m pitchiq resume --list       the runs marked active, and whether each is alive
    python -m pitchiq resume --install    add the Task Scheduler task (once per PC)
    python -m pitchiq resume --uninstall  remove it

While `process_match` runs, it marks the run active in
`PitchIQ-data/stats/<game>/run/active.json`. The mark holds the command, the folder
it ran from, the PIQ_* settings and the key's path (never the key), and the PID.
A run that ends clears the mark, whether it finished, a check stopped it, or someone
pressed Ctrl+C. A run the machine killed keeps it. At the next logon, the task
"PitchIQ\\Resume run" relaunches the same command with no window, its output in
`run/resume.log`. Finished steps are skipped as always. The Woodbridge run lost 6.5
hours to a Windows Update restart.

A run is resumed at most MAX_RESUMES times in a row, so a run that keeps killing the
machine stops being restarted.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO.parent / 'PitchIQ-data'
MARK = 'active.json'
MAX_RESUMES = 3
TASK_PATH, TASK_NAME = '\\PitchIQ\\', 'Resume run'
DELAY = 'PT2M'  # after logon: the GPU driver and the network are up by then
KEPT_ENV = ('PIQ_', 'PITCHIQ_')  # settings the run reads; PITCHIQ_SA_KEY is a path


def mark(out: Path, argv: list[str]) -> None:
    """Mark the run whose files go to `out` active (process_match, at its start)."""
    import psutil

    me = psutil.Process()
    out.mkdir(parents=True, exist_ok=True)
    info = dict(
        argv=list(map(str, argv)),
        cwd=os.getcwd(),
        env={k: v for k, v in os.environ.items() if k.startswith(KEPT_ENV) and k != 'PIQ_RESUMED'},
        pid=me.pid,
        created=me.create_time(),
        started=time.strftime('%Y-%m-%d %H:%M'),
        resumes=int(os.environ.get('PIQ_RESUMED', '0')),
    )
    (out / MARK).write_text(json.dumps(info, indent=1), encoding='utf-8')


def clear(out: Path) -> None:
    """The run ended on its own: nothing to resume."""
    try:
        (out / MARK).unlink()
    except FileNotFoundError:
        pass


def keep_awake() -> None:
    """Ask Windows not to sleep while this process runs (it lapses when the process ends).
    This is the request a video player makes, not a power setting."""
    if sys.platform == 'win32':
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def alive(info: dict) -> bool:
    """Whether the marked process is still the one running (a PID can be reused)."""
    import psutil

    try:
        return abs(psutil.Process(info['pid']).create_time() - info['created']) < 2
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
        return False


def marks() -> list[tuple[Path, dict]]:
    """Every run marked active, newest first."""
    out = []
    for p in DATA.glob(f'stats/*/run/{MARK}'):
        try:
            out.append((p, json.loads(p.read_text(encoding='utf-8'))))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda pi: -pi[1].get('created', 0))


def note(run_dir: Path, text: str) -> None:
    """A line in the run's own run.log, in process_match's format."""
    line = f'{time.strftime("%Y-%m-%d %H:%M")}  {text}'
    print(line, flush=True)
    with open(run_dir / 'run.log', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def launch(path: Path, info: dict) -> int:
    """Start the marked command again, detached and windowless. Its PID."""
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':  # the task starts this with pythonw: no window
        python = python.with_name('python.exe')
    env = dict(os.environ, **info.get('env', {}), PIQ_RESUMED=str(info.get('resumes', 0) + 1))
    log = open(path.parent / 'resume.log', 'a', encoding='utf-8')
    log.write(f'\n=== {time.ctime()}  resumed: process_match {" ".join(info["argv"])}\n')
    log.flush()
    cmd = [str(python), '-W', 'ignore', '-m', 'pitchiq', 'process_match', *info['argv']]
    flags = 0
    if sys.platform == 'win32':
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    kw = dict(cwd=info.get('cwd') or REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:  # outlive the task's job object
        proc = subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)  # BREAKAWAY_FROM_JOB
    except OSError:
        proc = subprocess.Popen(cmd, creationflags=flags, **kw)
    return proc.pid


def resume() -> int:
    """Relaunch the newest run that was cut off. One at a time: a second would stop at
    process_match's check that no other run is going."""
    for path, info in marks():
        if alive(info):
            print(f'{path.parent}: still running (PID {info["pid"]})')
            return 0
    for path, info in marks():
        run_dir = path.parent
        if info.get('resumes', 0) >= MAX_RESUMES:
            note(
                run_dir,
                f'NOT RESUMED: cut off {info["resumes"] + 1} times in a row. Look at run.log and '
                'logs/, then run the command again by hand.',
            )
            path.unlink()
            continue
        pid = launch(path, info)
        note(
            run_dir,
            f'resume: the run started {info.get("started", "?")} was cut off (a restart or a '
            f'crash); started it again as PID {pid}, output in {run_dir / "resume.log"}',
        )
        return 0
    print('no run to resume')
    return 0


def _ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True,
        text=True,
    )


def install() -> int:
    """The logon task: this module under pythonw (no window), two minutes after logon."""
    pyw = Path(sys.executable).with_name('pythonw.exe')
    script = f"""
$a = New-ScheduledTaskAction -Execute '{pyw}' -Argument '-m pitchiq.cli.resume' -WorkingDirectory '{REPO}'
$t = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"
$t.Delay = '{DELAY}'
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Description 'Restarts a PitchIQ process_match run that a reboot cut off (pitchiq/cli/resume.py).' -Force | Out-Null
Get-ScheduledTask -TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}' | Select-Object TaskPath, TaskName, State | Format-List
"""
    r = _ps(script)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def uninstall() -> int:
    r = _ps(
        f"Unregister-ScheduledTask -TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}' -Confirm:$false"
    )
    print(r.stdout.strip() or r.stderr.strip() or 'removed')
    return r.returncode


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq resume',
        description='Restart a pipeline run that a reboot cut off (what the logon task runs).',
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        '--list', action='store_true', help='the runs marked active, and if each is alive'
    )
    g.add_argument('--install', action='store_true', help='add the Task Scheduler task (once a PC)')
    g.add_argument('--uninstall', action='store_true', help='remove it')
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.list:
        for path, info in marks():
            state = 'running' if alive(info) else 'cut off'
            print(
                f'{path.parent}  {state}  started {info.get("started")}  {" ".join(info["argv"])}'
            )
        return 0
    return resume()


if __name__ == '__main__':
    sys.exit(main())
