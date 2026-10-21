"""Ball Clicker items: 75 frames per half over 12 halves, spread across each half's play.

Each half's play range (its pass range; Piscataway H2 read off the video 2026-10-16: kickoff 75:00,
whistle 122:40) minus the known restart waits and off-play stretches (score_set.py's DEAD / OFF) is
cut into 75 equal slots by frame count, and one frame is drawn at random inside each slot (fixed
seed). Slot s goes to batch 1 + s % 5, so each of the 5 batches covers every half from end to end.

Per item: <id>_s.webp, the frame itself at full resolution (the click is in its pixels), and
<id>_l.webp, a 2.1 s loop of every third frame from f-30 to f+30 at 960x540 (10 fps, real speed).

Held-out halves (NB H2, Piscataway H1/H2, WH H1) are test-only: never trained on.

    python build.py [<out root>]        -> <root>/b1..b5/{items.json, media}, <root>/items_all.json
"""
import json
import os
import sys
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image

DL = 'C:/Users/alexv/Downloads/'
VID = {
    'nb': DL + "North Brunswick vs South Brunswick High School Boys' Varsity Soccer - Tactical.mp4",
    'ww': DL + 'Home v West Windsor South 9-19-2026 - Tactical.mp4',
    'eb': DL + 'Home v East Brunswick 9-24-26 - Tactical.mp4',
    'scrim': DL + '8-26-26 Scrimmage v Gov Livingston - Tactical.mp4',
    'wh': DL + 'Home v Wardlaw-Hartridge 10_8_26 & Senior Night - Tactical.mp4',
    'pi': DL + 'Home v Piscataway 10-6-26 - Tactical.mp4',
}
# tag, title, video key, first, last, excluded [(a, b)], held out
HALVES = [
    ('nb1', 'NB H1', 'nb', 3750, 78750, [], False),
    ('ww1', 'WW H1', 'ww', 23400, 100800, [(23400, 24899)], False),
    ('ww2', 'WW H2', 'ww', 120600, 210150, [(120600, 120789), (209500, 210151)], False),
    ('eb1', 'EB H1', 'eb', 76800, 154350, [(76800, 77430), (78045, 78689), (152600, 154350)], False),
    ('eb2', 'EB H2', 'eb', 168750, 251100, [], False),
    ('scrimh1', 'Scrimmage H1', 'scrim', 32400, 108000, [], False),
    ('scrimh2', 'Scrimmage H2', 'scrim', 127350, 201150, [], False),
    ('wh2', 'WH H2', 'wh', 142200, 224100, [], False),
    ('nb2', 'NB H2', 'nb', 99000, 232340, [(99000, 100800), (230500, 232339)], True),
    ('pi1', 'Piscataway H1', 'pi', 41100, 118800, [], True),
    ('pi2', 'Piscataway H2', 'pi', 135000, 220800, [], True),
    ('wh1', 'WH H1', 'wh', 49500, 126000, [], True),
]
PER_HALF, BATCHES, HALF_LOOP = 75, 5, 30
SEED = 20261016
STILL_Q, LOOP_Q, LOOP_W, LOOP_H = 88, 50, 960, 540


def plan():
    rng = np.random.default_rng(SEED)
    items = []
    for tag, title, vk, lo, hi, excl, held in HALVES:
        fr = np.arange(lo + HALF_LOOP, hi - HALF_LOOP + 1)
        keep = np.ones(len(fr), bool)
        for a, b in excl:
            keep &= ~((fr >= a - HALF_LOOP) & (fr <= b + HALF_LOOP))
        fr = fr[keep]
        for s, part in enumerate(np.array_split(fr, PER_HALF)):
            f = int(part[rng.integers(len(part))])
            items.append(dict(id='%s-%02d' % (tag, s), half=tag, title=title, video=vk, f=f, slot=s,
                              batch=1 + s % BATCHES, held=held))
    return items


def render(job):
    root, its = job
    cap = cv2.VideoCapture(VID[its[0]['video']])
    out = []
    for it in sorted(its, key=lambda d: d['f']):
        d = os.path.join(root, 'b%d' % it['batch'])
        s_p, l_p = os.path.join(d, it['id'] + '_s.webp'), os.path.join(d, it['id'] + '_l.webp')
        if not (os.path.exists(s_p) and os.path.exists(l_p)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, it['f'] - HALF_LOOP)
            fr = []
            for _ in range(2 * HALF_LOOP + 1):
                ok, im = cap.read()
                assert ok, '%s: read failed near %d' % (it['id'], it['f'])
                fr.append(im)
            mid = fr[HALF_LOOP]
            assert mid.shape[:2] == (1080, 1920), mid.shape
            Image.fromarray(mid[..., ::-1]).save(s_p + '.part', 'WEBP', quality=STILL_Q, method=4)
            ims = [Image.fromarray(cv2.resize(x, (LOOP_W, LOOP_H), interpolation=cv2.INTER_AREA)[..., ::-1])
                   for x in fr[::3]]
            ims[0].save(l_p + '.part', 'WEBP', save_all=True, append_images=ims[1:], duration=100, loop=0,
                        quality=LOOP_Q, method=4)
            os.replace(s_p + '.part', s_p); os.replace(l_p + '.part', l_p)
        out.append(it['id'])
    return its[0]['half'], len(out)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/clicker'
    items = plan()
    for b in range(1, BATCHES + 1):
        os.makedirs(os.path.join(root, 'b%d' % b), exist_ok=True)
    jobs = [(root, [it for it in items if it['half'] == h[0]]) for h in HALVES]
    with Pool(6) as p:
        for half, n in p.imap_unordered(render, jobs):
            print(half, n, 'items', flush=True)
    # each batch's order: halves interleaved, shuffled, so any stretch of a sitting mixes games
    rng = np.random.default_rng(SEED + 1)
    for b in range(1, BATCHES + 1):
        its = [it for it in items if it['batch'] == b]
        its = [its[i] for i in rng.permutation(len(its))]
        rows = [dict(id=it['id'], half=it['half'], title=it['title'], f=it['f'], still=it['id'] + '_s.webp',
                     loop=it['id'] + '_l.webp') for it in its]
        json.dump(dict(batch='b%d' % b, items=rows), open(os.path.join(root, 'b%d' % b, 'items.json'), 'w'), indent=0)
    json.dump(dict(halves=[dict(tag=h[0], title=h[1], lo=h[3], hi=h[4], excl=h[5], held=h[6]) for h in HALVES],
                   items=items, seed=SEED), open(os.path.join(root, 'items_all.json'), 'w'), indent=1)
    sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(root) for f in fs)
    print('%d items, %.0f MB' % (len(items), sz / 1e6))


if __name__ == '__main__':
    main()
