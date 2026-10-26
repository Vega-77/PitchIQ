"""The ball classifier (cls_B.pt's job) retrained on Ball Clicker answers as well as the human crops.

Each clicked dev frame gives one crop set: the frame's top-KEEP candidates under the ranker
(train_ranker.py's blobs and scores), cut from a cached copy of the frame with candidate_crops'
own cut() and context(), so they are what score_candidates would cut there.

  ball      the candidate nearest a 'ball' click, if within R px
  other     every candidate further than 2R from the click, and every candidate on an 'out' frame
  skipped   candidates between the two (a second blob on the same ball), and 'dead' / 'cant' /
            'hidden' answers (train_ranker.USE)

The 1,520 human crops in crop_labels/crops.jsonl (tags tr00..tr16) are always in training.

    python -m pitchiq.balltrack.train_click_classifier build <feats.npz> <frames dir> <items.json> <prefix>
        (--oof oof.npz | --model ranker.model.json) [--affine ranker.model.json.affine.json]
        -> <prefix><half>crops.npz + <prefix>labels.json
    python -m pitchiq.balltrack.train_click_classifier logo <prefix> <items.json> [--epochs 30]
    python -m pitchiq.balltrack.train_click_classifier train <prefix> <out.pt> [--epochs 30] [--cal cal.json]

The ranker scores go through the trained ranker's affine so the ctx column s is on rank6's scale;
build --oof takes that same affine for the out-of-fold margins.

train --cal takes {a, b}: the checkpoint's log-odds become a * raw + b (Scorer applies it).  cls_C's
came from matching the quantiles of each dev frame's top score, leave-one-game-out, to cls_B's, so
the tracker's thresholds (ring 2, ballgate's LOST_P) keep their meaning; it uses no labels.
"""

import argparse
import glob
import json
import os
from collections import Counter, defaultdict

import numpy as np

from . import candidate_crops as cc
from .candidate_features import KEEP
from .train_ranker import R, game

S2 = os.path.dirname(os.path.abspath(__file__))
HUMAN = ['tr%02d' % i for i in range(17)]
CONF = 2.0  # log-odds: a ring this sure is drawn (clickstages' "cls > 2")


def human_labels():
    """crop_labels/crops.jsonl as label records (the 'unsure' ones left out)."""
    out = []
    for line in open(os.path.join(S2, 'crop_labels', 'crops.jsonl')):
        d = json.loads(line)[1]
        if d['label'] != 'unsure':
            out.append(dict(tag=d['tag'], f=int(d['f']), j=int(d['j']), label=d['label']))
    return out


def load_ppl_pieces(half, frames):
    """Person boxes at the given abs frames of a half, from its pieces' <half>_qNNNppl.npz."""
    want = np.array(sorted(set(frames)), np.int64)
    out = {}
    for p in sorted(glob.glob(os.path.join(S2, '%s_q[0-9][0-9][0-9]ppl.npz' % half))):
        z = np.load(p)
        m = (z['conf'] >= cc.MINCONF) & np.isin(z['f'], want)
        f, box = z['f'][m], z['box'][m].astype(np.float64)
        for ab in np.unique(f):
            out.setdefault(int(ab), box[f == ab])
    return out


