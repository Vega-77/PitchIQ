"""Sorting the unseen stretches into what they were.

The interesting behaviour here is almost entirely about what the module refuses
to say: it will not call a stoppage a failure, it will not call an unchecked run
a clean one, and it will not decide a straddling gap by majority.
"""

import pytest

from cv.blind import (
    ACCOUNTED,
    DEAD,
    EXPLAIN_WINDOW_S,
    LONG_BLIND_S,
    UNCHECKED,
    UNEXPLAINED,
    Blindness,
    BlindSpell,
    blindness,
    blindness_warnings,
    tagged_moments,
    unseen_stretches,
)
from cv.phases import DeadSpan, PhaseTable, VideoClock
from cv.possession import FrameState
from cv.teams import UNKNOWN

FPS = 10.0


def states(seen_pattern, fps=FPS):
    """One state per entry; True means the ball was located on that frame."""
    return [
        FrameState(
            timestamp_s=i / fps,
            holder_track=None,
            team=UNKNOWN,
            distance_px=None,
            ball_seen=bool(seen),
        )
        for i, seen in enumerate(seen_pattern)
    ]


def blind_from(seconds_seen, fps=FPS):
    """States from a list of `(seen, seconds)` runs."""
    pattern = []
    for seen, secs in seconds_seen:
        pattern.extend([seen] * int(round(secs * fps)))
    return states(pattern, fps)


def tag(kind, clock_s):
    return {'type': kind, 'matchClockS': clock_s}


def table(*spans):
    return PhaseTable(spans=[
        DeadSpan(start_s=a, end_s=b, opened_by=by, closed_by='throw_in')
        for a, b, by in spans
    ])


class TestFindingTheStretches:
    def test_a_run_of_unseen_frames_is_one_stretch(self):
        found = unseen_stretches(states([1, 1, 0, 0, 0, 1, 1]))
        assert len(found) == 1
        start, end = found[0]
        assert start == pytest.approx(0.2)
        # Ends at the next seen frame, not at the last unseen one: those three
        # frames own the three intervals that begin at them.
        assert end == pytest.approx(0.5)

    def test_two_runs_split_by_a_single_sighting_are_two_stretches(self):
        assert len(unseen_stretches(states([0, 0, 1, 0, 0]))) == 2

    def test_a_run_that_reaches_the_end_stops_at_the_last_timestamp(self):
        found = unseen_stretches(states([1, 0, 0, 0]))
        assert found[-1][1] == pytest.approx(0.3)

    def test_a_clip_where_the_ball_was_always_visible_has_no_stretches(self):
        assert unseen_stretches(states([1] * 20)) == []

    def test_the_stretches_add_up_to_what_possession_calls_no_ball_time(self):
        """The two figures have to agree or one of them is wrong on the page.

        `summarise` takes the span times the unseen share; this takes the
        intervals directly. They are the same arithmetic seen from two sides,
        and a drift between them would show up as a split that does not fit
        inside the total it is a split of.
        """
        from cv.possession import summarise

        frames = states([1] * 10 + [0] * 30 + [1] * 10 + [0] * 20 + [1] * 30)
        total = sum(b - a for a, b in unseen_stretches(frames))
        assert total == pytest.approx(summarise(frames, smooth_window=0).no_ball_s,
                                      abs=0.15)

    def test_no_states_at_all_is_no_stretches_rather_than_a_crash(self):
        assert unseen_stretches([]) == []
        assert unseen_stretches(None) == []


