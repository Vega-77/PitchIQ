"""Yellow-paint density in the top-down map (fraction of views that saw
yellow at each ground cell).   -> vyellow.npy, vyellow.jpg"""
import sys
import cv2, numpy as np
sys.path.insert(0, 'C:/Users/alexv/Desktop/Repos/PitchIQ')
from cv import lines as L
import vfeats
z = np.load('vmap.npz'); t = np.load('vtop.npz')
B, K, T, size = t['B'], t['K'], t['T'], tuple(int(v) for v in t['size'])
Ki = np.linalg.inv(K)
hit = np.zeros(size[::-1], np.float32); seen = np.zeros(size[::-1], np.float32)
cap = cv2.VideoCapture(vfeats.VIDEO)
for k in np.where(z['ok'])[0][::2]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
    if not ok: continue
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    y = cv2.inRange(hsv, (16, 80, 90), (34, 255, 255)).astype(np.float32) / 255
    fm = L.field_mask(im).astype(np.float32) / 255; fm[:300] = 0
    M = T.dot(B).dot(Ki).dot(z['H'][k])
    hit += cv2.warpPerspective(y * fm, M, size); seen += cv2.warpPerspective(fm, M, size)
d = hit / np.maximum(seen, 1)
d[seen < 3] = 0
np.save('vyellow.npy', d)
v = np.clip(d / np.percentile(d[d > 0], 99.5), 0, 1)
cv2.imwrite('vyellow.jpg', (v * 255).astype(np.uint8)[350:1600, 0:760])
print('done', d.shape, 'cells seen>=3:', int((seen >= 3).sum()))
