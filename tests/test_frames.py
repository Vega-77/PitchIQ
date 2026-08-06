"""The single pass that finally lets one frame know all three of its facts.

Before cv/frames.py the pipeline ran the model twice over two identity spaces
that could not see each other: a detection pass that knew the ball and the
shirt colours but numbered players per-detection, and a tracking pass that gave
players persistent ids but discarded the ball. An event needs the ball, a
persistent player and that player's team at the same instant, so it needed
these joined.

These tests run the assembly without a GPU. Both the detector and the tracker
are injected, because everything TrackedFramePass does with a frame is
bookkeeping — which rows go where, which frames get skipped, when a colour gets
sampled — and bookkeeping is the part that breaks quietly.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_frames.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.detector import CLASS_BALL, CLASS_PERSON
from cv.frames import (
    COL_CONF,
    FrameRecord,
    FrameTable,
    TrackedFramePass,
    attach_trajectory,
)
from cv.possession import median_player_height
from cv.teams import TEAM_A, UNKNOWN

FPS = 30.0
WIDTH, HEIGHT = 640, 480


# ------------------------------------------------------------------ fakes


class FakeBoxes:
    """The slice of ultralytics' Boxes that the tracker and this module touch.

    Deliberately not a mock: the real object is indexed with a boolean mask and
    read through .cls/.conf/.xyxy, and a mock would happily accept an access
    pattern the real class rejects.
    """

    def __init__(self, rows):
        # rows: [(cls, conf, x1, y1, x2, y2)]
        self._rows = np.array(rows, dtype=np.float32).reshape(-1, 6)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, mask):
        return FakeBoxes(self._rows[mask])

    @property
    def cls(self):
        return self._rows[:, 0]

    @property
    def conf(self):
        return self._rows[:, 1]

    @property
    def xyxy(self):
        return self._rows[:, 2:6]


class FakeDetector:
    """Returns a canned FakeBoxes per frame and records what it was asked for."""

    def __init__(self, per_frame):
        self.per_frame = per_frame
        self.calls: list[int] = []          # frames per call, in call order
        self.seen: list[np.ndarray] = []

    def detect_batch_raw(self, images):
        self.calls.append(len(images))
        self.seen.extend(images)
        out = []
        for image in images:
            # The frame's identity is stamped into pixel [0, 0, 0] by the
            # fixtures below, so the fake can answer per-frame.
            out.append(FakeBoxes(self.per_frame(int(image[0, 0, 0]))))
        return out


class FakeTracker:
    """Assigns a stable track id per detection slot, and remembers frame order.

    Slot-based ids are enough: what these tests care about is that the ids the
    tracker returns survive into the record, not that the association is any
    good.
    """

    def __init__(self):
        self.frames_seen = 0

    def update(self, boxes, img=None, **kwargs):
        self.frames_seen += 1
        rows = []
        for i, (conf, xyxy) in enumerate(zip(boxes.conf, boxes.xyxy)):
            # [x1, y1, x2, y2, track_id, score, cls, idx] — STrack.result
            rows.append([*xyxy, 100 + i, conf, CLASS_PERSON, i])
        return np.array(rows, dtype=np.float32).reshape(-1, 8)


def frames_stamped(indices):
    """Frames whose [0,0,0] pixel is the frame number, so fakes can identify them."""
    out = []
    for i in indices:
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        frame[:, :] = i % 256
        out.append(frame)
    return out


def one_batch(first_index, count):
    return [(first_index, frames_stamped(range(first_index, first_index + count)))]


def batches_of(first_index, count, size):
    out = []
    index = first_index
    while index < first_index + count:
        take = min(size, first_index + count - index)
        out.append((index, frames_stamped(range(index, index + take))))
        index += take
    return out


def player_row(conf=0.9, x=100.0, y=100.0, w=20.0, h=60.0):
    return (CLASS_PERSON, conf, x, y, x + w, y + h)


def ball_row(conf=0.5, x=300.0, y=300.0):
    return (CLASS_BALL, conf, x, y, x + 6, y + 6)


def make_pass(per_frame, **kwargs):
    tracker = FakeTracker()
    detector = FakeDetector(per_frame)
    runner = TrackedFramePass(
        detector=detector,
        tracker_factory=lambda name, device: tracker,
        **kwargs,
    )
    return runner, detector, tracker


# ------------------------------------------------------------------ records


class TestFrameRecord:
    def record(self, rows):
        return FrameRecord(
            frame_index=7, timestamp_s=7 / FPS,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
        )

    def test_boxes_match_the_shape_possession_expects(self):
        record = self.record([[3, 10, 20, 30, 80, 0.9]])
        assert record.boxes() == [(3, (10.0, 20.0, 30.0, 80.0))]

    def test_ground_point_is_the_bottom_centre(self):
        record = self.record([[3, 10, 20, 30, 80, 0.9]])
        assert record.player_boxes()[0].ground_point == (20.0, 80.0)

    def test_scale_agrees_with_possession(self):
        """One definition of "a player height", not two.

        The touch detector divides pixel distances by this to stay invariant to
        zoom, and possession divides by possession.median_player_height. If the
        two ever disagreed, a touch and a possession change could be measured
        against different rulers on the same frame.
        """
        rows = [[1, 0, 0, 10, 40, 0.9], [2, 0, 0, 10, 70, 0.9], [3, 0, 0, 10, 100, 0.9]]
        record = self.record(rows)
        assert record.scale_px == median_player_height(record.boxes())

    def test_an_empty_frame_is_not_an_error(self):
        record = self.record([])
        assert len(record) == 0
        assert record.boxes() == []
        assert record.scale_px == 0.0


class TestFrameTable:
    def table(self, indices, **kwargs):
        return FrameTable(
            fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            records=[
                FrameRecord(frame_index=i, timestamp_s=i / FPS,
                            players=np.empty((0, 6), dtype=np.float32))
                for i in indices
            ],
            **kwargs,
        )

    def test_at_finds_contiguous_frames(self):
        table = self.table(range(100, 110))
        assert table.at(104).frame_index == 104

    def test_at_finds_strided_frames(self):
        """A strided run leaves holes, and lookup has to survive them.

        An offset-from-the-first-record calculation is right only when records
        are contiguous, and silently returns the wrong frame when they are not.
        """
        table = self.table(range(100, 120, 3))
        assert table.at(106).frame_index == 106
        assert table.at(107) is None

    def test_at_outside_the_window_is_none(self):
        table = self.table(range(100, 110))
        assert table.at(50) is None
        assert table.at(500) is None

    def test_unknown_track_has_no_team_rather_than_a_guess(self):
        table = self.table(range(3), team_by_track={1: TEAM_A})
        assert table.team_of(1) == TEAM_A
        assert table.team_of(999) == UNKNOWN

    def test_metres_are_none_without_a_calibration(self):
        """None, not a number.

        A caller handed a number back has no way to tell an estimate from a
        measurement, and every metre-based statistic downstream would inherit
        the fiction without ever being told.
        """
        assert self.table(range(3)).to_pitch((10.0, 10.0)) is None

    def test_ball_coverage_separates_seen_from_filled_in(self):
        table = self.table(range(4))
        table.records[0].ball_xy = (1.0, 1.0)
        table.records[0].ball_observed = True
        table.records[1].ball_xy = (2.0, 2.0)      # interpolated
        assert table.ball_coverage() == 0.5
        assert table.ball_observed_share() == 0.25


# ------------------------------------------------------------------ the pass


class TestSinglePass:
    def test_one_record_per_frame_in_order(self):
        runner, _, _ = make_pass(lambda i: [player_row()])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=batches_of(100, 20, 8),
        )
        assert [r.frame_index for r in table.records] == list(range(100, 120))

    def test_frames_reach_the_tracker_in_order_and_exactly_once(self):
        """BoT-SORT compares each frame to the one before it.

        Its camera-motion compensation is stateful, so a batch replayed out of
        order does not merely reorder the output — it corrupts the motion
        estimate for every frame after it. Nothing about the returned table
        would look wrong, which is why this is checked at the tracker.
        """
        runner, _, tracker = make_pass(lambda i: [player_row()])
        runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=batches_of(0, 40, 16),
        )
        assert tracker.frames_seen == 40

    def test_timestamps_come_from_absolute_frame_numbers(self):
        """A seek lands on a keyframe, not where it was aimed.

        Dating from the requested start rather than the frame actually read
        would put every event a fraction of a second out, and this output has to
        line up with a hand-tagged match log.
        """
        runner, _, _ = make_pass(lambda i: [player_row()])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(26, 4),
        )
        assert table.records[0].timestamp_s == pytest.approx(26 / FPS)

    def test_track_ids_survive_into_the_record(self):
        runner, _, _ = make_pass(lambda i: [player_row(), player_row(x=200)])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 3),
        )
        assert table.records[0].track_ids() == [100, 101]

    def test_low_confidence_players_reach_the_tracker_but_not_the_record(self):
        """The ByteTrack bargain, and the reason detection runs at ball
        confidence.

        A detection below the player threshold is not worth reporting as a
        sighting, but it is worth handing to the tracker: ByteTrack uses
        low-scoring boxes to re-associate tracks that already exist, while
        gating track *creation* on a higher threshold. The two-pass arrangement
        this replaced threw that away by detecting at 0.25.
        """
        runner, _, tracker = make_pass(
            lambda i: [player_row(conf=0.9), player_row(conf=0.12, x=200)],
            conf=0.25,
        )
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 1),
        )
        assert len(table.records[0]) == 1
        assert table.records[0].players[0][COL_CONF] == pytest.approx(0.9)

    def test_the_ball_never_reaches_the_tracker(self):
        """The ball is kept out of association entirely.

        Handing it to the tracker is how it gets lost — a small fast object
        never confirms, and ultralytics drops unconfirmed detections rather
        than scoring them low. Keeping it out also removes any chance of a ball
        box being associated into a player track.
        """
        seen = []

        class Recorder(FakeTracker):
            def update(self, boxes, img=None, **kwargs):
                seen.append(list(boxes.cls))
                return super().update(boxes, img, **kwargs)

        recorder = Recorder()
        runner = TrackedFramePass(
            detector=FakeDetector(lambda i: [player_row(), ball_row()]),
            tracker_factory=lambda name, device: recorder,
        )
        runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 3),
        )
        assert all(CLASS_BALL not in row for row in seen)

    def test_ball_detections_are_collected_as_candidates(self):
        runner, _, _ = make_pass(lambda i: [player_row(), ball_row(conf=0.4)])
        _, _, balls = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(10, 3),
        )
        assert sorted(balls) == [10, 11, 12]
        assert balls[10][0].xy == (303.0, 303.0)

    def test_balls_below_the_ball_threshold_are_dropped(self):
        runner, _, _ = make_pass(
            lambda i: [ball_row(conf=0.02)], ball_conf=0.08,
        )
        _, _, balls = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 3),
        )
        assert balls == {}

    def test_colours_are_keyed_on_real_track_ids(self):
        """The whole reason this module exists.

        The previous pipeline keyed colours on a per-detection pseudo-id, so a
        colour could never be attributed to a player across frames — which is
        why PlayerReport.team was hardcoded to UNKNOWN.
        """
        runner, _, _ = make_pass(lambda i: [player_row()], colour_every=1)
        _, colours, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 6),
        )
        assert set(colours) == {100}
        assert len(colours[100]) == 6

    def test_colour_sampling_respects_its_cadence(self):
        runner, _, _ = make_pass(lambda i: [player_row()], colour_every=3)
        _, colours, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 9),
        )
        assert len(colours[100]) == 3           # frames 0, 3, 6

    def test_colour_samples_are_capped_per_track(self):
        """A cap, so one long track cannot outvote fifty short ones.

        assign_teams clusters over whatever it is handed; an uncapped track
        running the whole half would dominate the k-means on its own.
        """
        runner, _, _ = make_pass(
            lambda i: [player_row()], colour_every=1, max_colour_samples=4,
        )
        _, colours, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 20),
        )
        assert len(colours[100]) == 4


class TestThumbnails:
    """The picture of each figure has to be taken here or not at all.

    What survives a batch is a few numbers per detection, never the image — the
    same constraint that puts colour sampling in this loop. These check the
    bookkeeping around that, not the cropping itself (tests/test_thumbs.py).
    """

    def test_a_tracked_figure_comes_out_with_a_picture(self):
        runner, _, _ = make_pass(
            lambda i: [player_row(h=70.0)], colour_every=1,
        )
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 4),
        )
        assert set(table.thumb_by_track) == {100}
        assert table.thumb_by_track[100].data_uri.startswith('data:image/jpeg')

    def test_the_biggest_sighting_wins_across_frames(self):
        """A player runs towards the camera and gets more recognisable."""
        heights = {0: 40.0, 1: 55.0, 2: 96.0, 3: 62.0}
        runner, _, _ = make_pass(
            lambda i: [player_row(h=heights[i])], colour_every=1,
        )
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 4),
        )
        held = table.thumb_by_track[100]
        assert held.height_px == 96.0
        assert held.frame_index == 2

    def test_a_figure_never_seen_cleanly_has_no_entry_at_all(self):
        """Not a None sitting in the dict.

        A None would read as a track that was considered and permanently
        rejected, when in fact every sighting so far has just been unusable.
        """
        runner, _, _ = make_pass(
            # Wider than tall the whole way through — two players in one box.
            lambda i: [player_row(w=120.0, h=70.0)], colour_every=1,
        )
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 4),
        )
        assert table.thumb_by_track == {}

    def test_it_shares_the_colour_cadence(self):
        """One interval for both, because both ask the same thing of the same
        pixels. A second one would be a second thing to tune with no evidence
        for either setting."""
        runner, _, _ = make_pass(
            lambda i: [player_row(h=40.0 + i)], colour_every=4,
        )
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=one_batch(0, 8),
        )
        # Frames 0 and 4 were sampled; 4 is the taller of the two.
        assert table.thumb_by_track[100].frame_index == 4


class TestStride:
    def test_stride_skips_frames_before_inference(self):
        """Skipping after inference would save nothing — inference is the cost."""
        runner, detector, _ = make_pass(lambda i: [player_row()])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=batches_of(0, 24, 8), stride=3,
        )
        assert [r.frame_index for r in table.records] == [0, 3, 6, 9, 12, 15, 18, 21]
        assert sum(detector.calls) == 8

    def test_stride_is_measured_from_the_first_frame_actually_decoded(self):
        """Not from zero.

        The seek lands where it lands, so anchoring the stride to absolute frame
        zero would make which frames get processed depend on where the keyframe
        happened to be.
        """
        runner, _, _ = make_pass(lambda i: [player_row()])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=batches_of(26, 12, 8), stride=4,
        )
        assert [r.frame_index for r in table.records] == [26, 30, 34]

    def test_stride_of_one_processes_everything(self):
        runner, _, _ = make_pass(lambda i: [player_row()])
        table, _, _ = runner.run(
            'fake.mp4', fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            batches=batches_of(0, 10, 4), stride=1,
        )
        assert len(table.records) == 10


class TestAttachTrajectory:
    def test_solved_points_land_on_their_frames(self):
        """The ball is solved over the window, so it arrives after the records.

        cv/ball.py picks a path by dynamic programming over every candidate at
        once — it cannot answer frame by frame, so the records exist first and
        the ball is written back onto them.
        """
        from cv.ball import BallPoint, BallTrajectory

        table = FrameTable(
            fps=FPS, frame_width=WIDTH, frame_height=HEIGHT,
            records=[
                FrameRecord(i, i / FPS, np.empty((0, 6), dtype=np.float32))
                for i in range(5)
            ],
        )
        attach_trajectory(table, BallTrajectory([
            BallPoint(1, 1 / FPS, (10.0, 10.0), True, 0.6),
            BallPoint(2, 2 / FPS, (20.0, 20.0), False),
        ]))

        assert table.records[0].ball_xy is None
        assert table.records[1].ball_xy == (10.0, 10.0)
        assert table.records[1].ball_observed is True
        assert table.records[2].ball_observed is False
        assert table.records[4].ball_xy is None
