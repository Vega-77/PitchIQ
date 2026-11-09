"""Calibrate a game's camera from the coach's pitch pins, a half at a time.

    PIQ_VIDEO=<video> python -m pitchiq.experiments.pincal prep <tag> <lo> <hi> --lines gold
    python -m pitchiq.experiments.pincal transfer <h1 tag> <h2 tag>
    python -m pitchiq.experiments.pincal cal <tag> [--with <other half>] [--install]

The steps that calibrated Piscataway, Colonia and Woodbridge (2026-10-23), moved
here from the scratch folder `lc/` (`held_gather.py`, `lc.py`, `pano_rgb.py`,
`transfer.py`, `xfer_ok.py`, `pin_cal.py`) with the same numbers:

  prep      autocal's gather and prep (SIFT links between samples, paint masks;
            `autocal_run`'s own stages, in `PitchIQ-data/autocal/<tag>/`), then
            loop closure (every sample tied to reference views and all
            rotations and zooms solved together: the camera never moves, so a
            sample far apart in time but looking at the same place is linked
            directly), then a colour panorama of the whole view to pin on
  transfer  the rotation between the halves' loop closures, from keyframes that
            see the same scenery. If the tripod stayed put (most pairs agree,
            p90 under 1 deg, a pure rotation explains the matches) the first
            half's pins are carried to the second and the coach pins once
  cal       pins -> camera position, aim and pitch size (`pitchiq/pins.py`);
            with --with, both halves at once (one camera and pitch, each half
            its own aim and angle scale); then paint polishes the aim. Writes
            the tracker's `<tag>_reg.npz` and `<tag>_venue.npz` (--install
            copies them to `balltrack/pitch_registration/`)

Each half's files are in `PitchIQ-data/pincal/<tag>/` (`PIQ_PINCAL` moves it):
pano_rgb.jpg and pano_rgb.json are what the coach pins on; pins.json
({"pins": {name: [x, y]}} in pano_rgb pixels) is what they clicked.
`process_match` runs all of this and carries the panorama and the pins to and
from the site (`upload_pano`, `read_pins`).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pickle
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from pitchiq import autocal as A
from pitchiq import pins as P
from pitchiq.experiments import autocal_run as AR

REPO = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get('PIQ_PINCAL', REPO.parent / 'PitchIQ-data' / 'pincal'))
HOMOG = REPO / 'pitchiq' / 'balltrack' / 'pitch_registration'

# loop closure (lc.py)
WORKERS = int(os.environ.get('AUTOCAL_WORKERS', min(12, os.cpu_count() or 1)))
KEY_SEP = 0.3  # a new keyframe when no keyframe is within this share of the half field of view
KEY_ZOOM = 0.3  # ... or of this log zoom
N_KEYS = 3  # keyframes each sample is matched to
N_KK = 4  # keyframes each keyframe is matched to
MIN_GAP = 8  # samples: closer pairs are already linked by gather
MAX_DISAGREE = 12.0  # deg between a link's rotation and the chain's
S_PX = 2.0  # Cauchy scale, px
ROUNDS = 4  # long-link filter rounds: vs the chain, then vs each solve

PANO_FRAMES = 120  # frames painted into the colour panorama
PANO_QUALITY = 88  # its JPEG quality

# transfer (transfer.py, xfer_ok.py). WW H1 -> H2 (same tripod): 477/497 pairs on M,
# p90 0.42 deg, rotation-only rms p90 1.41 px; the gate leaves room for that.
MIN_INL = 60
GATE_SHARE, GATE_P90, GATE_RMS = 0.7, 1.0, 3.0

MIN_PINS = 4
# a pin the others miss by more than this is a mis-click (Woodbridge's worst: 21 px)
BAD_PIN_PX = 40.0


def load(p):
    with open(p, 'rb') as fh:
        return pickle.load(fh)


def save(p, obj):
    p = str(p)
    with open(p + '.part', 'wb') as fh:
        pickle.dump(obj, fh)
    os.replace(p + '.part', p)


def here(tag: str) -> Path:
    return WORK / tag


def logger(tag: str):
    d = here(tag)
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fh = open(d / 'log.txt', 'a', encoding='utf-8')

    def log(s):
        line = '%5.0f s  %s' % (time.time() - t0, s)
        print(line, flush=True)
        fh.write(line + '\n')
        fh.flush()

    return log


def spec_of(tag: str) -> dict:
    return load(AR.WORK / tag / 'spec.pkl')


# ================================================================ prep: gather


def gather(tag: str, lo: int, hi: int, lines: str, log) -> None:
    """autocal_run's gather and prep stages only (its cal and height place the pitch
    from paint, which pins replace). A half autocal already gathered is reused."""
    d = AR.WORK / tag
    d.mkdir(parents=True, exist_ok=True)
    spec = dict(lo=lo, hi=hi, lines=lines, camera=None, video=os.environ['PIQ_VIDEO'])
    if (d / 'spec.pkl').exists():
        old = AR._load(d / 'spec.pkl')
        if {k: old.get(k) for k in ('lo', 'hi', 'lines', 'video')} != {
            k: spec[k] for k in ('lo', 'hi', 'lines', 'video')
        }:
            raise SystemExit(f'{d} was set up with another half or video: {old}')
    else:
        AR._save(d / 'spec.pkl', spec)
    A.LINES = lines
    if not (d / 'gather.pkl').exists():
        log('== gather %s %d..%d lines %s' % (tag, lo, hi, lines))
        AR._save(d / 'gather.pkl', AR.gather(lo, hi, log))
    if not (d / 'prep.pkl').exists():
        log('== prep')
        Q, img = AR.prep(AR._load(d / 'gather.pkl'), log)
        AR._save(d / 'prep.pkl', Q)
        import cv2

        cv2.imwrite(str(d / 'pano.jpg'), img)


# ================================================================ prep: loop closure


def axes(R):
    """Each sample's optical axis in sample 0's camera frame (R: 0 -> k)."""
    return np.einsum('nij,i->nj', R, np.array([0, 0, 1.0]))  # R_k^T e_z


def view_score(ax, f, k, others):
    """How far apart two views are: axis angle over the narrower half field of view, plus log zoom ratio."""
    hf = np.arctan(960.0 / np.minimum(f[k], f[others]))
    ang = np.arccos(np.clip(ax[others] @ ax[k], -1, 1))
    return ang / hf + np.abs(np.log(f[others] / f[k])), ang / hf


def pick_keys(R, f):
    ax = axes(R)
    keys = [0]
    for k in range(1, len(R)):
        K = np.array(keys)
        hf = np.arctan(960.0 / np.minimum(f[k], f[K]))
        ang = np.arccos(np.clip(ax[K] @ ax[k], -1, 1))
        if not np.any((ang < KEY_SEP * hf) & (np.abs(np.log(f[K] / f[k])) < KEY_ZOOM)):
            keys.append(k)
    return np.array(keys)


def plan(R, f, keys):
    """(sample, keyframe) pairs to match."""
    ax = axes(R)
    jobs = set()
    for k in range(len(R)):
        far = keys[np.abs(keys - k) >= MIN_GAP]
        if not len(far):
            continue
        sc, rel = view_score(ax, f, k, far)
        for i in np.argsort(sc)[:N_KEYS]:
            if rel[i] < 0.8:
                jobs.add((k, int(far[i])))
    for k in keys:
        far = keys[np.abs(keys - k) >= MIN_GAP]
        if not len(far):
            continue
        sc, rel = view_score(ax, f, k, far)
        for i in np.argsort(sc)[:N_KK]:
            if rel[i] < 0.8:
                jobs.add(tuple(sorted((int(k), int(far[i])))))
    return sorted(jobs)


_G = {}


def _init(video, kdir):
    os.environ['PIQ_VIDEO'] = video
    _G.update(kdir=kdir, cache={})


def _key_feats(args):
    import cv2

    frames, ks = args
    cap = AR._video()
    sift = cv2.SIFT_create(nfeatures=3000)
    for k in ks:
        xy, d = A.frame_features(AR._frame(cap, frames[k]), sift)
        np.savez(os.path.join(_G['kdir'], 'k%05d.npz' % k), xy=xy, d=d)
    return len(ks)


def _keyfeat(k):
    c = _G['cache']
    if k not in c:
        if len(c) > 60:
            c.pop(next(iter(c)))
        z = np.load(os.path.join(_G['kdir'], 'k%05d.npz' % k))
        c[k] = (z['xy'], z['d'])
    return c[k]


def _match(args):
    """Samples in a block: read each once, match to its keyframes. Returns [(i, j, pairs or None)]."""
    import cv2

    frames, block = args  # block: [(i, [j...])], i a sample read from the video
    cap = AR._video()
    sift = cv2.SIFT_create(nfeatures=3000)
    out = []
    for i, js in block:
        fi = A.frame_features(AR._frame(cap, frames[i]), sift)
        for j in js:
            out.append((i, j, A.link_inliers(fi, _keyfeat(j))))
    return out


def pool_map(fn, items, video, kdir):
    import multiprocessing as mp

    with mp.get_context('spawn').Pool(WORKERS, initializer=_init, initargs=(video, kdir)) as p:
        return p.map(fn, items, chunksize=1)


def residual(R, f, ii, jj, xi, xj):
    """Pixels of xi (sample ii) carried into sample jj, minus xj. R: 0 -> k."""
    return residual_pp(R[ii], f[ii], R[jj], f[jj], xi, xj)


def residual_pp(Ri, fi, Rj, fj, xi, xj):
    """The same with each pair's own matrices, so one side can be perturbed alone."""
    r = np.c_[(xi - A.CENTRE) / fi[:, None], np.ones(len(xi))]
    w = np.einsum('pk,pkl->pl', r, Ri)  # R_i^T ray (row form)
    c = np.einsum('pl,pml->pm', w, Rj)  # R_j w
    return c[:, :2] / c[:, 2:3] * fj[:, None] + A.CENTRE - xj


