"""TAPNext bridges on the replay's picks, only on the gaps that hold a clicked dev frame.

    PYTHONPATH=. python pitchiq/experiments/ballpush/tnclick.py jobs  <run> <variant>
    PYTHONPATH=. python pitchiq/experiments/ballpush/tnclick.py run   <run> <variant>   (GPU; -> ball-push/tn/<run>/<tag>.npy)
    PYTHONPATH=. python pitchiq/experiments/ballpush/tnclick.py score <run> <variant>

The shipped rule (evaluation/hybrid.py hyb90): a gap of 1..MAXGAP frames between two tracked picks (src 1), not
touching a frame the shipped 3D chain calls air, takes the blend of the forward and backward TAPNext tracks when
each lands within ANC px of the pick at its far end and they stay within AGR px of each other.  Rejected gaps keep
what the replay had.  Only gaps holding a clicked frame are run, so the dev score is exact and the run is short.
Reads the main checkout's tapnet clone, checkpoint and <tag>_ball3dd.npz; writes only under PitchIQ-data/ball-push.
"""

import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

MAIN = 'C:/Users/alexv/Desktop/Repos/PitchIQ/pitchiq/balltrack'
BP = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push'
VID = json.load(open(common.PIPE + '/ballfit/halfvideo.json'))
# one video per game: a held-out half reads its game's file (nb2 -> nb1's video, etc.)
for _h, _d in (('nb2', 'nb1'), ('pi1', 'pi2'), ('wh1', 'wh2'), ('co1', 'co2'), ('wb2', 'wb1')):
    VID.setdefault(_h, VID[_d])
if os.environ.get('PIQ_VIDEO'):  # a new game's halves (pitchiq/balltrack/ball17.py)
    for _h in os.environ.get('TN_TAGS', '').split(','):
        if _h:
            VID[_h] = [os.environ['PIQ_VIDEO']]
MAXGAP = int(os.environ.get('MAXGAP', 90))
ANC, AGR = 20.0, 24.0
# ALLGAPS=1: every gap of the half, not only those holding a click (the real pass; hours of GPU per half).
# TN_TAGS=a,b: only these halves.  EXPORT=<dir>: score also writes <dir>/<tag>.npz (abs, x, y, src, ring) in the
# p2d layout (src 1 tracker, 2 fill, 3 bridge; ring = after short-run and lost-stretch filters).
ALLGAPS = bool(os.environ.get('ALLGAPS'))
TN_TAGS = [t for t in os.environ.get('TN_TAGS', '').split(',') if t]
EXPORT = os.environ.get('EXPORT')
# PARTIAL=A:N: in a gap the rule rejects (both tracks exist), still ring the runs of >= N frames where the two
# tracks are within A px of each other (their midpoint).  Off by default.
# TN_SET=held: the held-out halves; RC=<replay_cache dir> and BPNAME=<name> (bp_<name>_<tag>.npz) point at their replay.
TAGS = common.HELD if os.environ.get('TN_SET') == 'held' else common.DEV
RC = os.environ.get('RC')
BPNAME = os.environ.get('BPNAME')
ENDP = (
    float(os.environ['ENDP']) if os.environ.get('ENDP') else None
)  # bridge only if both end frames' best score >= this
PARTIAL = (
    [float(v) for v in os.environ['PARTIAL'].split(':')] if os.environ.get('PARTIAL') else None
)


def air_mask(tag, ab):
    p = os.path.join(MAIN, 'pitch_registration', '%s_ball3dd.npz' % tag)
    if not os.path.exists(p):
        return np.zeros(len(ab), bool), False
    z = np.load(p)
    m = np.zeros(len(ab), bool)
    i = z['abs'] - ab[0]
    k = (i >= 0) & (i < len(ab))
    m[i[k]] = (z['state'] == 'air')[k]
    return m, True


