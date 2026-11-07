"""Fine-tune the shirt-number reader on the Number Lab taps (Woodbridge wb1 only, 2026-11-07).

The off-the-shelf SoccerNet reader (numread.py) reads ~30% of clips at 0 wrong: it was trained
on broadcast close-ups, and here a number is ~10 px tall. The user tapped, in 40 crops of each of
51 named wb1 clips, the crops where the whole number can be read (answers_wb1.json, 845 of 2040).
That trains both halves of the reader on this camera:

- legibility (ResNet-34): tapped vs untapped, whole crop;
- digits (PARSeq): the player's number on the tapped crops' torso; with --blank, also the end
  token alone on untapped crops, so "nothing readable" becomes an answer it can give.

    python -m pitchiq.experiments.numtrain oof [--blank] [--name oof]   # 5 person folds on wb1
    python -m pitchiq.experiments.numtrain oof --by window --name oofw  # 3 window folds on wb1
    python -m pitchiq.experiments.numtrain final [--blank]              # all wb1 -> read wb2 once
    python -m pitchiq.experiments.numread score --wb1 _oof              # thresholds on wb1 folds

Folds hold out whole people (kit + number), so each wb1 clip is read by a model that never saw
that person: stricter than wb2, which has the same two teams. wb2 is held out: never trained on.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib

import numpy as np
import torch
import torch.nn.functional as F

from pitchiq.experiments.numread import HELD_OUT, OUT, PAD_X, PAD_Y, TORSO, load_crops, read

LABDIR = OUT.parent / 'numlab'
FOLDS = 5
# digits half: epochs, learning rate, and whether only the image encoder learns (the decoder,
# which holds what digit sequences look like, kept as SoccerNet trained it)
CFG = dict(ep_str=15, lr_str=5e-5, enc_only=False)
SEED = 0


def samples():
    """(qid, crop index, readable, number) for every crop shown in the lab."""
    ans = json.loads((LABDIR / 'answers_wb1.json').read_text(encoding='utf-8'))
    meta = {
        g['id']: g
        for g in json.loads((LABDIR / 'site' / 'grids.json').read_text(encoding='utf-8'))['grids']
    }
    C = load_crops('wb1')
    out = []
    for qid, a in ans.items():
        assert meta[qid]['tag'] not in HELD_OUT
        shown = [
            c['f']
            for c in json.loads(
                (LABDIR / 'site' / 'grids' / f'{qid}.json').read_text(encoding='utf-8')
            )['crops']
        ]
        k_of = {int(f): k for k, f in enumerate(C[qid]['frame'])}
        yes = set(a['yes'])
        for f in shown:
            if f in a.get('other', []):
                continue  # another number readable: not this player's, not "unreadable" either
            out.append((qid, k_of[f], f in yes, meta[qid]['num']))
    return out, meta, C


def person(meta, qid):
    return meta[qid]['kit'], meta[qid]['num']


def fold_of(meta, qids, by='person'):
    """by='person': people to folds, round robin after a seeded shuffle; clips with no lab grid
    by a hash of the id. by='window': one fold per lab window (wb1_w06_... -> 'w06'), so every
    person is also trained on in other windows, as the same teams are in the other half."""
    if by == 'window':
        ws = sorted({q.split('_')[1] for q in qids})
        return {q: ws.index(q.split('_')[1]) for q in qids}
    people = sorted({person(meta, q) for q in meta}, key=lambda p: (p[0], int(p[1])))
    rng = np.random.default_rng(SEED)
    rng.shuffle(people)
    fp = {p: i % FOLDS for i, p in enumerate(people)}
    return {q: fp[person(meta, q)] if q in meta else zlib.crc32(q.encode()) % FOLDS for q in qids}


# ---------- augmentation ----------
def _jpg(b):
    import cv2

    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]


def _colour(im, rng):
    x = im.astype(np.float32)
    x = x * rng.uniform(0.75, 1.25) + rng.uniform(-20, 20)
    x = x * rng.uniform(0.9, 1.1, 3)
    if rng.random() < 0.3:  # softer / lower-res, as farther players are
        import cv2

        h, w = x.shape[:2]
        s = rng.uniform(0.5, 0.85)
        x = cv2.resize(
            cv2.resize(x, (max(4, int(w * s)), max(4, int(h * s))), interpolation=cv2.INTER_AREA),
            (w, h),
            interpolation=cv2.INTER_LINEAR,
        )
    return np.clip(x, 0, 255).astype(np.uint8)


def _torso(img, rng=None, j=0.04):
    H, W = img.shape[:2]
    bh, bw = H / (1 + 2 * PAD_Y), W / (1 + 2 * PAD_X)
    t = np.array(TORSO, float)
    if rng is not None:
        t = t + rng.uniform(-j, j, 4)
    y0, y1 = int(PAD_Y * bh + t[0] * bh), int(PAD_Y * bh + t[1] * bh)
    x0, x1 = int(PAD_X * bw + t[2] * bw), int(PAD_X * bw + t[3] * bw)
    y0, y1, x0, x1 = max(0, y0), min(H, y1), max(0, x0), min(W, x1)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return img
    return img[y0:y1, x0:x1]


def _whole(img, rng):
    H, W = img.shape[:2]
    m = rng.uniform(0, 0.06, 4)
    return img[int(m[0] * H) : H - int(m[1] * H), int(m[2] * W) : W - int(m[3] * W)]


def _tensor(ims, size, mode, dev):
    x = torch.cat(
        [
            F.interpolate(
                torch.from_numpy(np.ascontiguousarray(c)).permute(2, 0, 1)[None].float().to(dev)
                / 255,
                size=size,
                mode=mode,
                align_corners=False,
                antialias=True,
            )
            for c in ims
        ]
    )
    return x.clamp(0, 1)


# ---------- losses ----------
def parseq_loss(m, x, labels):
    """Teacher-forced loss on both decoding orders the reader uses: left to right, and the
    cloze refinement (every other position visible). Label '' = the end token alone."""
    B, L, dev = len(labels), 4, x.device
    tin = torch.full((B, L), m.pad, dtype=torch.long, device=dev)
    tin[:, 0] = m.bos
    tout = torch.full((B, L), -100, dtype=torch.long, device=dev)
    for b, s in enumerate(labels):
        ids = [int(c) + 1 for c in s] + [m.eos]
        tout[b, : len(ids)] = torch.tensor(ids, device=dev)
        if len(ids) > 1:
            tin[b, 1 : len(ids)] = torch.tensor(ids[:-1], device=dev)
    memory = m.encoder(x)
    pos = m.pos_queries[:, :L].expand(B, -1, -1)
    causal = torch.triu(torch.full((L, L), float('-inf'), device=dev), 1)
    cloze = causal.clone()
    cloze[torch.triu(torch.ones(L, L, dtype=torch.bool, device=dev), 2)] = 0
    pad = torch.zeros((B, L), device=dev).masked_fill(tin == m.pad, float('-inf'))
    loss = 0
    for mask, pm in ((causal, None), (cloze, pad)):
        lg = m.head(m._decode(tin, memory, pos, mask, pm))
        loss = loss + F.cross_entropy(lg.reshape(-1, lg.shape[-1]), tout.reshape(-1))
    return loss


def train(rows, C, blank=False, dev='cuda', ep_leg=6, log=print):
    """Fine-tuned state dicts {'parseq', 'leg'} from lab rows (qid, k, readable, number)."""
    from pitchiq.experiments.parseq_lite import load_legibility, load_parseq

    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    ims = {(q, k): _jpg(bytes(C[q]['jpg'][k])) for q, k, _, _ in rows}
    mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)

    # legibility: BatchNorm statistics kept as they are (small batches, little data)
    leg = load_legibility().to(dev).eval()
    opt = torch.optim.AdamW(leg.parameters(), lr=5e-5, weight_decay=1e-4)
    for ep in range(ep_leg):
        order, tot = rng.permutation(len(rows)), 0.0
        for i in range(0, len(order), 32):
            b = [rows[j] for j in order[i : i + 32]]
            x = _tensor(
                [_whole(_colour(ims[q, k], rng), rng) for q, k, _, _ in b],
                (256, 256),
                'bilinear',
                dev,
            )
            y = torch.tensor([float(r) for _, _, r, _ in b], device=dev)
            loss = F.binary_cross_entropy_with_logits(leg((x - mean) / std)[:, 0], y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += float(loss) * len(b)
        log(f'    legibility epoch {ep + 1}: loss {tot / len(rows):.3f}')

    ps = load_parseq().to(dev).eval()
    sr = [r for r in rows if r[2] or blank]
    learn = ps.encoder.parameters() if CFG['enc_only'] else ps.parameters()
    opt = torch.optim.AdamW(learn, lr=CFG['lr_str'], weight_decay=1e-4)
    for ep in range(CFG['ep_str']):
        order, tot = rng.permutation(len(sr)), 0.0
        for i in range(0, len(order), 64):
            b = [sr[j] for j in order[i : i + 64]]
            x = _tensor(
                [_torso(_colour(ims[q, k], rng), rng) for q, k, _, _ in b],
                ps.img_size,
                'bicubic',
                dev,
            )
            loss = parseq_loss(ps, (x - 0.5) / 0.5, [n if r else '' for _, _, r, n in b])
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(ps.parameters(), 1.0)
            opt.step()
            tot += float(loss) * len(b)
        log(f'    digits epoch {ep + 1}: loss {tot / len(sr):.3f}')
    return {
        'parseq': {k: v.detach().cpu() for k, v in ps.state_dict().items()},
        'leg': {k: v.detach().cpu() for k, v in leg.state_dict().items()},
    }


def crop_report(reader, rows, C, label):
    """Per-crop numbers on held lab crops: legibility split, digits right on readable crops."""
    ims = [_jpg(bytes(C[q]['jpg'][k])) for q, k, _, _ in rows]
    lg = np.concatenate([reader.legibility(ims[i : i + 256]) for i in range(0, len(ims), 256)])
    rd = []
    for i in range(0, len(ims), 256):
        rd += reader.digits([_torso(im) for im in ims[i : i + 256]])
    y = np.array([r for _, _, r, _ in rows])
    pos, neg = lg[y], lg[~y]
    auc = float((pos[:, None] > neg[None, :]).mean()) if len(pos) and len(neg) else float('nan')
    ok = sum(rd[i][0] == rows[i][3] for i in range(len(rows)) if rows[i][2])
    bad = sum(
        rd[i][0] != rows[i][3] and 1 <= len(rd[i][0]) <= 2 for i in range(len(rows)) if rows[i][2]
    )
    blank_unread = sum(rd[i][0] == '' for i in range(len(rows)) if not rows[i][2])
    print(
        f'  {label}: legibility AUC {auc:.3f}; readable crops read right {ok}/{y.sum()} '
        f'(wrong number {bad}); unreadable crops read as blank {blank_unread}/{(~y).sum()}',
        flush=True,
    )


def oof(blank=False, name='oof', by='person'):
    from pitchiq.experiments.parseq_lite import Reader

    rows, meta, C = samples()
    fq = fold_of(meta, list(C), by)
    base = Reader()
    parts = []
    for f in sorted(set(fq.values())):
        tr = [r for r in rows if fq[r[0]] != f]
        te = [r for r in rows if fq[r[0]] == f]
        ppl = sorted({person(meta, q) for q in meta if fq[q] == f})
        print(f'fold {f}: train {len(tr)} crops, test {len(te)} crops, people {ppl}', flush=True)
        sd = train(tr, C, blank)
        r = Reader(tuned=sd)
        crop_report(base, te, C, 'off the shelf')
        crop_report(r, te, C, 'fine-tuned   ')
        read('wb1', reader=r, qids={q for q in C if fq[q] == f}, name=f'wb1_{name}{f}')
        parts.append(np.load(OUT / f'reads_wb1_{name}{f}.npz'))
        del r
        torch.cuda.empty_cache()
    np.savez(
        OUT / f'reads_wb1_{name}.npz',
        **{k: np.concatenate([p[k] for p in parts]) for k in parts[0].files},
    )
    print(f'-> reads_wb1_{name}.npz', flush=True)


def final(blank=False, name='ft'):
    from pitchiq.experiments.parseq_lite import MODELS, Reader

    rows, meta, C = samples()
    print(f'final: train {len(rows)} wb1 crops', flush=True)
    sd = train(rows, C, blank)
    dst = MODELS / f'reader_{name}_wb1.pt'
    torch.save(sd, dst)
    print(f'-> {dst}', flush=True)
    read('wb2', final=True, reader=Reader(tuned=sd), name=f'wb2_{name}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['oof', 'final'])
    ap.add_argument('--blank', action='store_true')
    ap.add_argument('--name', default=None)
    ap.add_argument('--by', choices=['person', 'window'], default='person')
    ap.add_argument('--ep', type=int, default=CFG['ep_str'])
    ap.add_argument('--lr', type=float, default=CFG['lr_str'])
    ap.add_argument('--enc-only', action='store_true')
    a = ap.parse_args(argv)
    CFG.update(ep_str=a.ep, lr_str=a.lr, enc_only=a.enc_only)
    print('config', CFG, flush=True)
    if a.cmd == 'oof':
        oof(a.blank, a.name or 'oof', a.by)
    else:
        final(a.blank, a.name or 'ft')
    return 0


if __name__ == '__main__':
    sys.exit(main())
