"""pitchiq.detector.whole_batches: a slow NMS no longer drops a batch's later images.

On 23 Nov 2026 the wbt1 pass lost every player in 18 frames: ultralytics' NMS ran past its
wall-clock limit (2 s + 0.05 s an image) three times on a busy PC and returned the rest of each
batch of 8 empty. These tests stand in a clock that jumps 100 s a look, so the limit is passed
after the first image.
"""

import pytest

torch = pytest.importorskip('torch')
pytest.importorskip('ultralytics')

from ultralytics.utils import nms  # noqa: E402

from pitchiq import detector  # noqa: E402


class _SlowClock:
    """time.time() that moves 100 s every call."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        self.t += 100.0
        return self.t


def _batch(n=3):
    """n images' raw predictions, one class: two well-apart boxes each, confident."""
    # (bs, 4 + nc, anchors): xywh then the class score
    p = torch.zeros((n, 5, 2))
    p[:, 0, :] = torch.tensor([50.0, 300.0])  # x centres
    p[:, 1, :] = 50.0
    p[:, 2, :] = 20.0
    p[:, 3, :] = 40.0
    p[:, 4, :] = 0.9
    return p


@pytest.fixture
def unpatched(monkeypatch):
    """ultralytics' own NMS, put back after the test (whole_batches patches the module)."""
    orig = nms.non_max_suppression
    while getattr(orig, 'whole_batches', False):  # an earlier test (or import) patched it
        orig = orig.func
    monkeypatch.setattr(nms, 'non_max_suppression', orig)
    monkeypatch.setattr(nms, 'time', _SlowClock())
    return orig


def test_the_limit_drops_images_without_it(unpatched):
    out = nms.non_max_suppression(_batch(), conf_thres=0.25)
    assert [len(o) for o in out] == [2, 0, 0]  # what wbt1 saw: the batch's tail comes back empty


def test_whole_batches_keeps_every_image(unpatched):
    detector.whole_batches()
    out = nms.non_max_suppression(_batch(), conf_thres=0.25)
    assert [len(o) for o in out] == [2, 2, 2]


def test_same_boxes_when_the_limit_is_never_hit(monkeypatch):
    orig = nms.non_max_suppression
    while getattr(orig, 'whole_batches', False):
        orig = orig.func
    monkeypatch.setattr(nms, 'non_max_suppression', orig)
    before = nms.non_max_suppression(_batch(), conf_thres=0.25)
    detector.whole_batches()
    after = nms.non_max_suppression(_batch(), conf_thres=0.25)
    assert all(torch.equal(a, b) for a, b in zip(before, after))


def test_twice_is_harmless(unpatched):
    detector.whole_batches()
    first = nms.non_max_suppression
    detector.whole_batches()
    assert nms.non_max_suppression is first
    assert first.func is unpatched  # one layer, not two
