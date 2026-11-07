"""Shirt-number reader, scored on the Player ID Lab answers (Woodbridge, 2026-11-07).

A whole-game identity needs a signal that does not drift: appearance and place in the team
narrow a player to a few people (idscore.py), the number on his back decides. A wrong number
joins two people for the rest of the game, so the reader must abstain rather than guess:
it is judged on how many clips it reads wrong (should be ~0) before how many it reads at all.

    python -m pitchiq.experiments.numread crops <tag> [--final]   # player crops, every 2nd frame
    python -m pitchiq.experiments.numread read <tag> [--final]    # per-crop reads (cached)
    python -m pitchiq.experiments.numread score [--final]         # per clip and per person

Crops come from the same linker run as the lab (it draws nothing at random), so each crop is
exactly the box the user was shown. A lab answer is true only for the 12 s clip stretch; reads
outside it are kept (flag `in_clip`) for later, not scored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
LAB = REPO.parent / 'PitchIQ-data' / 'idlab'
OUT = REPO.parent / 'PitchIQ-data' / 'numread'
HELD_OUT = {'nb2', 'pi1', 'wh1', 'co1', 'wb2'}
STEP = 2  # every 2nd frame: a turn that shows the number lasts longer than that
PAD_X, PAD_Y = 0.15, 0.05  # crop margin around the box, of its width / height


def questions(tag):
    return json.loads((LAB / 'site/sets' / tag / 'questions.json').read_text(encoding='utf-8'))[
        'questions'
    ]


def crops(tag, final=False):
    """Every 2nd frame of each lab identity: the box crop at native resolution, as JPEG."""
    import cv2

    if tag in HELD_OUT and not final:
        raise SystemExit(f'{tag} is held out: scored once, at the end (--final)')
    from pitchiq.experiments.wholemap import video_of

    v = video_of(tag)
    if v:
        os.environ['PIQ_VIDEO'] = v  # before player_lab is imported: unset it reads the scrimmage
    sys.path.insert(0, str(REPO))
    from pitchiq.experiments import player_lab
    from pitchiq.experiments.spotlab import linked

    qs = questions(tag)
    det, meta = player_lab.load_detections(tag)
    spans = sorted({(q['lo'], q['hi']) for q in qs})
    jobs = {}  # frame -> [(qid, box, in_clip)]
    for (lo, hi), res in zip(spans, linked(tag, det, meta, spans)):
        by = {i.pid: i for i in res.identities}
        for q in (q for q in qs if (q['lo'], q['hi']) == (lo, hi)):
            ident = by[q['pid']]
            assert (int(ident.frames[0]), int(ident.frames[-1])) == (q['first'], q['last']), (
                'linker drifted: ' + q['id']
            )
            c0, c1 = min(q['clip']['frames']), max(q['clip']['frames'])
            for k in range(0, len(ident.frames), STEP):
                if ident.interp[k]:
                    continue
                f = int(ident.frames[k])
                jobs.setdefault(f, []).append(
                    (q['id'], ident.boxes[k].astype(float), c0 <= f <= c1)
                )
        print(f'window {lo}..{hi}: {sum(len(j) for j in jobs.values())} crops so far', flush=True)
    del det
    got = {q['id']: dict(frame=[], box=[], in_clip=[], jpg=[]) for q in qs}
    cap = cv2.VideoCapture(player_lab.VIDEO)
    fs = sorted(jobs)
    f = fs[0]
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
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
        for qid, b, inc in jobs[f]:
            w, h = b[2] - b[0], b[3] - b[1]
            x0, x1 = int(max(0, b[0] - PAD_X * w)), int(min(W, b[2] + PAD_X * w))
            y0, y1 = int(max(0, b[1] - PAD_Y * h)), int(min(H, b[3] + PAD_Y * h))
            if x1 - x0 < 8 or y1 - y0 < 16:
                continue
            g = got[qid]
            g['frame'].append(f)
            g['box'].append(b)
            g['in_clip'].append(inc)
            g['jpg'].append(
                cv2.imencode('.jpg', img[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
            )
        if f % 3000 == 0:
            print(f'  frame {f} / {fs[-1]}', flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT / f'crops_{tag}.npz',
        **{
            f'{qid}__{k}': (np.array(v, dtype=object) if k == 'jpg' else np.asarray(v))
            for qid, g in got.items()
            for k, v in g.items()
        },
    )
    n = sum(len(g['frame']) for g in got.values())
    print(f'{n} crops of {len(got)} identities -> {OUT / f"crops_{tag}.npz"}', flush=True)


def load_crops(tag):
    z = np.load(OUT / f'crops_{tag}.npz', allow_pickle=True)
    out = {}
    for k in z.files:
        qid, f = k.split('__')
        out.setdefault(qid, {})[f] = z[k]
    return out


TORSO = (0.12, 0.58, -0.08, 1.08)  # of the box: top, bottom, left, right (back number)


def torso(img, n_box):
    """The back/chest of a crop cut with PAD_X/PAD_Y, from box proportions (no pose)."""
    H, W = img.shape[:2]
    bh, bw = H / (1 + 2 * PAD_Y), W / (1 + 2 * PAD_X)
    y0, y1 = int(PAD_Y * bh + TORSO[0] * bh), int(PAD_Y * bh + TORSO[1] * bh)
    x0, x1 = int(PAD_X * bw + TORSO[2] * bw), int(PAD_X * bw + TORSO[3] * bw)
    return img[max(0, y0) : min(H, y1), max(0, x0) : min(W, x1)]


def read(tag, final=False, batch=256):
    """Legibility of each whole crop and the digits read on its torso; cached per half."""
    import cv2

    if tag in HELD_OUT and not final:
        raise SystemExit(f'{tag} is held out: scored once, at the end (--final)')
    from pitchiq.experiments.parseq_lite import Reader

    r = Reader()
    C = load_crops(tag)
    keys, leg, txt, prob, dist = [], [], [], [], []
    todo = [(qid, k) for qid, g in C.items() for k in range(len(g['frame']))]
    for i in range(0, len(todo), batch):
        part = todo[i : i + batch]
        ims = [
            cv2.imdecode(np.frombuffer(C[q]['jpg'][k], np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
            for q, k in part
        ]
        leg.append(r.legibility(ims))
        for s, p, d in r.digits([torso(im, None) for im in ims]):
            txt.append(s)
            prob.append((p + [0.0, 0.0, 0.0])[:3])
            dist.append(d)
        keys += part
        if (i // batch) % 40 == 0:
            print(f'  {i + len(part)} / {len(todo)}', flush=True)
    np.savez(
        OUT / f'reads_{tag}.npz',
        qid=np.array([q for q, _ in keys]),
        frame=np.array([int(C[q]['frame'][k]) for q, k in keys]),
        in_clip=np.array([bool(C[q]['in_clip'][k]) for q, k in keys]),
        h=np.array([float(C[q]['box'][k][3] - C[q]['box'][k][1]) for q, k in keys]),
        leg=np.concatenate(leg),
        txt=np.array(txt),
        prob=np.array(prob, np.float32),
        dist=np.array(dist, np.float32),
    )
    print(f'{len(keys)} reads -> {OUT / f"reads_{tag}.npz"}', flush=True)


def number_of(label):
    """The shirt number in a person's label ('10pink wristbands' -> '10'); None when the label
    has none or the user was unsure of it ('5? pink', '15-18')."""
    m = re.match(r'\s*(\d{1,2})(\D|$)', label or '')
    if not m or re.match(r'\s*\d{1,2}\s*[?\-]', label):
        return None
    return m.group(1)


def load_reads(tag):
    z = np.load(OUT / f'reads_{tag}.npz')
    return {k: z[k] for k in z.files}


def decide(R, idx, tl, tc, n_min, share, roster=None):
    """One clip's number from its crops `idx`: crops the legibility model passes (>= tl) whose
    read is 1-2 digits with every character and the end token at >= tc (and, with a lineup,
    is on it); the most common read wins if it is seen in >= n_min different seconds and makes
    >= `share` of the kept crops. Else None (no read)."""
    keep = [
        i
        for i in idx
        if R['leg'][i] >= tl
        and 1 <= len(R['txt'][i]) <= 2
        and R['prob'][i][: len(R['txt'][i]) + 1].min() >= tc
        and (roster is None or str(R['txt'][i]) in roster)
    ]
    if not keep:
        return None, 0, 0
    vals, cnt = np.unique(R['txt'][keep], return_counts=True)
    j = int(cnt.argmax())
    secs = len({int(R['frame'][i]) // 30 for i in keep if R['txt'][i] == vals[j]})
    if secs >= n_min and cnt[j] >= share * len(keep):
        return str(vals[j]), secs, len(keep)
    return None, secs, len(keep)


GRID = [
    (tl, tc, n, s)
    for tl in (0.0, 0.3, 0.5, 0.7, 0.9)
    for tc in (0.5, 0.8, 0.9, 0.95, 0.99)
    for n in (1, 2, 3, 4, 6)
    for s in (0.5, 0.7, 0.9, 1.0)
]


def _tally(items, R, p, rosters=None):
    right = wrong = none = 0
    bad = []
    for q, num in items:
        idx = R[q['tag']]['_by'][q['id']]
        got, n, k = decide(R[q['tag']], idx, *p, roster=rosters and rosters[q['kit']])
        if got is None:
            none += 1
        elif got == num:
            right += 1
        else:
            wrong += 1
            bad.append((q['id'], q['name'], got, n, k))
    return right, wrong, none, bad


def score(final=False):
    from pitchiq.experiments.idscore import truth

    tags = ['wb1', 'wb2'] if final else ['wb1']
    qs = truth(tags)
    R = {}
    for t in tags:
        R[t] = load_reads(t)
        by = {}
        for i, (q, inc) in enumerate(zip(R[t]['qid'], R[t]['in_clip'])):
            if inc:
                by.setdefault(str(q), []).append(i)
        R[t]['_by'] = by
    items = {t: [] for t in tags}
    other = {t: [] for t in tags}
    for q in qs:
        if q['v'] != 'named' or not q['sure']:
            continue
        num = number_of(q['name'])
        (items if num else other)[q['tag']].append((q, num))
    for t in tags:
        print(
            f'{t}: {len(items[t])} sure named clips with a number; {len(other[t])} without '
            f'({sorted({q["name"] for q, _ in other[t]})})'
        )

    # 1. what a single crop says (in-clip crops of numbered clips)
    for t in tags:
        rows = [(i, num) for q, num in items[t] for i in R[t]['_by'].get(q['id'], [])]
        n = len(rows)
        rd = [(i, num) for i, num in rows if len(R[t]['txt'][i]) in (1, 2)]
        ok = sum(R[t]['txt'][i] == num for i, num in rd)
        print(f'{t}: {n} crops; {len(rd)} read as 1-2 digits, {ok} of them the right number')

    # 2. thresholds chosen on wb1 only: most clips read with no wrong one, then the strictest
    # such setting (a wrong number joins two people for the rest of the game)
    ppl = [
        json.loads(f.read_text(encoding='utf-8'))
        for f in (LAB / 'answers/games/wb/people').glob('*.json')
    ]
    lineup = {
        k: {number_of(x['label']) for x in ppl if x['kit'] == k and not x.get('into')} - {None}
        for k in ('dark', 'light')
    }
    for mode, rosters in (
        ('no lineup', None),
        ('with the lineup (numbers each kit wears)', lineup),
    ):
        print(f'\n== {mode} ==')
        if rosters:
            print('  lineup:', {k: sorted(v, key=int) for k, v in rosters.items()})
        res = [(p, _tally(items['wb1'], R, p, rosters)) for p in GRID]
        print('  wb1 frontier (wrong reads allowed -> most clips right):')
        for w in range(0, 4):
            c = [(p, r) for p, r in res if r[1] <= w]
            p, r = max(c, key=lambda pr: pr[1][0])
            print(
                f'    <= {w} wrong: {r[0]} right, {r[1]} wrong, {r[2]} no read  '
                f'(legible >= {p[0]}, char >= {p[1]}, >= {p[2]} s, share >= {p[3]})'
            )
        zero = [(p, r) for p, r in res if r[1] == 0]
        top = max(r[0] for _, r in zero)
        p = max((p for p, r in zero if r[0] == top), key=lambda p: (p[2], p[1], p[0], p[3]))
        print(
            f'  chosen on wb1 (no wrong read, strictest): legible >= {p[0]}, char >= {p[1]}, >= {p[2]} s, share >= {p[3]}'
        )
        for t in tags:
            r, w, none, bad = _tally(items[t], R, p, rosters)
            held = ' (held out)' if t in HELD_OUT else ''
            print(f'    {t}{held}: {r} right, {w} wrong, {none} no read, of {len(items[t])} clips')
            for x in bad:
                print(
                    f'       wrong: {x[0]} is {x[1]!r}, read {x[2]!r} ({x[3]} s, {x[4]} crops kept)'
                )
            for q, _ in other[t]:
                got, n, k = decide(
                    R[t], R[t]['_by'].get(q['id'], []), *p, roster=rosters and rosters[q['kit']]
                )
                if got:
                    print(
                        f'       no number in the label: {q["id"]} {q["name"]!r} read {got!r} ({n} s of {k})'
                    )
            pp = {}
            for q, num in items[t]:
                got, _, _ = decide(
                    R[t], R[t]['_by'].get(q['id'], []), *p, roster=rosters and rosters[q['kit']]
                )
                pp.setdefault((q['kit'], q['name']), []).append(got == num if got else None)
            found = sum(any(x is True for x in v) for v in pp.values())
            print(f'       people read right on at least one clip: {found} of {len(pp)}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('crops')
    c.add_argument('tag')
    c.add_argument('--final', action='store_true')
    c = sub.add_parser('read')
    c.add_argument('tag')
    c.add_argument('--final', action='store_true')
    c = sub.add_parser('score')
    c.add_argument('--final', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd == 'crops':
        crops(a.tag, a.final)
    elif a.cmd == 'read':
        read(a.tag, a.final)
    elif a.cmd == 'score':
        score(a.final)
    return 0


if __name__ == '__main__':
    sys.exit(main())
