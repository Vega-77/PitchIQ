"""detect_candidates.one_blas_pool: the ball detector's workers start with one BLAS thread.

A spawned worker re-imports detect_candidates, and `import pitchiq` loads numpy before
worker() runs, so a thread count set there is too late: OpenBLAS had already sized its buffers
for every core (0.7 GB of commit a worker on a 28-thread PC, session step 3.17).
"""

import os

from pitchiq.balltrack import detect_candidates


def test_workers_start_with_one_blas_thread_and_this_process_keeps_its_own(monkeypatch):
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '8')
    monkeypatch.delenv('OMP_NUM_THREADS', raising=False)
    monkeypatch.delenv('MKL_NUM_THREADS', raising=False)
    with detect_candidates.one_blas_pool(1) as pool:
        seen = pool.map(os.getenv, detect_candidates.ONE_BLAS)  # read inside the worker
    assert seen == ['1'] * len(detect_candidates.ONE_BLAS)
    assert os.environ['OPENBLAS_NUM_THREADS'] == '8'
    assert 'OMP_NUM_THREADS' not in os.environ and 'MKL_NUM_THREADS' not in os.environ
