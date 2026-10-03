"""Why each labelled flight was missed: ball coverage, and how a fit on the
true window scores against the scanner's gates."""
import os
import numpy as np
import vflight as V
import vflscan as S
import vdrag as D

HERE = os.path.dirname(os.path.abspath(__file__))
T = np.load(os.path.join(HERE, 'airtruth.npz'))
tr = V.Track('vid1')
V.ground_all(tr)
fid, ab = T['fid'], T['abs']
print('%-13s %5s %5s %6s %6s %6s %6s %6s  %s' % ('flight', 'dur', 'has', 'rms', 'apex', 'roll', 'drms', 'gate', 'why'))
cnt = {}
for k in range(fid.max() + 1):
    idx = np.where(fid == k)[0]
    if not len(idx):
        continue
    a, b = int(ab[idx[0]] - tr.abs[0]), int(ab[idx[-1]] - tr.abs[0])
    L = b - a
    h = tr.has[a:b + 1]
    why = []
    if L < 9:
        why.append('short')
    if h.mean() < 0.7:
        why.append('coverage')
    # nearest ends with a detection
    ha = np.where(tr.has[a:b + 1])[0]
    rms = apex = roll = drms = np.nan
    if len(ha) >= 6:
        aa, bb = a + ha[0], a + ha[-1]
        r = S.quick_fit(tr, aa, bb)
        if r:
            th, rms, _ = r
            apex = V.G * th[5] ** 2 / 8
            roll = S.roll_fit(tr, aa, bb)
            if rms > S.MAX_RMS: why.append('rms')
            if apex < S.MIN_APEX: why.append('low')
            if roll < max(S.ROLL_RATIO * rms, S.ROLL_MIN): why.append('roll-ok')
        try:
            th2, e = D.fit(tr, aa, bb); drms = float(np.sqrt(np.mean(e ** 2)))
        except Exception:
            pass
    else:
        why.append('nodet')
    for w in why or ['pass']:
        cnt[w] = cnt.get(w, 0) + 1
    print('%d-%d  %4.1f %5.2f %6.1f %6.2f %6.1f %6.1f %6s  %s' % (
        ab[idx[0]], ab[idx[-1]], (L + 1) / 30, h.mean(), rms, apex, roll, drms, '', ','.join(why) or 'PASS'))
print(cnt)