class TestWhatTheTagLogExplains:
    def test_a_stretch_inside_a_stoppage_is_dead_not_a_failure(self):
        frames = blind_from([(1, 5), (0, 20), (1, 5)])
        result = blindness(frames, phases=table((4.0, 27.0, 'out_of_bounds')))
        assert [s.kind for s in result.spells] == [DEAD]
        assert result.seconds(UNEXPLAINED) == 0.0

    def test_a_stretch_in_live_play_with_nothing_tagged_is_unexplained(self):
        frames = blind_from([(1, 5), (0, 20), (1, 5)])
        result = blindness(frames, phases=table(), tag_log=[tag('foul', 200.0)])
        assert [s.kind for s in result.spells] == [UNEXPLAINED]
        assert result.seconds(UNEXPLAINED) == pytest.approx(20.0, abs=0.2)

    def test_a_tag_inside_the_stretch_accounts_for_it(self):
        frames = blind_from([(1, 5), (0, 20), (1, 5)])
        result = blindness(frames, phases=table(), tag_log=[tag('corner', 12.0)])
        assert [s.kind for s in result.spells] == [ACCOUNTED]
        assert result.spells[0].tags == ('corner',)

    def test_a_restart_tapped_just_after_the_stretch_still_accounts_for_it(self):
        """The case the whole window exists for.

        Taggers tap the restart and skip what caused it. A `corner` with no
        `out_of_bounds` in front of it opens no dead span at all, so the ball
        being out of play and out of shot looks like live football nobody could
        see. It is not.
        """
        frames = blind_from([(1, 5), (0, 10), (1, 5)])
        just_after = 15.0 + EXPLAIN_WINDOW_S - 1.0
        result = blindness(frames, phases=table(), tag_log=[tag('corner', just_after)])
        assert result.spells[0].kind == ACCOUNTED

    def test_a_tag_well_outside_the_window_explains_nothing(self):
        frames = blind_from([(1, 5), (0, 10), (1, 5)])
        far = 15.0 + EXPLAIN_WINDOW_S + 10.0
        result = blindness(frames, phases=table(), tag_log=[tag('corner', far)])
        assert result.spells[0].kind == UNEXPLAINED

    def test_the_nearest_tag_is_named_first(self):
        frames = blind_from([(1, 10), (0, 10), (1, 5)])
        result = blindness(frames, phases=table(), tag_log=[
            tag('sub', 10.5), tag('foul', 15.0),
        ])
        assert result.spells[0].tags[0] == 'foul'

    def test_tags_are_read_on_the_video_clock_not_the_match_clock(self):
        """A five-second window will not survive being applied to the wrong clock.

        The log runs on the tablet's clock, which stops at the break; the video
        does not. Without the conversion a second-half stretch is compared
        against tags a whole interval away and every one of them comes out
        unexplained.
        """
        frames = blind_from([(1, 5), (0, 10), (1, 5)])
        clock = VideoClock(video_offset_s=0.0, second_half_video_s=105.0,
                           second_half_clock_s=5.0)
        # On the match clock this tag is at 8s; on video it lands inside the gap.
        assert clock.to_video(8.0) == pytest.approx(108.0)
        naive = blindness(frames, phases=table(), tag_log=[tag('corner', 8.0)])
        assert naive.spells[0].kind == ACCOUNTED
        shifted = blindness(frames, phases=table(), tag_log=[tag('corner', 8.0)],
                            clock=clock)
        assert shifted.spells[0].kind == UNEXPLAINED


class TestStraddling:
    def test_a_gap_covering_a_stoppage_and_live_play_is_split_not_voted_on(self):
        """Twelve seconds explained and eight not, rather than one verdict.

        A majority rule here would either forgive eight seconds of real
        blackout or invent twelve seconds of one, depending which way the gap
        happened to lean.
        """
        frames = blind_from([(1, 5), (0, 20), (1, 5)])
        result = blindness(frames, phases=table((5.0, 17.0, 'out_of_bounds')))
        kinds = [s.kind for s in result.spells]
        assert kinds == [DEAD, UNEXPLAINED]
        assert result.seconds(DEAD) == pytest.approx(12.0, abs=0.2)
        assert result.seconds(UNEXPLAINED) == pytest.approx(8.0, abs=0.2)

    def test_the_pieces_still_add_up_to_the_stretch_they_came_from(self):
        frames = blind_from([(1, 2), (0, 30), (1, 2)])
        result = blindness(
            frames,
            phases=table((5.0, 10.0, 'foul'), (20.0, 25.0, 'out_of_bounds')),
            tag_log=[tag('sub', 15.0)],
        )
        assert result.total_s == pytest.approx(30.0, abs=0.2)
        assert (
            result.seconds(DEAD)
            + result.seconds(ACCOUNTED)
            + result.seconds(UNEXPLAINED)
        ) == pytest.approx(result.total_s)

    def test_a_stoppage_entirely_inside_the_gap_leaves_live_play_on_both_sides(self):
        frames = blind_from([(1, 2), (0, 30), (1, 2)])
        result = blindness(frames, phases=table((10.0, 20.0, 'foul')))
        assert [s.kind for s in result.spells] == [UNEXPLAINED, DEAD, UNEXPLAINED]


