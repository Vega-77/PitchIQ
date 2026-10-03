import sys, cv2, numpy as np, vfeats
im = np.load('vid1_img.npz'); a = list(im['abs'])
lo, hi, st = map(int, sys.argv[1:4])
cap = cv2.VideoCapture(vfeats.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, lo); tiles = []
for f in range(lo, hi + 1):
    ok, fr = cap.read()
    if (f - lo) % st: continue
    i = a.index(f); x, y = im['ix'][i], im['iy'][i]
    if np.isfinite(x):
        cv2.circle(fr, (int(x), int(y)), 18, (0, 255, 0), 2)
    fr = cv2.resize(fr, (480, 270)); cv2.putText(fr, str(f), (5, 20), 0, 0.6, (255, 255, 255), 2)
    tiles.append(fr)
while len(tiles) % 4: tiles.append(np.zeros_like(tiles[0]))
cv2.imwrite('sheet.jpg', np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))
