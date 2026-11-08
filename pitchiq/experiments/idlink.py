"""Whole-half player identity: join the linker's fragments into people (Woodbridge, 2026-11-07).

The linker starts afresh every window and splits a player whenever it loses them, so a half
is ~7000 fragments for ~25 people. Joining them uses what stays true of a person:

- their look (detector features);
- their place in the team's shape (a right back stays deep on the right);
- their shirt number, as a soft belief over 0-99 (a 25/26 near miss still points at them);
- where they were last seen, only to rule out moves no one could make.

    python -m pitchiq.experiments.idlink frags <tag>     # every fragment: positions, look
    python -m pitchiq.experiments.idlink numbers <tag>   # number belief per fragment
    python -m pitchiq.experiments.idlink parts <tag>     # body-part colours (opt-in cue)
    python -m pitchiq.experiments.idlink join <tag>      # people; scored on Player ID Lab names

Scored on the Player ID Lab answers: the named clips are fragments of this same linker run
(same windows, same deterministic linker), so each named clip is a (window, pid) here. Two
named clips of one person should end in one person; of two people, never (a wrong join
mixes two players' stats for the rest of the game, so it counts worse than a missed one).
Settings were chosen on wb1 only. Held out halves (nb2, pi1, wh1, co1, wb2) are refused
without --final.

wb1 (dev), pairs of named clips, same person joined / two people joined:
    look + place, after a last-seen stitch pass     17/85   8/699
    look + place + number (w 0.5) + move veto      33/85   6/699
wb2 (held out, scored once 2026-11-07, not tuned on):
    look + place                                   14/66  12/571
    look + place + number                          29/66   6/571
    look + place + number + move veto (chosen)     25/66   9/571
The number transfers (joins double, fewer wrong); the veto's wb1 gain (11 -> 6 wrong) did not.
Dead ends on wb1: stitching by last-seen place first (chains of wrong hand-offs block later
joins), last seen as a bonus (33 -> 23), seeding people from sure number reads then attaching
(22/85 at 51 wrong: too few seeds force pieces into the wrong person), holding back a join
while either side has a near-equal rival that overlaps the partner in time (dropped: 0 wrong
but 9/85; retried later: 27-34/85 at 7-10 wrong, sure-number purity no better). Wrong people
are mixtures of two players taking turns on camera; number swaps sit inside the linker's raw
tracklets (0/51 at its joins), so splitting at linker joins (`units`) does not separate them.

Body parts (hair, face, arms and bands, knees, socks, sock length, cleats; `parts`, opt-in):
on sure-number sightings they pick the right player 58% alone vs 70% for the detector look,
but place + look + parts 85% vs 78%. As one more joiner cue on wb1 (a sightings' parts
averaged into one distance, weight 1):
    numbers visible, lab pairs                33/85  6/699  ->  35/85 10/699
    sure numbers hidden, lab pairs            23/85  8/699  ->  33/85 12/699
    sure numbers hidden, sure-number pairs    31% 1.4%      ->  35% 1.5%
More joins where numbers are missing, but the new wrong ones merge players sharing a colour
(#8 and #5, both pink); as a veto only (cost above a floor, x2-x5) no better. Off by default.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DATA = REPO.parent / 'PitchIQ-data'
OUT = DATA / 'idlink'
MAP = DATA / 'stats' / 'map'
HG = REPO / 'pitchiq' / 'balltrack' / 'h3d_hyb90'
HELD_OUT = {'nb2', 'pi1', 'wh1', 'co1', 'wb2'}
ROLE = {'player': 0, 'keeper': 1, 'referee': 2, 'off': 3}
SAMPLE = 3  # frames between kept positions (10 per second)


def _guard(tag, final):
    if tag in HELD_OUT and not final:
        raise SystemExit(f'{tag} is held out: scored once, at the end (--final)')


def _video(tag):
    from pitchiq.experiments.wholemap import video_of

    v = video_of(tag)
    if v:
        os.environ['PIQ_VIDEO'] = v  # before player_lab is imported: unset it reads the scrimmage
    sys.path.insert(0, str(REPO))


def frags(tag, final=False):
    """Every fragment of the half: team (the half's 0/1 after the map's swap, 2 unknown), role,
    span, mean look, and its box and pitch position 10 times a second."""
    _guard(tag, final)
    _video(tag)
    from pitchiq.experiments import player_lab
    from pitchiq.experiments.spotlab import _subset, linked

    m = np.load(MAP / f'{tag}.npz')
    wl, wh, swap = m['win_lo'], m['win_hi'], m['swap']
    L, W = float(m['length_m']), float(m['width_m'])
    sm = np.load(HG / f'{tag}_smooth2.npz')
    Hs, a0 = sm['H'], int(sm['abs'][0])
    D = np.diag([L / 120, W / 80, 1.0])
    det, meta = player_lab.load_detections(tag)
    spans = [(int(a), int(b)) for a, b in zip(wl, wh)]
    F = {
        k: []
        for k in ('win', 'pid', 'team', 'role', 'first', 'last', 'n_seen', 'feat', 'desc', 'off')
    }
    S = {k: [] for k in ('f', 'box', 'x', 'y')}
    # the linker's pieces inside each fragment: span, look, and the join that started it
    B = {k: [] for k in ('frag', 'first', 'last', 'n', 'feat', 'stage', 'cost')}
    n = 0
    for w, ((lo, hi), res) in enumerate(zip(spans, linked(tag, det, meta, spans))):
        sub = _subset(det, (det.frame >= lo) & (det.frame < hi))
        by_start = {int(j[2]): (str(j[0]), float(j[3])) for j in res.joins}
        for ident in res.identities:
            ok = ~ident.interp
            if ok.sum() < 3:
                continue
            team = {'team_a': 0, 'team_b': 1}.get(ident.team, 2)
            if team < 2 and swap[w]:
                team = 1 - team
            rows = ident.rows[ok]
            v = sub.feat[rows].astype(np.float64).mean(0)
            idx = np.where(ok)[0]
            idx = np.unique(np.r_[idx[::SAMPLE], idx[-1]])
            fr, bx = ident.frames[idx].astype(int), ident.boxes[idx].astype(np.float32)
            k = np.clip(fr - a0, 0, len(Hs) - 1)
            feet = np.stack([(bx[:, 0] + bx[:, 2]) / 2, bx[:, 3], np.ones(len(bx))], 1)
            g = np.einsum('ij,njk,nk->ni', D, Hs[k], feet)
            xy = g[:, :2] / g[:, 2:3]
            xy[(fr - a0 < 0) | (fr - a0 >= len(Hs))] = np.nan
            F['win'].append(w)
            F['pid'].append(int(ident.pid))
            F['team'].append(team)
            F['role'].append(ROLE.get(ident.role, 3))
            F['first'].append(int(ident.frames[ok][0]))
            F['last'].append(int(ident.frames[ok][-1]))
            F['n_seen'].append(int(ok.sum()))
            F['feat'].append(v / (np.linalg.norm(v) or 1))
            F['desc'].append(np.nanmedian(sub.desc[rows], 0))
            fo = ident.frames[ok]
            cuts = list(ident.piece_first[1:]) + [int(fo[-1]) + 1]
            f_lo = int(fo[0])
            for k, (pc, f_hi) in enumerate(zip(ident.pieces, cuts)):
                sel = (fo >= f_lo) & (fo < f_hi)
                if sel.any():
                    v = sub.feat[rows[sel]].astype(np.float64).mean(0)
                    st, c = by_start.get(int(pc), ('?', np.nan)) if k else ('', np.nan)
                    B['frag'].append(len(F['win']) - 1)
                    B['first'].append(int(fo[sel][0]))
                    B['last'].append(int(fo[sel][-1]))
                    B['n'].append(int(sel.sum()))
                    B['feat'].append((v / (np.linalg.norm(v) or 1)).astype(np.float16))
                    B['stage'].append(st)
                    B['cost'].append(c)
                f_lo = f_hi
            F['off'].append(n)
            S['f'].append(fr)
            S['box'].append(bx)
            S['x'].append(xy[:, 0].astype(np.float32))
            S['y'].append(xy[:, 1].astype(np.float32))
            n += len(fr)
        print(f'window {w} {lo}..{hi}: {len(res.identities)} identities', flush=True)
    del det
    F['off'].append(n)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT / f'frags_{tag}.npz',
        **{k: np.asarray(v, np.float32 if k == 'feat' else None) for k, v in F.items()},
        **{f's_{k}': np.concatenate(v) for k, v in S.items()},
        **{f'b_{k}': np.asarray(v) for k, v in B.items()},
        fps=float(m['fps']),
        length_m=L,
        width_m=W,
        win_lo=wl,
        win_hi=wh,
    )
    print(f'{len(F["win"])} fragments, {n} samples -> {OUT / f"frags_{tag}.npz"}', flush=True)


def load_frags(tag):
    z = np.load(OUT / f'frags_{tag}.npz')
    return {k: z[k] for k in z.files}


def numbers(tag, final=False, every=3, min_h=35.0, tuned=None, batch=256):
    """The reader's legibility and digit probabilities on fragment crops: every `every`-th kept
    position (~3 a second) where the player is at least `min_h` px tall (smaller backs are not
    readable here). Read as the video streams past, so no crops are stored.
    `tuned`: a numtrain.py model; never one trained on this half's own taps."""
    _guard(tag, final)
    _video(tag)
    import cv2
    import torch

    from pitchiq.experiments import player_lab
    from pitchiq.experiments.numread import PAD_X, PAD_Y, torso
    from pitchiq.experiments.parseq_lite import Reader

    sd = None
    if tuned:
        if tag in Path(tuned).stem:
            raise SystemExit(f'{tuned} was trained on {tag}')
        sd = torch.load(tuned, map_location='cpu', weights_only=True)
    r = Reader(tuned=sd)
    F = load_frags(tag)
    jobs = {}
    off = F['off']
    for i in range(len(F['win'])):
        if F['team'][i] > 1 or F['role'][i] > 1:
            continue
        for k in range(off[i], off[i + 1], every):
            b = F['s_box'][k]
            if b[3] - b[1] >= min_h:
                jobs.setdefault(int(F['s_f'][k]), []).append((i, b))
    print(f'{sum(len(v) for v in jobs.values())} crops on {len(jobs)} frames', flush=True)
    out = {k: [] for k in ('frag', 'frame', 'h', 'leg', 'dist')}
    pend = []

    def flush():
        if not pend:
            return
        ims = [c for _, _, _, c in pend]
        out['leg'].append(r.legibility(ims))
        out['dist'].append(np.stack([d for _, _, d in r.digits([torso(c, None) for c in ims])]))
        for i, f, h, _ in pend:
            out['frag'].append(i)
            out['frame'].append(f)
            out['h'].append(h)
        pend.clear()

    cap = cv2.VideoCapture(player_lab.VIDEO)
    fs = sorted(jobs)
    cap.set(cv2.CAP_PROP_POS_FRAMES, fs[0])
    want = set(fs)
    for f in range(fs[0], fs[-1] + 1):
        if f not in want:
            if not cap.grab():
                break
            continue
        ok, img = cap.read()
        if not ok:
            break
        H, W = img.shape[:2]
        for i, b in jobs[f]:
            w, h = b[2] - b[0], b[3] - b[1]
            x0, x1 = int(max(0, b[0] - PAD_X * w)), int(min(W, b[2] + PAD_X * w))
            y0, y1 = int(max(0, b[1] - PAD_Y * h)), int(min(H, b[3] + PAD_Y * h))
            if x1 - x0 < 8 or y1 - y0 < 16:
                continue
            pend.append((i, f, float(h), img[y0:y1, x0:x1, ::-1].copy()))
        if len(pend) >= batch:
            flush()
        if f % 9000 == 0:
            print(f'  frame {f} / {fs[-1]}', flush=True)
    flush()
    name = f'numbers_{tag}' + ('_tuned' if tuned else '')
    np.savez(
        OUT / f'{name}.npz',
        frag=np.asarray(out['frag'], np.int32),
        frame=np.asarray(out['frame'], np.int32),
        h=np.asarray(out['h'], np.float32),
        leg=np.concatenate(out['leg']).astype(np.float32),
        dist=np.concatenate(out['dist']).astype(np.float32),
    )
    print(f'{len(out["frag"])} reads -> {OUT / f"{name}.npz"}', flush=True)


