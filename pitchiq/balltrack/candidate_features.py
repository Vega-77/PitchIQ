"""Per-candidate features: still rows from the blob dicts, and the stabilised tile stack.

blob_candidates.blobs gives each frame's candidates; still_rows turns them into the
ranker's still columns.  tiles_for builds the 21 grey tiles (frames -10..+10 around the
anchor, warped onto it, at 640x360 through the same JPEG the ranker was trained on) that
temporal_features.temporal_rows reads.
"""

import math

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


def still_rows(bs, cmaj, cara):
    """One row of still features per blob (NAMES order), and the blob centres (n, 2) in source px.

    cmaj, cara: the expected ball major axis and side as a linear fit in image row
    (still4.npz), so the size columns are relative to what a ball there should be.
    """
    pts = np.array([[b['cx'], b['cy']] for b in bs], np.float32)
    rows = []
    for b in bs:
        dxy = np.hypot(pts[:, 0] - b['cx'], pts[:, 1] - b['cy'])
        nn = float(np.sort(dxy)[1]) if len(dxy) > 1 else 999.0
        e_maj = max(1.0, cmaj[0] * b['cy'] + cmaj[1])
        e_side = max(1.0, cara[0] * b['cy'] + cara[1])
        rows.append(
            [
                b['minor'],
                b['major'],
                b['area'],
                b['fill'],
                b['elong'],
                b['circ'],
                b['extent'],
                b['v_in'],
                b['v_max'],
                b['v_std'],
                b['v_ring'],
                b['v_ring_max'],
                b['con'],
                b['rel_con'],
                b['s_in'],
                b['s_ring'],
                b['h_in'],
                b['grass_ring'],
                b['other_ring'],
                b['white_far'],
                b['lap'],
                b['cx'] / 1920.0,
                b['cy'] / 1080.0,
                b['major'] / e_maj,
                b['minor'] / e_maj,
                math.sqrt(b['area']) / e_side,
                nn,
                float((dxy < 100).sum() - 1),
                float(len(bs)),
                b['split'],
            ]
            + b['th']
        )
    return np.array(rows, np.float32), pts
