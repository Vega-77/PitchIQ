"""Whether a run would have fitted inside a live half.

The arithmetic is small; the judgement in it is not. Three things are load
bearing and each has its own tests below:

  - the remainder between the stages and the whole is a reported line, not a
    rounding error, because that is where an optimisation gets wasted;
  - a run over no footage has *no* real-time factor rather than a factor of
    zero, the same absent-is-not-zero rule the quality block already follows;
  - a clip does not scale onto a half by multiplication. Loading the model is
    paid once and `merge_tracks` grows faster than the footage, so the three
    kinds of stage project three different ways. This is not a refinement:
    measured cold, the pipeline reads 1.48x and "impossible"; the same run once
    warm reads 0.33x and "comfortably live". See cv/timing.py for the figures.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_timing.py -q
"""

from __future__ import annotations

import pytest

from cv.timing import (
    FIXED, HALF_S, SUPERLINEAR, Timings, fastest_rate_that_fits,
)


def run(total_s: float, **stages: float) -> Timings:
    """A finished run: named stages, and a total that may exceed their sum."""
    timings = Timings()
    for name, seconds in stages.items():
        timings.add(name, seconds)
    timings.total_s = total_s
    return timings


class TestStages:
    def test_the_same_stage_twice_is_one_line(self):
        """A loop re-entered is not two stages.

        The reader wants to know what detection cost, not how many times the
        batch loop went round.
        """
        timings = Timings()
        timings.add('detect', 2.0)
        timings.add('detect', 3.0)
        assert len(timings.stages) == 1
        assert timings.stages[0].seconds == pytest.approx(5.0)

    def test_a_stage_that_raised_still_records_its_time(self):
        """The run that fell over is exactly the one somebody wants timed."""
        timings = Timings()
        with pytest.raises(ValueError):
            with timings.stage('detect'):
                raise ValueError('out of memory')
        assert timings.stages[0].name == 'detect'
        assert timings.stages[0].seconds >= 0

    def test_stages_are_read_most_expensive_first(self):
        timings = run(10.0, detect=6.0, identity=3.0, ball=1.0)
        assert [s.name for s in timings.ranked()] == ['detect', 'identity', 'ball']

    def test_the_worst_scaling_inside_a_name_wins(self):
        """A name covering several kinds grows like the worst of them."""
        timings = Timings()
        timings.add('identity', 1.0)
        timings.add('identity', 1.0, scaling=SUPERLINEAR)
        assert timings.stages[0].scaling == SUPERLINEAR

        other = Timings()
        other.add('setup', 1.0, scaling=FIXED)
        other.add('setup', 1.0)
        assert other.stages[0].scaling == 'linear'


class TestTheRemainder:
    def test_the_gap_between_the_stages_and_the_whole_is_named(self):
        # Ten seconds of run, seven of it accounted for. The other three are
        # setup, imports and everything nobody wrapped, and a breakdown that
        # hid them would send a reader optimising the wrong stage.
        timings = run(10.0, detect=5.0, identity=2.0)
        assert timings.accounted_s == pytest.approx(7.0)
        assert timings.unaccounted_s == pytest.approx(3.0)

    def test_a_remainder_is_never_negative(self):
        """Overlapping stages, or a total nobody set.

        Reporting a negative remainder as if it were time saved is worse than
        reporting nothing.
        """
        assert run(1.0, detect=5.0).unaccounted_s == 0.0
        assert Timings().unaccounted_s == 0.0

    def test_the_remainder_survives_into_the_json(self):
        assert run(10.0, detect=7.0).to_json(60.0)['unaccounted_s'] == pytest.approx(3.0)


class TestKeepingUp:
    def test_the_factor_is_work_per_second_of_football(self):
        assert run(30.0, detect=30.0).realtime_factor(60.0) == pytest.approx(0.5)

    def test_a_fixed_cost_is_kept_out_of_the_factor(self):
        """The measurement that forced this module to be rewritten.

        A real cold run: six seconds of footage, 8.86s of work, 1.86s of it
        loading the detector. Divided flat that is 1.48x and a verdict of
        "cannot keep up". The work that actually scales is 7.0s over 6, which
        is 1.17x — and the same pipeline warm is 0.33x.
        """
        timings = Timings()
        timings.add('load the detector', 1.86, scaling=FIXED)
        timings.add('detect', 7.0)
        timings.total_s = 8.86
        assert timings.realtime_factor(6.0) == pytest.approx(7.0 / 6.0)
        assert timings.fixed_s == pytest.approx(1.86)
        assert timings.scaling_s == pytest.approx(7.0)

    def test_the_unmeasured_remainder_is_assumed_to_scale(self):
        """The pessimistic default, and deliberately so.

        Nobody measured the gap. Calling it fixed would flatter every run on a
        deadline nobody can afford to be optimistic about.
        """
        timings = Timings()
        timings.add('detect', 4.0)
        timings.total_s = 10.0
        assert timings.scaling_s == pytest.approx(10.0)
        assert timings.realtime_factor(10.0) == pytest.approx(1.0)

    def test_no_footage_has_no_factor_rather_than_a_factor_of_zero(self):
        # A run over an empty window did not achieve real time.
        assert run(30.0, detect=30.0).realtime_factor(0.0) is None
        assert run(30.0, detect=30.0).realtime_factor(None) is None
        assert Timings().realtime_factor(60.0) is None

    def test_keeps_up_is_the_factor_under_one(self):
        assert run(30.0, detect=30.0).keeps_up(60.0) is True
        assert run(90.0, detect=90.0).keeps_up(60.0) is False
        assert Timings().keeps_up(60.0) is None

    def test_exactly_real_time_does_not_keep_up(self):
        """1.0 is the boundary and it falls on the wrong side of it.

        A run that exactly matches the football finishes at the whistle with
        nothing in hand, and a whole match day has hiccups in it.
        """
        assert run(60.0, detect=60.0).keeps_up(60.0) is False


