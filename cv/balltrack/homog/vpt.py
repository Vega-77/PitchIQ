import cv2, numpy as np, sys
z = np.load('vmap.npz'); t = np.load('vtop.npz')
ab = int(sys.argv[1]); k = int(np.where(z['abs'] == ab)[0][0])
M = t['T'].dot(t['B']).dot(np.linalg.inv(t['K'])).dot(z['H'][k])
pts = np.float32(eval(sys.argv[2])).reshape(-1, 1, 2)
for p, q in zip(pts[:, 0], cv2.perspectiveTransform(pts, M)[:, 0]): print(p, '->', np.round(q - [0, 350], 1))
