"""The streaming frame reader that lets analyse_match survive a real match.

analyse_match used to read its whole window into a list before detecting
anything — 2.8 MB per 720p frame, so roughly 5 GB a minute and about 227 GB for
a 45-minute half. It could analyse a clip and could never analyse a match.

These tests pin the two properties that fix depends on: every frame in the
window is delivered exactly once and in order, and no more than one batch is
ever held at a time.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_pipeline_stream.py -q
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from cv.pipeline import BATCH_FRAMES, _stream_batches

FPS = 30.0
WIDTH, HEIGHT = 64, 48


def stamp(frame_number: int) -> int:
    """The pixel value frame N is filled with. Steps of 4 survive mp4v."""
    return (frame_number % 64) * 4


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    """A 90-frame video whose Nth frame is filled with a value derived from N.

    Encoding the index into the pixels lets the tests assert on ordering rather
    than merely on counts. The step is 4 rather than 1 because mp4v is lossy and
    quantises a one-unit difference between flat frames away entirely — the
    first version of this fixture tested the encoder, not the reader.
    """
    path = tmp_path_factory.mktemp('video') / 'counter.mp4'
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*'mp4v'), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        pytest.skip('no mp4v encoder available in this OpenCV build')

    for i in range(90):
        writer.write(np.full((HEIGHT, WIDTH, 3), stamp(i), dtype=np.uint8))
    writer.release()
    return path


def collect(path, start_s=0.0, end_s=None):
    return list(_stream_batches(path, FPS, start_s, end_s))


def test_delivers_every_frame_in_the_window(clip):
    batches = collect(clip, 0.0, 1.0)          # 30 frames
    assert sum(len(b) for _, b in batches) == 30


def test_batches_are_capped_so_memory_stays_flat(clip):
    # The whole point: whatever the window, only one batch is live at a time.
    batches = collect(clip, 0.0, 3.0)
    assert all(len(b) <= BATCH_FRAMES for _, b in batches)
    assert max(len(b) for _, b in batches) == BATCH_FRAMES


def test_indices_are_contiguous_absolute_frame_numbers(clip):
    batches = collect(clip, 0.0, 2.0)
    expected = batches[0][0]
    for first_index, batch in batches:
        assert first_index == expected
        expected += len(batch)


def test_a_window_shorter_than_one_batch_still_yields(clip):
    batches = collect(clip, 0.0, 0.2)          # 6 frames, under BATCH_FRAMES
    assert len(batches) == 1
    assert len(batches[0][1]) == 6


def test_the_seek_lands_near_where_it_was_aimed(clip):
    """Near, not exact — and that is the point of absolute indices.

    CAP_PROP_POS_FRAMES seeks to the nearest keyframe, so asking for frame 30
    can deliver frame 26. The reader reports where it actually landed rather
    than pretending, because analyse_match dates every detection from this and
    that dating has to line up with a hand-tagged match log.
    """
    first_index, _ = collect(clip, 1.0, 1.5)[0]
    assert first_index == pytest.approx(30, abs=15)


def test_frames_arrive_in_order_without_repeats(clip):
    """Ordering, checked through the pixels.

    Asserting the exact value written for each frame does not work: mp4v is
    lossy and decodes this fixture a frame behind, so [0, 4, 8] comes back as
    [0, 0, 3]. That lag preserves order, which is the property worth pinning —
    it still catches a batch delivered twice or out of sequence.
    """
    values = [
        int(frame[0, 0, 0])
        for _, batch in collect(clip, 0.0, 1.5)
        for frame in batch
    ]
    assert values == sorted(values), 'frames came back out of order'
    assert len(set(values)) > len(values) // 2, 'too many repeated frames'


def test_no_end_reads_to_the_last_frame(clip):
    total = sum(len(b) for _, b in collect(clip))
    assert total == 90


def test_a_window_past_the_end_yields_nothing(clip):
    assert collect(clip, 60.0, 70.0) == []


def test_missing_file_is_reported_as_such(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(_stream_batches(tmp_path / 'nope.mp4', FPS, 0.0, 1.0))
