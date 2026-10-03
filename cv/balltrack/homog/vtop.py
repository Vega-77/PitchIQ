"""Top-down venue map.  G_k = B . K^-1 . H_k maps keyframe k's pixels to the
ground plane (units: camera heights), B = rows (e1 along field, e2 across, -n).
    python vtop.py [every] [px_per_unit]   -> vtop.jpg, vtop.npz (B, K, T)"""
import os, sys
import cv2, numpy as np
sys.path.insert(0, 'C:/Users/alexv/Desktop/Repos/PitchIQ')
from cv import lines as L
import vfeats
z = np.load('vmap.npz'); V = np.load('vvp.npz')
f = float(V['f']); K = np.array([[f, 0, 960], [0, f, 540], [0, 0, 1.]]); Ki = np.linalg.inv(K)
n = V['n']; d1 = V['v1'] - V['v1'].dot(n) * n; e1 = d1 / np.linalg.norm(d1); e2 = np.cross(e1, n)   # e1 x e2 = -n: seen from above, not below
B = np.array([e1, e2, -n])
def apply(H, p):
    q = np.c_[p, np.ones(len(p))].dot(H.T); return q[:, :2] / q[:, 2:3]
every = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ppu = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
G = {k: B.dot(Ki).dot(z['H'][k]) for k in np.where(z['ok'])[0]}
# bounds from the lower (near-ground) part of each frame
pts = np.concatenate([apply(g, np.float32([[0, 700], [1919, 700], [1919, 1079], [0, 1079], [960, 450]])) for g in G.values()])
lo, hi = np.percentile(pts, 0.5, 0), np.percentile(pts, 99.5, 0)
T = np.array([[ppu, 0, -lo[0] * ppu], [0, ppu, -lo[1] * ppu], [0, 0, 1]])
size = (int((hi[0] - lo[0]) * ppu) + 1, int((hi[1] - lo[1]) * ppu) + 1)
print('ground extent (camera heights) x %.1f..%.1f  y %.1f..%.1f  canvas %s' % (lo[0], hi[0], lo[1], hi[1], size))
acc = np.zeros(size[::-1] + (3,), np.float32); ws = np.zeros(size[::-1], np.float32)
cap = cv2.VideoCapture(vfeats.VIDEO)
for k in sorted(G)[::every]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
    if not ok: continue
    m = L.field_mask(im).astype(np.float32) / 255
    m[:300] = 0                                     # sky / far background
    M = T.dot(G[k])
    w = cv2.warpPerspective(m, M, size, flags=cv2.INTER_LINEAR)
    acc += cv2.warpPerspective(im.astype(np.float32), M, size) * w[..., None]; ws += w
out = (acc / np.maximum(ws, 1e-3)[..., None]).astype(np.uint8); out[ws < 0.5] = 0
cv2.imwrite('vtop.jpg', out)
np.savez('vtop.npz', B=B, K=K, T=T, size=size)
print('vtop.jpg')
