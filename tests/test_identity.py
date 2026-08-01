"""Merging track fragments back into players.

The one hard rule is that two tracks seen in the same frame are two people, and
most of this file exists to make sure no amount of matching colour or
convenient timing can override it. A wrong merge attributes one player's work
to another and there is no way to undo it downstream; a missed merge leaves two
clusters that the coach maps to the same player, which still sums correctly.
So the tests lean hard on the refusals.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_identity.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.frames import FrameRecord, FrameTable
from cv.identity import (
    MAX_BRIDGE_S,
    cluster_of_track,
    fragmentation,
    merge_tracks,
    track_spans,
)
from cv.teams import TEAM_A, TEAM_B, UNKNOWN

FPS = 30.0
SCALE = 60.0            # player height in pixels


def box(track_id, x, y):
    return [track_id, x - 10, y - SCALE, x + 10, y, 0.9]


def table(frames, teams=None):
    """frames: {frame_index: [(track_id, x, y), ...]}."""
    records = []
    for index in sorted(frames):
        rows = [box(t, x, y) for t, x, y in frames[index]]
        records.append(FrameRecord(
            frame_index=index,
            timestamp_s=index / FPS,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
        ))
    return FrameTable(
        fps=FPS, frame_width=1280, frame_height=720,
        records=records, team_by_track=teams or {},
    )


def run_of(track_id, start, count, x0=500.0, y=400.0, step=1.0):
    """One track visible for `count` consecutive frames."""
    return {
        start + i: [(track_id, x0 + i * step, y)]
        for i in range(count)
    }


def combine(*parts):
    merged: dict[int, list] = {}
    for part in parts:
        for index, entries in part.items():
            merged.setdefault(index, []).extend(entries)
    return merged


class TestSpans:
    def test_a_span_records_where_a_track_started_and_ended(self):
        spans = track_spans(table(run_of(1, 0, 10, x0=100.0, step=10.0)))
        span = spans[1]
        assert span.first_xy[0] == pytest.approx(100.0)
        assert span.last_xy[0] == pytest.approx(190.0)
        assert span.sightings == 10

    def test_overlap_is_frame_exact(self):
        """Not interval-based.

        Two tracks whose time ranges overlap but which never share a frame can
        still be one player seen either side of an occlusion. Only actually
        appearing together proves two people.
        """
        spans = track_spans(table(combine(
            run_of(1, 0, 10),
            run_of(2, 5, 10, y=200.0),
        )))
        assert spans[1].overlaps(spans[2])

        apart = track_spans(table(combine(
            run_of(1, 0, 10),
            run_of(2, 20, 10),
        )))
        assert not apart[1].overlaps(apart[2])


class TestRefusals:
    def test_tracks_seen_together_never_merge(self):
        """The one certainty available, and it must beat every other signal.

        These two are the same colour, the same size, and adjacent — everything
        short of the frame evidence says one player.
        """
        frames = {
            i: [(1, 500.0, 400.0), (2, 520.0, 400.0)]
            for i in range(40)
        }
        clusters = merge_tracks(table(frames))
        assert len(clusters) == 2

    def test_a_long_absence_is_not_bridged(self):
        """Twenty seconds later at the same spot might be the same person.

        Might is not enough: a wrong merge cannot be undone downstream, whereas
        two clusters mapped to one player still sum correctly.
        """
        gap_frames = int((MAX_BRIDGE_S + 3) * FPS)
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 30),
            run_of(2, 30 + gap_frames, 30),
        )))
        assert len(clusters) == 2

    def test_a_reappearance_far_away_is_not_bridged(self):
        """Nobody crosses forty metres in a tenth of a second."""
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 30, x0=100.0),
            run_of(2, 33, 30, x0=1200.0),
        )))
        assert len(clusters) == 2

    def test_a_different_shirt_is_not_bridged(self):
        colours = {
            1: [np.array([50.0, 60.0, 10.0])],
            2: [np.array([50.0, -60.0, 10.0])],
        }
        clusters = merge_tracks(
            table(combine(run_of(1, 0, 30), run_of(2, 33, 30))), colours
        )
        assert len(clusters) == 2

    def test_a_cluster_never_absorbs_a_track_that_overlaps_any_member(self):
        """Chained merges have to check the whole cluster, not just the pair.

        Track 3 does not overlap track 2, but it does overlap track 1, and 1 and
        2 have already been merged. Comparing only the adjacent pair would let 3
        join anyway and put one player in two places at once.
        """
        frames = combine(
            run_of(1, 0, 20),               # frames 0-19
            run_of(2, 22, 20, x0=520.0),    # frames 22-41, bridges from 1
            {i: [(3, 540.0, 400.0)] for i in range(10, 20)},   # overlaps 1
        )
        clusters = merge_tracks(table(frames), min_sightings=5)
        index = cluster_of_track(clusters)
        assert index[1] == index[2]
        assert index[3] != index[1]


class TestMerging:
    def test_a_brief_occlusion_is_bridged(self):
        """The case the whole module exists for.

        A player walks behind an opponent for a few frames and comes back with
        a new id, a couple of metres from where they vanished.
        """
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 30, x0=500.0),
            run_of(2, 36, 30, x0=560.0),
        )))
        assert len(clusters) == 1
        assert clusters[0].track_ids == {1, 2}

    def test_three_fragments_chain_into_one_cluster(self):
        """Union-find, not pairwise: a chain must collapse, not leave pairs."""
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 20, x0=500.0),
            run_of(2, 24, 20, x0=540.0),
            run_of(3, 48, 20, x0=580.0),
        )), min_sightings=5)
        assert len(clusters) == 1
        assert clusters[0].track_ids == {1, 2, 3}

    def test_matching_colour_permits_a_bridge(self):
        colours = {
            1: [np.array([50.0, 20.0, 10.0])],
            2: [np.array([52.0, 22.0, 12.0])],
        }
        clusters = merge_tracks(
            table(combine(run_of(1, 0, 30), run_of(2, 36, 30, x0=560.0))), colours
        )
        assert len(clusters) == 1

    def test_team_is_decided_by_majority_across_fragments(self):
        """One fragment mislabelled by a bad colour sample must not win."""
        frames = combine(
            run_of(1, 0, 20, x0=500.0),
            run_of(2, 24, 20, x0=540.0),
            run_of(3, 48, 20, x0=580.0),
        )
        teams = {1: TEAM_A, 2: TEAM_A, 3: TEAM_B}
        clusters = merge_tracks(table(frames, teams), min_sightings=5)
        assert clusters[0].team == TEAM_A

    def test_a_cluster_with_no_known_team_stays_unknown(self):
        clusters = merge_tracks(table(run_of(1, 0, 30)))
        assert clusters[0].team == UNKNOWN


class TestOutput:
    def test_tiny_clusters_are_dropped_as_noise(self):
        clusters = merge_tracks(table(run_of(1, 0, 3)), min_sightings=20)
        assert clusters == []

    def test_clusters_are_ordered_by_how_much_was_seen(self):
        """The coach confirms these by hand, so the biggest go first."""
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 60, y=100.0),
            run_of(2, 0, 25, y=500.0),
        )), min_sightings=5)
        assert clusters[0].sightings > clusters[1].sightings
        assert [c.cluster_id for c in clusters] == [0, 1]

    def test_minutes_come_from_the_span_not_the_frame_count(self):
        clusters = merge_tracks(table(run_of(1, 0, 60)))
        assert clusters[0].minutes_tracked == pytest.approx(59 / FPS / 60)

    def test_fragmentation_reports_tracks_per_player(self):
        """The number that says how far to trust anything per-player."""
        clusters = merge_tracks(table(combine(
            run_of(1, 0, 20, x0=500.0),
            run_of(2, 24, 20, x0=540.0),
        )), min_sightings=5)
        assert fragmentation(clusters) == pytest.approx(2.0)

    def test_no_clusters_gives_zero_fragmentation_not_a_crash(self):
        assert fragmentation([]) == 0.0

    def test_json_is_all_primitives(self):
        import json
        clusters = merge_tracks(table(run_of(1, 0, 30)))
        data = json.loads(json.dumps(clusters[0].to_json()))
        assert data['track_ids'] == [1]

    def test_an_empty_table_is_not_an_error(self):
        assert merge_tracks(FrameTable(fps=FPS, frame_width=1, frame_height=1)) == []
