"""Pseudo-labels from mined picks (mine.py), and their check against the owner's clicks.

A run = consecutive frames with a pick.  A confident run has >= MIN_LEN frames and a median pick score
>= MIN_MEDP.  On a confident run:
  ball    the pick on every STEP-th frame, and on every frame where the pick is "hard": score <= HARD_P or
          put there by the gap filler (the positives the classifier lacks)
  other   on those frames, when the pick itself scores > NEG_PICK_P (the track is sure HERE), every cached
          candidate further than FAR px from the pick with score > NEG_P, plus NEG_EASY random other far ones
Gate-blanked stretches are NOT used as negatives: on the clicks their picks were 8/8 on the ball.
Only cached candidates can be labelled (cache.py); a pick off the cache is counted and dropped.

    python pseudo.py <oof | no_<game> | own_<game>> [check]   -> selftrain/labels/<name>.npz (+ counts per game)
       own_<game>: that game's own picks under its out-of-fold scores (for the transductive variant)
       check: compare with the clicks of the same halves (only honest for 'oof')
"""
import os
import sys

import numpy as np

import st

MIN_LEN = int(os.environ.get('ST_MIN_LEN', '60'))
MIN_MEDP = float(os.environ.get('ST_MIN_MEDP', '3'))
STEP = int(os.environ.get('ST_STEP', '5'))
HARD_P = 2.0
NEG_PICK_P = 2.0
NEG_P = float(os.environ.get('ST_NEG_P', '-2'))
NEG_EASY = 2
FAR = 60.0
BALL, OTHER = 0, 4


def runs(z):
    """Per frame: on, length and median pick score of the run it is in."""
    on = (z['src'] > 0) & np.isfinite(z['x'])
    d = np.diff(np.r_[0, on.astype(np.int8), 0])
    L = np.zeros(len(on), np.int64)
    MP = np.full(len(on), np.nan)
    for b, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        L[b:e] = e - b
        MP[b:e] = np.median(z['p'][b:e])
    return on, L, MP


