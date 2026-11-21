"""process_match retries a failed step once, with fewer processes when it ran out of memory."""

from types import SimpleNamespace

import pytest

from pitchiq.cli import preflight
from pitchiq.cli import process_match as pm


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, 'DATA', tmp_path)
    a = SimpleNamespace(game='zz', plan=False, video=tmp_path / 'v.mp4', lines='white')
    r = pm.Run(a)
    r.logs.mkdir(parents=True)
    return r


def fake_launch(r, results, log_text):
    """Each try returns the next exit code and appends its text to the step's log."""
    tries = []

    def launch(name, args):
        tries.append(tuple(args))
        with open(r.logs / f'{name}.log', 'a', encoding='utf-8') as f:
            f.write(log_text[len(tries) - 1] + '\n')
        return results[len(tries) - 1]

    r.launch = launch
    return tries


def test_a_map_out_of_memory_tries_again_with_half_the_processes(run):
    tries = fake_launch(
        run,
        [1, 0],
        ['numpy._core._exceptions._ArrayMemoryError: Unable to allocate 4.7 GiB', 'done'],
    )
    args = ('pitchiq.experiments.wholemap', 'zz1', '--procs', 8)
    assert run.py('map_zz1', *args, fewer=pm.fewer_map) == 0
    assert tries == [args, ('pitchiq.experiments.wholemap', 'zz1', '--procs', 4)]
    assert 'out of memory' in (run.out / 'run.log').read_text(encoding='utf-8')


def test_any_other_failure_tries_the_same_command_again(run):
    tries = fake_launch(run, [1, 0], ['KeyError: 7', 'done'])
    args = ('pitchiq.experiments.wholemap', 'zz1', '--procs', 8)
    assert run.py('map_zz1', *args, fewer=pm.fewer_map) == 0
    assert tries == [args, args]


def test_a_step_that_fails_twice_stops_the_run_with_its_log(run):
    fake_launch(run, [1, 3], ['first', 'Traceback: the second try'])
    with pytest.raises(pm.Stop, match='failed twice') as e:
        run.py('stats_zz1', 'pitchiq.experiments.cached_report', 'zz1')
    assert 'the second try' in str(e.value)


def test_a_step_run_without_check_is_not_retried(run):
    tries = fake_launch(run, [2], ['no transfer'])
    assert run.py('transfer_zz2', 'pitchiq.experiments.pincal', check=False) == 2
    assert len(tries) == 1


def test_a_pass_out_of_memory_runs_fewer_detectors_and_one_lane():
    from pitchiq.balltrack import config

    a = pm.fewer_pass(('pitchiq.balltrack.half', 'zz1', 0, 9))
    assert a == [
        'pitchiq.balltrack.half',
        'zz1',
        0,
        9,
        '--workers',
        config.HALF_WORKERS // 2,
        '--lanes',
        1,
    ]


def test_out_of_memory_is_read_from_the_log():
    assert preflight.out_of_memory('OSError: [WinError 1455] The paging file is too small')
    assert preflight.out_of_memory('torch.OutOfMemoryError: CUDA out of memory.')
    assert not preflight.out_of_memory('ValueError: no frames')


def test_processes_fit_the_free_memory(monkeypatch):
    monkeypatch.setattr(preflight, 'free_gb', lambda: 19.0)
    assert preflight.procs_for(8, 3.5) == 4  # (19 - 3) // 3.5
    monkeypatch.setattr(preflight, 'free_gb', lambda: 2.0)
    assert preflight.procs_for(8, 3.5) == 1
    monkeypatch.setattr(preflight, 'free_gb', lambda: 60.0)
    assert preflight.procs_for(8, 3.5) == 8
