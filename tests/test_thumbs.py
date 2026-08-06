"""Pictures of the tracked figures, and the three things worse than no picture.

The mapping step is the one part of this pipeline a human has to do, and until
now it asked them to identify a teenager from a time span and a swatch of kit
colour. These crops are what turns that into looking at somebody.

What the tests below are mostly about is the *rejections*. Storing a crop is
easy; the value is entirely in refusing to store a bad one, because a picker
full of half-players and two-players-in-one-box is worse than a picker full of
swatches — a coach can tell a swatch is uninformative, and cannot tell that the
smudge they just named was actually two people.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_thumbs.py -q
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from cv.identity import PlayerCluster
from cv.thumbs import (
    MAX_THUMB_H,
    MIN_THUMB_H,
    Thumb,
    attach_thumbs,
    consider,
    crop_score,
    cut,
    encode,
    fit_budget,
)

WIDTH, HEIGHT = 640, 480


def frame(value: int = 120) -> np.ndarray:
    """A frame with something in it — an all-black one encodes suspiciously well."""
    rng = np.random.default_rng(7)
    return rng.integers(
        0, 255, size=(HEIGHT, WIDTH, 3), dtype=np.uint8,
    ) // 2 + np.uint8(value // 2)


def box(x=100.0, y=100.0, w=24.0, h=70.0):
    return (x, y, x + w, y + h)


# ------------------------------------------------------------------ scoring


class TestWhatMakesAPortrait:
    def test_a_clean_standing_player_scores_its_height(self):
        assert crop_score(box(h=70.0), WIDTH, HEIGHT) == 70.0

    def test_taller_is_better_because_it_is_more_pixels_of_a_person(self):
        near = crop_score(box(h=110.0), WIDTH, HEIGHT)
        far = crop_score(box(h=45.0), WIDTH, HEIGHT)
        assert near > far

    def test_a_smudge_is_refused(self):
        """Below the floor there is no face, no number and no hair."""
        assert crop_score(box(h=MIN_THUMB_H - 1, w=8.0), WIDTH, HEIGHT) is None

    def test_a_box_wider_than_tall_is_refused(self):
        """Two players the detector merged, or somebody on the ground.

        This one matters more than it looks: a merged box is *large*, so without
        the rule it would beat every honest crop of either player and become the
        picture of both of them.
        """
        assert crop_score(box(w=90.0, h=70.0), WIDTH, HEIGHT) is None

    @pytest.mark.parametrize('spot', ['left', 'right', 'top', 'bottom'])
    def test_a_player_clipped_by_the_frame_edge_is_refused(self, spot):
        """Half a player is not a smaller player, but it looks like one."""
        boxes = {
            'left': (0.0, 100.0, 30.0, 170.0),
            'right': (WIDTH - 30.0, 100.0, float(WIDTH), 170.0),
            'top': (100.0, 0.0, 130.0, 70.0),
            'bottom': (100.0, HEIGHT - 70.0, 130.0, float(HEIGHT)),
        }
        assert crop_score(boxes[spot], WIDTH, HEIGHT) is None

    def test_a_player_just_inside_the_edge_is_kept(self):
        """The rejection is about clipping, not about being near a touchline."""
        assert crop_score((5.0, 100.0, 35.0, 170.0), WIDTH, HEIGHT) is not None


# ------------------------------------------------------------------ cutting


class TestCutting:
    def test_it_returns_the_pixels_inside_the_box(self):
        patch = cut(frame(), box(x=100.0, y=100.0, w=24.0, h=70.0))
        assert patch.shape[:2] == (70, 24)

    def test_it_never_upscales(self):
        """A player forty pixels tall gets a forty-pixel picture.

        Blowing every crop up to a uniform size would invent detail the sensor
        never recorded and make an unusable one look usable.
        """
        patch = cut(frame(), box(h=40.0))
        assert patch.shape[0] == 40

    def test_it_shrinks_something_enormous(self):
        patch = cut(frame(), box(x=10.0, y=10.0, w=100.0, h=400.0))
        assert patch.shape[0] == MAX_THUMB_H

    def test_shrinking_keeps_the_shape_of_a_person(self):
        patch = cut(frame(), box(x=10.0, y=10.0, w=100.0, h=400.0))
        # 100/400 going in, and the same going out to within a rounded pixel.
        assert abs(patch.shape[1] / patch.shape[0] - 0.25) < 0.02

    def test_a_box_off_the_frame_entirely_is_nothing(self):
        assert cut(frame(), (700.0, 500.0, 730.0, 570.0)) is None


class TestEncoding:
    def test_it_produces_something_an_img_tag_can_use(self):
        uri = encode(cut(frame(), box()))
        assert uri.startswith('data:image/jpeg;base64,')

    def test_the_payload_is_real_jpeg(self):
        raw = base64.b64decode(encode(cut(frame(), box())).split(',', 1)[1])
        # SOI marker. Enough to catch the byte order going wrong on the way out.
        assert raw[:2] == b'\xff\xd8'

    def test_one_picture_costs_a_few_kilobytes(self):
        """The whole design rests on this staying small.

        Forty of these travel in a Firestore document that stops at a megabyte
        and already carries a heatmap per cluster.
        """
        assert len(encode(cut(frame(), box(h=100.0)))) < 8_000


# ------------------------------------------------------------------ choosing


class TestKeepingTheBest:
    def test_the_first_usable_sighting_is_kept(self):
        held = consider(None, frame(), 3, 10, box(h=60.0))
        assert held.track_id == 3
        assert held.frame_index == 10
        assert held.height_px == 60.0

    def test_a_bigger_sighting_replaces_it(self):
        held = consider(None, frame(), 3, 10, box(h=60.0))
        better = consider(held, frame(), 3, 90, box(h=95.0))
        assert better.height_px == 95.0
        assert better.frame_index == 90

    def test_a_smaller_sighting_does_not(self):
        held = consider(None, frame(), 3, 10, box(h=95.0))
        same = consider(held, frame(), 3, 90, box(h=60.0))
        assert same is held

    def test_an_unusable_sighting_never_displaces_a_good_one(self):
        """The merged-box case again, arriving after a clean crop.

        It is the largest box of the match, so anything ranking on size alone
        would take it.
        """
        held = consider(None, frame(), 3, 10, box(h=95.0))
        same = consider(held, frame(), 3, 90, box(w=200.0, h=180.0))
        assert same is held

    def test_a_track_only_ever_seen_badly_keeps_no_picture(self):
        """It keeps its kit swatch, which is what the picker had before."""
        assert consider(None, frame(), 3, 10, box(w=200.0, h=180.0)) is None


# ------------------------------------------------------------------ clusters


def cluster(cluster_id, track_ids, sightings=100):
    return PlayerCluster(
        cluster_id=cluster_id, track_ids=set(track_ids), sightings=sightings,
    )


def thumb(track_id, height, size=1000):
    return Thumb(
        track_id=track_id, frame_index=1, height_px=height,
        data_uri='data:image/jpeg;base64,' + 'A' * size,
    )


class TestAttaching:
    def test_a_cluster_takes_the_best_picture_of_its_fragments(self):
        """A person split five ways is one row in the picker.

        Best rather than first or longest: the only thing that makes one
        fragment's crop better than another's is how much of the person it got.
        """
        one = cluster(0, [1, 2, 3])
        attach_thumbs([one], {1: thumb(1, 40), 2: thumb(2, 96), 3: thumb(3, 55)})
        assert one.thumb_height_px == 96

    def test_a_cluster_whose_fragments_were_never_seen_cleanly_gets_none(self):
        one = cluster(0, [1, 2])
        attach_thumbs([one], {})
        assert one.thumb is None
        assert one.thumb_height_px is None

    def test_it_survives_a_partially_pictured_cluster(self):
        one = cluster(0, [1, 2])
        attach_thumbs([one], {2: thumb(2, 61)})
        assert one.thumb_height_px == 61

    def test_the_picture_reaches_the_json(self):
        one = cluster(0, [1])
        attach_thumbs([one], {1: thumb(1, 61)})
        published = one.to_json()
        assert published['thumb'].startswith('data:image/jpeg')
        assert published['thumb_height_px'] == 61


class TestTheBudget:
    def test_everything_fits_when_there_is_room(self):
        clusters = [cluster(i, [i]) for i in range(5)]
        attach_thumbs(clusters, {i: thumb(i, 60) for i in range(5)})
        kept, dropped = fit_budget(clusters, budget_bytes=100_000)
        assert (kept, dropped) == (5, 0)

    def test_the_briefest_figures_go_without_first(self):
        """A document one byte over the limit does not publish at all.

        Not the thumbnails, not the heatmaps, not the clusters — so this trades
        the pictures nobody was going to use for the run reaching the coach.
        """
        big = cluster(0, [0], sightings=5000)
        small = cluster(1, [1], sightings=12)
        attach_thumbs([big, small], {0: thumb(0, 90), 1: thumb(1, 90)})

        kept, dropped = fit_budget([big, small], budget_bytes=1500)
        assert (kept, dropped) == (1, 1)
        assert big.thumb is not None
        assert small.thumb is None

    def test_a_dropped_picture_takes_its_height_with_it(self):
        """Otherwise the picker reports how tall a photograph it does not have."""
        one = cluster(0, [0])
        attach_thumbs([one], {0: thumb(0, 90)})
        fit_budget([one], budget_bytes=10)
        assert one.thumb is None
        assert one.thumb_height_px is None

    def test_clusters_without_a_picture_are_not_counted_against_it(self):
        clusters = [cluster(i, [i]) for i in range(3)]
        attach_thumbs(clusters, {0: thumb(0, 60)})
        kept, dropped = fit_budget(clusters, budget_bytes=100_000)
        assert (kept, dropped) == (1, 0)