class TestHowLate:
    def test_a_run_that_keeps_up_is_zero_minutes_late(self):
        # Zero is a real answer here, unlike the factor: it keeps up, so the
        # report is ready at the whistle.
        assert run(30.0, detect=30.0).lag_s(60.0) == 0.0

    def test_a_run_at_half_again_is_half_a_half_late(self):
        late = run(90.0, detect=90.0).lag_s(60.0, half_s=HALF_S)
        assert late == pytest.approx(0.5 * HALF_S)

    def test_loading_the_model_does_not_make_the_report_late(self):
        """It happens before kick-off, not during the half."""
        timings = Timings()
        timings.add('load the detector', 600.0, scaling=FIXED)
        timings.add('detect', 30.0)
        timings.total_s = 630.0
        assert timings.lag_s(60.0) == 0.0
        assert timings.keeps_up(60.0) is True

    def test_the_projection_pays_the_fixed_cost_once(self):
        timings = Timings()
        timings.add('load the detector', 13.0, scaling=FIXED)
        timings.add('detect', 30.0)
        timings.total_s = 43.0
        # 30s of work over 60s of footage is 0.5x, so a 2700s half costs 1350s
        # of work — plus the same 13 seconds of loading, not 585 of it.
        assert timings.project_s(60.0, half_s=2700.0) == pytest.approx(1363.0)

    def test_the_lag_scales_with_the_half_it_is_projected_onto(self):
        """An under-14 half is shorter, and so is the lag."""
        timings = run(120.0, detect=120.0)
        assert timings.lag_s(60.0, half_s=1800.0) == pytest.approx(1800.0)
        assert timings.lag_s(60.0, half_s=2700.0) == pytest.approx(2700.0)

    def test_no_footage_has_no_lag(self):
        assert run(90.0, detect=90.0).lag_s(0.0) is None


class TestTheProjectionIsAFloor:
    def test_a_quadratic_stage_that_matters_makes_it_optimistic(self):
        timings = Timings()
        timings.add('detect', 8.0)
        timings.add('identity', 2.0, scaling=SUPERLINEAR)
        timings.total_s = 10.0
        assert timings.optimistic() is True

    def test_a_fixed_stage_never_makes_a_projection_optimistic(self):
        """It makes it pessimistic, which is handled by excluding it."""
        timings = Timings()
        timings.add('load the detector', 8.0, scaling=FIXED)
        timings.add('detect', 2.0)
        timings.total_s = 10.0
        assert timings.optimistic() is False

    def test_a_quadratic_stage_too_small_to_matter_does_not(self):
        # Half a percent of the run. Quadratic or not, it does not change
        # whether the report arrives, and qualifying every projection over it
        # would make the qualification meaningless.
        timings = Timings()
        timings.add('detect', 995.0)
        timings.add('identity', 5.0, scaling=SUPERLINEAR)
        timings.total_s = 1000.0
        assert timings.optimistic() is False

    def test_an_all_linear_run_projects_straight(self):
        assert run(10.0, detect=10.0).optimistic() is False

    def test_the_qualification_travels_in_the_json(self):
        timings = Timings()
        timings.add('identity', 5.0, scaling=SUPERLINEAR)
        timings.add('detect', 5.0)
        timings.total_s = 10.0
        assert timings.to_json(60.0)['optimistic'] is True


class TestJson:
    def test_every_value_survives_json(self):
        import json
        timings = run(10.0, detect=7.0, identity=1.0)
        json.dumps(timings.to_json(120.0))

    def test_the_shape_a_reader_depends_on(self):
        data = run(120.0, detect=100.0).to_json(60.0, half_s=2700.0)
        assert data['total_s'] == pytest.approx(120.0)
        assert data['footage_s'] == pytest.approx(60.0)
        assert data['realtime_factor'] == pytest.approx(2.0)
        assert data['keeps_up'] is False
        assert data['lag_s'] == pytest.approx(2700.0)
        assert data['projected_half_s'] == pytest.approx(5400.0)
        assert data['fixed_s'] == pytest.approx(0.0)
        assert data['scaling_s'] == pytest.approx(120.0)
        assert data['stages'][0]['name'] == 'detect'

    def test_an_uninstrumented_run_reports_nulls_not_zeroes(self):
        data = Timings().to_json(None)
        assert data['realtime_factor'] is None
        assert data['keeps_up'] is None
        assert data['lag_s'] is None
        assert data['footage_s'] is None


class TestChoosingARate:
    def test_the_highest_rate_that_fits_wins(self):
        assert fastest_rate_that_fits({6.0: 0.4, 12.0: 0.8, 30.0: 1.9}) == 12.0

    def test_nothing_fitting_is_none_rather_than_the_slowest_rate(self):
        """A real finding, and it must not be rounded into a recommendation.

        "Run at 6Hz" and "6Hz is still too slow" are opposite answers, and
        returning the lowest measured rate would turn the second into the
        first.
        """
        assert fastest_rate_that_fits({6.0: 1.4, 12.0: 2.2}) is None

    def test_a_budget_below_one_leaves_headroom(self):
        rates = {6.0: 0.4, 12.0: 0.85, 30.0: 1.9}
        assert fastest_rate_that_fits(rates, budget=1.0) == 12.0
        assert fastest_rate_that_fits(rates, budget=0.7) == 6.0

    def test_a_rate_that_was_never_measured_is_skipped(self):
        assert fastest_rate_that_fits({6.0: 0.4, 30.0: None}) == 6.0
