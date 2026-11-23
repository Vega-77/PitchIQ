"""autocal_run.spawn_pool: the panorama's workers load OpenBLAS with one thread each, which
cut the step's commit from 24 to 7 GB (session step 3.11), and this process keeps its own."""

import os

from pitchiq.experiments import autocal_run as AR


def _blas(_):
    return os.environ.get('OPENBLAS_NUM_THREADS')


def test_the_workers_get_one_blas_thread_and_this_process_keeps_its_setting(monkeypatch):
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '7')
    with AR.spawn_pool(2) as pool:
        assert pool.map(_blas, range(2)) == ['1', '1']
    assert os.environ['OPENBLAS_NUM_THREADS'] == '7'


def test_a_variable_unset_here_stays_unset(monkeypatch):
    monkeypatch.delenv('OPENBLAS_NUM_THREADS', raising=False)
    with AR.spawn_pool(1) as pool:
        assert pool.map(_blas, [0]) == ['1']
    assert 'OPENBLAS_NUM_THREADS' not in os.environ
