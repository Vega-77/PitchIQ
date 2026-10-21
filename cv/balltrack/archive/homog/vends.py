import cv2, numpy as np, vfeats
z = np.load('vmap.npz'); t = np.load('vtop.npz')
B, K, T, size = t['B'], t['K'], t['T'], tuple(int(v) for v in t['size'])
Ki = np.linalg.inv(K)
ks = np.where(z['ok'])[0]
cen = []
for k in ks:
    M = T.dot(B).dot(Ki).dot(z['H'][k])
    p = cv2.perspectiveTransform(np.float32([[[960, 700]]]), M)[0, 0]
    cen.append(p)
cen = np.array(cen); cy = cen[:, 1] - 350
o = np.argsort(cy)
print('footprint centre y (crop) range', cy.min(), cy.max())
picks = list(ks[o[:3]]) + list(ks[o[-3:]])
cap = cv2.VideoCapture(vfeats.VIDEO)
tiles = []; raws = []
for k in picks:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
    M = T.dot(B).dot(Ki).dot(z['H'][k])
    w = cv2.warpPerspective(im, M, size)[350:1600, 0:760]
    tiles.append(w); raws.append(cv2.resize(im, (640, 360)))
    print(k, z['abs'][k], cy[list(ks).index(k)])
cv2.imwrite('ends_top.jpg', np.hstack(tiles))
cv2.imwrite('ends_raw.jpg', np.vstack([np.hstack(raws[:3]), np.hstack(raws[3:])]))