# ---------------------------------------------------------------- joining
MIN_SEEN = 10  # fragments seen in fewer frames (a third of a second) are left alone
NUM_TL, NUM_FLOOR = 0.5, 0.02  # as idscore.numbers
PARTS = ('hair', 'face', 'arms', 'knees', 'socks', 'cleats')  # Lab colour each, then sock length
PART_SLOTS = [slice(3 * k, 3 * k + 3) for k in range(len(PARTS))] + [slice(18, 19)]
PART_DIM = 19


def team_shape(tag, F):
    """Each team's outfield centroid per frame (from the whole-half map) and the sign that
    turns positions to 'facing the way the team attacks' (as idscore.position)."""
    z = np.load(MAP / f'{tag}.npz')
    f, x, y, t, r, s = (z[k] for k in ('f', 'x', 'y', 'team', 'role', 'seen'))
    n = int(f.max()) + 1
    C = np.full((n, 2, 2), np.nan)
    for tm in (0, 1):
        m = (t == tm) & (r == 0) & s
        cnt = np.bincount(f[m], minlength=n)
        sx = np.bincount(f[m], x[m], minlength=n)
        sy = np.bincount(f[m], y[m], minlength=n)
        ok = cnt >= 6
        C[ok, tm, 0], C[ok, tm, 1] = sx[ok] / cnt[ok], sy[ok] / cnt[ok]
    end = {}
    for tm in (0, 1):
        k = (r == 1) & (t == tm) & s
        end[tm] = (x[k].mean() if k.sum() > 300 else np.nan, x[(t == tm) & (r == 0) & s].mean())
    use = 0 if all(np.isfinite(end[q][0]) for q in (0, 1)) else 1
    sign = {q: (-1.0 if end[q][use] > end[1 - q][use] else 1.0) for q in (0, 1)}
    return C, sign