def load(run, nm):
    common.point_at_data()
    from pitchiq.experiments.clicker import clickscore as cs

    items = {
        it['id']: it
        for it in json.load(open(os.path.join(common.CLICKS, 'items_all.json')))['items']
    }
    A = cs.answers(os.path.join(common.CLICKS, 'db'))
    out = {}
    for tag in TAGS:
        if TN_TAGS and tag not in TN_TAGS:
            continue
        f = glob.glob(
            '%s/*_%s_%s.npz'
            % (RC or '%s/trk2/%s/replay_cache' % (BP, run), nm.replace(':', '_'), tag)
        )
        f = [p for p in f if not os.path.basename(p).startswith('bp_')]
        if not f:
            continue
        z = dict(np.load(f[0]))
        want = [
            (i, a, int(items[i]['f']))
            for i, a in A.items()
            if items.get(i, {}).get('half') == tag and a['v'] in ('ball', 'out')
        ]
        out[tag] = (z, want)
    return out, cs


def gaps(z, want, tag):
    """[(i, j)] index pairs of tracked picks whose gap holds a clicked frame; clicks in air gaps; has ball3dd."""
    lo, S, X = int(z['lo']), z['src'], z['x']
    n = len(S)
    anc = np.flatnonzero((S == 1) & np.isfinite(X))
    air, has = air_mask(tag, lo + np.arange(n))
    ci = sorted({f - lo for _, _, f in want if 0 <= f - lo < n})
    res, nair = [], 0
    if ALLGAPS:
        for i, j in zip(anc[:-1].tolist(), anc[1:].tolist()):
            if 1 <= j - i - 1 <= MAXGAP and not air[i : j + 1].any():
                res.append((i, j))
        return res, nair, has
    for c in ci:
        k = np.searchsorted(anc, c)
        if k == 0 or k >= len(anc) or anc[k] == c:
            continue
        i, j = int(anc[k - 1]), int(anc[k])
        if not 1 <= j - i - 1 <= MAXGAP:
            continue
        if air[i : j + 1].any():
            nair += 1
            continue
        if (i, j) not in res:
            res.append((i, j))
    return res, nair, has


def outdir(run, nm):
    from pitchiq.balltrack.tapnext_bridge import WINDOW

    return os.path.join(
        os.environ.get('TN_ROOT') or os.path.join(BP, 'tn'),
        run
        + '_'
        + nm.replace(':', '_').replace('+', '_')
        + ('' if MAXGAP == 90 else '_g%d' % MAXGAP)
        + ('' if WINDOW == 384 else '_s%d' % WINDOW)  # TAPNext's window: 384 px until 2026-11-24
        + ('_all' if ALLGAPS else ''),
    )


def cmd_jobs(run, nm):
    D, _ = load(run, nm)
    tot = 0
    for tag, (z, want) in D.items():
        g, nair, has = gaps(z, want, tag)
        fr = sum(j - i + 1 for i, j in g)
        tot += fr
        print(
            '%-8s src codes %s  gaps with a click %3d  frames %5d  clicks in air gaps %d  ball3dd %s  video %s'
            % (
                tag,
                np.unique(z['src']).tolist(),
                len(g),
                fr,
                nair,
                has,
                os.path.exists(VID.get(tag, ['?'])[0]),
            )
        )
    print(
        'track-frames (both directions) about %d -> %.0f min at 171 ms'
        % (2 * tot, 2 * tot * 0.171 / 60)
    )


def cmd_run(run, nm):
    import cv2
    import torch

    sys.path.insert(0, os.path.join(MAIN, 'tapnext_bridge', 'tapnet'))
    from tapnet.tapnextpp.votsp2026.model import TAPNextPP
    from pitchiq import extras

    extras.tapnext_missing = lambda: []
    from pitchiq.balltrack.tapnext_bridge import tn_engine as E

    net = TAPNextPP.from_checkpoint(
        os.path.join(MAIN, 'tapnext_bridge', 'tapnextpp_512.ckpt'),
        device='cuda',
        input_resolution=E.S,
    )._model
    D, _ = load(run, nm)
    od = outdir(run, nm)
    os.makedirs(od, exist_ok=True)
    for tag, (z, want) in D.items():
        op = os.path.join(od, '%s.npy' % tag)
        if os.path.exists(op):
            continue
        lo, X, Y = int(z['lo']), z['x'], z['y']
        cap = cv2.VideoCapture(VID[tag][0])
        res = []
        for i, j in sorted(gaps(z, want, tag)[0]):
            a, b = lo + i, lo + j
            fr = E.read_range(cap, a, b, set(range(a, b + 1)))
            tr = {}
            for d, order, seed in (
                (1, range(a, b + 1), (X[i], Y[i])),
                (-1, range(b, a - 1, -1), (X[j], Y[j])),
            ):
                if d < 0 and np.hypot(*(tr[1][-1] - (X[j], Y[j]))) > ANC:
                    break
                G = E.Group(net, [seed])
                with torch.amp.autocast('cuda', dtype=torch.float16):
                    xy = np.array([G.step([fr[f]])[0][0] for f in order])
                tr[d] = xy
                res.append((a, b, d, xy))
            print(
                '%s  gap %d..%d  %s' % (tag, a, b, 'both' if -1 in tr else 'fwd missed'), flush=True
            )
        np.save(op, np.array(res, dtype=object), allow_pickle=True)
    print('TN RUN DONE', flush=True)


