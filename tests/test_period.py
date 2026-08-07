"""Deciding which half a clip is, and saying so when the answer is shaky.

`MatchOrientation.attacking_end` flips the whole pitch on the period. Get it
wrong and every shot map, heatmap reading, pressing zone, territory split,
turnover-by-third, passing network and xG figure in the report is mirrored — and
the output looks exactly as plausible as a correct one. Nothing downstream can
catch it, because a shot map at the wrong end is a shot map.

So these tests are mostly about precedence and about noise: the tagged log beats
the flag, a disagreement is said out loud, and a window that runs through the
break is called out rather than quietly averaged into one answer.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_period.py -q
"""

from __future__ import annotations

from cv.phases import periods_from_log
from cv.pipeline import MatchReport, _resolve_period


def entry(kind, clock_s):
    return {'type': kind, 'matchClockS': clock_s, 'kind': 'event'}


BOTH_HALVES = [
    entry('kickoff_1st', 0.0), entry('halftime', 2700.0),
    entry('kickoff_2nd', 3600.0), entry('full_time', 6300.0),
]


def a_report(log=None, duration_s=900.0, offset_s=0.0):
    report = MatchReport(source='clip.mp4', duration_s=duration_s, processing_s=1.0)
    report.periods = periods_from_log(log).shifted(offset_s) if log else None
    return report


class TestTheLogWins:
    def test_a_second_half_is_recognised_without_the_flag(self):
        """The whole point. A default of `first_half` on second-half footage
        mirrors the entire pitch and nothing in the output shows it."""
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, None, 3700.0, 4600.0) == 'second_half'
        assert report.period_source == 'log'
        assert report.warnings == []

    def test_a_first_half_is_recognised_too(self):
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, None, 600.0, 1500.0) == 'first_half'
        assert report.period_source == 'log'

    def test_a_flag_that_disagrees_loses_and_is_reported(self):
        # One of the two is wrong and it matters which — but the log is a
        # record of what somebody watched and the flag is a default.
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, 'first_half', 3700.0, 4600.0) == 'second_half'
        assert report.period_source == 'log'
        assert any('the log wins' in w for w in report.warnings)

    def test_a_flag_that_agrees_says_nothing(self):
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, 'second_half', 3700.0, 4600.0) == 'second_half'
        assert report.warnings == []

    def test_the_video_offset_is_applied_before_the_lookup(self):
        # The log is on the match clock and the window is in video seconds.
        report = a_report(BOTH_HALVES, offset_s=-3500.0)
        assert _resolve_period(report, None, 200.0, 1100.0) == 'second_half'


class TestWithoutALog:
    def test_the_flag_is_used_and_named_as_the_source(self):
        report = a_report()
        assert _resolve_period(report, 'second_half', 0.0, 900.0) == 'second_half'
        assert report.period_source == 'flag'
        assert report.warnings == []

    def test_nothing_at_all_falls_back_to_the_first_half(self):
        report = a_report()
        assert _resolve_period(report, None, 0.0, 900.0) == 'first_half'
        assert report.period_source == 'default'

    def test_the_fallback_is_not_a_warning(self):
        """Every uncalibrated exploratory run would trip it, and a warning that
        fires on every run takes the ones that matter down with it."""
        report = a_report()
        _resolve_period(report, None, 0.0, 900.0)
        assert report.warnings == []

    def test_a_log_with_no_kickoff_taps_is_the_same_as_no_log(self):
        report = a_report([entry('foul', 100.0), entry('throw_in', 140.0)])
        assert _resolve_period(report, None, 0.0, 900.0) == 'first_half'
        assert report.period_source == 'default'
        assert report.warnings == []


class TestThroughTheBreak:
    def test_it_warns_and_says_which_way_it_drew_the_pitch(self):
        report = a_report(BOTH_HALVES)
        chosen = _resolve_period(report, None, 2400.0, 6000.0)

        assert chosen == 'second_half', 'the half holding most of the window'
        assert len(report.warnings) == 1
        assert 'runs through the break' in report.warnings[0]
        assert 'mirrored' in report.warnings[0]
        assert 'second half' in report.warnings[0]

    def test_a_disputed_run_is_not_trustworthy(self):
        """`trustworthy` is `not warnings`, and half a report drawn backwards is
        exactly what that flag exists for."""
        report = a_report(BOTH_HALVES)
        _resolve_period(report, None, 2400.0, 6000.0)
        assert report.warnings

    def test_the_dominant_half_wins_rather_than_the_first_one_seen(self):
        report = a_report(BOTH_HALVES)
        # Forty minutes of the first half, five of the second.
        assert _resolve_period(report, None, 300.0, 3900.0) == 'first_half'


class TestAWindowTheLogDoesNotReach:
    def test_it_warns_about_the_offset_rather_than_guessing_a_half(self):
        # A log with kickoffs in it and a window touching neither means the
        # offset is probably wrong — which also puts every dead span in the
        # wrong place, silently.
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, None, 2800.0, 3400.0) == 'first_half'
        assert report.period_source == 'default'
        assert any('video offset' in w for w in report.warnings)

    def test_an_explicit_flag_still_takes_effect(self):
        report = a_report(BOTH_HALVES)
        assert _resolve_period(report, 'second_half', 2800.0, 3400.0) == 'second_half'
        assert report.period_source == 'flag'


class TestTheWindowItself:
    def test_an_open_ended_window_runs_to_the_end_of_what_was_processed(self):
        """`--end` is optional, so the fallback has to be the footage actually
        read rather than the length of the file."""
        report = a_report(BOTH_HALVES, duration_s=900.0)
        assert _resolve_period(report, None, 3700.0, None) == 'second_half'

    def test_a_missing_start_is_the_beginning_of_the_video(self):
        report = a_report(BOTH_HALVES, duration_s=900.0)
        assert _resolve_period(report, None, None, None) == 'first_half'
