"""Score player identity against the Player ID Lab answers (Woodbridge, 2026-11-07).

The lab asked who each of the 20 longest players of 3 windows a half is, from a 12 s clip of
that identity. An answer is true only for that stretch: a long identity can hold someone else
before or after it. So every score here looks only at the clip stretch.

    python -m pitchiq.experiments.idscore            # wb1 (dev) only
    python -m pitchiq.experiments.idscore --final    # and wb2 (held out: read, never tuned on)

Reports:
  1. within a window, how often the linker's identities hold two people (lab "mixed", and two
     identities named as one person far apart at the same moment);
  2. across windows, what links today (nothing: ids restart each window);
  3. whether the linker's appearance vectors (detector features, colour descriptor) could link
     a clip to the same person in another window: nearest-neighbour hit rate against chance,
     and same-vs-other AUC, among clips of the same kit.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
LAB = REPO.parent / 'PitchIQ-data' / 'idlab'
MAP = REPO.parent / 'PitchIQ-data' / 'stats' / 'map'
CLASH = {'wb2_w01_p0000'}  # named 14afro (unsure) while 14afro's sure clip is 1500 px away


def _load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def truth(tags):
    """Questions with the answer that counts (wb1x re-asks win), people resolved through merges."""
    root = LAB / 'answers'
    ans = {
        s: {p.stem: _load(p) for p in (root / 'sets' / s / 'answers').glob('*.json')}
        for s in ('wb1', 'wb2', 'wb1x')
    }
    ppl = {p.stem: _load(p) for p in (root / 'games/wb/people').glob('*.json')}

    def resolve(pid):
        for _ in range(20):
            if not ppl.get(pid, {}).get('into'):
                return pid
            pid = ppl[pid]['into']
        return pid

    out = []
    for tag in tags:
        for q in _load(LAB / 'site/sets' / tag / 'questions.json')['questions']:
            a = ans['wb1x'].get(q['id']) or ans[tag].get(q['id'])
            if not a:
                continue
            pid = resolve(a['person']) if a.get('person') else None
            out.append(
                dict(
                    q,
                    v=a['v'],
                    person=pid,
                    name=ppl.get(pid, {}).get('label', ''),
                    sure=a.get('sure', True) is not False and q['id'] not in CLASH,
                )
            )
    return out


def _boxes(q, frames):
    """The identity's box at each frame, interpolated from its once-a-second samples."""
    s = np.asarray(q['samples'], float)
    return np.stack([np.interp(frames, s[:, 0], s[:, k]) for k in range(1, 5)], 1)


def _iou(a, b):
    x0, y0 = np.maximum(a[:, 0], b[0]), np.maximum(a[:, 1], b[1])
    x1, y1 = np.minimum(a[:, 2], b[2]), np.minimum(a[:, 3], b[3])
    i = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    u = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / np.maximum(u, 1e-9)


def appearance(tag, qs):
    """Mean detector feature and colour descriptor of each question over its 12 s clip."""
    cache = LAB / f'idscore_{tag}.npz'  # the 5 GB feature load takes 8 min; the means don't change
    if cache.exists():
        z = np.load(cache)
        got = {str(i): k for k, i in enumerate(z['id'])}
        if all(q['id'] in got for q in qs):
            for q in qs:
                k = got[q['id']]
                q['n_rows'] = int(z['n'][k])
                if q['n_rows']:
                    q['feat'], q['desc'] = z['feat'][k], z['desc'][k]
            return
    sys.path.insert(0, str(REPO))
    from pitchiq.experiments import player_lab

    det, _ = player_lab.load_detections(tag)
    by = det.rows_by_frame()
    for q in qs:
        f0, f1 = min(q['clip']['frames']), max(q['clip']['frames'])
        frames = np.arange(f0, f1 + 1, 3)
        frames = frames[(frames >= q['first']) & (frames <= q['last'])]
        rows = []
        for f, b in zip(frames, _boxes(q, frames)):
            r = by.get(int(f))
            if r is None or not len(r):
                continue
            iou = _iou(det.box[r], b)
            if iou.max() >= 0.5:
                rows.append(int(r[iou.argmax()]))
        rows = np.asarray(rows, int)
        q['n_rows'] = len(rows)
        if len(rows):
            v = det.feat[rows].astype(np.float64).mean(0)
            q['feat'] = v / (np.linalg.norm(v) or 1)
            q['desc'] = np.nanmedian(det.desc[rows], 0)
    del det
    nf = len(next(q['feat'] for q in qs if 'feat' in q))
    np.savez(
        cache,
        id=np.array([q['id'] for q in qs]),
        n=np.array([q['n_rows'] for q in qs]),
        feat=np.stack([q.get('feat', np.zeros(nf)) for q in qs]),
        desc=np.stack([q.get('desc', np.full(6, np.nan)) for q in qs]),
    )