def cmd_back(run, nm):
    """Backward tracks for the gaps whose forward track missed (cmd_run skips them) -> <tag>_back.npy; for PARTIAL."""
    import cv2
    import torch

    sys.path.insert(0, os.path.join(MAIN, 'tapnext_bridge', 'tapnet'))
    from tapnet.tapnextpp.votsp2026.model import TAPNextPP
    from pitchiq import extras

    extras.tapnext_missing = lambda: []
    from pitchiq.balltrack.tapnext_bridge import tn_engine as E

    net = TAPNextPP.from_checkpoint(
        os.path.join(MAIN, 'tapnext_bridge', 'tapnextpp_512.ckpt'),
        device='cuda',
        input_resolution=E.S,
    )._model
    D, _ = load(run, nm)
    od = outdir(run, nm)
    for tag, (z, want) in D.items():
        ip, op = os.path.join(od, '%s.npy' % tag), os.path.join(od, '%s_back.npy' % tag)
        if os.path.exists(op) or not os.path.exists(ip):
            continue
        lo, X, Y = int(z['lo']), z['x'], z['y']
        have = {(int(a), int(b), int(d)) for a, b, d, _ in np.load(ip, allow_pickle=True)}
        cap = cv2.VideoCapture(VID[tag][0])
        res = []
        for a, b, d in sorted(have):
            if d != 1 or (a, b, -1) in have:
                continue
            fr = E.read_range(cap, a, b, set(range(a, b + 1)))
            G = E.Group(net, [(X[b - lo], Y[b - lo])])
            with torch.amp.autocast('cuda', dtype=torch.float16):
                xy = np.array([G.step([fr[f]])[0][0] for f in range(b, a - 1, -1)])
            res.append((a, b, -1, xy))
            print('%s  gap %d..%d  back' % (tag, a, b), flush=True)
        np.save(op, np.array(res + [(0, 0, 0, np.zeros((1, 2)))], dtype=object), allow_pickle=True)
    print('TN BACK DONE', flush=True)


