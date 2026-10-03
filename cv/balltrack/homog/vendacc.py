import sys, cv2, numpy as np, vfeats
sys.path.insert(0, 'C:/Users/alexv/Desktop/Repos/PitchIQ')
from cv import lines as L
z = np.load('vmap.npz'); t = np.load('vtop.npz')
B, K, T, size = t['B'], t['K'], t['T'], tuple(int(v) for v in t['size'])
Ki = np.linalg.inv(K); ks = np.where(z['ok'])[0]
cy = {}
for k in ks:
    M = T.dot(B).dot(Ki).dot(z['H'][k])
    cy[k] = cv2.perspectiveTransform(np.float32([[[960, 700]]]), M)[0, 0, 1] - 350
cap = cv2.VideoCapture(vfeats.VIDEO)
tiles = []
for name, sel, (y0, y1) in [('top', lambda v: v < 430, (150, 550)), ('bot', lambda v: v > 830, (800, 1200))]:
    hit = np.zeros(size[::-1], np.float32); seen = hit.copy(); rgb = np.zeros(size[::-1] + (3,), np.float32)
    use = [k for k in ks if sel(cy[k])]
    for k in use:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
        if not ok: continue
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        # paint response: yellowish hue and brighter than local grass
        g = cv2.medianBlur(hsv[..., 2], 15).astype(np.float32)
        br = np.clip(hsv[..., 2].astype(np.float32) - g, 0, 60) / 60
        yl = ((hsv[..., 0] >= 14) & (hsv[..., 0] <= 36) & (hsv[..., 1] >= 50)).astype(np.float32)
        fm = L.field_mask(im).astype(np.float32) / 255; fm[:300] = 0
        M = T.dot(B).dot(Ki).dot(z['H'][k])
        hit += cv2.warpPerspective(br * yl * fm, M, size); seen += cv2.warpPerspective(fm, M, size)
        rgb += cv2.warpPerspective(im.astype(np.float32) * fm[..., None], M, size)
    d = hit / np.maximum(seen, 1); d[seen < 3] = 0
    np.save('vend_%s.npy' % name, d)
    v = (255 * np.clip(d / np.percentile(d[d > 0], 99.7), 0, 1)).astype(np.uint8)[350:1600, 0:760][y0:y1]
    v = cv2.cvtColor(v, cv2.COLOR_GRAY2BGR)
    m = (rgb / np.maximum(seen, 1)[..., None]).clip(0, 255).astype(np.uint8)[350:1600, 0:760][y0:y1]
    for im2 in (v, m):
        for x in range(0, 760, 50): cv2.line(im2, (x, 0), (x, 399), (0, 0, 255) if x % 100 == 0 else (90, 90, 90), 1)
        for y in range(0, 400, 50): cv2.line(im2, (0, y), (759, y), (0, 0, 255) if (y + y0) % 100 == 0 else (90, 90, 90), 1)
        cv2.putText(im2, '%s y0=%d n=%d' % (name, y0, len(use)), (5, 20), 0, 0.6, (255, 255, 255), 2)
    tiles.append(np.hstack([v, m]))
cv2.imwrite('ends_acc.jpg', np.vstack(tiles))
