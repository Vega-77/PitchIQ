import numpy as np, cv2
for name, (y0, y1) in [('top', (100, 560)), ('bot', (720, 1200))]:
    d = np.load('vend_%s.npy' % name)[350:1600, 0:760].astype(np.float32)
    r = np.clip(d - cv2.blur(d, (21, 21)), 0, None)
    for xr in [(130, 185), (440, 500)]:
        c = r[y0:y1, xr[0]:xr[1]]
        xc = xr[0] + np.argmax(c.sum(0))
        p = r[y0:y1, xc - 2:xc + 3].max(1)
        p = np.convolve(p, np.ones(9) / 9, 'same')
        th = 0.35 * np.percentile(p, 97)
        on = np.where(p > th)[0] + y0
        print(name, 'col', xc, 'on y range', on.min() if len(on) else None, on.max() if len(on) else None,
              ' profile/20:', ' '.join('%d:%.0f' % (y, 1e4 * p[y - y0:y - y0 + 20].mean()) for y in range(y0, y1, 20)))
    # horizontal lines inside box columns
    p = r[y0:y1, 170:450].mean(1); p = np.convolve(p, np.ones(3) / 3, 'same')
    print(name, 'rows', [(y0 + i, round(1e4 * p[i])) for i in np.argsort(p)[::-1][:10]])
