"""The report diff, which is the regression suite the pipeline has never had.

The thing worth being careful about here is what counts as "the same". Too
strict and every run fails on a floating-point digit and the check gets switched
off; too loose and a real change slides through. The cases below are the ones
that decide which side of that line the tool sits on.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_compare_reports.py -q
"""

import json

from cv.experiments.compare_reports import VOLATILE, compare, main


def only(differences):
    assert len(differences) == 1, differences
    return differences[0]


class TestAgreement:
    def test_identical_reports_say_nothing(self):
        report = {'quality': {'ball_seen_share': 0.83}, 'warnings': []}
        assert compare(report, json.loads(json.dumps(report))) == []

    def test_a_last_decimal_place_is_not_a_change(self):
        """Otherwise every run fails on a numpy version and this gets muted."""
        assert compare({'x': 0.8300000001}, {'x': 0.83}) == []

    def test_processing_time_is_never_a_difference(self):
        """It is a property of the machine, not of the football."""
        assert 'processing_s' in VOLATILE
        assert compare({'processing_s': 11.6}, {'processing_s': 240.0}) == []


class TestNumbers:
    def test_a_real_move_is_reported_with_its_size(self):
        assert '+20.0%' in only(compare({'x': 100.0}, {'x': 120.0}))

    def test_the_tolerance_is_honoured(self):
        assert compare({'x': 100.0}, {'x': 101.0}, tolerance=0.05) == []
        assert compare({'x': 100.0}, {'x': 101.0}, tolerance=0.001) != []

    def test_near_zero_uses_the_absolute_floor(self):
        """0.001 and 0.002 differ by 100% and by nothing that matters.

        Without a floor, every quantity that lives near zero — and a good
        calibration error does — reports a change on every run.
        """
        assert compare({'x': 0.0}, {'x': 1e-9}) == []
        assert compare({'x': 0.0}, {'x': 0.5}) != []

    def test_booleans_are_not_numbers(self):
        """True == 1 in Python, and a trustworthy flag flipping is not a 0%
        numeric change."""
        assert compare({'trustworthy': True}, {'trustworthy': False}) != []


class TestAbsence:
    def test_unmeasured_becoming_zero_is_a_difference(self):
        """The change this project cares about most.

        "We could not measure this" turning into "we measured 0.0" is exactly
        the regression the absent-is-not-zero rule exists to prevent, and a
        purely numeric comparison would crash on it or call it equal.
        """
        assert 'null -> 0.0' in only(compare({'live_share': None}, {'live_share': 0.0}))

    def test_zero_becoming_unmeasured_is_too(self):
        assert compare({'dead_ball_s': 0.0}, {'dead_ball_s': None}) != []

    def test_both_absent_agree(self):
        assert compare({'x': None}, {'x': None}) == []

    def test_a_new_key_is_named(self):
        assert 'new' in only(compare({}, {'reconciliation': {}}))

    def test_a_lost_key_is_named(self):
        assert 'gone' in only(compare({'quality': {}}, {}))


class TestStructure:
    def test_nested_paths_are_reported_in_full(self):
        """A bare "0.83 -> 0.61" in a report with two hundred numbers in it is
        not actionable."""
        line = only(compare(
            {'teams': {'team_a': {'xg': 1.2}}},
            {'teams': {'team_a': {'xg': 2.4}}},
        ))
        assert line.startswith('teams.team_a.xg:')

    def test_a_list_that_changed_length_reports_the_count_and_stops(self):
        """Two lists that slipped by one differ at almost every index, and
        four hundred of those bury the one fact worth having."""
        line = only(compare({'events': [1, 2, 3]}, {'events': [1, 2, 3, 4]}))
        assert '3 items became 4' in line

    def test_same_length_lists_are_compared_element_wise(self):
        line = only(compare(
            {'events': [{'type': 'pass'}, {'type': 'shot'}]},
            {'events': [{'type': 'pass'}, {'type': 'carry'}]},
        ))
        assert line.startswith('events[1].type:')

    def test_a_long_value_is_shortened_rather_than_dumped(self):
        line = only(compare({'w': ['x' * 400]}, {'w': ['y' * 400]}))
        assert len(line) < 200

    def test_ignored_keys_are_ignored_at_every_depth(self):
        assert compare(
            {'a': {'processing_s': 1.0}}, {'a': {'processing_s': 99.0}},
        ) == []


class TestTheCommand:
    def write(self, tmp_path, name, data):
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_an_unchanged_run_exits_zero(self, tmp_path, capsys):
        report = {'quality': {'ball_seen_share': 0.83}}
        a = self.write(tmp_path, 'a.json', report)
        b = self.write(tmp_path, 'b.json', report)

        assert main([str(a), str(b)]) == 0
        assert 'no change' in capsys.readouterr().out

    def test_a_changed_run_exits_one_and_lists_what_moved(self, tmp_path, capsys):
        a = self.write(tmp_path, 'a.json', {'quality': {'ball_seen_share': 0.83}})
        b = self.write(tmp_path, 'b.json', {'quality': {'ball_seen_share': 0.41}})

        assert main([str(a), str(b)]) == 1
        out = capsys.readouterr().out
        assert 'quality.ball_seen_share' in out
        # Said out loud, because exit 1 reads as a failure and this is not one.
        assert 'not a failure' in out

    def test_quiet_gives_the_code_and_nothing_else(self, tmp_path, capsys):
        a = self.write(tmp_path, 'a.json', {'x': 1})
        b = self.write(tmp_path, 'b.json', {'x': 2})

        assert main([str(a), str(b), '--quiet']) == 1
        assert capsys.readouterr().out == ''

    def test_a_missing_file_is_a_different_code_from_a_difference(self, tmp_path):
        """Exit 2, so a typo'd path cannot be read as a clean run or as drift."""
        a = self.write(tmp_path, 'a.json', {'x': 1})
        assert main([str(a), str(tmp_path / 'nope.json')]) == 2

    def test_any_window_lets_two_clips_be_compared(self, tmp_path):
        a = self.write(tmp_path, 'a.json', {
            'source': 'first.mp4', 'window': {'start_s': 0}, 'duration_s': 15.0,
            'quality': {'ball_seen_share': 0.8},
        })
        b = self.write(tmp_path, 'b.json', {
            'source': 'second.mp4', 'window': {'start_s': 600}, 'duration_s': 30.0,
            'quality': {'ball_seen_share': 0.8},
        })

        assert main([str(a), str(b)]) == 1
        assert main([str(a), str(b), '--any-window']) == 0
