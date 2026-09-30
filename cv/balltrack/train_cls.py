"""A second opinion on each detector candidate, from its pixels and its place.

The detector ranks ~40 candidates a frame and the true ball lands anywhere
from 1st to 30th, because a white cleat, a sock, a light-haired head or a
spare ball on the touchline scores as high as the 8-14 px match ball.  No
threshold on s separates them and no tracker can string together a ball it
is never shown first.  So each candidate is looked at again, alone, by a
model whose only job is "is THIS blob the ball", and that verdict replaces
(or joins) s as the emission the tracker reads.

INPUT.  crops.py writes, per clip, `<tag>crops.npz`: a 48x48 BGR crop at
native resolution centred on every candidate, plus a context vector (ctx)
of where it sits - people nearby, inside the pitch, distance to the nearest
player, the old score s and its rank.  Pixels alone cannot tell the match
ball from a spare one lying off the pitch; context alone cannot tell a ball
from a cleat at a player's feet.  Both go in.

MODEL.  The crop is centre-cropped to 32x32 (the ball is <=14 px; 32 keeps
a ring of grass/boot/leg around it, which is what separates a ball from a
boot toe).  Four conv blocks (conv3x3-BN-ReLU, the first three followed by
2x2 max pool) take it to a 4x4x64 map.  That map is read two ways and the
two are concatenated: the mean over all 16 cells (what surrounds it) and
the mean over the centre 2x2 (what the candidate itself is).  A pure global
pool would throw away "the white thing is IN THE MIDDLE", which is the only
thing that makes this crop about this candidate and not its neighbour.
ctx goes through Linear(K,32)-ReLU; the two are joined by Linear-ReLU-Linear
to 5 logits: ball, cleat, head, otherball, other.  Five classes rather than
one sigmoid because they are free (the labels exist) and because telling
the net WHY a thing is not the ball is more signal per labelled crop than
"no".  Tracker pseudo-labels arrive as "notball" (not the ball, reason
unknown); they are folded into "other", which is what "other" already
means, rather than given a sixth class no human label would share.
The score used downstream is the log-odds of ball:
logit_ball - logsumexp(other logits).  57,845 parameters with K=11 ctx
columns; a frame of 40 crops is one tiny batch.

TRAINING.  Class-weighted cross entropy: the ball class carries half the
total weight and the four non-ball classes the other half, split by their
counts, so a clip with 1 ball and 39 distractors per frame does not teach
"always say no".  Pseudo-labels carry their own `weight` multiplier.
A record with a `src` field is a pseudo-label; one without is a human's.
When both exist for the same (tag, f, j) the human's wins whatever the order.
Augmentation: horizontal flip (no vertical: light comes from above and a
cleat's sole is below it), +-3 px shift of the 32 window inside the 48 crop
(the detector's centre is itself off by a few px), and per-crop brightness
and contrast jitter (floodlight vs sun, shadow of the stand).

VALIDATION.  Frames of one clip are near copies of each other, so a random
split would score the net on crops it has effectively seen.  Folds are made
of whole clips: no tag is in both train and val.  Two numbers per fold:
  - ROC AUC ball-vs-rest over the labelled val crops;
  - the one that matters: in each val frame that has a hand-labelled ball,
    rank that ball among ALL of the frame's candidates (labelled or not)
    by the new score and by the old s.  Fraction at rank 0 and in the top 3.
    A tracker is fed the top of the list; AUC over pooled crops does not
    say whether the ball got there.
Both are reported twice: over every labelled row, and over human-labelled
rows only.  A pseudo-label (a record carrying `src`) is some model's
opinion, and grading against it grades agreement with that model - the
human line is the one to believe; the other is there because early on it
may be the only one with enough frames to say anything.  Folds run a fixed epoch count and report the
last epoch - no picking the best epoch on val.

    python train_cls.py train --tags tr00,tr01,tr02 [--epochs 30] [--out cls.pt]
    python train_cls.py score tr00 --model cls.pt       # -> tr00cls.npz

    from train_cls import load_model
    m = load_model('cls.pt'); p = m.score(crops_uint8, ctx)   # log-odds
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

S2 = os.path.dirname(os.path.abspath(__file__))

CLASSES = ['ball', 'cleat', 'head', 'otherball', 'other']
CIDX = {c: i for i, c in enumerate(CLASSES)}
CIDX['notball'] = CIDX['other']   # tracker pseudo-negatives: not ball, why unknown
BALL = CIDX['ball']
WIN = 32          # side of the window the net sees, cut from the 48 crop
SHIFT = 3         # max +- px of train-time shift of that window
CTX_CLIP = 5.0    # standardised ctx is clipped to +-this (dist_px can be huge)


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def device():
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')


# --------------------------------------------------------------------- data

def load_crops(tag):
    """Everything in <tag>crops.npz, as plain arrays."""
    z = np.load(os.path.join(S2, '%scrops.npz' % tag), allow_pickle=True)
    d = dict(f=z['f'].astype(np.int64), j=z['j'].astype(np.int64),
             s=z['s'].astype(np.float32), crop=z['crop'],
             ctx=np.asarray(z['ctx'], np.float32))
    d['ctx_names'] = [str(n) for n in z['ctx_names']]
    assert d['crop'].ndim == 4 and d['crop'].shape[-1] == 3, d['crop'].shape
    assert d['crop'].shape[1] >= WIN + 2 * SHIFT, 'crop smaller than window+shift'
    return d


def load_labels(path):
    """{(tag, f, j): (class index, weight, hand)}.

    hand = the record has no `src`.  A human record beats a pseudo one for
    the same key; otherwise the later record wins.
    """
    with open(path) as fh:
        recs = json.load(fh)
    out = {}
    bad = 0
    for r in recs:
        lab = r['label']
        if lab not in CIDX:
            bad += 1
            continue
        hand = 'src' not in r
        w = float(r.get('weight', 1.0))
        key = (str(r['tag']), int(r['f']), int(r['j']))
        old = out.get(key)
        if old is not None and old[2] and not hand:
            continue
        out[key] = (CIDX[lab], w, hand)
    if bad:
        print('labels: %d records with an unknown label skipped' % bad)
    return out


def join(tag, d, labels):
    """Row indices of d that carry a label, and their (y, w, hand)."""
    rows, y, w, hand = [], [], [], []
    for i, (f, j) in enumerate(zip(d['f'], d['j'])):
        v = labels.get((tag, int(f), int(j)))
        if v is not None:
            rows.append(i); y.append(v[0]); w.append(v[1]); hand.append(v[2])
    return (np.asarray(rows, np.int64), np.asarray(y, np.int64),
            np.asarray(w, np.float32), np.asarray(hand, bool))


def reorder_ctx(ctx, have, want):
    """ctx columns named `have` -> columns in the order `want`."""
    if list(have) == list(want):
        return ctx
    missing = [n for n in want if n not in have]
    assert not missing, 'ctx is missing %s' % missing
    return ctx[:, [list(have).index(n) for n in want]]


def ctx_stats(ctx):
    x = np.nan_to_num(ctx.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    mu = x.mean(0)
    sd = x.std(0)
    sd[sd < 1e-6] = 1.0
    return mu.astype(np.float32), sd.astype(np.float32)


def ctx_norm(ctx, mu, sd):
    x = np.nan_to_num(ctx.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip((x - mu) / sd, -CTX_CLIP, CTX_CLIP).astype(np.float32)


# -------------------------------------------------------------------- model

def block(cin, cout, pool):
    layers = [nn.Conv2d(cin, cout, 3, padding=1, bias=False),
              nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]
    if pool:
        layers.append(nn.MaxPool2d(2))
    return nn.Sequential(*layers)


class BallNet(nn.Module):
    def __init__(self, k_ctx, n_cls=len(CLASSES), w=(16, 32, 48, 64)):
        super().__init__()
        self.conv = nn.Sequential(block(3, w[0], True),      # 32 -> 16
                                  block(w[0], w[1], True),   # 16 -> 8
                                  block(w[1], w[2], True),   # 8 -> 4
                                  block(w[2], w[3], False))  # 4x4x64
        self.ctx = nn.Sequential(nn.Linear(k_ctx, 32), nn.ReLU(inplace=True))
        self.head = nn.Sequential(nn.Linear(2 * w[3] + 32, 64),
                                  nn.ReLU(inplace=True), nn.Dropout(0.2),
                                  nn.Linear(64, n_cls))

    def forward(self, img, ctx):
        m = self.conv(img)                       # B x C x 4 x 4
        g = m.mean((2, 3))                       # surroundings
        c = m[:, :, 1:3, 1:3].mean((2, 3))       # the candidate itself
        return self.head(torch.cat([g, c, self.ctx(ctx)], 1))


def ball_logodds(logits):
    """log P(ball) - log P(not ball), from 5-class logits."""
    rest = torch.cat([logits[:, :BALL], logits[:, BALL + 1:]], 1)
    return logits[:, BALL] - torch.logsumexp(rest, 1)


def to_input(crops_u8, dev, oy=None, ox=None):
    """uint8 N x H x W x 3 (BGR) -> float N x 3 x WIN x WIN on dev.

    oy/ox: per-row top-left of the window (train-time shift); centred if None.
    """
    t = torch.as_tensor(crops_u8, device=dev)
    n, h, w = t.shape[0], t.shape[1], t.shape[2]
    if oy is None:
        y0, x0 = (h - WIN) // 2, (w - WIN) // 2
        t = t[:, y0:y0 + WIN, x0:x0 + WIN]
    else:
        ar = torch.arange(WIN, device=dev)
        ry = (torch.as_tensor(oy, device=dev)[:, None] + ar)[:, :, None]
        rx = (torch.as_tensor(ox, device=dev)[:, None] + ar)[:, None, :]
        b = torch.arange(n, device=dev)[:, None, None]
        t = t[b, ry, rx]
    return t.permute(0, 3, 1, 2).float().div_(255.0).sub_(0.5)


def augment(crops_u8, dev, rng):
    n, h, w = crops_u8.shape[:3]
    cy, cx = (h - WIN) // 2, (w - WIN) // 2
    oy = cy + rng.integers(-SHIFT, SHIFT + 1, n)
    ox = cx + rng.integers(-SHIFT, SHIFT + 1, n)
    x = to_input(crops_u8, dev, oy, ox)
    flip = torch.as_tensor(rng.random(n) < 0.5, device=dev)
    x = torch.where(flip[:, None, None, None], x.flip(3), x)
    contrast = torch.as_tensor(rng.uniform(0.75, 1.25, n), device=dev,
                               dtype=torch.float32)[:, None, None, None]
    bright = torch.as_tensor(rng.uniform(-0.12, 0.12, n), device=dev,
                             dtype=torch.float32)[:, None, None, None]
    mean = x.mean((1, 2, 3), keepdim=True)
    return (x - mean) * contrast + mean + bright


def n_params(m):
    return sum(p.numel() for p in m.parameters())


# ------------------------------------------------------------------ scoring

class Scorer:
    """A trained BallNet plus the ctx normalisation it was trained with."""

    def __init__(self, net, mu, sd, ctx_names, dev):
        self.net, self.mu, self.sd = net.eval(), mu, sd
        self.ctx_names, self.dev = list(ctx_names), dev

    @torch.no_grad()
    def score(self, crops_uint8, ctx, ctx_names=None, batch=2048):
        """-> float32 log-odds of ball, one per row.

        crops_uint8: N x H x W x 3 BGR, H,W >= 32 (the centre 32 is used).
        ctx: N x K raw (unnormalised) context; if ctx_names is given the
        columns are reordered to the trained order, else they must match it.
        """
        n = len(crops_uint8)
        if n == 0:
            return np.zeros(0, np.float32)
        if ctx_names is not None:
            ctx = reorder_ctx(np.asarray(ctx), ctx_names, self.ctx_names)
        c = ctx_norm(np.asarray(ctx), self.mu, self.sd)
        out = np.empty(n, np.float32)
        for a in range(0, n, batch):
            x = to_input(np.ascontiguousarray(crops_uint8[a:a + batch]), self.dev)
            k = torch.as_tensor(c[a:a + batch], device=self.dev)
            out[a:a + batch] = ball_logodds(self.net(x, k)).float().cpu().numpy()
        return out

    @torch.no_grad()
    def probs(self, crops_uint8, ctx, ctx_names=None, batch=2048):
        """-> N x len(CLASSES) float32 softmax, same inputs as score()."""
        if ctx_names is not None:
            ctx = reorder_ctx(np.asarray(ctx), ctx_names, self.ctx_names)
        c = ctx_norm(np.asarray(ctx), self.mu, self.sd)
        out = np.empty((len(crops_uint8), len(CLASSES)), np.float32)
        for a in range(0, len(crops_uint8), batch):
            x = to_input(np.ascontiguousarray(crops_uint8[a:a + batch]), self.dev)
            k = torch.as_tensor(c[a:a + batch], device=self.dev)
            out[a:a + batch] = torch.softmax(self.net(x, k), 1).float().cpu().numpy()
        return out


def load_model(path, dev=None):
    dev = dev or device()
    ck = torch.load(path, map_location=dev, weights_only=False)  # our own file
    net = BallNet(len(ck['ctx_names'])).to(dev)
    net.load_state_dict(ck['state'])
    return Scorer(net, np.asarray(ck['mu'], np.float32),
                  np.asarray(ck['sd'], np.float32), ck['ctx_names'], dev)


# ----------------------------------------------------------------- training

def class_weights(y):
    """Ball gets half the total weight, the non-ball classes share the rest."""
    cnt = np.bincount(y, minlength=len(CLASSES)).astype(np.float64)
    n_ball, n_rest = cnt[BALL], cnt.sum() - cnt[BALL]
    cw = np.zeros(len(CLASSES))
    if n_ball > 0:
        cw[BALL] = 0.5 * len(y) / n_ball
    if n_rest > 0:
        for i in range(len(CLASSES)):
            if i != BALL and cnt[i] > 0:
                cw[i] = 0.5 * len(y) / n_rest
    return cw.astype(np.float32)


def fit(crops, ctx, y, w, epochs, dev, seed, bs=256, lr=2e-3, log=''):
    """Train a fresh BallNet on normalised ctx; returns the net."""
    seed_all(seed)
    rng = np.random.default_rng(seed)
    net = BallNet(ctx.shape[1]).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    steps = max(1, epochs * ((len(y) + bs - 1) // bs))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps,
                                                pct_start=0.15)
    sw = w * class_weights(y)[y]
    ctx_t = torch.as_tensor(ctx, device=dev)
    y_t = torch.as_tensor(y, device=dev)
    sw_t = torch.as_tensor(sw, device=dev)
    for ep in range(epochs):
        net.train()
        perm = rng.permutation(len(y))
        tot, t0 = 0.0, time.time()
        for a in range(0, len(perm), bs):
            b = perm[a:a + bs]
            if len(b) < 2:           # BatchNorm cannot train on one sample
                continue
            x = augment(crops[b], dev, rng)
            bt = torch.as_tensor(b, device=dev)
            logits = net(x, ctx_t[bt])
            loss = (F.cross_entropy(logits, y_t[bt], reduction='none')
                    * sw_t[bt]).sum() / sw_t[bt].sum()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(b)
        if ep == 0 or (ep + 1) % 5 == 0 or ep + 1 == epochs:
            print('%s  epoch %2d/%d  loss %.4f  %.1fs' % (log, ep + 1, epochs,
                                                         tot / len(y), time.time() - t0))
    return net.eval()


def auc(pos, neg):
    """Mann-Whitney ROC AUC with ties counted half."""
    if len(pos) == 0 or len(neg) == 0:
        return float('nan')
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind='mergesort')
    ranks = np.empty(len(allv))
    sv = allv[order]
    i = 0
    while i < len(sv):            # average ranks over ties
        k = i
        while k + 1 < len(sv) and sv[k + 1] == sv[i]:
            k += 1
        ranks[order[i:k + 1]] = 0.5 * (i + k) + 1
        i = k + 1
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def frame_ranks(f, score, is_ball):
    """For every frame with a ball row: rank (0 = top) of its best ball row
    among all rows of that frame.  Ties count against the ball."""
    out = []
    for fr in np.unique(f[is_ball]):
        m = f == fr
        sc, bl = score[m], is_ball[m]
        best = sc[bl].max()
        out.append(int((sc[~bl] >= best).sum()))
    return np.asarray(out, np.int64)


def rank_line(name, r):
    if len(r) == 0:
        return '    %-4s  no frames' % name
    return ('    %-4s  rank0 %5.1f%%  top3 %5.1f%%  median %4.1f  mean %5.2f  (%d frames)'
            % (name, 100 * np.mean(r == 0), 100 * np.mean(r < 3),
               np.median(r), r.mean(), len(r)))


def evaluate(scorer, data, tags):
    """{'all': ..., 'hand': ...}: AUC over labelled crops and per-frame ball
    rank by new score vs old s, over all labels and over human labels only.
    Ranks are always among ALL of the frame's candidates."""
    acc = {k: dict(ps=[], ns=[], rn=[], ro=[]) for k in ('all', 'hand')}
    for t in tags:
        d = data[t]
        p = scorer.score(d['crop'], d['ctx'], d['ctx_names'])
        rows, y, _, hand = d['lab']
        for k, keep in (('all', np.ones(len(rows), bool)), ('hand', hand)):
            r, yy, A = rows[keep], y[keep], acc[k]
            A['ps'].append(p[r[yy == BALL]]); A['ns'].append(p[r[yy != BALL]])
            is_ball = np.zeros(len(p), bool)
            is_ball[r[yy == BALL]] = True
            if is_ball.any():
                A['rn'].append(frame_ranks(d['f'], p, is_ball))
                A['ro'].append(frame_ranks(d['f'], d['s'], is_ball))
    cat = lambda v: np.concatenate(v) if v else np.zeros(0)
    out = {}
    for k, A in acc.items():
        pos, neg = cat(A['ps']), cat(A['ns'])
        out[k] = dict(auc=auc(pos, neg), n_pos=len(pos), n_neg=len(neg),
                      r_new=cat(A['rn']).astype(np.int64),
                      r_old=cat(A['ro']).astype(np.int64))
    return out


