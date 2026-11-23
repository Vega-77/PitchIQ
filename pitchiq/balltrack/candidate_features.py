"""Per-candidate features: still rows from the blob dicts, and the stabilised tile stack.

blob_candidates.blobs gives each frame's candidates; still_rows turns them into the
ranker's still columns.  tiles_for builds the 21 grey tiles (frames -10..+10 around the
anchor, warped onto it, at 640x360 through the same JPEG the ranker was trained on) that
temporal_features.temporal_rows reads.
"""

import os

import cv2
import numpy as np

OFFS = list(range(-10, 11))
NOFF = len(OFFS)
CZERO = OFFS.index(0)
TW, TH = 640, 360
SCALE = 1920.0 / TW  # 3.0
JPEGQ = 72  # the labelling sheets were written at this quality, and
# the model learnt on that noise; match it
KEEP = 40  # candidates written out per frame (track.py's K)
if os.environ.get(
    'PIQ_KEEP'
):  # an experiment's detector workers widen the cut through this (at most 63)
    KEEP = int(os.environ['PIQ_KEEP'])


def F_scaled(M, s):
    """The same affine, expressed in a picture resized by s."""
    A = M[:, :2]
    t = M[:, 2]
    c = 0.5 * (s - 1.0)
    out = np.zeros((2, 3), np.float64)
    out[:, :2] = A
    out[:, 2] = s * t + c - A.dot(np.array([c, c]))
    return out


def tiles_for(bgrs, mats, fids=None, cache=None):
    """The 21 stabilised grey tiles, through the same JPEG the model saw.

    `cache` (frame id -> its resized frame, with `fids` naming bgrs) saves
    resizing each frame 21 times, once per anchor it serves; the warp and the
    JPEG are per anchor and are not cached. The caller evicts old frames."""
    out = np.empty((len(bgrs), TH, TW), np.float32)
    for k in range(len(bgrs)):
        small = None if cache is None else cache.get(fids[k])
        if small is None:
            small = cv2.resize(bgrs[k], (TW, TH), interpolation=cv2.INTER_AREA)
            if cache is not None:
                cache[fids[k]] = small
        if mats[k] is not None:
            small = cv2.warpAffine(
                small,
                F_scaled(mats[k][:2], 1.0 / SCALE),
                (TW, TH),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        enc = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), JPEGQ])[1]
        small = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        out[k] = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return out


STILL_KEYS = (
    'minor',
    'major',
    'area',
    'fill',
    'elong',
    'circ',
    'extent',
    'v_in',
    'v_max',
    'v_std',
    'v_ring',
    'v_ring_max',
    'con',
    'rel_con',
    's_in',
    's_ring',
    'h_in',
    'grass_ring',
    'other_ring',
    'white_far',
    'lap',
)
NN_EPS = 1e-5  # relative margin: a float32 difference and hypotf are ~3e-7 from exact
NN_K = 8  # neighbours read per blob before its row is settled


def _neighbours(pts):
    """Each blob's nearest other blob, and how many others lie under 100 px.

    Exactly what the float32 row hypot(pts - pts_i) gives (its second smallest,
    self included, and its count under 100, less self), without building the
    n x n block.  A k-d tree on the exact distances finds the few blobs that can
    matter; their distances are then taken the float32 way, and a row the margin
    cannot settle is done in full.
    """
    from scipy.spatial import cKDTree

    n = len(pts)
    px, py = pts[:, 0], pts[:, 1]
    P = pts.astype(np.float64)
    tree = cKDTree(P)

    def row(i):
        return np.hypot(px - px[i], py - py[i])

    k = min(NN_K, n)
    d, j = tree.query(P, k=k)
    h = np.hypot(px[j] - px[:, None], py[j] - py[:, None])  # float32, the original's way
    nn = np.partition(h, 1, axis=1)[:, 1].astype(np.float64)
    if k < n:
        # a blob outside the k nearest is >= d_k (1 - 3e-7) away by float32
        for i in np.nonzero(~(nn < d[:, k - 1] * (1 - NN_EPS)))[0].tolist():
            nn[i] = np.partition(row(i), 1)[1]
    lo = tree.query_ball_point(P, 100.0 * (1 - NN_EPS), return_length=True)
    hi = tree.query_ball_point(P, 100.0 * (1 + NN_EPS), return_length=True)
    cnt = lo.astype(np.float64) - 1
    # a blob within the margin of 100 px: that row is counted the float32 way
    for i in np.nonzero(hi != lo)[0].tolist():
        cnt[i] = np.count_nonzero(row(i) < 100) - 1
    return nn, cnt


def still_rows(bs, cmaj, cara):
    """One row of still features per blob (NAMES order), and the blob centres (n, 2) in source px.

    cmaj, cara: the expected ball major axis and side as a linear fit in image row
    (still4.npz), so the size columns are relative to what a ball there should be.

    Filled a column at a time in float64, which holds every value the per-blob
    rows held exactly (Python floats, int32s, float32s), and cast to float32
    once, as np.array(rows, np.float32) did: the same bytes, ~4x faster.
    """
    pts = np.array([[b['cx'], b['cy']] for b in bs], np.float32)
    if not bs:
        return np.array([], np.float32), pts
    n = len(bs)
    cols = np.empty((n, 36), np.float64)
    for j, key in enumerate(STILL_KEYS):
        cols[:, j] = [b[key] for b in bs]
    cx = np.array([b['cx'] for b in bs], np.float64)
    cy = np.array([b['cy'] for b in bs], np.float64)
    e_maj = np.maximum(1.0, cmaj[0] * cy + cmaj[1])
    e_side = np.maximum(1.0, cara[0] * cy + cara[1])
    cols[:, 21] = cx / 1920.0
    cols[:, 22] = cy / 1080.0
    cols[:, 23] = cols[:, 1] / e_maj
    cols[:, 24] = cols[:, 0] / e_maj
    cols[:, 25] = np.sqrt(cols[:, 2]) / e_side
    if n > 1:
        cols[:, 26], cols[:, 27] = _neighbours(pts)
    else:
        cols[:, 26] = 999.0
        cols[:, 27] = 0.0
    cols[:, 28] = float(n)
    cols[:, 29] = [b['split'] for b in bs]
    cols[:, 30:36] = [b['th'] for b in bs]
    return cols.astype(np.float32), pts
