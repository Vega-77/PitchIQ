"""Reading stoppages out of a tagged match log.

The log is the only record of when the ball was out of play, and every test
here is built from hand-written entries so the right answer is known. What they
mostly pin down is the *direction* of each compromise: a span shrinks rather
than grows, an untagged restart times out rather than running to the whistle,
and a restart nobody set up is ignored rather than guessed backwards from.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_phases.py -q
"""

from __future__ import annotations

import json

import pytest

from cv.experiments.event_report import load_tag_log, load_video_timing
from cv.phases import (
    DEAD_CAP_S,
    MAX_DEAD_S,
    TAG_SLOP_S,
    DeadSpan,
    PeriodSpan,
    PhaseTable,
    VideoClock,
    periods_from_log,
    phases_from_log,
)


def entry(kind, clock_s):
    return {'type': kind, 'matchClockS': clock_s, 'kind': 'event'}


class TestOpeningAndClosing:
    def test_a_stoppage_and_its_restart_make_one_span(self):
        table = phases_from_log([
            entry('out_of_bounds', 100.0),
            entry('throw_in', 120.0),
        ])

        assert len(table.spans) == 1
        span = table.spans[0]
        assert span.opened_by == 'out_of_bounds'
        assert span.closed_by == 'throw_in'
        assert not span.timed_out

    def test_a_second_stoppage_while_already_dead_extends_nothing(self):
        """A foul and the card that follows it are one interruption."""
        table = phases_from_log([
            entry('foul', 100.0),
            entry('card', 108.0),
            entry('free_kick', 130.0),
        ])

        assert len(table.spans) == 1
        assert table.spans[0].opened_by == 'foul'
        assert table.spans[0].closed_by == 'free_kick'

    def test_a_restart_with_no_stoppage_is_ignored(self):
        """Where the ball went out would be a guess, and a wrong one deletes
        possession that really happened."""
        assert phases_from_log([entry('throw_in', 50.0)]).spans == []

    def test_entries_are_read_in_clock_order_not_file_order(self):
        table = phases_from_log([
            entry('throw_in', 120.0),
            entry('out_of_bounds', 100.0),
        ])
        assert len(table.spans) == 1

    def test_half_time_closes_whatever_is_open(self):
        table = phases_from_log([
            entry('foul', 2600.0),
            entry('halftime', 2620.0),
        ])
        assert table.spans[0].closed_by == 'halftime'

    def test_an_empty_log_leaves_everything_live(self):
        table = phases_from_log([])
        assert table.spans == []
        assert table.dead_s == 0.0
        assert table.is_live(0.0)
        assert table.is_live(9999.0)


class TestSlop:
    """Tags land when somebody notices, not when the whistle goes."""

    def test_a_span_is_shrunk_at_both_ends(self):
        table = phases_from_log([
            entry('out_of_bounds', 100.0),
            entry('throw_in', 140.0),
        ])
        span = table.spans[0]

        assert span.start_s == pytest.approx(100.0 + TAG_SLOP_S)
        assert span.end_s == pytest.approx(140.0 - TAG_SLOP_S)
        assert span.duration_s < 40.0, 'never grown'

    def test_a_stoppage_shorter_than_the_slop_is_dropped(self):
        """Inside the tagger's reaction time there is nothing we can claim."""
        table = phases_from_log([
            entry('out_of_bounds', 100.0),
            entry('throw_in', 100.0 + TAG_SLOP_S),
        ])
        assert table.spans == []

    def test_the_moment_of_the_tag_itself_is_still_counted_live(self):
        table = phases_from_log([
            entry('out_of_bounds', 100.0),
            entry('throw_in', 140.0),
        ])
        assert table.is_live(100.0), 'the instant it was tapped'
        assert table.is_live(140.0)
        assert not table.is_live(120.0)