def _belief(P, w):
    p = (w[:, None] * P).sum(0) / w.sum() if w.sum() > 0 else np.full(100, 0.01)
    return (1 - NUM_FLOOR) * p + NUM_FLOOR / 100


def _crop_beliefs(dist):
    D = dist.astype(np.float64)
    n = len(D)
    one = D[:, 0, 1:11] * D[:, 1, :1]
    two = (D[:, 0, 1:11, None] * D[:, 1, None, 1:11] * D[:, 2, 0, None, None]).reshape(n, 100)
    P = np.concatenate([one, two[:, 10:]], 1)
    return P / (P.sum(1, keepdims=True) + 1e-12)


def units(F, split):
    """Cut fragments at the linker joins `split(stage, cost)` calls doubtful: each run of pieces
    between cuts becomes a unit with its own span, look and samples (fields as a fragment,
    plus `frag`, the fragment it came from)."""
    nf = len(F['win'])
    pieces = {}
    for k in range(len(F['b_frag'])):
        pieces.setdefault(int(F['b_frag'][k]), []).append(k)
    U = {k: [] for k in ('frag', 'win', 'pid', 'team', 'role', 'first', 'last', 'n_seen', 'feat')}
    S = {k: [] for k in ('f', 'box', 'x', 'y')}
    off, n = [], 0
    for i in range(nf):
        ks = sorted(pieces.get(i, []), key=lambda k: F['b_first'][k])
        runs, cur = [], []
        for k in ks:
            if cur and split(str(F['b_stage'][k]), float(F['b_cost'][k])):
                runs.append(cur)
                cur = []
            cur.append(k)
        if cur:
            runs.append(cur)
        if not runs:  # no piece table: the fragment whole
            runs = [None]
        sl = slice(F['off'][i], F['off'][i + 1])
        sf = F['s_f'][sl]
        for r in runs:
            if r is None:
                lo, hi, ns, v = F['first'][i], F['last'][i], F['n_seen'][i], F['feat'][i]
            else:
                lo, hi = F['b_first'][r[0]], F['b_last'][r[-1]]
                wt = F['b_n'][r].astype(np.float64)
                ns = int(wt.sum())
                v = (F['b_feat'][r].astype(np.float64) * wt[:, None]).sum(0)
                v = v / (np.linalg.norm(v) or 1)
            m = (sf >= lo) & (sf <= hi)
            for k, key in (('frag', i), ('first', lo), ('last', hi), ('n_seen', ns), ('feat', v)):
                U[k].append(key)
            for k in ('win', 'pid', 'team', 'role'):
                U[k].append(F[k][i])
            off.append(n)
            for k in S:
                S[k].append(F[f's_{k}'][sl][m])
            n += int(m.sum())
    off.append(n)
    out = {k: np.asarray(v) for k, v in U.items()}
    out['feat'] = out['feat'].astype(np.float32)
    out['off'] = np.asarray(off)
    out.update({f's_{k}': np.concatenate(v) for k, v in S.items()})
    out['fps'] = F['fps']
    return out


