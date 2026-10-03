import numpy as np
r = np.load('vid1_reg.npz'); p = np.load('../vid1pan.npz'); lo = int(p['lo']); cum = p['cum']
pts = np.array([[200, 500, 1], [1720, 500, 1], [1720, 1000, 1], [200, 1000, 1], [960, 800, 1]], float).T
def proj(H): q = H.dot(pts); return (q[:2] / q[2]).T
e1 = []; e2 = []; e0 = []
for i in range(len(r['abs']) - 1):
    a, b = r['abs'][i], r['abs'][i + 1]; Ha, Hb = r['Hsb'][i], r['Hsb'][i + 1]
    ca, cb = cum[a - lo], cum[b - lo]
    e1.append(np.abs(proj(Ha.dot(np.linalg.inv(ca)).dot(cb)) - proj(Hb)).max())
    e2.append(np.abs(proj(Ha.dot(ca).dot(np.linalg.inv(cb))) - proj(Hb)).max())
    e0.append(np.abs(proj(Ha) - proj(Hb)).max())
for n, e in [('no motion', e0), ('inv(ca)cb', e1), ('ca inv(cb)', e2)]:
    print('%-11s max SB err per half-second: median %.2f p90 %.2f p99 %.2f max %.1f' % (n, np.median(e), np.percentile(e, 90), np.percentile(e, 99), np.max(e)))
c = np.array([proj(H)[4] for H in r['Hsb']])
print('centre-point SB x range', c[:, 0].min().round(1), c[:, 0].max().round(1), ' y', c[:, 1].min().round(1), c[:, 1].max().round(1))
