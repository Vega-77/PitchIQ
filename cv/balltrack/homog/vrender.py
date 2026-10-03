"""Draw the venue map: every placed keyframe warped into the reference plane
and averaged (moving players wash out, paint stays).

    python vrender.py [every] [width]   -> vmap.jpg
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, 'C:/Users/alexv/Desktop/Repos/PitchIQ')
from cv import lines as L                          # noqa: E402
import vfeats                                      # noqa: E402


def apply(H, p):
    q = np.c_[p, np.ones(len(p))].dot(H.T)
    return q[:, :2] / q[:, 2:3]


def canvas(z, width):
    """Scale+shift T (map plane -> canvas px) covering the field footprints."""
    corners = np.float32([[0, 330], [1919, 330], [1919, 1079], [0, 1079]])
    pts = np.concatenate([apply(H, corners) for H, ok in zip(z['H'], z['ok'])
                          if ok])
    lo, hi = np.percentile(pts, 1, 0), np.percentile(pts, 99, 0)
    s = width / (hi[0] - lo[0])
    T = np.array([[s, 0, -lo[0] * s], [0, s, -lo[1] * s], [0, 0, 1]])
    return T, (int(width), int((hi[1] - lo[1]) * s) + 1)


def main():
    every = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    z = np.load(os.path.join(HERE, 'vmap.npz'))
    T, size = canvas(z, width)
    acc = np.zeros(size[::-1] + (3,), np.float32)
    wsum = np.zeros(size[::-1], np.float32)
    cap = cv2.VideoCapture(vfeats.VIDEO)
    for k in range(0, len(z['abs']), every):
        if not z['ok'][k]:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k]))
        ok, im = cap.read()
        if not ok:
            continue
        m = L.field_mask(im).astype(np.float32) / 255
        M = T.dot(z['H'][k])
        w = cv2.warpPerspective(m, M, size)
        acc += cv2.warpPerspective(im.astype(np.float32), M, size) * w[..., None]
        wsum += w
    out = (acc / np.maximum(wsum, 1e-3)[..., None]).astype(np.uint8)
    out[wsum < 0.5] = 0
    cv2.imwrite(os.path.join(HERE, 'vmap.jpg'), out)
    np.save(os.path.join(HERE, 'vmap_T.npy'), T)
    print('vmap.jpg', size)


if __name__ == '__main__':
    main()
