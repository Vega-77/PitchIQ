import sys, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
tag = 'vid1'; lo, hi = int(sys.argv[1]), int(sys.argv[2])
b = np.load('%s_ball3d.npz' % tag); im = np.load('%s_img.npz' % tag); F = np.load('%s_flights.npz' % tag)['F']
a = b['abs']; m = (a >= lo) & (a <= hi); t = (a[m] - lo) / 30.
fig, ax = plt.subplots(3, 1, figsize=(16, 8), sharex=True)
ax[0].plot(t, im['iy'][m], '.', ms=2, c='k'); ax[0].invert_yaxis(); ax[0].set_ylabel('img y')
st = b['state'][m]
for s, c in [('ground', 'g'), ('air', 'r'), ('line', 'orange')]:
    k = st == s
    ax[1].plot(t[k], np.clip(b['y'][m][k], -20, 120), '.', ms=2, c=c, label=s)
ax[1].axhline(80); ax[1].legend(loc='upper right'); ax[1].set_ylabel('SB y (fixed)')
ax[2].plot(t, np.where(st == 'air', b['z'][m] / 1.78, np.nan), c='r'); ax[2].set_ylabel('height (circle radii)')
for r in F:
    if hi >= r[0] and r[1] >= lo:
        for x in ax: x.axvspan((r[0] - lo) / 30, (r[1] - lo) / 30, color='r', alpha=.12)
for x in ax: x.grid(alpha=.3); x.set_xticks(np.arange(0, (hi - lo) / 30 + 1, 2))
plt.tight_layout(); plt.savefig('fl3_%d.png' % lo, dpi=70)