class TestTimeouts:
    def test_an_untagged_restart_times_out(self):
        table = phases_from_log([entry('foul', 100.0)])

        assert len(table.spans) == 1
        span = table.spans[0]
        assert span.timed_out
        assert span.closed_by is None
        assert span.end_s == pytest.approx(100.0 + MAX_DEAD_S - TAG_SLOP_S)

    def test_a_restart_beyond_the_cap_still_times_out(self):
        table = phases_from_log([
            entry('foul', 100.0),
            entry('free_kick', 100.0 + MAX_DEAD_S + 60.0),
        ])
        assert table.spans[0].timed_out
        assert table.spans[0].end_s == pytest.approx(100.0 + MAX_DEAD_S - TAG_SLOP_S)

    def test_a_goal_gets_a_shorter_cap_than_anything_else(self):
        """Nothing tags the kickoff after a goal, so without this every goal
        would delete the two minutes that followed it."""
        table = phases_from_log([entry('goal', 300.0)])
        span = table.spans[0]

        assert span.timed_out
        assert span.duration_s == pytest.approx(DEAD_CAP_S['goal'] - 2 * TAG_SLOP_S)
        assert span.duration_s < MAX_DEAD_S

    def test_timeouts_are_countable(self):
        table = phases_from_log([
            entry('foul', 100.0),
            entry('out_of_bounds', 400.0),
            entry('throw_in', 420.0),
        ])
        assert table.timed_out == 1
        assert len(table.spans) == 2


class TestQueries:
    def table(self):
        return PhaseTable(spans=[
            DeadSpan(100.0, 120.0, 'out_of_bounds', 'throw_in'),
            DeadSpan(200.0, 210.0, 'foul', 'free_kick'),
        ])

    def test_dead_seconds_add_up(self):
        assert self.table().dead_s == pytest.approx(30.0)

    def test_live_is_the_complement(self):
        table = self.table()
        assert table.is_live(50.0)
        assert not table.is_live(110.0)
        assert table.is_live(150.0)
        assert not table.is_live(205.0)
        assert table.is_live(500.0)

    def test_a_window_reports_the_share_that_was_live(self):
        table = self.table()
        assert table.live_share(0.0, 100.0) == pytest.approx(1.0)
        assert table.live_share(100.0, 120.0) == pytest.approx(0.0)
        assert table.live_share(90.0, 130.0) == pytest.approx(0.5)

    def test_a_window_overlapping_two_spans_counts_both(self):
        assert self.table().dead_between(0.0, 1000.0) == pytest.approx(30.0)

    def test_an_empty_window_is_not_a_division_by_zero(self):
        assert self.table().live_share(50.0, 50.0) == 0.0


class TestClockConversion:
    """The log runs on the match clock, the pipeline on video time."""

    def test_shifting_moves_every_span(self):
        table = PhaseTable(spans=[DeadSpan(100.0, 120.0, 'foul', 'free_kick')])
        shifted = table.on_video(VideoClock(30.0))

        assert shifted.spans[0].start_s == pytest.approx(130.0)
        assert shifted.spans[0].end_s == pytest.approx(150.0)
        assert not shifted.is_live(140.0)
        assert shifted.is_live(110.0)

    def test_shifting_keeps_why_each_span_exists(self):
        table = PhaseTable(spans=[DeadSpan(100.0, 120.0, 'foul', None)])
        span = table.on_video(VideoClock(-10.0)).spans[0]

        assert span.opened_by == 'foul'
        assert span.timed_out

    def test_the_original_is_untouched(self):
        table = PhaseTable(spans=[DeadSpan(100.0, 120.0, 'foul', 'free_kick')])
        table.on_video(VideoClock(500.0))
        assert table.spans[0].start_s == 100.0