def cmd_score(run, nm):
    from pitchiq.balltrack import ballgate

    if os.environ.get(
        'RP_LOSTP'
    ):  # same override as replay.py; without it the score re-gated at the shipped 1.5
        ballgate.LOST_P = float(os.environ['RP_LOSTP'])
    print('gate bar %.2f' % ballgate.LOST_P, flush=True)
    D, cs = load(run, nm)
    od = outdir(run, nm)
    T = {k: dict(right=0, wrong_ball=0, missed=0, out_ring=0) for k in ('before', 'after')}
    st = dict(gaps=0, acc=0, land=0, agree=0, part=0, endp=0)
    for tag, (z, want) in D.items():
        lo, n = int(z['lo']), len(z['src'])
        b = np.load(
            '%s/bp_%s_%s.npz'
            % (RC or '%s/trk2/%s/replay_cache' % (BP, run), BPNAME or run + '_global', tag)
        )
        bp = dict(zip(b['f'].tolist(), b['p'].tolist()))
        ballgate.best_p = lambda t, bp=bp: bp
        X, Y, S, CT = z['x'].copy(), z['y'].copy(), z['src'].copy(), z['cont'].astype(bool)
        ver = {'before': (X.copy(), Y.copy(), S.copy())}
        tr = {}
        p = os.path.join(od, '%s.npy' % tag)
        for a, bb, d, xy in np.load(p, allow_pickle=True) if os.path.exists(p) else []:
            tr[(int(a), int(bb), int(d))] = np.asarray(xy, float)
        p = os.path.join(od, '%s_back.npy' % tag)
        if PARTIAL and os.environ.get('BACK') and os.path.exists(p):
            for a, bb, d, xy in np.load(p, allow_pickle=True):
                if d:
                    tr[(int(a), int(bb), int(d))] = np.asarray(xy, float)
        for i, j in gaps(z, want, tag)[0]:
            f, g = tr.get((lo + i, lo + j, 1)), tr.get((lo + i, lo + j, -1))
            if f is None:
                continue
            st['gaps'] += 1
            why = None
            if ENDP is not None and min(bp.get(lo + i, -99.0), bp.get(lo + j, -99.0)) < ENDP:
                why = 'endp'
            elif (
                g is None
                or np.hypot(*(g[-1] - (X[i], Y[i]))) > ANC
                or np.hypot(*(f[-1] - (X[j], Y[j]))) > ANC
            ):
                why = 'land'
            elif np.hypot(*(f[1:-1] - g[::-1][1:-1]).T).max() > AGR:
                why = 'agree'
            if why:
                st[why] += 1
                if PARTIAL and g is not None:
                    gr = g[::-1]
                    ok = np.hypot(*(f - gr).T) <= PARTIAL[0]
                    ok[0] = ok[-1] = False
                    u = 1
                    while u < len(ok) - 1:
                        if not ok[u]:
                            u += 1
                            continue
                        v = u
                        while ok[v + 1]:
                            v += 1
                        if v - u + 1 >= PARTIAL[1]:
                            q = (f[u : v + 1] + gr[u : v + 1]) / 2
                            X[i + u : i + v + 1], Y[i + u : i + v + 1], S[i + u : i + v + 1] = (
                                q[:, 0],
                                q[:, 1],
                                3,
                            )
                            st['part'] += v - u + 1
                        u = v + 1
                continue
            g = g[::-1]
            L = j - i - 1
            w = np.arange(1, L + 1) / (L + 1.0)
            q = (1 - w)[:, None] * f[1:-1] + w[:, None] * g[1:-1]
            X[i + 1 : j], Y[i + 1 : j], S[i + 1 : j] = q[:, 0], q[:, 1], 3
            st['acc'] += 1
        ver['after'] = (X, Y, S)
        on0 = (ver['before'][2] > 0) & np.isfinite(ver['before'][0])
        g0 = ballgate.lost(tag, lo, on0)
        for k, (x, y, s) in ver.items():
            on = (s > 0) & np.isfinite(x)
            # the gate comes from the replay's own picks for both versions; bridged frames are not gated (f6b0c45)
            flash = ballgate.short_runs(on | CT) | (g0 & (s != 3))
            if EXPORT and k == 'after':
                os.makedirs(EXPORT, exist_ok=True)
                np.savez(
                    os.path.join(EXPORT, '%s.npz' % tag),
                    abs=lo + np.arange(n),
                    x=np.where(on, x, np.nan),
                    y=np.where(on, y, np.nan),
                    src=np.where(on, s, 0).astype(np.int8),
                    ring=on & ~flash,
                )
            for iid, a, fr in want:
                i = fr - lo
                ring = bool(0 <= i < n and on[i] and not flash[i])
                chk = cs.judge(a, (x[i], y[i]) if ring else None, 'ground' if ring else '')
                c = T[k]
                if a['v'] == 'ball':
                    c['right'] += chk[1]
                    c['wrong_ball'] += ring and not chk[1]
                    c['missed'] += not ring
                else:
                    c['out_ring'] += ring
    print(
        'gaps run %(gaps)d  accepted %(acc)d  rejected: landing %(land)d  disagree %(agree)d  weak ends %(endp)d  partial frames ringed %(part)d'
        % st
    )
    for k, c in T.items():
        print(
            '%-7s right %3d  wrong on ball %3d  missed %3d  | out rings %2d  | wrong total %3d'
            % (
                k,
                c['right'],
                c['wrong_ball'],
                c['missed'],
                c['out_ring'],
                c['wrong_ball'] + c['out_ring'],
            )
        )


if __name__ == '__main__':
    dict(jobs=cmd_jobs, run=cmd_run, back=cmd_back, score=cmd_score)[sys.argv[1]](
        sys.argv[2], sys.argv[3]
    )
