"""Player ID by formation slots: each team is ten outfield positions and a keeper, every
fragment is put in one position, and each position's fragments are then split into the
players who held it (substitutes take over a position).

    python -m pitchiq.experiments.idslots <tag> [--final]

Uses the fragments and number reads of `idlink` (frags_<tag>.npz, numbers_<tag>.npz).

1. Positions: a fragment's place is its median position relative to its team's centre,
   facing the way the team attacks (idlink.Half.place). Weighted k-means of the fragments'
   places gives ten starting positions per team.
2. Filling them: one fragment at a time, the surest first (the biggest gap between its best
   and second-best position), into the cheapest position free for its whole span (one player
   per position at a time). Cost = place / median + look / median + number + impossible move
   from the position's previous or next player. Positions are re-centred and refilled.
3. Players: inside each position, fragments are joined by look and number (average linkage)
   into the players who held it.

wb1 (2026-11-07). Lab pairs: same person joined / two people joined. Sure-number pairs (their
number reads hidden from the joiner): same / different joined.
    idlink join (today)                     33/85   6/699    31%  1.4%
    positions from fragments                30/85  40/699    49%  9.0%
    ... then split by look and number       30/85  36/699    30%  6.2%  (306 players)
    positions over idlink's groups          23/85   1/699    81%  1.2%  (70 of 227 placed)
    idlink + those positions                33/85   6/699    = today: one group per position
Not better than today, so wb2 was not scored. Why: a player stands a median 5.5 m from their
own average place, and the next teammate's average is 5.4 m away. Players on the pitch at the
same time take turns in one position (wb1 #4 and #8: seen together 189 s, both mostly in one
position). Teammates' looks are too alike to split a position into its players. Even
knowing each player's true average, place picks the right one of 9 for 66% of fragments
(look 68%, both 74%). The map's own slot chains (topdown.chain, movement only) are no
identity at all: 1/85 same, 13/699 two people.
"""

from __future__ import annotations

import argparse
import bisect

import numpy as np

from pitchiq.experiments import idlink as L


def _kmeans(P, w, k, seed=0, restarts=20, iters=50):
    rng = np.random.default_rng(seed)
    best = (np.inf, None)
    for _ in range(restarts):
        C = P[rng.choice(len(P), 1, p=w / w.sum())]
        while len(C) < k:  # k-means++
            d = ((P[:, None] - C[None]) ** 2).sum(2).min(1) * w
            C = np.vstack([C, P[rng.choice(len(P), p=d / d.sum())]])
        for _ in range(iters):
            lab = ((P[:, None] - C[None]) ** 2).sum(2).argmin(1)
            C = np.array(
                [
                    np.average(P[lab == j], 0, w[lab == j]) if (lab == j).any() else C[j]
                    for j in range(k)
                ]
            )
        inertia = (((P - C[lab]) ** 2).sum(1) * w).sum()
        if inertia < best[0]:
            best = (inertia, C)
    return best[1]