def _auc(pos, neg):
    if not len(pos) or not len(neg):
        return float('nan')
    allv = np.concatenate([pos, neg])
    rank = allv.argsort().argsort() + 1.0
    return (rank[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def link_test(qs, key, label):
    """Nearest clip of the same kit in another window: same person? Against chance."""
    named = [q for q in qs if q['v'] == 'named' and q['sure'] and key in q]
    hits = chance = n = 0
    pos, neg = [], []
    for i, a in enumerate(named):
        cand = [
            b
            for b in named
            if b['kit'] == a['kit'] and (b['tag'], b['win']) != (a['tag'], a['win'])
        ]
        if not any(b['person'] == a['person'] for b in cand):
            continue  # nobody to find
        if key == 'feat':
            d = np.array([1 - a['feat'] @ b['feat'] for b in cand])
        else:
            # the colour descriptor has gaps (NaN): compare the parts both clips have
            sd = np.nanstd(np.stack([q[key] for q in named]), 0) + 1e-9
            d = np.array([np.sqrt(np.nanmean(((a[key] - b[key]) / sd) ** 2)) for b in cand])
            d = np.where(np.isfinite(d), d, np.inf)
        same = np.array([b['person'] == a['person'] for b in cand])
        n += 1
        hits += bool(same[d.argmin()])
        chance += same.mean()
        for j, b in enumerate(cand):
            if id(b) > id(a):
                (pos if same[j] else neg).append(-d[j])
    print(
        f'  {label:28} nearest is the same person {hits}/{n} ({100 * hits / max(n, 1):.0f}%), '
        f'chance {100 * chance / max(n, 1):.0f}%; same-vs-other AUC {_auc(np.array(pos), np.array(neg)):.2f}'
    )


def within(qs, tag):
    """Identities that hold two people: seen in the clip, or implied by a same-time clash."""
    t = [q for q in qs if q['tag'] == tag]
    mixed = sum(q['v'] == 'mixed' for q in t)
    by = defaultdict(list)
    for q in t:
        if q['v'] == 'named':
            by[(q['person'], q['win'])].append(q)
    held = set()
    for (_, _), group in by.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                lo, hi = max(a['first'], b['first']), min(a['last'], b['last'])
                # a hand-over can overlap a few frames; a second of it is two people
                if hi - lo > 30:
                    held |= {a['id'], b['id']}
    pairs = sum(len(g) > 1 for g in by.values())
    print(
        f'  {tag}: {len(t)} identities; {mixed} show two people inside the 12 s clip; '
        f'{len(held)} are alive while a same-person identity is elsewhere (so hold someone else '
        f'outside the clip); {pairs} person-windows have 2+ of the 20 longest identities'
    )


def fragments(tag):
    """Identities of 15 s+ per window in the whole-half map, against the ~22 players on the pitch."""
    p = MAP / f'{tag}.npz'
    if not p.exists():
        return
    z = np.load(p)
    fps = float(z['fps'])
    ok = (z['role'] == 0) & (z['team'] < 2) & z['seen']
    w = np.searchsorted(z['win_lo'], z['f'][ok], side='right') - 1
    c = Counter(zip(w.tolist(), z['trk'][ok].tolist()))
    per = Counter()
    for (wi, _), k in c.items():
        if k >= 15 * fps:
            per[wi] += 1
    allp = Counter(wi for (wi, _) in c)
    nw = len(z['win_lo'])
    print(
        f'  {tag}: {nw} windows of 3 min; per window {np.mean([allp[i] for i in range(nw)]):.0f} player '
        f'identities, {np.mean([per[i] for i in range(nw)]):.0f} of them seen 15 s+ (about 22 players on the pitch)'
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--final', action='store_true', help='also read wb2 (held out)')
    a = ap.parse_args(argv)
    tags = ['wb1', 'wb2'] if a.final else ['wb1']
    qs = truth(tags)
    print(
        'answers:',
        dict(Counter(q['v'] for q in qs)),
        '| unsure or clashing named:',
        sum(q['v'] == 'named' and not q['sure'] for q in qs),
    )
    print('\n1. within a window (the linker today)')
    for t in tags:
        within(qs, t)
        fragments(t)
    print('\n2. across windows today: ids restart every 3 min, so no clip links to any other.')
    people = Counter((q['tag'], q['person']) for q in qs if q['v'] == 'named')
    wins = defaultdict(set)
    for q in qs:
        if q['v'] == 'named':
            wins[(q['tag'], q['person'])].add(q['win'])
    for t in tags:
        ps = [k for k in people if k[0] == t]
        print(
            f'  {t}: {len(ps)} people named; {sum(len(wins[k]) > 1 for k in ps)} of them are seen in 2-3 windows, '
            f'each split into separate ids'
        )
    print(
        '\n3. could appearance link them? (clips of the same kit, other windows'
        + (', either half)' if a.final else ')')
    )
    for t in tags:
        appearance(t, [q for q in qs if q['tag'] == t])
    lost = [q['id'] for q in qs if q.get('n_rows', 0) < 10]
    if lost:
        print('  few detections matched:', lost)
    for t in tags:
        print(f' {t} only:')
        link_test([q for q in qs if q['tag'] == t], 'feat', 'detector features')
        link_test([q for q in qs if q['tag'] == t], 'desc', 'colour descriptor')
    if a.final:
        print(' both halves (wb2 held out):')
        link_test(qs, 'feat', 'detector features')
        link_test(qs, 'desc', 'colour descriptor')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
