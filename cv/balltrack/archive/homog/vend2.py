import cv2, numpy as np, vfeats
z = np.load('vmap.npz'); t = np.load('vtop.npz')
B, K, T, size = t['B'], t['K'], t['T'], tuple(int(v) for v in t['size'])
Ki = np.linalg.inv(K)
cap = cv2.VideoCapture(vfeats.VIDEO)
out = []
for k, (y0, y1) in [(336, (850, 1250)), (646, (0, 400))]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
    M = T.dot(B).dot(Ki).dot(z['H'][k])
    w = cv2.warpPerspective(im, M, size)[350:1600, 0:760][y0:y1]
    # grid every 50 px for reading coordinates
    for x in range(0, 760, 50): cv2.line(w, (x, 0), (x, 399), (0, 0, 255) if x % 100 == 0 else (80, 80, 80), 1)
    for y in range(0, 400, 50): cv2.line(w, (0, y), (759, y), (0, 0, 255) if (y + y0) % 100 == 0 else (80, 80, 80), 1)
    cv2.putText(w, 'y0=%d' % y0, (5, 20), 0, 0.6, (255, 255, 255), 2)
    out.append(cv2.resize(w, None, fx=1.3, fy=1.3))
cv2.imwrite('ends_zoom.jpg', np.vstack(out))
