"""Shared bits of the self-training line: paths, pieces, clicks, the crop cache reader."""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402

BT = common.BT
DATA = common.DATA
OOF = DATA + '/scratch-2026-10-26/oof_cls'
OUT = DATA + '/ball-push/selftrain'
CACHE = OUT + '/cache'
DEV = list(common.DEV)
GAMES = ['co', 'eb', 'nb', 'pi', 'scrim', 'wb', 'wh', 'ww']  # sorted: fold i = oof_cls.py's seed i
CSIZE = 40  # cached crop side (the net reads the centre 32, training shifts it +-3)
KEEP_P = -5.0  # cached: every candidate whose out-of-fold score is above this ...
KEEP_TOP = 3  # ... and each frame's top few by that score, and all of a clicked frame

# ST_HELD=1: the same builders (cache, tcache, mine oof, pseudo own_<game>) on the held-out halves, under the
# SHIPPED scores, into selftrain/held/.  No held-out answer is read in this mode (clicks() is empty).
HELD_MODE = bool(os.environ.get('ST_HELD'))
SIBLING = dict(nb2='nb1', pi1='pi2', wh1='wh2', co1='co2', wb2='wb1')  # same game, same video file
if HELD_MODE:
    OOF = BT
    OUT = OUT + '/held'
    CACHE = OUT + '/cache'
    DEV = list(common.HELD)
# ST_OUT / ST_OOF: another output root and another folder of base scores (a re-detected half under BP_BT, whose
# base scores are the shipped classifier's on the new candidates)
if os.environ.get('ST_OUT'):
    OUT = os.environ['ST_OUT']
    CACHE = OUT + '/cache'
if os.environ.get('ST_OOF'):
    OOF = os.environ['ST_OOF']
# ST_SUB=<name>: cache.py / tcache.py build only the rows listed in selftrain/<name>/rows/<piece>.npz (f, j),
# into selftrain/<name>/cache and /tcache (prop.py's click-propagation rows)
SUB = os.environ.get('ST_SUB')
if SUB:
    OUT = OUT + '/' + SUB
    CACHE = OUT + '/cache'


def game(tag):
    tag = tag.split('_q')[0]
    return tag[:-1] if not tag.startswith('scrim') else 'scrim'


def pieces(tags=None):
    out = []
    for tag in tags or DEV:
        assert tag in DEV, 'not a dev half: %s' % tag
        for pp in sorted(glob.glob(os.path.join(BT, '%s_q[0-9][0-9][0-9].npz' % tag))):
            out.append(os.path.basename(pp)[:-4])
    return out


def videos():
    v = {k: v[0] for k, v in json.load(open(DATA + '/scratch-2026-10-26/halfvideo.json')).items()}
    for h, sib in SIBLING.items():
        v.setdefault(h, v[sib])
    return v


def clicks():
    """{half: [(item id, abs frame, answer dict)]} for the dev 'ball' and 'out' answers."""
    if HELD_MODE:
        return {t: [] for t in DEV}
    common.point_at_data()
    from pitchiq.experiments.clicker import clickscore as cs

    items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
    A = cs.answers(os.path.join(cs.ROOT, 'db'))
    out = {t: [] for t in DEV}
    for i, a in A.items():
        it = items.get(i)
        if it and it['half'] in DEV and a['v'] in ('ball', 'out'):
            out[it['half']].append((i, int(it['f']), a))
    return out


def load_oof(pt, d=OOF):
    """(f, j, p) of a piece, sorted by (f, j)."""
    z = np.load(os.path.join(d, '%scls.npz' % pt))
    f, j, p = z['f'].astype(np.int64), z['j'].astype(np.int64), z['p'].astype(np.float32)
    o = np.lexsort((j, f))
    return f[o], j[o], p[o]


def load_cache(pt, crops=True):
    z = np.load(os.path.join(CACHE, '%s.npz' % pt), mmap_mode=None)
    d = dict(f=z['f'].astype(np.int64), j=z['j'].astype(np.int64), ctx=z['ctx'])
    if crops:
        d['crop'] = z['crop']
    return d