def build(feats, frames_dir, items_path, prefix, oof=None, model=None, affine=None):
    """Crops + labels for every clicked frame in feats, ranked by the out-of-fold margins (oof)
    or by a trained ranker (model), then mapped through affine (a json {a, b}) when given."""
    import cv2
    import xgboost as xgb

    z = dict(np.load(feats))
    items = json.load(open(items_path))
    if oof:
        s = np.load(oof)['s'].astype(np.float64)
    else:
        b = xgb.Booster()
        b.load_model(model)
        s = b.predict(xgb.DMatrix(z['X']), output_margin=True).astype(np.float64)
    aff = json.load(open(affine)) if affine else dict(a=1.0, b=0.0)
    s = aff['a'] * s + aff['b']
    labels = []
    by_half = defaultdict(list)
    for k, iid in enumerate(z['ids']):
        by_half[str(z['half'][k])].append(k)
    for half, ks in sorted(by_half.items()):
        ppl = load_ppl_pieces(half, [int(items[str(z['ids'][k])]['f']) for k in ks])
        tag = prefix + half
        F, J, X, Y, S, C, K = [], [], [], [], [], [], []
        cnt = Counter()
        for k in ks:
            iid = str(z['ids'][k])
            it = items[iid]
            rows = np.flatnonzero(z['item'] == k)
            if not len(rows):
                continue
            top = rows[np.argsort(-s[rows], kind='stable')[:KEEP]]
            img = cv2.imread(os.path.join(frames_dir, iid + '.png'))
            f = int(it['f'])
            x, y, sc = z['pts'][top, 0], z['pts'][top, 1], s[top].astype(np.float32)
            n = len(top)
            crops = np.zeros((n, 48, 48, 3), np.uint8)
            for j in range(n):
                cc.cut(img, x[j], y[j], 48, crops[j])
            boxes = ppl.get(f)
            ctx = cc.context(x, y, sc, boxes, True)
            F.append(np.full(n, f, np.int32))
            J.append(np.arange(n, dtype=np.int16))
            X.append(x)
            Y.append(y)
            S.append(sc)
            C.append(crops)
            K.append(ctx)
            d = np.hypot(x - it['x'], y - it['y']) if it['v'] == 'ball' else np.full(n, np.inf)
            jb = int(np.argmin(d)) if it['v'] == 'ball' else -1
            for j in range(n):
                if it['v'] == 'ball' and j == jb and d[j] <= R:
                    lab = 'ball'
                elif d[j] > 2 * R:
                    lab = 'other'
                else:
                    continue
                cnt[lab] += 1
                labels.append(dict(tag=tag, f=f, j=j, label=lab, item=iid))
        np.savez_compressed(
            os.path.join(S2, '%scrops.npz' % tag),
            f=np.concatenate(F),
            j=np.concatenate(J),
            x=np.concatenate(X),
            y=np.concatenate(Y),
            s=np.concatenate(S),
            crop=np.concatenate(C),
            ctx=np.concatenate(K),
            ctx_names=np.array(cc.CTX_NAMES),
            size=48,
            tag=tag,
        )
        print('%-10s %d frames  %s' % (tag, len(ks), dict(cnt)), flush=True)
    json.dump(labels, open(os.path.join(S2, '%slabels.json' % prefix), 'w'))


def _data(prefix):
    from . import train_ball_classifier as T

    recs = json.load(open(os.path.join(S2, '%slabels.json' % prefix))) + human_labels()
    lp = os.path.join(S2, '%slabels_all.json' % prefix)
    json.dump(recs, open(lp, 'w'))
    labels = T.load_labels(lp)
    ck = sorted({r['tag'] for r in recs if r['tag'].startswith(prefix)})
    data, names = {}, None
    for t in HUMAN + ck:
        d = T.load_crops(t)
        names = names or d['ctx_names']
        d['lab'] = T.join(t, d, labels)
        data[t] = d
    item = {(r['tag'], r['f']): r['item'] for r in recs if 'item' in r}
    return data, names, ck, item


