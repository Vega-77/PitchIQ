"""Team identification and possession.

Both work in pixel space, so unlike the movement metrics they can run on the
footage available today. The tests build synthetic shirts and synthetic
possession so the right answer is known by construction — on real video nobody
can say what the true possession split was to the second.
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.phases import DeadSpan, PhaseTable
from cv.possession import (
    FrameState,
    frame_holder,
    median_player_height,
    summarise,
)
from cv.teams import (
    TEAM_A,
    TEAM_B,
    UNKNOWN,
    assign_teams,
    separation,
    shirt_colour,
    torso_patch,
)

FPS = 25.0


# ---------------------------------------------------------------------------
# Synthetic players
# ---------------------------------------------------------------------------

def player_frame(colours, box_h=120, box_w=44, gap=90, grass=(60, 110, 55)):
    """An image of players in the given BGR shirt colours, standing on grass.

    Each figure has a dark head, a coloured shirt and pale shorts, so a naive
    average over the whole box lands somewhere between all three — which is
    exactly why the code samples only the torso band.
    """
    width = gap * len(colours) + 80
    frame = np.full((300, width, 3), grass, dtype=np.uint8)
    boxes = []

    for i, colour in enumerate(colours):
        x1 = 40 + i * gap
        y1 = 90
        x2, y2 = x1 + box_w, y1 + box_h

        head_h = int(box_h * 0.2)
        torso_bottom = y1 + int(box_h * 0.58)

        frame[y1:y1 + head_h, x1:x2] = (45, 45, 45)
        frame[y1 + head_h:torso_bottom, x1:x2] = colour
        frame[torso_bottom:y2, x1:x2] = (225, 225, 225)

        boxes.append((i, (x1, y1, x2, y2)))

    return frame, boxes


RED = (40, 40, 200)
YELLOW = (40, 210, 230)
GREEN_KEEPER = (60, 200, 90)


class TestTorsoSampling:
    def test_crops_inside_the_box(self):
        frame, boxes = player_frame([RED])
        patch = torso_patch(frame, boxes[0][1])

        assert patch is not None
        assert patch.size > 0

    def test_ignores_head_shorts_and_grass(self):
        """A whole-box average would be badly contaminated; the torso median
        should land on the shirt itself."""
        frame, boxes = player_frame([RED])
        patch = torso_patch(frame, boxes[0][1])

        median = np.median(patch.reshape(-1, 3), axis=0)
        assert median == pytest.approx(np.array(RED), abs=12)

    def test_rejects_a_box_too_small_to_read(self):
        frame, _ = player_frame([RED])
        assert torso_patch(frame, (10, 10, 12, 13)) is None


class TestTeamAssignment:
    def build(self, colours, repeats=6):
        samples = {}
        for _ in range(repeats):
            frame, boxes = player_frame(colours)
            for track_id, xyxy in boxes:
                colour = shirt_colour(frame, xyxy)
                if colour is not None:
                    samples.setdefault(track_id, []).append(colour)
        return samples

    def test_splits_two_clearly_different_kits(self):
        samples = self.build([RED, RED, RED, YELLOW, YELLOW, YELLOW])
        assignment = assign_teams(samples)

        teams = [assignment.team_of(i) for i in range(6)]
        assert len(set(teams[:3])) == 1, 'the three red shirts should agree'
        assert len(set(teams[3:])) == 1, 'the three yellow shirts should agree'
        assert teams[0] != teams[3], 'the two kits should not share a team'

    def test_reports_confidence(self):
        samples = self.build([RED, RED, YELLOW, YELLOW])
        assignment = assign_teams(samples)

        assert all(assignment.confidence[i] > 0.3 for i in range(4))

    def test_flags_an_odd_kit_as_unknown(self):
        # A keeper in a third colour should not be forced into either team.
        samples = self.build([RED, RED, RED, YELLOW, YELLOW, YELLOW, GREEN_KEEPER])
        assignment = assign_teams(samples, outlier_ratio=0.35)

        assert assignment.team_of(6) == UNKNOWN

    def test_needs_at_least_two_tracks(self):
        assert assign_teams({}).by_track == {}
        assert assign_teams({1: [np.array([50.0, 10.0, 10.0])]}).by_track == {}

    def test_separation_is_large_for_distinct_kits(self):
        wide = assign_teams(self.build([RED, RED, YELLOW, YELLOW]))
        assert separation(wide) > 25

    def test_separation_is_small_for_similar_kits(self):
        """Two near-identical kits should be reported as barely separated, so a
        caller can distrust the team stats rather than believe them."""
        near = assign_teams(self.build([(40, 40, 200), (40, 40, 200),
                                        (55, 55, 195), (55, 55, 195)]))
        assert separation(near) < 12

    def test_swap_exchanges_the_labels(self):
        assignment = assign_teams(self.build([RED, RED, YELLOW, YELLOW]))
        before = assignment.team_of(0)
        assignment.swap()

        assert assignment.team_of(0) != before

    def test_a_single_bad_frame_does_not_flip_a_track(self):
        # Five good red samples and one yellow-looking outlier.
        samples = self.build([RED, RED, YELLOW, YELLOW])
        samples[0].append(np.array([200.0, 120.0, 190.0]))

        assignment = assign_teams(samples)
        assert assignment.team_of(0) == assignment.team_of(1)


# ---------------------------------------------------------------------------
# Possession
# ---------------------------------------------------------------------------

def boxes_at(positions, height=100, width=40):
    """Detection boxes standing at the given ground points."""
    out = []
    for track_id, (x, y) in positions.items():
        out.append((track_id, (x - width / 2, y - height, x + width / 2, y)))
    return out


class TestFrameHolder:
    def test_picks_the_nearest_player(self):
        boxes = boxes_at({1: (100, 400), 2: (300, 400)})
        holder, team, distance = frame_holder((110, 400), boxes, lambda t: TEAM_A)

        assert holder == 1
        assert distance == pytest.approx(10, abs=1)

    def test_nobody_has_a_ball_that_is_far_away(self):
        boxes = boxes_at({1: (100, 400)})
        holder, team, _ = frame_holder((900, 400), boxes, lambda t: TEAM_A)

        assert holder is None
        assert team == UNKNOWN

    def test_radius_scales_with_zoom(self):
        """A fixed pixel gap must mean different things at different zooms.

        At 1.6 player-heights the radius is 96px for a 60px player and 320px
        for a 200px one, so 150px sits either side of the line — which is the
        whole point of scaling by player height instead of using a constant.
        """
        ball = (100 + 150, 400)

        zoomed_out = boxes_at({1: (100, 400)}, height=60)
        zoomed_in = boxes_at({1: (100, 400)}, height=200)

        out_holder, _, _ = frame_holder(ball, zoomed_out, lambda t: TEAM_A)
        in_holder, _, _ = frame_holder(ball, zoomed_in, lambda t: TEAM_A)

        assert out_holder is None, '150px is far when players are 60px tall'
        assert in_holder == 1, '150px is close when players are 200px tall'

    def test_handles_a_missing_ball(self):
        holder, team, _ = frame_holder(None, boxes_at({1: (100, 400)}), lambda t: TEAM_A)
        assert holder is None

    def test_handles_no_players(self):
        holder, team, _ = frame_holder((100, 400), [], lambda t: TEAM_A)
        assert holder is None

    def test_median_height_ignores_junk(self):
        boxes = [(1, (0, 0, 10, 100)), (2, (0, 0, 10, 110)), (3, (0, 0, 10, 0))]
        assert median_player_height(boxes) == pytest.approx(105, abs=1)


class TestExcludingNonPlayers:
    """A referee trailing play is regularly the closest figure to the ball."""

    def test_the_nearest_figure_can_be_ignored(self):
        boxes = boxes_at({1: (105, 400), 2: (140, 400)})

        holder, _, _ = frame_holder((100, 400), boxes, lambda t: TEAM_A)
        assert holder == 1, 'nearest wins when everybody counts'

        holder, _, _ = frame_holder(
            (100, 400), boxes, lambda t: TEAM_A, is_player=lambda t: t != 1,
        )
        assert holder == 2, 'and the next nearest wins when they do not'

    def test_non_players_do_not_set_the_scale(self):
        """The radius is measured in player heights, so a row of small figures
        on a distant touchline quietly shrinks it for everyone."""
        boxes = boxes_at({1: (100, 400)}, height=200)
        boxes += boxes_at({2: (900, 100), 3: (950, 100)}, height=20)
        ball = (100 + 150, 400)

        holder, _, _ = frame_holder(ball, boxes, lambda t: TEAM_A)
        assert holder is None, 'the tiny figures dragged the median down'

        holder, _, _ = frame_holder(
            ball, boxes, lambda t: TEAM_A, is_player=lambda t: t == 1,
        )
        assert holder == 1

    def test_excluding_everybody_is_not_a_crash(self):
        holder, team, _ = frame_holder(
            (100, 400), boxes_at({1: (100, 400)}), lambda t: TEAM_A,
            is_player=lambda t: False,
        )
        assert holder is None
        assert team == UNKNOWN


def states(sequence, fps=FPS, ball_seen=True):
    """Build per-frame states from a list of team labels."""
    return [
        FrameState(timestamp_s=i / fps, holder_track=None, team=team,
                   distance_px=0.0, ball_seen=ball_seen)
        for i, team in enumerate(sequence)
    ]


class TestSummary:
    def test_splits_time_between_the_teams(self):
        # 4 seconds A, then 2 seconds B.
        sequence = [TEAM_A] * int(4 * FPS) + [TEAM_B] * int(2 * FPS)
        summary = summarise(states(sequence))

        assert summary.share(TEAM_A) == pytest.approx(2 / 3, abs=0.05)
        assert summary.share(TEAM_B) == pytest.approx(1 / 3, abs=0.05)

    def test_counts_turnovers(self):
        sequence = (
            [TEAM_A] * int(2 * FPS)
            + [TEAM_B] * int(2 * FPS)
            + [TEAM_A] * int(2 * FPS)
        )
        assert summarise(states(sequence)).turnovers == 2

    def test_ignores_flicker(self):
        """Two opponents contesting the ball swap nearest-player constantly;
        counting that raw would report several turnovers a second."""
        sequence = []
        for i in range(int(6 * FPS)):
            sequence.append(TEAM_A if (i // 2) % 2 == 0 else TEAM_B)

        summary = summarise(states(sequence))
        assert summary.turnovers == 0, 'rapid alternation is not a turnover'

    def test_a_brief_touch_does_not_break_a_spell(self):
        # A long A spell with a three-frame B deflection in the middle.
        sequence = (
            [TEAM_A] * int(3 * FPS) + [TEAM_B] * 3 + [TEAM_A] * int(3 * FPS)
        )
        summary = summarise(states(sequence))

        assert summary.turnovers == 0
        assert summary.share(TEAM_A) > 0.95


class TestDeadBall:
    """A player standing over the ball waiting to take a throw-in is a very
    clear holder, and every possession figure counted him as one."""

    def phases(self, start_s, end_s):
        return PhaseTable(spans=[DeadSpan(start_s, end_s, 'out_of_bounds', 'throw_in')])

    def test_stoppages_leave_the_possession_split(self):
        # 4s of A, then 4s where A is standing over the ball out of play.
        sequence = [TEAM_A] * int(8 * FPS)

        without = summarise(states(sequence))
        with_log = summarise(states(sequence), phases=self.phases(4.0, 8.0))

        assert without.dead_ball_s == 0.0
        assert with_log.dead_ball_s == pytest.approx(4.0, abs=0.2)
        assert with_log.live_s == pytest.approx(without.live_s - 4.0, abs=0.2)

    def test_the_split_itself_is_unchanged_when_both_teams_lose_the_same_time(self):
        sequence = [TEAM_A] * int(4 * FPS) + [TEAM_B] * int(4 * FPS)
        summary = summarise(states(sequence), phases=self.phases(3.5, 4.5))

        assert summary.share(TEAM_A) == pytest.approx(0.5, abs=0.05)

    def test_a_dead_stretch_does_not_count_as_a_turnover(self):
        sequence = (
            [TEAM_A] * int(3 * FPS) + [TEAM_A] * int(2 * FPS)
            + [TEAM_B] * int(3 * FPS)
        )
        summary = summarise(states(sequence), phases=self.phases(3.0, 5.0))

        assert summary.turnovers == 1, 'A to B, through a stoppage'

    def test_no_log_means_no_dead_time_recorded(self):
        """Zero because nobody told us, which the report has to say elsewhere."""
        summary = summarise(states([TEAM_A] * int(4 * FPS)))
        assert summary.dead_ball_s == 0.0

    def test_dead_time_is_not_eroded_by_the_smoothing(self):
        """The log is a fact, not a noisy per-frame estimate.

        Labels go on after the mode filter, so the whole span survives. Applied
        before it, a busy passage either side votes the edges away and a
        two-second stoppage arrives as a second and a half.
        """
        sequence = [TEAM_A] * int(8 * FPS)
        summary = summarise(states(sequence), phases=self.phases(3.0, 5.0))

        assert summary.dead_ball_s == pytest.approx(2.0, abs=0.15)


class TestBallMissing:
    """`no_ball_s` used to be span minus the other two, which the spells tile
    completely — so it was identically zero and looked fine forever."""

    def test_counts_the_time_with_no_ball_in_frame(self):
        seen = states([UNKNOWN] * int(2 * FPS))
        unseen = states([UNKNOWN] * int(2 * FPS), ball_seen=False)
        for i, state in enumerate(unseen):
            unseen[i] = FrameState(
                timestamp_s=2.0 + i / FPS, holder_track=None, team=UNKNOWN,
                distance_px=None, ball_seen=False,
            )

        summary = summarise(seen + unseen)
        assert summary.no_ball_s == pytest.approx(2.0, abs=0.15)

    def test_is_zero_when_the_ball_was_always_there(self):
        assert summarise(states([TEAM_A] * int(4 * FPS))).no_ball_s == 0.0

    def test_is_a_slice_of_contested_not_a_bucket_beside_it(self):
        """Documented as a diagnostic. If it ever starts double-counting, the
        three numbers stop summing to the window and nobody notices."""
        unseen = [
            FrameState(timestamp_s=i / FPS, holder_track=None, team=UNKNOWN,
                       distance_px=None, ball_seen=False)
            for i in range(int(3 * FPS))
        ]
        summary = summarise(unseen)

        assert summary.no_ball_s > 0
        assert summary.contested_s >= summary.no_ball_s

    def test_a_real_change_still_registers(self):
        sequence = [TEAM_A] * int(3 * FPS) + [TEAM_B] * int(3 * FPS)
        assert summarise(states(sequence)).turnovers == 1

    def test_contested_time_is_excluded_from_the_share(self):
        sequence = (
            [TEAM_A] * int(3 * FPS) + [UNKNOWN] * int(3 * FPS) + [TEAM_B] * int(3 * FPS)
        )
        summary = summarise(states(sequence))

        assert summary.contested_s == pytest.approx(3, abs=0.3)
        # Shares are of contested-free time, so an even split stays even.
        assert summary.share(TEAM_A) == pytest.approx(0.5, abs=0.05)

    def test_empty_input(self):
        assert summarise([]).live_s == 0
        assert summarise(states([TEAM_A])).live_s == 0

    def test_summary_line_reads_sensibly(self):
        sequence = [TEAM_A] * int(6 * FPS) + [TEAM_B] * int(2 * FPS)
        line = summarise(states(sequence)).summary_line('SB', 'Linden')

        assert 'SB 75%' in line
        assert 'Linden 25%' in line
