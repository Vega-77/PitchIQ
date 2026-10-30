"""Score the top-down map against Map Lab answers.

    python -m pitchiq.experiments.maplab_score <set dir> <answers.json> [--all]

<answers.json> is the set's answer chunks as read from the lab's store: a list of
{"id": "c000", "data": {"a": {qid: answer}}} (or bare {"a": ...} chunks, or one merged
{qid: answer} map). Only sample == "random" questions count unless --all; "can't tell"
never counts against the map. Taps are kept in camera pixels, so the same answers score
a rebuilt map: rebuild the set's model answers and score again.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

_H = {}


def _homs(tag):
    if tag not in _H:
        from pitchiq.balltrack import match_video as MV

        z = np.load(Path(MV.HG) / f'{tag}_smooth2.npz')
        _H[tag] = (z['H'], int(z['abs'][0]))
    return _H[tag]


def sb_of(tag, f, x, y):
    """Camera pixel at frame f -> StatsBomb (120 x 80)."""
    Hs, a0 = _homs(tag)
    q = Hs[int(f) - a0] @ [x, y, 1.0]
    return q[:2] / q[2]


def metres(q, d_sb):
    kx, ky = q['sb_per_m']
    return float(np.hypot(d_sb[0] / kx, d_sb[1] / ky))


def load_answers(path: Path) -> dict:
    j = json.loads(path.read_text())
    out = {}
    if (
        isinstance(j, dict)
        and 'a' not in j
        and all(isinstance(v, dict) and 'v' in v for v in j.values())
    ):
        return j
    for c in j if isinstance(j, list) else [j]:
        out.update((c.get('data', c) or {}).get('a', {}))
    return out


def _med(xs):
    return round(float(np.median(xs)), 2) if xs else None


def score(qs, ans, all_samples=False):
    R = {}
    by = {}
    for q in qs:
        a = ans.get(q['id'])
        if a is None or (not all_samples and q['sample'] != 'random' and q['type'] != 'landing'):
            continue
        by.setdefault(q['type'], []).append((q, a))

    # landing: random = tracker air runs; 'ground' = fast ground runs (did it miss a flight?)
    rows = by.get('landing', [])
    air = [(q, a) for q, a in rows if q['sample'] == 'random']
    gnd = [(q, a) for q, a in rows if q['sample'] == 'ground']
    err_m, err_s = [], []
    for q, a in air:
        if a['v'] == 'landed' and a.get('taps') and q['model'].get('land'):
            t = a['taps'][0]
            p = sb_of(q['tag'], t['f'], t['x'], t['y'])
            err_m.append(metres(q, p - np.array(q['model']['land'])))
            if q['model'].get('end') is not None:
                err_s.append((t['f'] - q['model']['end']) / 30.0)
    va = Counter(a['v'] for _, a in air)
    vg = Counter(a['v'] for _, a in gnd)
    R['landing'] = dict(
        air_runs=len(air),
        answers=dict(va),
        landing_error_m_median=_med(err_m),
        within_3m=sum(e <= 3 for e in err_m),
        within_8m=sum(e <= 8 for e in err_m),
        n_err=len(err_m),
        landing_late_s_median=_med(err_s),
        false_flights=va['never'],
        ground_runs=len(gnd),
        missed_flights=vg['landed'] + vg['touched'] + vg['offcam'],
        ground_answers=dict(vg),
    )

    rows = by.get('possession', [])
    agree, n, conf = 0, 0, Counter()
    for q, a in rows:
        if a['v'] == 'cant':
            continue
        m = q['model']['word']
        m = 'loose' if m == 'unknown' else m
        conf[(a['v'], m)] += 1
        n += 1
        agree += a['v'] == m
    R['possession'] = dict(
        n=n,
        agree=agree,
        pct=round(100 * agree / n, 1) if n else None,
        truth_vs_model={'%s/%s' % k: v for k, v in sorted(conf.items())},
    )

    rows = by.get('feet', [])
    d, team_ok, team_n, roles = [], 0, 0, Counter()
    for q, a in rows:
        roles[a['v']] += 1
        if a['v'] in ('dark', 'light', 'hidden') and a.get('taps') and q['model'].get('dot_m'):
            t = a['taps'][0]
            p = sb_of(q['tag'], q['frame'], t['x'], t['y'])
            kx, ky = q['sb_per_m']
            d.append(
                float(
                    np.hypot(p[0] / kx - q['model']['dot_m'][0], p[1] / ky - q['model']['dot_m'][1])
                )
            )
        if a['v'] in ('dark', 'light'):
            team_n += 1
            team_ok += a['v'] == q['model']['word']
    R['feet'] = dict(
        n=len(rows),
        answers=dict(roles),
        dot_error_m_median=_med(d),
        within_1m=sum(x <= 1 for x in d),
        within_2m=sum(x <= 2 for x in d),
        n_err=len(d),
        team_agree=team_ok,
        team_n=team_n,
        not_players=roles['referee'] + roles['coach'] + roles['spectator'] + roles['nobody'],
    )

    for kind in ('missing', 'extra'):
        rows = by.get(kind, [])
        per = [len(a.get('taps', [])) for q, a in rows if a['v'] != 'cant']
        ks = Counter(t.get('k') for q, a in rows for t in a.get('taps', []))
        rings = sum(len(q['model']['ringed']) for q, a in rows if a['v'] != 'cant')
        R[kind] = dict(
            stills=len(per),
            taps=sum(per),
            per_still=round(float(np.mean(per)), 2) if per else None,
            stills_clean=sum(p == 0 for p in per),
            rings_shown=rings,
            by_kind=dict(ks),
        )

    rows = by.get('same', [])
    v = Counter(a['v'] for _, a in rows)
    n = sum(v.values()) - v['cant']
    R['same'] = dict(
        answers=dict(v), right_joins=v['same'], pct=round(100 * v['same'] / n, 1) if n else None
    )

    rows = by.get('keeper', [])
    hit, shown, tapped = 0, 0, 0
    for q, a in rows:
        taps = a.get('taps', []) if a['v'] == 'marked' else []
        tapped += len(taps)
        shown += len(q['model']['keepers'])
        for t in taps:
            for k in q['model']['keepers']:
                b = k['box']
                if abs(t['x'] - (b[0] + b[2]) / 2) < max(40, b[2] - b[0]) and abs(
                    t['y'] - b[3]
                ) < max(40, (b[3] - b[1]) / 2):
                    hit += 1
                    break
    R['keeper'] = dict(
        frames=len(rows),
        answers=dict(Counter(a['v'] for _, a in rows)),
        keepers_tapped=tapped,
        drawn_as_keeper=hit,
        keepers_drawn=shown,
    )

    rows = by.get('motion', [])
    v = Counter(a['v'] for _, a in rows)
    n = sum(v.values()) - v['cant']
    R['motion'] = dict(
        answers=dict(v),
        natural_pct=round(100 * v['natural'] / n, 1) if n else None,
        notes=[a['note'] for _, a in rows if a.get('note')],
    )

    rows = [(q, a) for q, a in by.get('trust', []) if a.get('rate')]
    R['trust'] = dict(
        n=len(rows),
        players=_med([a['rate']['players'] for _, a in rows]),
        ball=_med([a['rate']['ball'] for _, a in rows]),
        players_mean=round(float(np.mean([a['rate']['players'] for _, a in rows])), 2)
        if rows
        else None,
        ball_mean=round(float(np.mean([a['rate']['ball'] for _, a in rows])), 2) if rows else None,
    )

    stops = {}
    for q, a in by.get('stops', []):
        if a['v'] == 'cant':
            continue
        fps, step = q['fps'], q['step']
        halves = q['model']['halves']
        live = np.zeros(q['frames'], bool)
        for lo, hi in halves.values():
            live[lo:hi] = True
        # each marked break: from half a thumbnail before its first to half one after its last
        brk = [
            (
                max(0, int((r['a'] - 0.5) * step)),
                min(q['frames'], int((r['b'] + 0.5) * step)),
                r['kind'],
            )
            for r in a['ranges']
        ]
        counted = sum(int(live[lo:hi].sum()) for lo, hi, k in brk) / fps
        edges = sorted(e for lo, hi, _ in brk for e in (lo, hi))
        err = {}
        for name, (lo, hi) in halves.items():
            for side, e in (('start', lo), ('end', hi)):
                err['%s %s' % (name, side)] = (
                    round(min(abs(e - x) for x in edges) / fps, 1) if edges else None
                )
        stops[q['tag']] = dict(
            breaks=[
                dict(kind=k, start_s=round(lo / fps), end_s=round(hi / fps)) for lo, hi, k in brk
            ],
            break_s_counted_live=round(counted),
            edge_error_s=err,
        )
    R['stops'] = stops
    R['notes'] = {q['id']: a['note'] for t in by.values() for q, a in t if a.get('note')}
    return R


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.maplab_score', description=__doc__.split('\n\n')[0]
    )
    p.add_argument('set_dir', type=Path)
    p.add_argument('answers', type=Path)
    p.add_argument('--all', action='store_true', help='score the hard samples too')
    a = p.parse_args(argv)
    qs = json.loads((a.set_dir / 'questions.json').read_text())['questions']
    ans = load_answers(a.answers)
    print('%d questions, %d answered' % (len(qs), sum(q['id'] in ans for q in qs)), file=sys.stderr)
    print(json.dumps(score(qs, ans, a.all), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