class TestLoadingTheFile:
    """The seam between the coach's browser and the pipeline.

    `doDownloadLog` in coach.js writes a wrapper object carrying the ids so a
    file found later can say which match it belongs to; `listLog` returns a bare
    list. Both have to load, because both will exist on somebody's disk.
    """

    def write(self, tmp_path, data):
        path = tmp_path / 'log.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return str(path)

    def test_reads_the_wrapper_the_coach_page_downloads(self, tmp_path):
        path = self.write(tmp_path, {
            'teamId': 't1', 'matchId': 'm1', 'videoOffsetS': 120,
            'entries': [entry('foul', 100.0), entry('free_kick', 130.0)],
        })
        assert len(load_tag_log(path)) == 2

    def test_reads_a_bare_list_too(self, tmp_path):
        path = self.write(tmp_path, [entry('foul', 100.0)])
        assert len(load_tag_log(path)) == 1

    def test_no_path_is_no_log(self):
        assert load_tag_log(None) is None
        assert load_tag_log('') is None

    def test_something_that_is_not_a_log_is_an_error_not_an_empty_run(self, tmp_path):
        """Silently analysing with no stoppages would look like a clean run."""
        path = self.write(tmp_path, {'teamId': 't1'})
        with pytest.raises(ValueError, match='not a match log'):
            load_tag_log(path)

    def test_the_loaded_file_drives_the_phases(self, tmp_path):
        path = self.write(tmp_path, {
            'entries': [entry('out_of_bounds', 100.0), entry('throw_in', 140.0)],
        })
        assert len(phases_from_log(load_tag_log(path)).spans) == 1

    def test_the_timing_numbers_ride_along_in_the_same_file(self, tmp_path):
        # Both are typed into the coach's video form and both are needed to
        # place a second-half timestamp, so carrying them here beats asking
        # whoever runs the pipeline to remember two numbers from a browser tab.
        path = self.write(tmp_path, {
            'videoOffsetS': 120, 'secondHalfVideoS': 3660,
            'entries': [entry('foul', 100.0)],
        })
        assert load_video_timing(path) == {
            'videoOffsetS': 120.0, 'secondHalfVideoS': 3660.0,
        }

    def test_a_bare_list_carries_no_timing_rather_than_zeroes(self, tmp_path):
        # The older shape, and what an export from anywhere else looks like. A
        # zero offset is a claim that the recording started at kick-off.
        path = self.write(tmp_path, [entry('foul', 100.0)])
        assert load_video_timing(path) == {}
        assert load_video_timing(None) == {}

    def test_junk_in_those_fields_is_left_out(self, tmp_path):
        path = self.write(tmp_path, {
            'videoOffsetS': 'two minutes', 'secondHalfVideoS': True,
            'entries': [],
        })
        assert load_video_timing(path) == {}


class TestJunkInput:
    def test_substitutions_do_not_open_a_stoppage(self):
        """A sub happens inside a stoppage something else already tagged, and
        nothing restarts play afterwards — so treating it as an opener would
        time out and delete two minutes of football."""
        assert phases_from_log([entry('sub', 500.0)]).spans == []

    def test_entries_without_a_clock_are_skipped(self):
        log = [
            {'type': 'out_of_bounds'},
            {'type': 'out_of_bounds', 'matchClockS': None},
            entry('out_of_bounds', 100.0),
            entry('throw_in', 130.0),
        ]
        assert len(phases_from_log(log).spans) == 1

    def test_entries_without_a_type_are_skipped(self):
        assert phases_from_log([{'matchClockS': 100.0}]).spans == []

    def test_none_is_an_empty_log(self):
        assert phases_from_log(None).spans == []

    def test_a_boolean_clock_is_not_a_number(self):
        """True == 1 in Python, and a clock of one second is not a stoppage."""
        assert phases_from_log([{'type': 'foul', 'matchClockS': True}]).spans == []


# ---------------------------------------------------------------- which half
#
# The taps have been in the log since Phase 3 and nothing read them for this.
# What they decide is which way each team was kicking, and that flips every
# positional figure in a report — so the tests worth having are the ones about
# refusing to answer: no kickoff taps, a window in the break, a window that runs
# through it.