def gather(data, tags, names):
    crops, ctx, y, w = [], [], [], []
    for t in tags:
        d = data[t]
        rows, yy, ww, _ = d['lab']
        crops.append(d['crop'][rows])
        ctx.append(reorder_ctx(d['ctx'], d['ctx_names'], names)[rows])
        y.append(yy); w.append(ww)
    return (np.concatenate(crops), np.concatenate(ctx),
            np.concatenate(y), np.concatenate(w))


def train_on(data, tags, names, epochs, dev, seed, log):
    crops, ctx, y, w = gather(data, tags, names)
    mu, sd = ctx_stats(ctx)
    net = fit(crops, ctx_norm(ctx, mu, sd), y, w, epochs, dev, seed, log=log)
    return Scorer(net, mu, sd, names, dev)


def cmd_train(a):
    dev = device()
    seed_all(a.seed)
    tags = [t for t in a.tags.split(',') if t]
    labels = load_labels(a.labels)
    data, names = {}, None
    for t in tags:
        d = load_crops(t)
        names = names or d['ctx_names']
        d['lab'] = join(t, d, labels)
        rows, y, _, hand = d['lab']
        cnt = np.bincount(y, minlength=len(CLASSES))
        print('%-8s %6d crops  %5d labelled (%d hand)  %s' % (
            t, len(d['f']), len(rows), hand.sum(),
            ' '.join('%s=%d' % (c, n) for c, n in zip(CLASSES, cnt))))
        data[t] = d
    tags = [t for t in tags if len(data[t]['lab'][0])]
    assert tags, 'no labelled crops in any listed tag'
    print('device %s   ctx %d: %s' % (dev, len(names), ','.join(names)))
    print('BallNet params: %d' % n_params(BallNet(len(names))))

    k = min(a.folds, len(tags))
    if k >= 2:
        order = list(tags)
        random.Random(a.seed).shuffle(order)
        folds = [order[i::k] for i in range(k)]
        pooled = {k: dict(rn=[], ro=[]) for k in ('all', 'hand')}
        for i, val in enumerate(folds):
            tr = [t for t in tags if t not in val]
            sc = train_on(data, tr, names, a.epochs, dev, a.seed + i, 'fold %d' % i)
            ev = evaluate(sc, data, val)
            print('fold %d  val=%s' % (i, ','.join(val)))
            for k in ('all', 'hand'):
                e = ev[k]
                if e['n_pos'] + e['n_neg'] == 0:
                    print('  %-4s labels: none' % k)
                    continue
                print('  %-4s labels: AUC %.4f  (%d ball / %d not)' % (
                    k, e['auc'], e['n_pos'], e['n_neg']))
                print(rank_line('new', e['r_new']))
                print(rank_line('s', e['r_old']))
                pooled[k]['rn'].append(e['r_new']); pooled[k]['ro'].append(e['r_old'])
        for k in ('all', 'hand'):
            if pooled[k]['rn']:
                print('all folds, %s labels' % k)
                print(rank_line('new', np.concatenate(pooled[k]['rn'])))
                print(rank_line('s', np.concatenate(pooled[k]['ro'])))
    else:
        print('only %d labelled tag(s): no leave-clips-out validation' % len(tags))

    sc = train_on(data, tags, names, a.epochs, dev, a.seed, 'all')
    out = a.out if os.path.isabs(a.out) else os.path.join(S2, a.out)
    torch.save(dict(state=sc.net.state_dict(), mu=sc.mu, sd=sc.sd,
                    ctx_names=list(names), classes=CLASSES, win=WIN,
                    tags=tags, epochs=a.epochs, seed=a.seed), out)
    print('wrote %s' % out)