class TestWhatItRefusesToClaim:
    def test_without_a_log_nothing_is_sorted_and_the_figures_are_withheld(self):
        """"Nobody looked" and "we looked and found nothing" are different.

        Three zeroes beside a four-minute total would read as a clean bill of
        health for a run where no check was even possible.
        """
        frames = blind_from([(1, 5), (0, 40), (1, 5)])
        result = blindness(frames)
        assert result.checked is False
        assert [s.kind for s in result.spells] == [UNCHECKED]
        published = result.to_json()
        assert published['total_s'] == pytest.approx(40.0, abs=0.2)
        assert published['dead_s'] is None
        assert published['accounted_s'] is None
        assert published['unexplained_s'] is None

    def test_an_empty_log_with_a_phase_table_still_counts_as_checked(self):
        """A tagged half with no stoppages in it is a checked half."""
        result = blindness(blind_from([(1, 2), (0, 10), (1, 2)]), phases=table())
        assert result.checked is True
        assert result.to_json()['dead_s'] == 0.0

    def test_a_run_where_the_ball_was_never_lost_reports_nothing(self):
        result = blindness(states([1] * 50), phases=table())
        assert result.spells == []
        assert result.total_s == 0.0
        assert result.to_json()['worst'] == []

    def test_the_split_never_exceeds_the_total_it_is_a_split_of(self):
        frames = blind_from([(1, 3), (0, 25), (1, 3), (0, 15), (1, 3)])
        result = blindness(
            frames,
            phases=table((5.0, 12.0, 'out_of_bounds')),
            tag_log=[tag('throw_in', 12.0), tag('foul', 35.0)],
        )
        assert result.seconds(DEAD) <= result.total_s
        assert result.seconds(ACCOUNTED) <= result.total_s


class TestWhichStretchesGetNamed:
    def test_the_longest_unresolved_stretch_is_the_one_carried(self):
        frames = blind_from([
            (1, 2), (0, 5), (1, 2), (0, 40), (1, 2), (0, 8), (1, 2),
        ])
        result = blindness(frames, phases=table())
        assert result.longest().duration_s == pytest.approx(40.0, abs=0.3)

    def test_a_stoppage_is_never_the_worst_stretch_however_long_it_ran(self):
        """Two minutes of a dead ball is not the worst thing in a match.

        Ranking on raw duration would put every substitution above every real
        blackout, which is the exact confusion this module exists to undo.
        """
        frames = blind_from([(1, 2), (0, 90), (1, 2), (0, 20), (1, 2)])
        result = blindness(frames, phases=table((2.0, 92.0, 'foul')))
        assert result.longest().duration_s == pytest.approx(20.0, abs=0.3)

    def test_the_named_list_runs_longest_first(self):
        frames = blind_from([
            (1, 1), (0, 6), (1, 1), (0, 20), (1, 1), (0, 12), (1, 1),
        ])
        named = blindness(frames, phases=table()).named()
        assert [round(s.duration_s) for s in named] == [20, 12, 6]

    def test_only_five_of_them_travel(self):
        pattern = []
        for _ in range(9):
            pattern.append((1, 1))
            pattern.append((0, 10))
        result = blindness(blind_from(pattern), phases=table())
        assert len(result.to_json()['worst']) == 5

    def test_nothing_is_carried_for_a_run_with_no_unresolved_stretches(self):
        result = blindness(blind_from([(1, 2), (0, 20), (1, 2)]),
                           phases=table((0.0, 30.0, 'out_of_bounds')))
        assert result.longest() is None
        assert result.to_json()['worst'] == []


