import sys, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
z = np.load('vid1_ballsb.npz'); a = z['abs']; im = np.load('vid1_img.npz'); rr = np.load('vid1_rollrms.npy')
lo, hi = int(sys.argv[1]), int(sys.argv[2]); m = (a >= lo) & (a <= hi); t = (a[m] - lo) / 30.
fig, ax = plt.subplots(3, 1, figsize=(16, 8), sharex=True)
ax[0].plot(t, im['iy'][m], '.', ms=2); ax[0].invert_yaxis(); ax[0].set_ylabel('img y')
ax[1].plot(t, np.clip(z['y'][m], -20, 120), '.', ms=2); ax[1].axhline(80); ax[1].set_ylabel('SB y')
ax[2].semilogy(t, rr[m], '.', ms=2); ax[2].axhline(5, c='r'); ax[2].set_ylabel('roll rms px')
for x in ax: x.grid(alpha=.3); x.set_xticks(np.arange(0, (hi - lo) / 30 + 1, 2))
plt.tight_layout(); plt.savefig('fl2.png', dpi=70)
