"""pitchiq/detector.py's weights: not in git, and a clone still gets the right file.

`*.pt` is ignored, so a fresh clone has no `pitchiq/weights/yolov8n.pt`. What
fills the gap is Ultralytics itself: handed a path that does not exist whose
name is one of its release assets, it downloads that asset to that path. The
file it fetches (release v8.4.0) was compared byte for byte with the one every
measurement in this repo used, on 2026-10-20: the same sha256, below. These
tests hold the two halves of that in place, so a rename of the default or an
Ultralytics that stops publishing the name fails here rather than at the field.

Run:  .venv/Scripts/python.exe -m pytest tests/test_detector_weights.py -q
"""

from __future__ import annotations

import hashlib

import pytest

from pitchiq.detector import DEFAULT_WEIGHTS

SHA256 = 'f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36'


def test_the_default_is_a_name_ultralytics_will_download():
    downloads = pytest.importorskip('ultralytics.utils.downloads')
    assert DEFAULT_WEIGHTS.name in downloads.GITHUB_ASSETS_NAMES


def test_the_readme_pins_the_same_file():
    readme = (DEFAULT_WEIGHTS.parents[2] / 'README.md').read_text(encoding='utf-8')
    assert 'v8.4.0/yolov8n.pt' in readme
    assert SHA256 in readme


@pytest.mark.skipif(not DEFAULT_WEIGHTS.exists(), reason='weights not downloaded yet')
def test_the_local_file_is_the_release_file():
    assert hashlib.sha256(DEFAULT_WEIGHTS.read_bytes()).hexdigest() == SHA256