def solve(R, f, links, iters=25, log=print):
    """links: list of (i, j, xi, xj). Every rotation and log zoom refined so all links agree
    (Cauchy IRLS, Levenberg-Marquardt on the sparse normal equations; sample 0 fixed)."""
    from scipy.sparse import coo_matrix, diags
    from scipy.sparse.linalg import spsolve

    n = len(R)
    seg = np.array([len(lk[2]) for lk in links])
    li = np.repeat(np.array([lk[0] for lk in links]), seg)
    lj = np.repeat(np.array([lk[1] for lk in links]), seg)
    xi = np.concatenate([lk[2] for lk in links])
    xj = np.concatenate([lk[3] for lk in links])
    starts = np.r_[0, np.cumsum(seg)[:-1]]
    Li = np.array([lk[0] for lk in links])
    Lj = np.array([lk[1] for lk in links])
    R = R.copy()
    f = f.astype(float).copy()
    eps = 1e-6
    E = A.rodrigues(np.eye(3) * eps)  # small rotations about x, y, z

    def cost(R, f):
        r = residual(R, f, li, lj, xi, xj)
        e2 = (r**2).sum(1)
        return float((S_PX**2 * np.log1p(e2 / S_PX**2)).sum()), r, e2

    c0, r, e2 = cost(R, f)
    lam = 1e-3
    for it in range(iters):
        w = 1.0 / (1.0 + e2 / S_PX**2)
        J = np.zeros((len(xi), 2, 8))
        Ri, Rj, fi, fj = R[li], R[lj], f[li], f[lj]
        for a in range(4):
            if a < 3:
                J[:, :, a] = (residual_pp(E[a] @ Ri, fi, Rj, fj, xi, xj) - r) / eps
                J[:, :, 4 + a] = (residual_pp(Ri, fi, E[a] @ Rj, fj, xi, xj) - r) / eps
            else:
                J[:, :, a] = (residual_pp(Ri, fi * np.exp(eps), Rj, fj, xi, xj) - r) / eps
                J[:, :, 4 + a] = (residual_pp(Ri, fi, Rj, fj * np.exp(eps), xi, xj) - r) / eps
        del Ri, Rj
        B = np.add.reduceat(np.einsum('p,pci,pcj->pij', w, J, J), starts, 0)  # per link 8x8
        g = np.add.reduceat(np.einsum('p,pci,pc->pi', w, J, r), starts, 0)  # per link 8
        rows, cols, vals = [], [], []
        for (a, A_), (b, B_) in (
            ((0, Li), (0, Li)),
            ((0, Li), (4, Lj)),
            ((4, Lj), (0, Li)),
            ((4, Lj), (4, Lj)),
        ):
            blk = B[:, a : a + 4, b : b + 4]
            rr = (4 * A_)[:, None, None] + np.arange(4)[None, :, None]
            cc = (4 * B_)[:, None, None] + np.arange(4)[None, None, :]
            rows.append(np.broadcast_to(rr, blk.shape).ravel())
            cols.append(np.broadcast_to(cc, blk.shape).ravel())
            vals.append(blk.ravel())
        H = coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
            shape=(4 * n, 4 * n),
        ).tocsr()
        G = np.zeros(4 * n)
        np.add.at(G, (4 * Li)[:, None] + np.arange(4), g[:, :4])
        np.add.at(G, (4 * Lj)[:, None] + np.arange(4), g[:, 4:])
        d = H.diagonal().copy()
        fix = np.zeros(4 * n)
        fix[:4] = 1e12  # sample 0 holds the gauge
        while True:
            M = (H + diags(lam * d + 1e-6 + fix)).tocsc()
            step = -spsolve(M, G).reshape(n, 4)
            Rn = A.rodrigues(step[:, :3]) @ R
            fn = f * np.exp(step[:, 3])
            c1, r1, e21 = cost(Rn, fn)
            if c1 < c0:
                R, f, r, e2 = Rn, fn, r1, e21
                lam = max(lam / 3, 1e-7)
                break
            lam *= 10
            if lam > 1e6:
                break
        ang = np.degrees(np.linalg.norm(step[:, :3], axis=1))
        log(
            'iter %d  cost %.0f -> %.0f  lam %.0e  step max %.3f deg  med |r| %.2f px'
            % (it, c0, c1, lam, ang.max(), np.median(np.sqrt(e2)))
        )
        if c1 >= c0 or (c0 - c1) / c0 < 1e-5:
            break
        c0 = c1
    return R, f


