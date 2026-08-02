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

from cv.experiments.event_report import load_tag_log
from cv.phases import (
    DEAD_CAP_S,
    MAX_DEAD_S,
    TAG_SLOP_S,
    DeadSpan,
    PhaseTable,
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
        shifted = table.shifted(30.0)

        assert shifted.spans[0].start_s == pytest.approx(130.0)
        assert shifted.spans[0].end_s == pytest.approx(150.0)
        assert not shifted.is_live(140.0)
        assert shifted.is_live(110.0)

    def test_shifting_keeps_why_each_span_exists(self):
        table = PhaseTable(spans=[DeadSpan(100.0, 120.0, 'foul', None)])
        span = table.shifted(-10.0).spans[0]

        assert span.opened_by == 'foul'
        assert span.timed_out

    def test_the_original_is_untouched(self):
        table = PhaseTable(spans=[DeadSpan(100.0, 120.0, 'foul', 'free_kick')])
        table.shifted(500.0)
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