class Half:
    """The units of one half (fragments, or fragments cut at doubtful linker joins), ready to
    join: start and end places, place in the team's shape, look, number evidence."""

    def __init__(self, tag, numbers_file=None, split=None):
        F0 = load_frags(tag)
        if split is None:
            F = dict(F0, frag=np.arange(len(F0['win'])))
        else:
            F = units(F0, split)
        self.tag, self.F, self.fps = tag, F, float(F['fps'])
        self._where = {(int(w), int(p)): i for i, (w, p) in enumerate(zip(F0['win'], F0['pid']))}
        off = F['off']
        keep = (F['team'] < 2) & (F['role'] <= 1) & (F['n_seen'] >= MIN_SEEN)
        self.ids = np.where(keep)[0]
        C, sign = team_shape(tag, F)
        n = len(F['win'])
        self.start_p = np.full((n, 2), np.nan)
        self.end_p = np.full((n, 2), np.nan)
        self.place = np.full((n, 2), np.nan)
        for i in self.ids:
            sl = slice(off[i], off[i + 1])
            fr, xy = F['s_f'][sl], np.stack([F['s_x'][sl], F['s_y'][sl]], 1).astype(np.float64)
            good = np.isfinite(xy).all(1)
            if good.sum() < 2:
                continue
            fr, xy = fr[good], xy[good]
            self.start_p[i] = xy[:3].mean(0)
            self.end_p[i] = xy[-3:].mean(0)
            tm = int(F['team'][i])
            c = C[np.clip(fr, 0, len(C) - 1), tm]
            rel = sign[tm] * (xy - c)
            rel = rel[np.isfinite(rel).all(1)]
            if len(rel) >= 3:
                self.place[i] = np.median(rel, 0)
        # body-part colours (hair, face, arms, knees, socks, cleats, sock length) per fragment
        self.parts = np.full((n, PART_DIM), np.nan)
        pf = OUT / f'parts_{tag}_all.npz'
        if pf.exists():
            z = np.load(pf)
            row = {int(f): k for k, f in enumerate(z['frag'])}
            V = np.concatenate([z[p] for p in PARTS] + [z['sock_len'][:, None]], 1)
            for i in range(n):
                k = row.get(int(F['frag'][i]))
                if k is not None:
                    self.parts[i] = V[k]
        # number evidence per fragment: summed crop beliefs and weights (pooled per person)
        self.numP = {}
        nf = numbers_file or OUT / f'numbers_{tag}.npz'
        if Path(nf).exists():
            z = np.load(nf)
            P = _crop_beliefs(z['dist'])
            w = np.where(z['leg'] >= NUM_TL, z['leg'], 0.0)
            u = self.unit_at(z['frag'], z['frame'])
            for i in np.unique(u[(u >= 0) & (w > 0)]):
                m = (u == i) & (w > 0)
                self.numP[int(i)] = (P[m] * w[m, None]).sum(0), float(w[m].sum())

    def unit_at(self, frag, frame):
        """The unit holding fragment `frag` at `frame` (arrays), -1 if none."""
        F = self.F
        order = np.lexsort((F['first'], F['frag']))
        uf, u0 = F['frag'][order], F['first'][order]
        frag, frame = np.asarray(frag), np.asarray(frame)
        lo = np.searchsorted(uf, frag)
        hi = np.searchsorted(uf, frag, side='right')
        out = np.full(len(frag), -1)
        for j in np.where(hi > lo)[0]:
            k = lo[j] + np.searchsorted(u0[lo[j] : hi[j]], frame[j], side='right') - 1
            out[j] = order[max(k, lo[j])]
        return out

    def unit_of(self, q):
        """The unit a lab clip (window, pid, once-a-second samples) mostly falls in."""
        i = self._where.get((q['win'], q['pid']))
        if i is None:
            return None
        fr = np.asarray(q['samples'], float)[:, 0].astype(int)
        u = self.unit_at(np.full(len(fr), i), fr)
        u = u[u >= 0]
        return int(np.bincount(u).argmax()) if len(u) else None

    def group(
        self,
        thr=2.5,
        cues=('feat', 'place', 'num', 'last'),
        w_num=0.5,
        w_parts=1.0,
        min_s=5.0,
        overlap=10,
        w_last=5.0,
        last_s=5.0,
        s0=3.0,
        sv=7.0,
    ):
        """Greedy average-linkage joining of fragments seen >= `min_s` into people: same team,
        never two places at once (> `overlap` frames), cost = look / median + place / median
        + w_num * (-log number likelihood ratio) + w_last * (impossible-move penalty). Stops
        when the cheapest join costs more than `thr`. Returns person per fragment, -1 left out.
        Cue 'parts' (opt-in) adds w_parts * the mean over body parts of colour gap / median.

        The last-seen term only penalises: where one ends and the other starts within `last_s`,
        a miss beyond s0 + sv * gap metres costs (miss / (s0 + sv * gap) - 1). As a bonus for
        near hand-offs it made joins worse (wb1 33 -> 23 of 85): the linker breaks a player
        exactly where players cross, so the nearest start is often someone else."""
        F = self.F
        ids = [int(i) for i in self.ids if F['n_seen'][i] >= min_s * self.fps]
        G = {}
        for i in ids:
            wt = float(F['n_seen'][i])
            ok = np.isfinite(self.place[i]).all()
            nP = self.numP.get(i)
            pv = self.parts[i]
            pm = np.array([np.isfinite(pv[s]).all() for s in PART_SLOTS], float) * wt
            G[i] = dict(
                pp=np.nan_to_num(pv)
                * np.concatenate([np.full(s.stop - s.start, m) for s, m in zip(PART_SLOTS, pm)]),
                pn=pm,
                team=int(F['team'][i]),
                spans=[(int(F['first'][i]), int(F['last'][i]))],
                feat=F['feat'][i] * wt,
                place=self.place[i] * wt if ok else np.zeros(2),
                pw=wt if ok else 0.0,
                num=nP[0] if nP else np.zeros(100),
                nw=nP[1] if nP else 0.0,
                members=[i],
                ends=[(int(F['first'][i]), self.start_p[i], int(F['last'][i]), self.end_p[i])],
            )

        def apart(a, b):
            sa, sb = G[a]['spans'], G[b]['spans']
            i = j = 0
            while i < len(sa) and j < len(sb):
                if min(sa[i][1], sb[j][1]) - max(sa[i][0], sb[j][0]) > overlap:
                    return False
                if sa[i][1] < sb[j][1]:
                    i += 1
                else:
                    j += 1
            return True

        def parts(a, b):
            A, B = G[a], G[b]
            fa, fb = A['feat'] / np.linalg.norm(A['feat']), B['feat'] / np.linalg.norm(B['feat'])
            out = {'feat': 1 - fa @ fb}
            if A['pw'] > 0 and B['pw'] > 0:
                out['place'] = np.linalg.norm(A['place'] / A['pw'] - B['place'] / B['pw'])
            else:
                out['place'] = np.nan
            out['num'] = -np.log(100 * float(_belief_pooled(A) @ _belief_pooled(B)))
            out['parts'] = np.array(
                [
                    np.linalg.norm(A['pp'][s] / A['pn'][k] - B['pp'][s] / B['pn'][k])
                    if A['pn'][k] > 0 and B['pn'][k] > 0
                    else np.nan
                    for k, s in enumerate(PART_SLOTS)
                ]
            )
            return out

        keys = sorted(G)
        by_team = {t: [g for g in keys if G[g]['team'] == t] for t in (0, 1)}
        # look and place medians over random same-team pairs put them on one scale
        rng = np.random.default_rng(0)
        smp = []
        for gs in by_team.values():
            for _ in range(min(3000, len(gs) ** 2) if len(gs) > 1 else 0):
                a, b = rng.choice(gs, 2, replace=False)
                smp.append(parts(a, b))
        med = {k: np.nanmedian([s[k] for s in smp]) for k in ('feat', 'place')}
        med['parts'] = (
            np.nanmedian([s['parts'] for s in smp], 0) if smp else np.ones(len(PART_SLOTS))
        )

        def cost(a, b):
            p = parts(a, b)
            c = 0.0
            if 'feat' in cues:
                c += p['feat'] / med['feat']
            if 'place' in cues:
                c += (p['place'] / med['place']) if np.isfinite(p['place']) else 1.0
            if 'num' in cues:
                c += w_num * p['num']
            if 'parts' in cues:
                d = p['parts'] / med['parts']
                c += w_parts * (np.nanmean(d) if np.isfinite(d).any() else 1.0)
            if 'last' in cues:
                c += w_last * self._impossible(G[a]['ends'], G[b]['ends'], last_s, s0, sv)
            return c

        import heapq

        heap, alive = [], set(keys)
        for gs in by_team.values():
            for i, a in enumerate(gs):
                for b in gs[i + 1 :]:
                    if apart(a, b):
                        heap.append((cost(a, b), a, b))
        heapq.heapify(heap)
        nxt_id = max(keys) + 1 if keys else 0
        while heap:
            c, a, b = heapq.heappop(heap)
            if c > thr:
                break
            if a not in alive or b not in alive:
                continue
            A, B = G[a], G[b]
            G[nxt_id] = dict(
                team=A['team'],
                spans=sorted(A['spans'] + B['spans']),
                feat=A['feat'] + B['feat'],
                place=A['place'] + B['place'],
                pw=A['pw'] + B['pw'],
                num=A['num'] + B['num'],
                nw=A['nw'] + B['nw'],
                pp=A['pp'] + B['pp'],
                pn=A['pn'] + B['pn'],
                members=A['members'] + B['members'],
                ends=sorted(A['ends'] + B['ends'], key=lambda e: e[0]),
            )
            alive -= {a, b}
            for o in list(alive):
                if G[o]['team'] == A['team'] and apart(nxt_id, o):
                    heapq.heappush(heap, (cost(nxt_id, o), nxt_id, o))
            alive.add(nxt_id)
            nxt_id += 1
        person = np.full(len(F['win']), -1)
        for k, g in enumerate(sorted(alive)):
            person[G[g]['members']] = k
        return person

    def _impossible(self, ea, eb, last_s, s0, sv):
        """Worst hand-off between two people (one ends, the other starts within `last_s`,
        nothing of either in between): miss / (s0 + sv * gap) - 1 when positive, else 0."""
        ev = sorted([(e, 0) for e in ea] + [(e, 1) for e in eb], key=lambda t: t[0][0])
        worst = 0.0
        for (e1, w1), (e2, w2) in zip(ev, ev[1:]):
            if w1 == w2:
                continue
            g = (e2[0] - e1[2]) / self.fps
            if g < 0 or g > last_s:
                continue
            d = np.linalg.norm(e2[1] - e1[3])
            if np.isfinite(d):
                worst = max(worst, d / (s0 + sv * g) - 1.0)
        return worst