def loop_closure(tag: str, log) -> None:
    """keys, match, solve -> pincal/<tag>/solve.pkl (lc.py without its write stage)."""
    d = AR.WORK / tag
    out = here(tag)
    spec = AR._load(d / 'spec.pkl')
    video = spec['video']
    os.environ['PIQ_VIDEO'] = video
    A.LINES = spec['lines']
    Q = AR._load(d / 'prep.pkl')
    frames = list(Q['frames'])
    Rc, fc = np.asarray(Q['R']), np.asarray(Q['f'], float)
    n = len(frames)
    log('%s: loop closure over %d samples, video %s' % (tag, n, os.path.basename(video)))

    kdir = str(out / 'keys')
    if not (out / 'keys.pkl').exists():
        keys = pick_keys(Rc, fc)
        os.makedirs(kdir, exist_ok=True)
        log('%d keyframes' % len(keys))
        blocks = [(frames, list(keys[s::WORKERS])) for s in range(WORKERS)]
        pool_map(_key_feats, blocks, video, kdir)
        save(out / 'keys.pkl', dict(keys=keys))
        log('keyframe features done')
    keys = load(out / 'keys.pkl')['keys']

    if not (out / 'match.pkl').exists():
        jobs = plan(Rc, fc, keys)
        by = {}
        for i, j in jobs:
            by.setdefault(i, []).append(j)
        items = sorted(by.items())
        size = max(8, -(-len(items) // (4 * WORKERS)))
        blocks = [(frames, items[s : s + size]) for s in range(0, len(items), size)]
        log('%d long links to try over %d samples' % (len(jobs), len(items)))
        got = [x for b in pool_map(_match, blocks, video, kdir) for x in b]
        save(out / 'match.pkl', dict(got=got))
        log(
            'matched: %d of %d with >= 30 inliers'
            % (sum(g is not None for _, _, g in got), len(got))
        )
    got = load(out / 'match.pkl')['got']

    if (out / 'solve.pkl').exists():
        return
    G = AR._load(d / 'gather.pkl')
    rng = np.random.default_rng(0)
    cand = []
    for i, j, pairs in got:
        if pairs is None:
            continue
        xi, xj = A.thin_link(pairs, rng=rng)
        Rr, _ = A.relative_rotation(xi, xj, fc[i])
        cand.append((i, j, xi, xj, Rr))
    # round 0 keeps the long links that agree with the chain; a wrong short link puts a step in
    # the chain, and the long links across a step over MAX_DISAGREE are lost with it (pi2: three
    # steps, the last -18 deg). So each later round keeps the links that agree with the last
    # solve instead, and solves again from it, until the kept set stops changing.
    R, f, ref, kept = Rc, fc, Rc, None
    long_links = []
    for rnd in range(ROUNDS):
        long_links, bad = [], 0
        for i, j, xi, xj, Rr in cand:
            rel = ref[j] @ ref[i].T
            dis = np.degrees(np.arccos(np.clip((np.trace(Rr @ rel.T) - 1) / 2, -1, 1)))
            if dis > MAX_DISAGREE:
                bad += 1
                continue
            long_links.append((i, j, xi, xj, dis))
        ids = {(lk[0], lk[1]) for lk in long_links}
        if kept is not None and len(ids ^ kept) <= 0.002 * len(cand):
            log('round %d: kept set changed by %d links; done' % (rnd, len(ids ^ kept)))
            break
        kept = ids
        dd = np.array([lk[4] for lk in long_links])
        log(
            'round %d: long links kept %d (dropped %d > %.0f deg); %s disagreement p50 %.2f p90 %.2f max %.2f deg'
            % (
                rnd,
                len(long_links),
                bad,
                MAX_DISAGREE,
                'chain' if rnd == 0 else 'solve',
                np.median(dd),
                np.percentile(dd, 90),
                dd.max(),
            )
        )
        links = [(i, j, xi, xj) for i, j, xi, xj in G['links']] + [lk[:4] for lk in long_links]
        log('solving with %d short + %d long links' % (len(G['links']), len(long_links)))
        R, f = solve(R, f, links, log=log)
        ref = R
    ch = np.degrees(
        np.linalg.norm([A.cv2.Rodrigues(R[k] @ Rc[k].T)[0].ravel() for k in range(n)], axis=1)
    )
    log(
        'moved from the chain: p50 %.2f p90 %.2f max %.2f deg; f ratio %.3f..%.3f'
        % (np.median(ch), np.percentile(ch, 90), ch.max(), (f / fc).min(), (f / fc).max())
    )
    save(out / 'solve.pkl', dict(R=R, f=f, frames=frames, n_long=len(long_links)))


# ================================================================ prep: the colour panorama


def pick_views(R, f, n):
    """About n samples covering the view, zoomed-in ones first."""
    ax = axes(R)
    order = np.argsort(-f)
    got = []
    for sep in np.linspace(0.6, 0.1, 26):
        got = []
        for k in order:
            if not got:
                got.append(k)
                continue
            G = np.array(got)
            hf = np.arctan(960.0 / np.minimum(f[k], f[G]))
            ang = np.arccos(np.clip(ax[G] @ ax[k], -1, 1))
            if not np.any(ang < sep * hf):
                got.append(k)
        if len(got) >= n:
            return np.array(got)
    return np.array(got)


def colour_pano(tag: str, log, n_frames: int = PANO_FRAMES) -> None:
    """The camera's whole view in colour, for pinning (pano_rgb.py).

    Panorama axes as autocal's (x = F*azimuth + cx, y = F*elevation + cy, sample 0's
    camera frame) at full resolution (F = median focal length). Each panorama pixel
    comes from the frame whose centre it is nearest, so players appear once, not ghosted."""
    import cv2

    out = here(tag)
    S = load(out / 'solve.pkl')
    spec = spec_of(tag)
    R, f, frames = S['R'], S['f'], S['frames']
    F = float(np.median(f))
    corners = np.array(
        [[0, 0], [1920, 0], [0, 1080], [1920, 1080], [960, 0], [960, 1080], [0, 540], [1920, 540]],
        float,
    )
    d = np.concatenate(
        [np.c_[(corners - A.CENTRE) / f[k], np.ones(8)] @ R[k] for k in range(len(R))]
    )
    az = np.arctan2(d[:, 0], d[:, 2])
    el = np.arctan2(d[:, 1], np.hypot(d[:, 0], d[:, 2]))
    x0, x1, y0, y1 = az.min() * F - 8, az.max() * F + 8, el.min() * F - 8, el.max() * F + 8
    w, h = int(x1 - x0), int(y1 - y0)
    pano = A.Panorama(np.zeros((1, 1), np.float32), np.zeros((1, 1), np.int32), F, -x0, -y0)
    ks = pick_views(R, f, n_frames)
    log('%s: colour panorama %dx%d at F %.0f, %d frames' % (tag, w, h, F, len(ks)))
    img_out = np.zeros((h, w, 3), np.uint8)
    best = np.full((h, w), np.inf, np.float32)
    cap = cv2.VideoCapture(spec['video'])
    for k in sorted(ks):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frames[k])
        ok, img = cap.read()
        if not ok:
            continue
        ys0, ys1, xs0, xs1 = A._footprint(pano, R[k], f[k], h, w)
        yy, xx = np.mgrid[ys0:ys1, xs0:xs1]
        azp = (xx - pano.cx) / F
        elp = (yy - pano.cy) / F
        D = np.stack([np.sin(azp) * np.cos(elp), np.sin(elp), np.cos(azp) * np.cos(elp)], -1)
        v = D @ R[k].T
        okz = v[..., 2] > 1e-3
        px = np.where(okz, v[..., 0] / np.where(okz, v[..., 2], 1), -9) * f[k] + 960
        py = np.where(okz, v[..., 1] / np.where(okz, v[..., 2], 1), -9) * f[k] + 540
        inside = okz & (px >= 0) & (px < 1919) & (py >= 0) & (py < 1079)
        score = np.hypot((px - 960) / 960, (py - 540) / 960).astype(np.float32)
        sub = best[ys0:ys1, xs0:xs1]
        take = inside & (score < sub)
        if not take.any():
            continue
        warped = cv2.remap(img, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR)
        o = img_out[ys0:ys1, xs0:xs1]
        o[take] = warped[take]
        sub[take] = score[take]
    cv2.imwrite(str(out / 'pano_rgb.jpg'), img_out, [cv2.IMWRITE_JPEG_QUALITY, PANO_QUALITY])
    meta = dict(
        tag=tag,
        F=F,
        cx=pano.cx,
        cy=pano.cy,
        w=w,
        h=h,
        frames=[int(frames[k]) for k in sorted(ks)],
    )
    (out / 'pano_rgb.json').write_text(json.dumps(meta))
    log('wrote pano_rgb.jpg, %d KB' % ((out / 'pano_rgb.jpg').stat().st_size // 1024))


def prepped(tag: str) -> bool:
    d = here(tag)
    return all((d / n).exists() for n in ('solve.pkl', 'keys.pkl', 'pano_rgb.jpg', 'pano_rgb.json'))


def prep(tag: str, lo: int, hi: int, lines: str) -> None:
    log = logger(tag)
    gather(tag, lo, hi, lines, log)
    loop_closure(tag, log)
    if not (here(tag) / 'pano_rgb.json').exists():
        colour_pano(tag, log)
    log('prep done')


# ================================================================ transfer


def _feats(tag, k):
    z = np.load(here(tag) / 'keys' / ('k%05d.npz' % k))
    return z['xy'], z['d']


def chordal_mean(Ms):
    U, _, Vt = np.linalg.svd(np.sum(Ms, 0))
    M = U @ Vt
    if np.linalg.det(M) < 0:
        M = U @ np.diag([1, 1, -1.0]) @ Vt
    return M


def _ang(Ra, Rb):
    return np.degrees(np.arccos(np.clip((np.trace(Ra @ Rb.T) - 1) / 2, -1, 1)))


def transfer(src: str, dst: str, log=print) -> dict:
    """The rotation M from src's loop-closure frame to dst's, and whether the camera
    stayed put. Writes pincal/<dst>/transfer_from_<src>.json and returns it."""
    S1, S2 = load(here(src) / 'solve.pkl'), load(here(dst) / 'solve.pkl')
    K1, K2 = load(here(src) / 'keys.pkl')['keys'], load(here(dst) / 'keys.pkl')['keys']
    log('%s %d keyframes, %s %d' % (src, len(K1), dst, len(K2)))
    F2 = {b: _feats(dst, b) for b in K2}
    rows = []
    rng = np.random.default_rng(0)
    for a in K1:
        fa = _feats(src, a)
        for b in K2:
            got = A.link_inliers(fa, F2[b])
            if got is None or len(got[0]) < MIN_INL:
                continue
            xa, xb = A.thin_link(got, rng=rng)
            Rr, fb = A.relative_rotation(xa, xb, S1['f'][a])
            pred = A.to_px(A.rays(xa, S1['f'][a]) @ Rr.T, fb)
            rms = float(np.sqrt(np.mean(np.sum((pred - xb) ** 2, 1))))
            rows.append((a, b, len(got[0]), S2['R'][b].T @ Rr @ S1['R'][a], fb / S2['f'][b], rms))
    log('%d keyframe pairs with >= %d inliers' % (len(rows), MIN_INL))
    res = dict(src=src, dst=dst, pairs=len(rows))
    if len(rows) < 5:
        res.update(ok=False, why='the halves do not see the same scenery from the same place')
    else:
        Ms = np.array([r[3] for r in rows])
        M = chordal_mean(Ms)
        for _ in range(3):
            d = np.array([_ang(m, M) for m in Ms])
            keep = d < max(0.3, 3 * np.median(d))
            M = chordal_mean(Ms[keep])
        d = np.array([_ang(m, M) for m in Ms])
        rms = np.array([r[5] for r in rows])
        fr = np.array([r[4] for r in rows])
        k, n = int(keep.sum()), len(rows)
        p90, rms90 = float(np.percentile(d, 90)), float(np.percentile(rms, 90))
        log(
            'M from %d of %d pairs; pairs vs M: p50 %.3f p90 %.3f max %.3f deg'
            % (k, n, np.median(d), p90, d.max())
        )
        log(
            'rotation-only fit of each pair: rms p50 %.2f p90 %.2f px; zoom vs %s p50 %.4f'
            % (np.median(rms), rms90, dst, np.median(fr))
        )
        bad = []
        if k < GATE_SHARE * n:
            bad.append('only %d of %d pairs agree' % (k, n))
        if p90 > GATE_P90:
            bad.append('p90 %.2f deg' % p90)
        if rms90 > GATE_RMS:
            bad.append('rms p90 %.2f px' % rms90)
        res.update(
            ok=not bad,
            why=', '.join(bad),
            M=M.tolist(),
            kept=k,
            p90_deg=p90,
            rms_p90_px=rms90,
        )
    log(
        'camera stayed put: %s%s'
        % ('yes' if res['ok'] else 'no', '' if res['ok'] else ' (%s)' % res['why'])
    )
    (here(dst) / f'transfer_from_{src}.json').write_text(json.dumps(res, indent=1))
    return res


def carry_pins(src: str, dst: str) -> dict:
    """src's pins.json carried into dst's panorama pixels by the transfer's M."""
    t = json.loads((here(dst) / f'transfer_from_{src}.json').read_text())
    if not t.get('ok'):
        raise SystemExit(f'the camera moved between {src} and {dst}: {dst} needs its own pins')
    M = np.array(t['M'])
    m1 = json.loads((here(src) / 'pano_rgb.json').read_text())
    m2 = json.loads((here(dst) / 'pano_rgb.json').read_text())
    pins = json.loads((here(src) / 'pins.json').read_text())['pins']
    names = list(pins)
    xy = np.array([pins[n] for n in names], float)
    D = P.dirs((xy[:, 0] - m1['cx']) / m1['F'], (xy[:, 1] - m1['cy']) / m1['F']) @ M.T
    az = np.arctan2(D[:, 0], D[:, 2])
    el = np.arctan2(D[:, 1], np.hypot(D[:, 0], D[:, 2]))
    out = np.c_[az * m2['F'] + m2['cx'], el * m2['F'] + m2['cy']]
    return {
        'pins': {n: [round(float(x), 1), round(float(y), 1)] for n, (x, y) in zip(names, out)},
        'from': src,
    }


# ================================================================ cal


def pin_angles(tag: str, path: Path | None = None):
    """Pins as (names, (n, 2) azimuth/elevation in sample 0's frame, F)."""
    meta = json.loads((here(tag) / 'pano_rgb.json').read_text())
    got = json.loads((path or here(tag) / 'pins.json').read_text())['pins']
    names = [n for n in got if n in P.NAMES]
    xy = np.array([got[n] for n in names], float).reshape(-1, 2)
    ang = np.c_[(xy[:, 0] - meta['cx']) / meta['F'], (xy[:, 1] - meta['cy']) / meta['F']]
    return names, ang, meta['F']


class BadPins(SystemExit):
    """Too few pins, or one the others disagree with: the coach should look again."""


def fit_pins(tag: str, others: list[str] = (), log=print) -> dict:
    """The pins alone (seconds, no video): camera, aim, pitch size and each pin's
    leave-one-out miss. Raises BadPins on too few pins or a pin the rest disagree with."""
    names, ang, F = pin_angles(tag)
    log('%d pins: %s' % (len(names), ', '.join(names)))
    if len(names) < MIN_PINS:
        raise BadPins(f'{tag}: {len(names)} pins; at least {MIN_PINS} are needed')
    C, R0, L, W, rms = P.solve(names, ang)
    log('1 pins:    %s; rms %.1f px' % (P.describe(C, L, W), rms * F))
    worst = {}
    if len(names) >= 5:
        loo = P.leave_one_out(names, ang)
        for n, e in sorted(loo.items(), key=lambda kv: -kv[1]):
            log('   leave-one-out %-22s %6.1f px' % (n, e * F))
        worst = {n: e * F for n, e in loo.items()}
    steps = {'pins': (C, R0, L, W)}
    k = 1.0
    if others:
        sets = [(names, ang)] + [pin_angles(t)[:2] for t in others]
        Fs = [F] + [pin_angles(t)[2] for t in others]
        C, Rj, L, W, _, per, ks = P.solve_joint(sets, scale=True)
        R0, k = Rj[0], ks[0]
        log(
            '1 joint with %s: %s; scales %s; rms %s px'
            % (
                ', '.join(others),
                P.describe(C, L, W),
                ' '.join('%.4f' % v for v in ks),
                ' '.join('%.1f' % (r * Fh) for r, Fh in zip(per, Fs)),
            )
        )
        loo = P.leave_one_out_joint(sets)
        e = np.array([v * Fs[i] for (i, _), v in loo.items()])
        log(
            '   joint leave-one-out p50 %.1f p90 %.1f px; this half worst: %s'
            % (
                np.median(e),
                np.percentile(e, 90),
                ', '.join(
                    '%s %.1f' % (n, v * F)
                    for (i, n), v in sorted(loo.items(), key=lambda kv: -kv[1])
                    if i == 0
                ),
            )
        )
        worst = {n: v * F for (i, n), v in loo.items() if i == 0}
        steps['joint'] = (C, R0, L, W)
    off = sorted((n for n, e in worst.items() if e > BAD_PIN_PX), key=lambda n: -worst[n])
    if off:
        raise BadPins(
            'the other pins disagree with ' + ', '.join('%s (%.0f px)' % (n, worst[n]) for n in off)
        )
    return dict(names=names, pose=(C, R0, L, W), k=k, steps=steps, worst=worst)


def calibrate(tag: str, others: list[str] = (), out_dir: Path | None = None, log=print) -> dict:
    """pin_cal.py --no-people: pins -> pose, then paint polishes the aim. Writes
    <out_dir>/<tag>_reg.npz and _venue.npz (default pincal/<tag>/cal) and pin_pose.json."""
    out = here(tag)
    out_dir = Path(out_dir or out / 'cal')
    spec = spec_of(tag)
    os.environ['PIQ_VIDEO'] = spec['video']
    # white on NB: without it the panorama reads gold paint and finds almost none
    A.LINES = spec['lines']
    S = load(out / 'solve.pkl')
    R, f, frames = S['R'], S['f'], list(S['frames'])

    fit = fit_pins(tag, others, log)
    names, (C, R0, L, W), k, steps, worst = (
        fit['names'],
        fit['pose'],
        fit['k'],
        fit['steps'],
        fit['worst'],
    )
    if others:
        R, f, miss = P.rescale_chain(R, f, k)
        log('   rotations and zooms rescaled by %.4f (worst grid miss %.2f px)' % (k, miss))

    G = AR._load(AR.WORK / tag / 'gather.pkl')
    packed = G.get('masks')
    pano = A.clean_pano(A.build_panorama(AR._Masks(packed, frames), R, f))
    pm = A.PitchModel(L=L, W=W)
    C2, R2, pm2, _ = A.refine_pano(pano, C, R0, pm, f_scale=25.0, fix_C=True, free_W=False)
    C2, R2, pm2, _ = A.refine_pano(pano, C2, R2, pm2, f_scale=3.0, fix_C=True, free_W=False)
    log(
        '3 aim:     %s; aim moved %.2f deg'
        % (P.describe(C2, pm2.L, pm2.W), np.degrees(np.linalg.norm(A.cv2.Rodrigues(R2 @ R0.T)[0])))
    )
    steps['aim'] = (C2, R2, pm2.L, pm2.W)
    pose = {
        st: dict(
            C=list(map(float, v[0])), R0=np.asarray(v[1]).tolist(), L=float(v[2]), W=float(v[3])
        )
        for st, v in steps.items()
    } | {'pin_names': names, 'with': list(others), 'k': k, 'worst_px': worst}
    (out / 'pin_pose.json').write_text(json.dumps(pose, indent=1))
    Qs = dict(frames=frames, R=R, f=f)
    out_dir.mkdir(parents=True, exist_ok=True)
    AR.write(tag, Qs, (C2, R2, pm2.L, pm2.W), out_dir, log, masks=packed)
    log('cal done -> %s' % out_dir)
    return pose


def install(tag: str) -> None:
    """The calibration into the tracker's folder (what the pass reads)."""
    for kind in ('reg', 'venue'):
        shutil.copyfile(here(tag) / 'cal' / f'{tag}_{kind}.npz', HOMOG / f'{tag}_{kind}.npz')


def calibrated(tag: str) -> bool:
    return all((here(tag) / 'cal' / f'{tag}_{k}.npz').exists() for k in ('reg', 'venue'))


# ================================================================ the site


# raw JPEG bytes a Firestore document carries (1 MiB a doc, base64 adds a third)
PART_BYTES = 700_000


def pano_parts(tag: str) -> tuple[dict, list[str]]:
    """The colour panorama as the site keeps it: a meta document and base64 parts."""
    meta = json.loads((here(tag) / 'pano_rgb.json').read_text())
    raw = (here(tag) / 'pano_rgb.jpg').read_bytes()
    parts = [
        base64.b64encode(raw[i : i + PART_BYTES]).decode('ascii')
        for i in range(0, len(raw), PART_BYTES)
    ]
    head = dict(w=meta['w'], h=meta['h'], parts=len(parts), bytes=len(raw), tag=tag)
    return head, parts


def _match_ref(client, team: str, match: str):
    return client.collection('teams').document(team).collection('matches').document(match)


def upload_pano(client, team: str, match: str, half: int, tag: str, needed: bool = True) -> None:
    """Put a half's panorama on the match for the coach to pin (cvPano/h<half>, parts
    h<half>_<i>). `needed` False marks a half whose pins are carried from the first."""
    from google.cloud import firestore

    head, parts = pano_parts(tag)
    col = _match_ref(client, team, match).collection('cvPano')
    for i, data in enumerate(parts):
        col.document(f'h{half}_{i}').set({'data': data})
    col.document(f'h{half}').set(
        head | {'half': half, 'needed': needed, 'problem': '', 'at': firestore.SERVER_TIMESTAMP}
    )


def mark_problem(client, team: str, match: str, half: int, text: str) -> None:
    """Tell the pin page what was wrong with the pins it saved."""
    _match_ref(client, team, match).collection('cvPano').document(f'h{half}').update(
        {'problem': text}
    )


def read_pins(client, team: str, match: str, half: int) -> dict | None:
    """The coach's pins for a half ({"pins": {name: [x, y]}} in panorama pixels), or None."""
    snap = _match_ref(client, team, match).collection('cvPins').document(f'h{half}').get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    pins = {
        n: [float(xy[0]), float(xy[1])]
        for n, xy in (d.get('pins') or {}).items()
        if n in P.NAMES and isinstance(xy, list) and len(xy) == 2
    }
    at = d.get('updatedAt')
    return {'pins': pins, 'at': at.isoformat() if hasattr(at, 'isoformat') else str(at)}


# ================================================================ command line


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.pincal', description=__doc__.split('\n')[0]
    )
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prep', help='gather, loop closure and the colour panorama of a half')
    p.add_argument('tag')
    p.add_argument('lo', type=int)
    p.add_argument('hi', type=int)
    p.add_argument('--lines', choices=('gold', 'white'), required=True)
    p = sub.add_parser('transfer', help="carry the first half's pins to the second")
    p.add_argument('src')
    p.add_argument('dst')
    p = sub.add_parser('cal', help='calibrate a half from its pins.json')
    p.add_argument('tag')
    p.add_argument(
        '--with', dest='others', nargs='+', default=[], help='the other half, pinned too'
    )
    p.add_argument('--install', action='store_true', help='copy into balltrack/pitch_registration')
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == 'prep':
        if not os.environ.get('PIQ_VIDEO'):
            raise SystemExit('set PIQ_VIDEO: unset, every stage would silently read the scrimmage')
        prep(a.tag, a.lo, a.hi, a.lines)
    elif a.cmd == 'transfer':
        t = transfer(a.src, a.dst)
        if t['ok'] and (here(a.src) / 'pins.json').exists():
            got = carry_pins(a.src, a.dst)
            (here(a.dst) / f'pins_from_{a.src}.json').write_text(json.dumps(got, indent=1))
            print(
                'carried %d pins -> %s'
                % (len(got['pins']), here(a.dst) / f'pins_from_{a.src}.json')
            )
        return 0 if t['ok'] else 1
    elif a.cmd == 'cal':
        calibrate(a.tag, a.others, log=logger(a.tag))
        if a.install:
            install(a.tag)
    return 0


if __name__ == '__main__':
    sys.exit(main())
