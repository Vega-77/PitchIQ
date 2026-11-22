"""Self-checks at the end of a run: signs, with no labels, that a game's stats are wrong.

Session step 3.8 (2026-11-22). The rule behind them is the user's: bad output gets flagged
without anybody watching. Each check returns a `Check`: `ok` is True, False (a flag) or None
(nothing to check against, for instance no logged score). None of them stops a run; the checks
that stop one come earlier (`checks.flight_flags`). `process_match` writes them into the report
(`self_checks`) and puts each flag at the top of its `warnings`, which the coach's match page
shows, and in run.log and notify.log.

- `goals`: the goals the stats found against the score the coach logged on the match page.
- `halves`: the two halves alike. Rates and shares that should not change much at half time.
- `possession`: possession spells of a believable length.
- `air`: the 3D ball's share of frames in the air, in the range football has.

The ranges are wide on purpose: they are for a run that went wrong, not for a game that was
odd. Where each comes from is written beside it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# One half's rate (touches, passes, spells a minute) or share (ball seen, a clear holder, the
# ball in the air) this many times the other's. Woodbridge's two halves differ by up to 1.29x
# (touches); the air share of 8 games' halves by up to 1.39x on dev pairs (eb). A half that saw
# the ball half as often, or lost its flights, is 2x.
HALF_X = 1.5
# Ours' possession share moving more than this between halves (points). Woodbridge: 2.8.
HALF_POSS_PP = 15.0
# Mean seconds of a possession spell. Professional games average about 15-20 s, and lower
# levels lose the ball sooner; under 5 s the stats split one team's spell each time the ball
# drops out of sight. Over 40 s a holder was kept through stoppages.
SPELL_S = (5.0, 40.0)
# Share of seen ball frames in the air. Under 3% the run has already stopped (flight_flags).
# The 8 games' halves are 5.9-19.1%; over 35% the camera height or flight units are wrong.
AIR_SHARE = (0.03, 0.35)
MIN_BALL = 5000  # fewer seen ball frames in a half and its air share is noise (as flight_flags)
MIN_MINUTES = 10.0  # a shorter half says nothing about rates


@dataclass(frozen=True)
class Check:
    key: str
    ok: bool | None
    text: str


def logged_score(entries: list[dict] | None) -> tuple[int, int] | None:
    """(ours, theirs) from the coach's match log (`shared/db.js::aggregateMatch` counts it the
    same way), or None when nothing was logged: an empty log is not a 0-0."""
    events = [e for e in entries or [] if e.get('kind') == 'event']
    if not events:
        return None
    goals = [e for e in events if e.get('type') == 'goal']
    them = sum(e.get('side') == 'them' for e in goals)
    return len(goals) - them, them


def goals(report: dict, score: tuple[int, int] | None, source: str = 'the coach’s log') -> Check:
    """The goals found (team_a is ours) against the logged score."""
    a, b = report['teams']['team_a'], report['teams']['team_b']
    found = (a.get('goals') or 0, b.get('goals') or 0)
    shots = (a.get('shots') or 0, b.get('shots') or 0)
    if score is None:
        return Check('goals', None, f'found {found[0]}-{found[1]}; no score to check against')
    if tuple(found) == tuple(score):
        return Check('goals', True, f'found {found[0]}-{found[1]}, as {source} says')
    text = f'found {found[0]}-{found[1]} goals, but {source} says {score[0]}-{score[1]}'
    short = [side for side, s, g in zip(('ours', 'theirs'), shots, score) if s < g]
    if short:
        text += f' ({" and ".join(short)}: fewer shots found than goals scored)'
    return Check('goals', False, text)


def _rates(h: dict) -> dict[str, float]:
    a, b, q = h['teams']['team_a'], h['teams']['team_b'], h.get('quality') or {}
    mins = h['duration_s'] / 60.0
    spells = sum((t.get('phase_of_play') or {}).get('total') or 0 for t in (a, b))
    out = {
        'touches a minute': (a.get('touches', 0) + b.get('touches', 0)) / mins,
        'passes a minute': (a.get('passes_attempted', 0) + b.get('passes_attempted', 0)) / mins,
        'possession spells a minute': spells / mins,
        'ball seen': q.get('ball_seen_share'),
        'a clear ball holder': q.get('clear_holder_share'),
    }
    return {k: v for k, v in out.items() if v}


def halves(
    h1: dict, h2: dict, air: tuple[float | None, float | None] = (None, None)
) -> list[Check]:
    """The halves alike: each rate or share within HALF_X of the other half's, and ours'
    possession within HALF_POSS_PP points."""
    if min(h['duration_s'] for h in (h1, h2)) < 60 * MIN_MINUTES:
        return [Check('halves', None, 'a half is too short to compare')]
    r1, r2 = _rates(h1), _rates(h2)
    if all(air):
        r1['the ball in the air'], r2['the ball in the air'] = air
    odd = []
    for k in r1.keys() & r2.keys():
        x = r2[k] / r1[k]
        if not 1 / HALF_X <= x <= HALF_X:
            odd.append(f'{k} {x:.2f}x the first half’s')
    p1 = 100 * h1['teams']['team_a']['possession_pct']
    p2 = 100 * h2['teams']['team_a']['possession_pct']
    if abs(p2 - p1) > HALF_POSS_PP:
        odd.append(f'our possession {p1:.0f}% then {p2:.0f}%')
    if odd:
        return [
            Check('halves', False, 'the second half is unlike the first: ' + '; '.join(sorted(odd)))
        ]
    return [Check('halves', True, f'alike within {HALF_X}x ({len(r1.keys() & r2.keys())} rates)')]


def possession(report: dict) -> Check:
    """Spells of a believable length: the time the ball was located over the spells cut."""
    a, b, q = report['teams']['team_a'], report['teams']['team_b'], report.get('quality') or {}
    spells = sum((t.get('phase_of_play') or {}).get('total') or 0 for t in (a, b))
    seen_s = report['duration_s'] - (q.get('no_ball_s') or 0)
    if not spells:
        return Check('possession', False, 'no possession spells at all')
    mean = seen_s / spells
    lo, hi = SPELL_S
    text = f'{spells} spells, {mean:.1f} s each on average'
    if mean < lo:
        return Check('possession', False, f'{text}: under {lo:.0f} s, the ball is lost too often '
                     'to follow a possession')  # fmt: skip
    if mean > hi:
        return Check('possession', False, f'{text}: over {hi:.0f} s, a holder was kept through '
                     'stoppages')  # fmt: skip
    return Check('possession', True, text)


def air_share(state) -> float | None:
    """The share of a half's seen ball frames in the air (`<tag>_ball3d2.npz` state), or None
    with too little ball to say."""
    import numpy as np

    state = np.asarray(state)
    air = int((state == 'air').sum())
    seen = air + int(np.isin(state, ('ground', 'unsure')).sum())
    return air / seen if seen >= MIN_BALL else None


def air(shares: dict[str, float | None]) -> Check:
    lo, hi = AIR_SHARE
    odd = [f'{t} {100 * s:.1f}%' for t, s in shares.items() if s is not None and not lo <= s <= hi]
    seen = ', '.join(f'{t} {100 * s:.1f}%' for t, s in shares.items() if s is not None)
    if not seen:
        return Check('air', None, 'too little ball to say')
    if odd:
        return Check('air', False, f'ball in the air {", ".join(odd)}, outside {100 * lo:.0f}-'
                     f'{100 * hi:.0f}%: the camera height or the flights are wrong')  # fmt: skip
    return Check('air', True, f'ball in the air {seen}')


def self_checks(
    report: dict,
    half_reports: list[dict],
    score: tuple[int, int] | None,
    air_by_half: dict[str, float | None],
    score_source: str = 'the coach’s log',
) -> list[Check]:
    out = [goals(report, score, score_source)]
    if len(half_reports) == 2:
        tags = list(air_by_half)
        pair = tuple(air_by_half.get(t) for t in tags[:2]) if len(tags) == 2 else (None, None)
        out += halves(*half_reports, air=pair)
    out += [possession(report), air(air_by_half)]
    return out


def flags(checks: list[Check]) -> list[str]:
    """The warnings line of every failed check."""
    return [f'self-check ({c.key}): {c.text}' for c in checks if c.ok is False]


def as_json(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