class TestPeriodsFromLog:
    def test_no_kickoff_taps_is_an_empty_table_not_a_guess(self):
        table = periods_from_log([entry('foul', 100.0), entry('goal', 300.0)])
        assert not table
        assert table.at(200.0) is None

    def test_a_kickoff_opens_a_half_that_halftime_closes(self):
        table = periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2700.0),
        ])
        assert table.spans == [PeriodSpan('first_half', 0.0, 2700.0)]

    def test_both_halves(self):
        table = periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2700.0),
            entry('kickoff_2nd', 3600.0), entry('full_time', 6300.0),
        ])
        assert [s.period for s in table.spans] == ['first_half', 'second_half']
        assert table.at(1000.0) == 'first_half'
        assert table.at(5000.0) == 'second_half'

    def test_the_break_belongs_to_neither_half(self):
        """Nobody is attacking anything while the sides are swapping over."""
        table = periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2700.0),
            entry('kickoff_2nd', 3600.0),
        ])
        assert table.at(3000.0) is None

    def test_before_the_first_kickoff_belongs_to_neither(self):
        """A shot in the warm-up is not first-half football."""
        table = periods_from_log([entry('kickoff_1st', 120.0)])
        assert table.at(60.0) is None
        assert table.at(200.0) == 'first_half'

    def test_a_missing_halftime_tap_is_closed_by_the_next_kickoff(self):
        table = periods_from_log([
            entry('kickoff_1st', 0.0), entry('kickoff_2nd', 3600.0),
        ])
        assert table.spans[0] == PeriodSpan('first_half', 0.0, 3600.0)
        assert table.spans[1].end_s is None

    def test_a_half_nobody_closed_runs_open_rather_than_being_guessed_at(self):
        """Assuming 45 minutes would invent the one boundary that matters."""
        assert periods_from_log([entry('kickoff_1st', 0.0)]).spans[0].end_s is None

    def test_a_double_tap_is_one_kickoff(self):
        table = periods_from_log([
            entry('kickoff_2nd', 3600.0), entry('kickoff_2nd', 3602.0),
        ])
        assert len(table.spans) == 1
        assert table.spans[0].start_s == 3600.0

    def test_the_offset_moves_it_onto_video_time(self):
        table = periods_from_log([
            entry('kickoff_2nd', 3600.0), entry('full_time', 6300.0),
        ]).on_video(VideoClock(-3500.0))
        assert table.at(200.0) == 'second_half'
        assert table.at(3600.0) is None


class TestWhatAWindowCovers:
    def both_halves(self):
        return periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2700.0),
            entry('kickoff_2nd', 3600.0), entry('full_time', 6300.0),
        ])

    def test_a_window_inside_one_half_covers_one(self):
        assert self.both_halves().covering(600.0, 1200.0) == ['first_half']

    def test_a_window_through_the_break_covers_both(self):
        assert self.both_halves().covering(2400.0, 3900.0) == [
            'first_half', 'second_half',
        ]

    def test_a_window_entirely_in_the_break_covers_nothing(self):
        assert self.both_halves().covering(2800.0, 3400.0) == []

    def test_the_dominant_half_is_the_one_holding_most_of_the_window(self):
        # Five minutes of the first half in front of forty of the second reads
        # as a second half, even though neither answer is right for all of it.
        assert self.both_halves().dominant(2400.0, 6000.0) == 'second_half'

    def test_dominant_is_none_when_the_window_touches_no_half(self):
        assert self.both_halves().dominant(2800.0, 3400.0) is None

    def test_an_open_ended_half_still_wins_a_window_it_holds(self):
        table = periods_from_log([entry('kickoff_2nd', 0.0)])
        assert table.dominant(100.0, 900.0) == 'second_half'