def _belief_pooled(g):
    p = g['num'] / g['nw'] if g['nw'] > 0 else np.full(100, 0.01)
    return (1 - NUM_FLOOR) * p + NUM_FLOOR / 100


def score(H, person, label=''):
    """Pairs of named lab clips (sure answers): same person joined, two people joined."""
    from pitchiq.experiments.idscore import truth

    qs = [q for q in truth([H.tag]) if q['v'] == 'named' and q['sure']]
    got = [(q, H.unit_of(q)) for q in qs]
    miss = [q['id'] for q, i in got if i is None]
    got = [(q, i) for q, i in got if i is not None]
    same = same_j = diff = diff_j = 0
    bad = []
    for x, (a, i) in enumerate(got):
        for b, j in got[x + 1 :]:
            if a['kit'] != b['kit']:
                continue
            joined = person[i] >= 0 and person[i] == person[j]
            if a['person'] == b['person']:
                same += 1
                same_j += joined
            else:
                diff += 1
                diff_j += joined
                if joined:
                    bad.append((a['id'], a['name'], b['id'], b['name']))
    print(
        f'  {label:36} same person joined {same_j:3d}/{same} ({100 * same_j / max(same, 1):3.0f}%), '
        f'two people joined {diff_j:3d}/{diff}' + (f'  [not found: {miss}]' if miss else ''),
        flush=True,
    )
    return same_j, same, diff_j, diff, bad


