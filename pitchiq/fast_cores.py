"""Take a run's processes out of Windows' efficiency mode, so they use the fast cores too.

Windows 11 runs background processes that say nothing about it in efficiency mode
(EcoQoS). On a hybrid processor that keeps them on the efficiency cores: on this PC's
i7-14700F the pipeline ran on its 12 E-cores while the 16 P-core threads sat at about 2%
(measured 23 Nov 2026, session step 3.14). Turning it off is a per-process hint, like a
priority (SetProcessInformation, ProcessPowerThrottling); no system setting changes. A
child does not inherit it, so `tree` switches every process under a root, and the caller
calls it again as new ones start. Measured with it off on an otherwise quiet PC, outputs
byte-identical: the pass's ball detector 1.6x faster (one 5-minute chunk, 12 workers,
347 -> 212 s), the ball 3D's flight search 4.0x with 16 workers instead of 6 (574 -> 144 s).

PIQ_FAST_CORES=1 turns it on for process_match's steps (and 16 flight-search workers in
the ball step instead of 6, which only pay with it on). Elsewhere, and off Windows, every
call here does nothing.
"""

from __future__ import annotations

import os
import sys

ON = os.environ.get('PIQ_FAST_CORES', '0') == '1'

_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS ProcessPowerThrottling
_EXECUTION_SPEED = 0x1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED: EcoQoS


def switch(pid: int) -> bool:
    """Efficiency mode off for one process: this program controls it, and it is off.
    True if Windows took it; False off Windows or if the process is gone."""
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [
            ('Version', wintypes.ULONG),
            ('ControlMask', wintypes.ULONG),
            ('StateMask', wintypes.ULONG),
        ]

    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.SetProcessInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(_PROCESS_SET_INFORMATION, False, pid)
    if not h:
        return False
    try:
        s = State(Version=1, ControlMask=_EXECUTION_SPEED, StateMask=0)
        return bool(
            k.SetProcessInformation(h, _PROCESS_POWER_THROTTLING, ctypes.byref(s), ctypes.sizeof(s))
        )
    finally:
        k.CloseHandle(h)


def tree(pid: int, done: set) -> None:
    """`switch` a process and everything it started, skipping those in `done`
    ((pid, start time) pairs, so a reused pid is a new process) and adding the ones
    switched. Nothing off Windows."""
    if sys.platform != 'win32':
        return
    import psutil

    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return
    for p in procs:
        try:
            key = (p.pid, p.create_time())
        except psutil.Error:  # it ended between the listing and the look
            continue
        if key not in done and switch(p.pid):
            done.add(key)
