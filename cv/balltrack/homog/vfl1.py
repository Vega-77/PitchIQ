import sys, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
z = np.load('vid1_ballsb.npz'); a = z['abs']; im = np.load('vid1_img.npz')
lo, hi = int(sys.argv[1]), int(sys.argv[2]); m = (a >= lo) & (a <= hi)
t = (a[m] - lo) / 30.
fig, ax = plt.subplots(3, 1, figsize=(16, 9), sharex=True)
ax[0].plot(t, np.clip(z['x'][m], -20, 140), '.', ms=2); ax[0].set_ylabel('SB x'); ax[0].axhline(0); ax[0].axhline(120)
ax[1].plot(t, np.clip(z['y'][m], -20, 120), '.', ms=2); ax[1].set_ylabel('SB y'); ax[1].axhline(80); ax[1].axhline(0)
ax[2].plot(t, im['iy'][m], '.', ms=2, label='img y'); ax[2].plot(t, im['ix'][m] / 2, '.', ms=2, label='img x/2'); ax[2].invert_yaxis(); ax[2].legend()
for x in ax: x.grid(alpha=.3)
ax[2].set_xlabel('s from frame %d' % lo)
plt.tight_layout(); plt.savefig('fl_%d.png' % lo, dpi=70)