def join(tag, final=False):
    """People for the half with the wb1-chosen joiner, against the cue ablations."""
    _guard(tag, final)
    H = Half(tag)
    print(f'{tag}: {len(H.ids)} fragments ({len(H.numP)} with a legible number crop)')
    score(H, np.arange(len(H.F['win'])), 'nothing joined (the linker today)')
    runs = [
        ('look + place', ('feat', 'place')),
        ('look + place + last seen', ('feat', 'place', 'last')),
        ('look + place + number', ('feat', 'place', 'num')),
        ('... + last seen + body parts', ('feat', 'place', 'num', 'last', 'parts')),
        ('look + place + number + last seen', ('feat', 'place', 'num', 'last')),
    ]
    for label, cues in runs:
        person = H.group(cues=cues)
        n = len(np.unique(person[person >= 0]))
        _, _, _, _, bad = score(H, person, f'{label} ({n} people)')
    for b in bad:
        print('    wrong join:', *b)
    np.savez_compressed(OUT / f'people_{tag}.npz', person=person, win=H.F['win'], pid=H.F['pid'])
    print('->', OUT / f'people_{tag}.npz')
    return person


def bodyparts(tag, final=False, n_box=15, min_h=55):
    """Body-part colours per outfield fragment seen >= 5 s: median Lab of the non-grass pixels of
    hair, face, arms (shirt-coloured pixels dropped: bare arms, sleeves, bands), knees, socks and
    cleats over up to `n_box` boxes at least `min_h` px tall, and sock length (share of leg rows
    in the sock colour). -> parts_<tag>_all.npz, read by Half for the 'parts' cue."""
    _guard(tag, final)
    _video(tag)
    import cv2

    from pitchiq.experiments import player_lab
    from pitchiq.pitchtrack import _green, grass_hue

    # (rows, column ranges) as shares of the box
    R = dict(
        hair=((0.00, 0.06), [(0.32, 0.68)]),
        face=((0.06, 0.13), [(0.35, 0.65)]),
        shirt=((0.20, 0.42), [(0.35, 0.65)]),
        arms=((0.18, 0.50), [(0.0, 0.25), (0.75, 1.0)]),
        knees=((0.58, 0.70), [(0.25, 0.75)]),
        leg=((0.55, 0.92), [(0.25, 0.75)]),
        socks=((0.80, 0.90), [(0.25, 0.75)]),
        cleats=((0.92, 1.00), [(0.20, 0.80)]),
    )
    F = load_frags(tag)
    fps, off = float(F['fps']), F['off']
    jobs = {}
    for i in range(len(F['win'])):
        if F['team'][i] > 1 or F['role'][i] != 0 or F['n_seen'][i] < 5 * fps:
            continue
        k = np.arange(off[i], off[i + 1])
        b = F['s_box'][k]
        k = k[(b[:, 3] - b[:, 1]) >= min_h]
        for j in k[np.linspace(0, len(k) - 1, min(n_box, len(k))).astype(int)] if len(k) else []:
            jobs.setdefault(int(F['s_f'][j]), []).append((i, F['s_box'][j]))
    print(f'{sum(map(len, jobs.values()))} boxes on {len(jobs)} frames', flush=True)

    def pixels(img, b, region, grass):
        (ra, rb), cols = R[region]
        H, W = img.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in b)
        r0, r1 = (
            int(np.clip(y1 + ra * (y2 - y1), 0, H - 1)),
            int(np.clip(y1 + rb * (y2 - y1), 1, H)),
        )
        out, rows = [np.zeros((0, 3))], [np.zeros(0, int)]
        for ca, cb in cols:
            c0, c1 = (
                int(np.clip(x1 + ca * (x2 - x1), 0, W - 1)),
                int(np.clip(x1 + cb * (x2 - x1), 1, W)),
            )
            p = img[r0 : max(r1, r0 + 1), c0 : max(c1, c0 + 1)]
            if p.size == 0:
                continue
            hsv = cv2.cvtColor(p, cv2.COLOR_BGR2HSV).reshape(-1, 3)
            g = _green(hsv)
            if grass is not None:
                g &= (np.abs(hsv[:, 0].astype(float) - grass[0]) <= grass[1]) & (
                    hsv[:, 1] <= grass[2]
                )
            out.append(cv2.cvtColor(p, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)[~g])
            rows.append(np.repeat(np.arange(p.shape[0]), p.shape[1])[~g])
        return np.concatenate(out), np.concatenate(rows)

    def med(lab):
        return np.median(lab, 0) if len(lab) >= 4 else np.full(3, np.nan)

    def describe(img, b, grass):
        v = {
            p: med(pixels(img, b, p, grass)[0])
            for p in ('hair', 'face', 'knees', 'socks', 'cleats')
        }
        shirt = med(pixels(img, b, 'shirt', grass)[0])
        arms = pixels(img, b, 'arms', grass)[0]
        if np.isfinite(shirt).all() and len(arms):
            arms = arms[np.linalg.norm(arms - shirt, axis=1) > 18]
        v['arms'] = med(arms)
        sl = np.nan
        leg, rr = pixels(img, b, 'leg', grass)
        if np.isfinite(v['socks']).all() and len(leg):
            m = [np.median(leg[rr == r], 0) for r in np.unique(rr) if (rr == r).sum() >= 2]
            if m:
                sl = float(np.mean([np.linalg.norm(x - v['socks']) < 15 for x in m]))
        return np.concatenate([v[p] for p in PARTS] + [[sl]])

    acc = {}
    cap = cv2.VideoCapture(player_lab.VIDEO)
    fs = sorted(jobs)
    want = set(fs)
    cap.set(cv2.CAP_PROP_POS_FRAMES, fs[0])
    for f in range(fs[0], fs[-1] + 1):
        if f not in want:
            if not cap.grab():
                break
            continue
        ok, img = cap.read()
        if not ok:
            break
        gr = grass_hue(img)
        for i, b in jobs[f]:
            acc.setdefault(i, []).append(describe(img, b, gr))
    ids = sorted(acc)
    V = np.array([np.nanmedian(np.array(acc[i]), 0) for i in ids])
    out = OUT / f'parts_{tag}_all.npz'
    cols = {p: V[:, 3 * k : 3 * k + 3] for k, p in enumerate(PARTS)}
    np.savez(
        out, frag=np.array(ids), sock_len=V[:, -1], n=np.array([len(acc[i]) for i in ids]), **cols
    )
    print(len(ids), 'fragments ->', out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['frags', 'numbers', 'parts', 'join'])
    ap.add_argument('tag')
    ap.add_argument('--final', action='store_true')
    ap.add_argument('--tuned', default=None, help='numtrain.py model for the reader')
    a = ap.parse_args(argv)
    if a.cmd == 'frags':
        frags(a.tag, a.final)
    elif a.cmd == 'numbers':
        numbers(a.tag, a.final, tuned=a.tuned)
    elif a.cmd == 'parts':
        bodyparts(a.tag, a.final)
    else:
        join(a.tag, a.final)
    return 0


if __name__ == '__main__':
    sys.exit(main())