def select(name, tags, check=False, src=None):
    src = src or name
    rng = np.random.default_rng(0)
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    ck = st.clicks() if check else {}
    rec = dict(pt=[], row=[], y=[], hard=[], f=[])
    names = []
    chk = dict(pos=0, pos_right=0, pos_out=0, hard=0, hard_right=0, neg=0, neg_ball=0)
    print('rule: run >= %d frames, median p >= %g, every %dth frame + hard; negatives p > %g + %d easy' % (
        MIN_LEN, MIN_MEDP, STEP, NEG_P, NEG_EASY))
    per_game = {}
    for tag in tags:
        z = np.load(st.OUT + '/mined/%s/%s.npz' % (src, tag))
        lo = int(z['lo'])
        on, L, MP = runs(z)
        conf = on & (L >= MIN_LEN) & (MP >= MIN_MEDP)
        hard = conf & ((z['p'] <= HARD_P) | (z['src'] == 2))
        idx = np.flatnonzero(conf)
        take = np.zeros(len(on), bool)
        take[idx[::STEP]] = True
        take |= hard
        click = {f: a for _, f, a in ck.get(tag, [])}
        for f in click:  # the check reads every clicked frame on a confident run
            if 0 <= f - lo < len(on) and conf[f - lo]:
                take[f - lo] = True
        c = dict(frames=int(conf.sum()), pos=0, hard=0, neg=0, off_cache=0)
        for k, pt in enumerate(z['pieces'].tolist()):
            sel = np.flatnonzero(take & (z['owner'] == k))
            if not len(sel):
                continue
            ca = st.load_cache(pt, crops=False)
            key = ca['f'] * 64 + ca['j']
            f0, j0, p0 = st.load_oof(pt, st.OOF if src == 'oof' else st.OUT + '/minecls/%s' % src)
            key0 = f0 * 64 + j0
            F = {fr['abs']: fr for fr in ball_tracker.load_frames(pt)}
            pi = len(names)
            names.append(pt)
            for i in sel.tolist():
                f, j = lo + i, int(z['j'][i])
                a = click.get(f)
                r = np.searchsorted(key, f * 64 + j)
                incache = r < len(key) and key[r] == f * 64 + j
                if a is not None:
                    chk['pos'] += 1
                    ok = a['v'] == 'ball' and np.hypot(z['x'][i] - a['x'], z['y'][i] - a['y']) <= 15
                    chk['pos_right'] += ok
                    chk['pos_out'] += a['v'] == 'out'
                    chk['hard'] += bool(hard[i])
                    chk['hard_right'] += bool(hard[i]) and ok
                if not incache:
                    c['off_cache'] += 1
                else:
                    rec['pt'].append(pi), rec['row'].append(r), rec['y'].append(BALL), rec['hard'].append(bool(hard[i])), rec['f'].append(f)
                    c['pos'] += 1
                    c['hard'] += bool(hard[i])
                if z['p'][i] <= NEG_PICK_P or z['src'][i] != 1:
                    continue
                a0, a1 = np.searchsorted(key, [f * 64, f * 64 + 64])
                jj = ca['j'][a0:a1]
                fr = F[f]
                d = np.hypot(fr['x'][jj] - z['x'][i], fr['y'][jj] - z['y'][i])
                pp = p0[np.searchsorted(key0, f * 64 + jj)]
                far = d > FAR
                hn = np.flatnonzero(far & (pp > NEG_P))
                ez = np.flatnonzero(far & (pp <= NEG_P))
                if len(ez) > NEG_EASY:
                    ez = rng.choice(ez, NEG_EASY, replace=False)
                for q in np.r_[hn, ez].astype(int).tolist():
                    rec['pt'].append(pi), rec['row'].append(a0 + q), rec['y'].append(OTHER), rec['hard'].append(bool(pp[q] > NEG_P)), rec['f'].append(f)
                    c['neg'] += 1
                    if a is not None:
                        chk['neg'] += 1
                        chk['neg_ball'] += a['v'] == 'ball' and np.hypot(fr['x'][jj[q]] - a['x'], fr['y'][jj[q]] - a['y']) <= 15
        print('%-8s confident-run frames %6d  ball %5d (hard %4d)  other %6d  picks off the cache %d' % (
            tag, c['frames'], c['pos'], c['hard'], c['neg'], c['off_cache']), flush=True)
        g = per_game.setdefault(st.game(tag), dict(frames=0, pos=0, hard=0, neg=0, off_cache=0))
        for k2 in g:
            g[k2] += c[k2]
    for g, c in per_game.items():
        print('game %-6s %s' % (g, c))
    print('ALL', {k2: sum(c[k2] for c in per_game.values()) for k2 in ('frames', 'pos', 'hard', 'neg', 'off_cache')})
    if check:
        print('check vs clicks:', chk)
        print('  pseudo-positives on clicked frames: %d, within 15 px of the click %d (%.1f%%), on "out" frames %d' % (
            chk['pos'], chk['pos_right'], 100.0 * chk['pos_right'] / max(1, chk['pos']), chk['pos_out']))
        print('  hard pseudo-positives: %d, right %d' % (chk['hard'], chk['hard_right']))
        print('  pseudo-negatives on clicked frames: %d, actually the ball %d (%.2f%%)' % (
            chk['neg'], chk['neg_ball'], 100.0 * chk['neg_ball'] / max(1, chk['neg'])))
    os.makedirs(st.OUT + '/labels', exist_ok=True)
    np.savez(st.OUT + '/labels/%s%s.npz' % (name, os.environ.get('ST_LAB', '')), names=np.array(names), pt=np.array(rec['pt']), row=np.array(rec['row']),
             y=np.array(rec['y']), hard=np.array(rec['hard']), f=np.array(rec['f']))


if __name__ == '__main__':
    name = sys.argv[1]
    if name.startswith('own_'):  # a game's own picks under its out-of-fold scores (transductive variant)
        select(name, [t for t in st.DEV if st.game(t) == name[4:]], src=os.environ.get('ST_OWN_SRC', 'oof'))
    else:
        tags = [t for t in st.DEV if name == 'oof' or st.game(t) != name.split('_', 1)[1]]
        select(name, tags, check='check' in sys.argv[2:])
