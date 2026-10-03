import numpy as np, cv2
d = np.load('vyellow.npy')[350:1600, 0:760].astype(np.float32)
b = cv2.blur(d, (41, 41)); r = np.clip(d - b, 0, None)
# halfway line row, and its extent along x
row = r[630:648].sum(0); hy = 630 + np.argmax(r[630:648, 100:500].sum(1))
print('halfway row', hy)
on = r[hy - 1:hy + 2].max(0) > 0.3 * np.percentile(r[hy, 100:500], 50)
xs = np.where(on)[0]; print('halfway x on-range', xs.min(), xs.max())
prof = r[hy-1:hy+2].max(0)
print('halfway strength by x/20:', ' '.join('%d:%.3f' % (x, prof[x:x+20].mean()) for x in range(0, 760, 20)))
# near touchline column over y 250..1050
col = r[250:1050, 20:80].sum(0); print('touchline col', 20 + np.argmax(col))
# circle: hough on hp around centre
roi = r[540:740, 200:420]; u = (255 * roi / roi.max()).astype(np.uint8)
c = cv2.HoughCircles(u, cv2.HOUGH_GRADIENT, 1, 50, param1=60, param2=15, minRadius=55, maxRadius=90)
print('circles', None if c is None else (c[0][:3] + [200, 540, 0]))
# candidate box lines: rows between 280..370 and 900..1000 over x 170..450
for lo, hi in [(260, 380), (900, 1010)]:
    p = r[lo:hi, 170:450].mean(1)
    print('rows', lo, hi, [(lo + i, round(p[i], 4)) for i in np.argsort(p)[::-1][:6]])