def frame_eval(sc, data, tags, item, answers, rec=None):
    """Per half: ball frames with the clicked candidate at rank 1 / with p > CONF; out frames with top p > CONF.
    rec: a list to append one record per frame to (half, item, answer, top p, the ball's p and rank)."""
    res = defaultdict(Counter)
    for t in tags:
        d = data[t]
        p = sc.score(d['crop'], d['ctx'])
        rows, y, _, _ = d['lab']
        ball_row = {int(d['f'][r]): r for r, c in zip(rows, y) if c == 0}
        for f in np.unique(d['f']):
            m = np.flatnonzero(d['f'] == f)
            iid = item.get((t, int(f)))
            v = answers.get(iid)
            h = t
            if rec is not None:
                r = ball_row.get(int(f))
                rec.append(
                    dict(
                        half=t,
                        item=iid,
                        v=v,
                        top=float(p[m].max()),
                        ball=None if r is None else float(p[r]),
                        rank=None if r is None else int((p[m] > p[r]).sum()),
                    )
                )
            if v == 'out':
                res[h]['out'] += 1
                res[h]['out conf'] += int(p[m].max() > CONF)
            elif v == 'ball':
                res[h]['ball'] += 1
                r = ball_row.get(int(f))
                if r is None:
                    continue
                res[h]['in top 40'] += 1
                rk = int((p[m] > p[r]).sum())
                res[h]['rank 1'] += int(rk == 0)
                res[h]['p > 2'] += int(p[r] > CONF)
                res[h]['rank1 & p > 2'] += int(rk == 0 and p[r] > CONF)
            # a ball frame whose top pick is a confident wrong candidate
            if v == 'ball':
                r = ball_row.get(int(f))
                top = m[int(np.argmax(p[m]))]
                res[h]['wrong conf top'] += int(p[top] > CONF and top != r)
    return res


def logo(prefix, items_path, epochs):
    from . import train_ball_classifier as T

    dev = T.device()
    data, names, ck, item = _data(prefix)
    answers = {k: v['v'] for k, v in json.load(open(items_path)).items()}
    games = sorted({game(t[len(prefix) :]) for t in ck})
    tot = Counter()
    allres = {}
    rec = []
    for i, g in enumerate(games):
        val = [t for t in ck if game(t[len(prefix) :]) == g]
        tr = HUMAN + [t for t in ck if t not in val]
        tr = [t for t in tr if len(data[t]['lab'][0])]
        T.seed_all(i)
        sc = T.train_on(data, tr, names, epochs, dev, i, 'leave out %s' % g)
        res = frame_eval(sc, data, val, item, answers, rec)
        for h, c in sorted(res.items()):
            print('%-10s %s' % (h, dict(c)), flush=True)
            tot.update(c)
            allres[h] = dict(c)
    print('ALL', dict(tot))
    json.dump(
        dict(halves=allres, frames=rec),
        open(os.path.join(S2, '%slogo.json' % prefix), 'w'),
        indent=0,
    )


def train(prefix, out, epochs, cal=None):
    import torch

    from . import train_ball_classifier as T

    dev = T.device()
    data, names, ck, _ = _data(prefix)
    tags = [t for t in HUMAN + ck if len(data[t]['lab'][0])]
    T.seed_all(0)
    sc = T.train_on(data, tags, names, epochs, dev, 0, 'all')
    c = json.load(open(cal)) if cal else dict(a=1.0, b=0.0)
    torch.save(
        dict(
            state=sc.net.state_dict(),
            mu=sc.mu,
            sd=sc.sd,
            ctx_names=list(names),
            tags=tags,
            epochs=epochs,
            cal=(c['a'], c['b']),
        ),
        out if os.path.isabs(out) else os.path.join(S2, out),
    )
    print('saved', out)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('build')
    b.add_argument('feats')
    b.add_argument('frames')
    b.add_argument('items')
    b.add_argument('prefix')
    b.add_argument('--oof')
    b.add_argument('--model')
    b.add_argument('--affine')
    lg = sp.add_parser('logo')
    lg.add_argument('prefix')
    lg.add_argument('items')
    lg.add_argument('--epochs', type=int, default=30)
    t = sp.add_parser('train')
    t.add_argument('prefix')
    t.add_argument('out')
    t.add_argument('--epochs', type=int, default=30)
    t.add_argument('--cal')
    o = ap.parse_args()
    if o.cmd == 'build':
        build(o.feats, o.frames, o.items, o.prefix, o.oof, o.model, o.affine)
    elif o.cmd == 'logo':
        logo(o.prefix, o.items, o.epochs)
    else:
        train(o.prefix, o.out, o.epochs, o.cal)


if __name__ == '__main__':
    main()
