import cv2, numpy as np, vfeats, sys
ab = int(sys.argv[1]); x0, y0, x1, y1 = map(int, sys.argv[2:6])
cap = cv2.VideoCapture(vfeats.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, ab); ok, im = cap.read()
c = im[y0:y1, x0:x1].copy()
for x in range((x0 // 50 + 1) * 50, x1, 50): cv2.line(c, (x - x0, 0), (x - x0, y1 - y0), (0, 0, 255) if x % 100 == 0 else (60, 60, 60), 1)
for y in range((y0 // 50 + 1) * 50, y1, 50): cv2.line(c, (0, y - y0), (x1 - x0, y - y0), (0, 0, 255) if y % 100 == 0 else (60, 60, 60), 1)
cv2.imwrite('raw_crop.jpg', c)