def cmd_score(a):
    seed_all(0)
    path = a.model if os.path.isabs(a.model) else os.path.join(S2, a.model)
    m = load_model(path)
    d = load_crops(a.tag)
    t0 = time.time()
    p = m.score(d['crop'], d['ctx'], d['ctx_names'])
    dt = time.time() - t0
    out = os.path.join(S2, '%scls.npz' % a.tag)
    np.savez(out, f=d['f'].astype(np.int32), j=d['j'].astype(np.int16),
             p=p.astype(np.float32))
    print('%s: %d crops scored in %.2fs (%.0f crops/s) -> %s' % (
        a.tag, len(p), dt, len(p) / max(dt, 1e-9), out))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('train')
    t.add_argument('--tags', required=True, help='comma separated clip tags')
    t.add_argument('--labels', default=os.path.join(S2, 'cls_labels.json'))
    t.add_argument('--epochs', type=int, default=30)
    t.add_argument('--folds', type=int, default=4)
    t.add_argument('--seed', type=int, default=0)
    t.add_argument('--out', default='cls.pt')
    s = sub.add_parser('score')
    s.add_argument('tag')
    s.add_argument('--model', default='cls.pt')
    a = ap.parse_args(argv)
    {'train': cmd_train, 'score': cmd_score}[a.cmd](a)


if __name__ == '__main__':
    main(sys.argv[1:])
