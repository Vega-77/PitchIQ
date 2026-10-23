"""Stage the Ring Review page: each scored frame with the person's click and the video's ring on it.

Every frame the scorer calls wrong, plus a fixed sample of frames it calls right, so a person can see
whether the scoring itself is right. The ring is the video's (ringcheck.py's rings.json, i.e.
match_video.ball); the right / wrong call is clickscore.judge's.

    python review.py <site dir> [right sample per check, default 25]
        -> <site>/index.html, data.json, f/<item id>.webp (the full-resolution still)

Verdicts: db verdicts/<item id> {v: agree|disagree|unsure, note, at}.
"""

import json
import os
import shutil
import sys

import numpy as np

from pitchiq.experiments.clicker import build, clickscore

HERE = os.path.dirname(os.path.abspath(__file__))
# check, right -> category on the page, in the page's order
CAT = {
    ('ballring', False): 'off',
    ('ballring', True): 'okring',
    ('ballmiss', False): 'noring',
    ('ballmiss', True): 'okempty',
    ('hidden', False): 'hidmiss',
    ('hidden', True): 'hidok',
}


def main():
    site = sys.argv[1]
    n_right = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    root = clickscore.ROOT
    A = clickscore.answers(os.path.join(root, 'db'))
    R = json.load(open(os.path.join(root, 'rings.json')))
    meta = {h['tag']: h for h in json.load(open(os.path.join(root, 'items_all.json')))['halves']}
    titles = {}
    stills = {}
    for b in sorted(os.listdir(root)):
        p = os.path.join(root, b, 'items.json')
        if os.path.exists(p):
            for it in json.load(open(p))['items']:
                titles[it['id']] = it['title']
                stills.setdefault(it['id'], os.path.join(root, b, it['still']))
    rows = []
    for k in sorted(R):
        r = R[k]
        j = clickscore.judge(A[k], (r['x'], r['y']), r['kind'])
        if j is None:
            continue
        check, right, why = j
        a = A[k]
        rows.append(
            dict(
                id=k,
                half=r['half'],
                title=titles[k],
                held=meta[r['half']]['held'],
                f=r['f'],
                v=a['v'],
                ax=a.get('x'),
                ay=a.get('y'),
                kind=r['kind'],
                rx=r['x'],
                ry=r['y'],
                d=None
                if not r['kind'] or a.get('x') is None
                else round(float(np.hypot(r['x'] - a['x'], r['y'] - a['y'])), 1),
                cat=CAT[check, right],
                why=why,
            )
        )
    rng = np.random.default_rng(build.SEED + 7)
    keep = []
    for c in ('off', 'hidmiss', 'noring', 'okring', 'hidok', 'okempty'):
        rs = [r for r in rows if r['cat'] == c]
        rs = [rs[i] for i in rng.permutation(len(rs))]
        keep += rs if c in ('off', 'hidmiss', 'noring') else rs[:n_right]
    fdir = os.path.join(site, 'f')
    os.makedirs(fdir, exist_ok=True)
    for f in os.listdir(fdir):
        if f[:-5] not in {r['id'] for r in keep}:
            os.remove(os.path.join(fdir, f))
    for r in keep:
        dst = os.path.join(fdir, r['id'] + '.webp')
        if not os.path.exists(dst):
            shutil.copy(stills[r['id']], dst)
    shutil.copy(os.path.join(HERE, 'review.html'), os.path.join(site, 'index.html'))
    counts = {c: sum(1 for r in rows if r['cat'] == c) for c in CAT.values()}
    json.dump(
        dict(items=keep, counts=counts, tol=clickscore.TOL, hid_tol=clickscore.HID_TOL),
        open(os.path.join(site, 'data.json'), 'w'),
        separators=(',', ':'),
    )
    sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(site) for f in fs)
    print(
        '%d of %d scored frames staged, %d files, %.1f MB  %s'
        % (len(keep), len(rows), len(os.listdir(fdir)) + 2, sz / 1e6, counts)
    )


if __name__ == '__main__':
    main()
