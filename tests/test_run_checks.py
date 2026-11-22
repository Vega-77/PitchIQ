"""The self-checks at the end of a run (session step 3.8).

A finished report is checked against itself and against the coach's logged score; each flag
lands on top of the report's warnings, and none stops the run."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from pitchiq import run_checks as rc
from pitchiq.cli import process_match as pm


def team(goals=0, shots=0, touches=300, passes=170, spells=140, poss=0.5):
    return dict(goals=goals, shots=shots, touches=touches, passes_attempted=passes,
                possession_pct=poss, phase_of_play={'total': spells})  # fmt: skip


def half(minutes=45.0, seen=0.25, holder=0.11, no_ball_s=1500.0, a=None, b=None):
    return dict(duration_s=60 * minutes, teams=dict(team_a=a or team(), team_b=b or team()),
                quality=dict(ball_seen_share=seen, clear_holder_share=holder, no_ball_s=no_ball_s))  # fmt: skip


def goal(side):
    return dict(kind='event', type='goal', side=side)


def test_an_empty_log_is_no_score_not_nil_nil():
    assert rc.logged_score(None) is None
    assert rc.logged_score([]) is None
    assert rc.logged_score([dict(kind='sub')]) is None
    assert rc.logged_score([dict(kind='event', type='shot', side='us')]) == (0, 0)


def test_the_logged_score_counts_goals_by_side():
    log = [goal('us'), goal('them'), goal('them'), dict(kind='event', type='foul', side='us')]
    assert rc.logged_score(log) == (1, 2)


def test_goals_found_against_the_score():
    report = half(a=team(goals=1, shots=4), b=team(goals=0, shots=0))
    assert rc.goals(report, (1, 0)).ok is True
    assert rc.goals(report, None).ok is None
    c = rc.goals(report, (2, 3))
    assert c.ok is False
    assert '1-0' in c.text and '2-3' in c.text
    assert 'theirs: fewer shots found than goals' in c.text
    assert 'ours' not in c.text.split('(')[1]  # ours had 4 shots for 2 goals


def test_alike_halves_pass_and_odd_ones_flag():
    assert [c.ok for c in rc.halves(half(), half(seen=0.21))] == [True]
    c = rc.halves(half(), half(seen=0.12))[0]  # the second half saw the ball half as often
    assert c.ok is False and 'ball seen 0.48x' in c.text
    c = rc.halves(half(), half(a=team(poss=0.75), b=team(poss=0.25)))[0]
    assert c.ok is False and 'our possession 50% then 75%' in c.text
    c = rc.halves(half(), half(), air=(0.18, 0.02))[0]
    assert c.ok is False and 'the ball in the air' in c.text


def test_a_short_half_is_not_compared():
    assert rc.halves(half(minutes=5), half())[0].ok is None


def test_possession_spells_of_a_believable_length():
    # 2700 s, 1500 s of it without the ball: 1200 s over the spells
    assert rc.possession(half(a=team(spells=40), b=team(spells=40))).ok is True  # 15 s
    short = rc.possession(half())  # 280 spells: 4.3 s
    assert short.ok is False and 'lost too often' in short.text
    long = rc.possession(half(a=team(spells=10), b=team(spells=10)))  # 60 s
    assert long.ok is False and 'stoppages' in long.text
    assert rc.possession(half(a=team(spells=0), b=team(spells=0))).ok is False


def test_air_share_in_range():
    state = np.array(['air'] * 1000 + ['ground'] * 4000 + ['unsure'] * 1000 + ['none'] * 9000)
    assert rc.air_share(state) == pytest.approx(1 / 6)
    assert rc.air_share(state[:4000]) is None  # under MIN_BALL seen frames
    assert rc.air({'x1': 0.17, 'x2': 0.19}).ok is True
    assert rc.air({'x1': 0.17, 'x2': 0.5}).ok is False
    assert rc.air({'x1': None, 'x2': None}).ok is None


def test_flags_are_the_failed_checks_only():
    checks = rc.self_checks(half(a=team(spells=40), b=team(spells=40)), [half(), half()],
                            (2, 3), {'x1': 0.17, 'x2': 0.18})  # fmt: skip
    assert [c.key for c in checks] == ['goals', 'halves', 'possession', 'air']
    assert rc.flags(checks) == ['self-check (goals): ' + checks[0].text]


def test_the_score_argument():
    assert pm.final_score('2-3') == (2, 3)
    assert pm.final_score(' 10:0 ') == (10, 0)
    with pytest.raises(Exception):
        pm.final_score('two-three')
    assert pm.build_parser().parse_args(['v.mp4', '--game', 'xy', '--score', '1-1']).score == (1, 1)


def test_the_score_comes_from_the_argument_then_the_tag_log(tmp_path):
    log = tmp_path / 'log.json'
    log.write_text(json.dumps({'entries': [goal('us'), goal('them'), goal('us')]}))
    r = SimpleNamespace(a=SimpleNamespace(score=(4, 4), tag_log=str(log)), db=None)
    assert pm.score_of(r) == ((4, 4), 'the final score given')
    r.a.score = None
    assert pm.score_of(r) == ((2, 1), 'the coach’s log')
    r.a.tag_log = None
    assert pm.score_of(r) == (None, '')


def test_self_check_puts_flags_on_top_of_the_warnings(tmp_path, monkeypatch):
    full = tmp_path / 'g_report.json'
    report = half(a=team(spells=40), b=team(spells=40)) | dict(
        warnings=['old one'], trustworthy=True
    )
    full.write_text(json.dumps(report))
    for t in ('g1', 'g2'):
        (tmp_path / f'pipe_{t}.json').write_text(json.dumps(half()))
    said, told = [], []
    r = SimpleNamespace(a=SimpleNamespace(score=(1, 0), tag_log=None), db=None, out=tmp_path,
                        tags=('g1', 'g2'), say=said.append, tell=lambda *x: told.append(x))  # fmt: skip
    monkeypatch.setattr(pm, 'TRACK', tmp_path)  # no 3D ball here: air is not checked
    pm.self_check(r, full)
    out = json.loads(full.read_text())
    assert out['warnings'][0].startswith(
        'self-check (goals): found 0-0 goals, but the final score given says 1-0'
    )
    assert out['warnings'][1:] == ['old one']
    assert out['trustworthy'] is False
    assert [c['key'] for c in out['self_checks']] == ['goals', 'halves', 'possession', 'air']
    assert any('FLAG' in s for s in said) and told and told[0][0] == 'self-checks'
    pm.self_check(r, full)  # again on the same file: the flag is not doubled
    assert json.loads(full.read_text())['warnings'].count(out['warnings'][0]) == 1