class Slots:
    def __init__(self, H: L.Half):
        self.H = H
        F = H.F
        self.n_frames = int(F['last'].max()) + 2

    def team(
        self,
        tm,
        k=10,
        min_s=5.0,
        w_place=1.0,
        w_look=1.0,
        w_num=0.5,
        w_last=5.0,
        thr=4.0,
        overlap=10,
        rounds=3,
        prior_s=20.0,
        last_s=5.0,
        s0=3.0,
        sv=7.0,
        min_gap=0.0,
        groups=None,
    ):
        """Position (0..k-1) per outfield fragment of team `tm`, -1 left out. With `groups`
        (a person per fragment, e.g. idlink's joins), whole groups are placed instead."""
        H, F, fps = self.H, self.H.F, self.H.fps
        ids = np.array(
            [
                i
                for i in H.ids
                if F['team'][i] == tm
                and F['role'][i] == 0
                and F['n_seen'][i] >= min_s * fps
                and np.isfinite(H.place[i]).all()
            ]
        )
        if groups is None:
            rows_of = [[int(i)] for i in ids]
        else:
            by = {}
            for i in ids:
                by.setdefault(int(groups[i]) if groups[i] >= 0 else -1 - int(i), []).append(int(i))
            rows_of = list(by.values())
            w_last = 0.0  # hand-offs are between fragments, not groups
        n = len(rows_of)
        wt = np.array([F['n_seen'][r].sum() for r in rows_of], np.float64)
        P = np.array([np.average(H.place[r], 0, F['n_seen'][r]) for r in rows_of])
        X = np.array(
            [(F['feat'][r].astype(np.float64) * F['n_seen'][r, None]).sum(0) for r in rows_of]
        )
        X /= np.linalg.norm(X, axis=1, keepdims=True)
        B = np.full((n, 100), 0.01)
        nw = np.zeros(n)
        for r, mem_r in enumerate(rows_of):
            got = [H.numP[i] for i in mem_r if i in H.numP]
            if got:
                nw[r] = sum(g[1] for g in got)
                B[r] = sum(g[0] for g in got) / nw[r]
        Bf = (1 - L.NUM_FLOOR) * B + L.NUM_FLOOR / 100
        spans = [[(int(F['first'][i]), int(F['last'][i])) for i in r] for r in rows_of]
        first = np.array([min(a for a, _ in sp_) for sp_ in spans])
        last = np.array([max(b for _, b in sp_) for sp_ in spans])
        sp = np.array([H.start_p[r[0]] for r in rows_of])
        ep = np.array([H.end_p[r[-1]] for r in rows_of])
        rng = np.random.default_rng(0)
        a, b = rng.integers(0, n, (2, 4000))
        med_p = np.median(np.linalg.norm(P[a] - P[b], axis=1))
        med_f = np.median(1 - (X[a] * X[b]).sum(1))
        C0 = _kmeans(P, wt, k)
        pw0 = prior_s * fps
        for _ in range(rounds):
            slot = np.full(n, -1)
            occ = np.zeros(
                (k, self.n_frames + 1), np.int32
            )  # cumulative occupancy, rebuilt per fill
            busy = np.zeros((k, self.n_frames), bool)
            ps, pw = C0 * pw0, np.full(k, pw0)
            fs, fw = np.zeros((k, X.shape[1])), np.zeros(k)
            ns, nws = np.zeros((k, 100)), np.zeros(k)
            mem = [[] for _ in range(k)]  # (first, row) sorted

            def col(s, rows):
                c = w_place * np.linalg.norm(P[rows] - ps[s] / pw[s], axis=1) / med_p
                if fw[s] > 0:
                    f = fs[s] / np.linalg.norm(fs[s])
                    c += w_look * (1 - X[rows] @ f) / med_f
                else:
                    c += w_look
                bs = (1 - L.NUM_FLOOR) * (
                    ns[s] / nws[s] if nws[s] > 0 else np.full(100, 0.01)
                ) + L.NUM_FLOOR / 100
                c += w_num * -np.log(100 * (Bf[rows] @ bs))
                cs = occ[s]
                if groups is None:
                    inside = cs[last[rows] + 1] - cs[first[rows]]
                else:
                    inside = np.array([sum(cs[b + 1] - cs[a] for a, b in spans[r]) for r in rows])
                c[inside > overlap] = np.inf
                if w_last > 0 and mem[s]:
                    starts = [m[0] for m in mem[s]]
                    for j, r in enumerate(rows):
                        if not np.isfinite(c[j]):
                            continue
                        q = bisect.bisect_left(starts, first[r])
                        worst = 0.0
                        if q > 0:
                            p = mem[s][q - 1][1]
                            g = (first[r] - last[p]) / fps
                            if 0 <= g <= last_s:
                                d = np.linalg.norm(sp[r] - ep[p])
                                if np.isfinite(d):
                                    worst = max(worst, d / (s0 + sv * g) - 1)
                        if q < len(mem[s]):
                            p = mem[s][q][1]
                            g = (first[p] - last[r]) / fps
                            if 0 <= g <= last_s:
                                d = np.linalg.norm(sp[p] - ep[r])
                                if np.isfinite(d):
                                    worst = max(worst, d / (s0 + sv * g) - 1)
                        c[j] += w_last * worst
                return c

            pend = np.ones(n, bool)
            Cm = np.stack([col(s, np.arange(n)) for s in range(k)], 1)
            while pend.any():
                rows = np.where(pend)[0]
                srt = np.sort(Cm[rows], 1)
                best = srt[:, 0]
                with np.errstate(invalid='ignore'):
                    gap = np.where(np.isfinite(srt[:, 1]), srt[:, 1] - best, 1e3)
                ok = (best <= thr) & (gap >= min_gap)
                if not ok.any():
                    break
                j = rows[ok][np.argmax(gap[ok])]
                s = int(np.argmin(Cm[j]))
                slot[j] = s
                pend[j] = False
                for a0, b0 in spans[j]:
                    busy[s, a0 : b0 + 1] = True
                occ[s, 1:] = np.cumsum(busy[s])
                ps[s] += P[j] * wt[j]
                pw[s] += wt[j]
                fs[s] += X[j] * wt[j]
                fw[s] += wt[j]
                ns[s] += B[j] * nw[j]
                nws[s] += nw[j]
                bisect.insort(mem[s], (int(first[j]), int(j)))
                rows = np.where(pend)[0]
                if len(rows):
                    Cm[rows, s] = col(s, rows)
            # re-centre on the filled positions (no prior) for the next round
            C0 = np.array(
                [
                    np.average(P[slot == s], 0, wt[slot == s]) if (slot == s).any() else C0[s]
                    for s in range(k)
                ]
            )
            self.last_slot = (ids, slot, C0)
        out = np.full(len(F['win']), -1)
        for r, mem_r in enumerate(rows_of):
            out[mem_r] = slot[r]
        return out

    def players(self, tm, slot, thr=1.5, w_look=1.0, w_num=0.5):
        """Inside each position, join its fragments by look and number into players."""
        H, F = self.H, self.H.F
        person = np.full(len(F['win']), -1)
        nxt = 0
        for s in np.unique(slot[slot >= 0]):
            rows = np.where(slot == s)[0]
            G = {}
            for i in rows:
                w = float(F['n_seen'][i])
                nP = H.numP.get(int(i))
                G[int(i)] = dict(
                    feat=F['feat'][i].astype(np.float64) * w,
                    num=nP[0] if nP else np.zeros(100),
                    nw=nP[1] if nP else 0.0,
                    members=[int(i)],
                )
            fe = np.stack([F['feat'][i] for i in rows]).astype(np.float64)
            fe /= np.linalg.norm(fe, axis=1, keepdims=True)
            med_f = np.median(1 - fe @ fe.T) if len(rows) > 2 else 1.0

            def cost(a, b):
                A, Bq = G[a], G[b]
                fa, fb = (
                    A['feat'] / np.linalg.norm(A['feat']),
                    Bq['feat'] / np.linalg.norm(Bq['feat']),
                )
                return w_look * (1 - fa @ fb) / med_f + w_num * -np.log(
                    100 * float(L._belief_pooled(A) @ L._belief_pooled(Bq))
                )

            alive = set(G)
            nid = max(G) + 1
            while len(alive) > 1:
                ks = sorted(alive)
                best = (np.inf, None, None)
                for x, a in enumerate(ks):
                    for b in ks[x + 1 :]:
                        c = cost(a, b)
                        if c < best[0]:
                            best = (c, a, b)
                if best[0] > thr:
                    break
                _, a, b = best
                G[nid] = {
                    'feat': G[a]['feat'] + G[b]['feat'],
                    'num': G[a]['num'] + G[b]['num'],
                    'nw': G[a]['nw'] + G[b]['nw'],
                    'members': G[a]['members'] + G[b]['members'],
                }
                alive -= {a, b}
                alive.add(nid)
                nid += 1
            for g in alive:
                person[G[g]['members']] = nxt
                nxt += 1
        return person

    def keepers(self, tm, min_s=5.0):
        H, F = self.H, self.H.F
        out = np.zeros(len(F['win']), bool)
        for i in H.ids:
            if F['team'][i] == tm and F['role'][i] == 1 and F['n_seen'][i] >= min_s * H.fps:
                out[i] = True
        return out


def run(H, slot_kw=None, player_kw=None, split=True):
    """Person per fragment: positions per team, keepers as one person each, optionally each
    position split into its players."""
    S = Slots(H)
    n = len(H.F['win'])
    person = np.full(n, -1)
    base = 0
    for tm in (0, 1):
        slot = S.team(tm, **(slot_kw or {}))
        if split:
            p = S.players(tm, slot, **(player_kw or {}))
        else:
            p = slot
        m = p >= 0
        person[m] = p[m] + base
        base = person.max() + 1
        person[S.keepers(tm)] = base
        base += 1
    return person


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('tag')
    ap.add_argument('--final', action='store_true')
    a = ap.parse_args(argv)
    L._guard(a.tag, a.final)
    H = L.Half(a.tag)
    L.score(H, H.group(), 'today: idlink join')
    p = run(H, split=False)
    L.score(H, p, f'positions only ({len(np.unique(p[p >= 0]))})')
    p = run(H)
    L.score(H, p, f'positions, then players ({len(np.unique(p[p >= 0]))})')


if __name__ == '__main__':
    main()