class TestVideoClock:
    """The interval, which one offset cannot cross.

    A tablet's clock stops at half-time and a recording does not, so the
    relation between the two is piecewise and every consumer of a tagged
    timestamp inherits whichever version it was given. These are the numbers
    from a real shape: recording started two minutes early, first half ran
    46:00, thirteen minutes of oranges.
    """

    OFFSET = 120.0
    FIRST_HALF_S = 46 * 60.0
    BREAK_S = 13 * 60.0
    RESTART_VIDEO_S = OFFSET + FIRST_HALF_S + BREAK_S

    def clock(self):
        return VideoClock(
            video_offset_s=self.OFFSET,
            second_half_video_s=self.RESTART_VIDEO_S,
            second_half_clock_s=self.FIRST_HALF_S,
        )

    def test_the_first_half_is_the_plain_offset(self):
        clock = self.clock()
        assert clock.to_video(0.0) == pytest.approx(self.OFFSET)
        assert clock.to_clock(self.OFFSET + 600.0) == pytest.approx(600.0)

    def test_the_second_half_is_not_carried_through_the_break(self):
        clock = self.clock()
        ten_minutes_in = self.FIRST_HALF_S + 600.0

        assert clock.to_video(ten_minutes_in) == pytest.approx(
            self.RESTART_VIDEO_S + 600.0
        )
        # And the size of what a single offset would have got wrong.
        naive = ten_minutes_in + self.OFFSET
        assert clock.to_video(ten_minutes_in) - naive == pytest.approx(self.BREAK_S)

    def test_the_break_reports_the_reading_it_froze_on(self):
        clock = self.clock()
        mid_break = self.OFFSET + self.FIRST_HALF_S + 300.0
        assert clock.to_clock(mid_break) == pytest.approx(self.FIRST_HALF_S)

    def test_it_round_trips_on_both_sides(self):
        clock = self.clock()
        for clock_s in (0.0, 600.0, self.FIRST_HALF_S, self.FIRST_HALF_S + 900.0):
            assert clock.to_clock(clock.to_video(clock_s)) == pytest.approx(clock_s)

    def test_the_restart_second_belongs_to_the_second_half(self):
        # `halftime` and `kickoff_2nd` share a reading, and the restart is the
        # position worth having.
        assert self.clock().to_video(self.FIRST_HALF_S) == pytest.approx(
            self.RESTART_VIDEO_S
        )

    def test_with_one_anchor_it_is_the_old_behaviour(self):
        clock = VideoClock(video_offset_s=self.OFFSET)
        assert not clock.knows_break
        assert clock.to_video(600.0) == pytest.approx(720.0)
        assert clock.to_clock(720.0) == pytest.approx(600.0)

    def test_a_negative_break_is_refused_rather_than_honoured(self):
        # The second half cannot kick off before the first one ended. Using it
        # would map two positions in the footage to one reading on the clock.
        clock = VideoClock(
            video_offset_s=self.OFFSET,
            second_half_video_s=self.OFFSET + 30 * 60.0,
            second_half_clock_s=self.FIRST_HALF_S,
        )
        assert not clock.knows_break
        assert clock.to_video(2400.0) == pytest.approx(2400.0 + self.OFFSET)

    def test_it_never_runs_backwards(self):
        # The property everything drawn on a timeline rests on.
        clock = self.clock()
        previous = float('-inf')
        for video_s in range(0, 7000, 17):
            reading = clock.to_clock(float(video_s))
            assert reading >= previous
            previous = reading

    def test_from_periods_takes_the_restart_clock_out_of_the_log(self):
        # Half the answer is already tagged: the second-half span starts on the
        # reading the break froze at. Only the video position has to be given.
        periods = periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2760.0),
            entry('kickoff_2nd', 2760.0), entry('full_time', 5400.0),
        ])
        clock = VideoClock.from_periods(periods, 120.0, 3660.0)

        assert clock.second_half_clock_s == pytest.approx(2760.0)
        assert clock.break_s == pytest.approx(780.0)

    def test_from_periods_without_a_second_half_stays_single_anchored(self):
        # A video position with no tagged restart to pin it to is not enough to
        # anchor anything, and guessing 45 minutes would invent the boundary
        # this whole module exists to read.
        periods = periods_from_log([entry('kickoff_1st', 0.0)])
        clock = VideoClock.from_periods(periods, 120.0, 3660.0)
        assert not clock.knows_break

    def test_the_break_becomes_a_gap_between_the_spans(self):
        # On a single-anchor clock the first half's video span swallowed the
        # interval, so a frame from the oranges was reported as first-half
        # football and drawn at whichever end that implies.
        periods = periods_from_log([
            entry('kickoff_1st', 0.0), entry('halftime', 2760.0),
            entry('kickoff_2nd', 2760.0),
        ])
        table = periods.on_video(VideoClock.from_periods(periods, 0.0, 3540.0))

        assert table.at(2000.0) == 'first_half'
        assert table.at(3000.0) is None          # in the break
        assert table.at(3600.0) == 'second_half'

    def test_dead_spans_move_with_it_too(self):
        table = PhaseTable(spans=[DeadSpan(3000.0, 3020.0, 'foul', 'free_kick')])
        clock = VideoClock(0.0, second_half_video_s=3540.0,
                           second_half_clock_s=2760.0)
        moved = table.on_video(clock).spans[0]

        assert moved.start_s == pytest.approx(3780.0)
        assert moved.opened_by == 'foul'