class TestTheWarning:
    def test_a_long_unexplained_blackout_is_worth_interrupting_a_coach_for(self):
        frames = blind_from([(1, 2), (0, LONG_BLIND_S + 10), (1, 2)])
        warnings = blindness_warnings(blindness(frames, phases=table()))
        assert len(warnings) == 1
        assert '40s' in warnings[0]

    def test_a_short_one_is_a_statistic_rather_than_a_warning(self):
        frames = blind_from([(1, 2), (0, LONG_BLIND_S - 10), (1, 2)])
        assert blindness_warnings(blindness(frames, phases=table())) == []

    def test_a_long_stoppage_raises_nothing(self):
        frames = blind_from([(1, 2), (0, 100), (1, 2)])
        result = blindness(frames, phases=table((0.0, 110.0, 'foul')))
        assert blindness_warnings(result) == []

    def test_many_blackouts_are_one_warning_with_a_count(self):
        """A warning list that is all one thing is a list nobody reads.

        `trustworthy` is `not warnings`, so a run of poor footage would
        otherwise bury the reconciliation and calibration warnings under fifty
        copies of the same sentence.
        """
        pattern = []
        for _ in range(6):
            pattern.append((1, 2))
            pattern.append((0, 40))
        warnings = blindness_warnings(blindness(blind_from(pattern), phases=table()))
        assert len(warnings) == 1
        assert '5 more like it' in warnings[0]

    def test_an_unchecked_run_says_so_rather_than_blaming_the_pipeline(self):
        frames = blind_from([(1, 2), (0, 45), (1, 2)])
        warnings = blindness_warnings(blindness(frames))
        assert len(warnings) == 1
        assert 'no tagged log' in warnings[0]

    def test_nothing_to_warn_about_is_no_warning(self):
        assert blindness_warnings(None) == []
        assert blindness_warnings(Blindness()) == []


class TestTheTaggedMoments:
    def test_entries_without_a_clock_are_dropped_rather_than_placed_at_zero(self):
        moments = tagged_moments([
            tag('corner', 10.0), {'type': 'foul'}, {'matchClockS': 20.0},
        ])
        assert moments == [(10.0, 'corner')]

    def test_a_boolean_clock_is_not_a_number(self):
        assert tagged_moments([{'type': 'foul', 'matchClockS': True}]) == []

    def test_they_come_back_in_time_order(self):
        moments = tagged_moments([tag('foul', 30.0), tag('corner', 10.0)])
        assert [m[0] for m in moments] == [10.0, 30.0]


class TestTheJson:
    def test_a_spell_carries_its_own_arithmetic(self):
        published = BlindSpell(12.0, 18.5, ACCOUNTED, ('corner',)).to_json()
        assert published == {
            'start_s': 12.0, 'end_s': 18.5, 'duration_s': 6.5,
            'kind': ACCOUNTED, 'tags': ['corner'],
        }

    def test_the_three_figures_fit_inside_the_total(self):
        frames = blind_from([(1, 2), (0, 20), (1, 2), (0, 10), (1, 2)])
        published = blindness(
            frames,
            phases=table((2.0, 12.0, 'out_of_bounds')),
            tag_log=[tag('throw_in', 12.0)],
        ).to_json()
        parts = (
            published['dead_s'] + published['accounted_s']
            + published['unexplained_s']
        )
        assert parts == pytest.approx(published['total_s'], abs=0.2)


class TestTheTerminalSummary:
    """The line a run prints, which is the only place most of this is read.

    `no_ball_s` has been printable since possession existed and was never worth
    much on its own — the same footage tagged and untagged printed the same
    figure, and the tagged run was the one being punished for it.
    """

    def report_with(self, seen: Blindness):
        from cv.pipeline import MatchReport

        report = MatchReport(source='clip.mp4', duration_s=120.0, processing_s=1.0)
        report.blindness = seen
        return report

    def test_the_split_and_the_worst_stretch_are_both_printed(self):
        frames = blind_from([(1, 2), (0, 20), (1, 2), (0, 40), (1, 2)])
        line = self.report_with(
            blindness(frames, phases=table((2.0, 12.0, 'out_of_bounds')))
        ).summary()
        assert '60s with no ball' in line
        assert '10s dead' in line
        assert 'worst 40s from 24s' in line

    def test_an_unchecked_run_says_so_instead_of_printing_three_zeroes(self):
        frames = blind_from([(1, 2), (0, 20), (1, 2)])
        line = self.report_with(blindness(frames)).summary()
        assert 'no tagged log' in line
        assert '0s dead' not in line

    def test_a_run_that_never_lost_the_ball_prints_no_line_at_all(self):
        line = self.report_with(blindness(states([1] * 30), phases=table())).summary()
        assert 'no ball' not in line
