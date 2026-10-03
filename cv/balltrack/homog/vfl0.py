import os, sys, numpy as np
TAG = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
sys.path.insert(0, '..'); import render_vid as RV
z = np.load(TAG + '_ballsb.npz'); a = z['abs']; H = z['H']
by = RV.picks_for(TAG)
ix = np.array([by.get(f, {}).get('x', np.nan) if by.get(f, {}).get('src') else np.nan for f in a])
iy = np.array([by.get(f, {}).get('y', np.nan) if by.get(f, {}).get('src') else np.nan for f in a])
np.savez(TAG + '_img.npz', abs=a, ix=ix, iy=iy)
x, y = z['x'], z['y']
v = np.hypot(np.diff(x), np.diff(y)) * 30          # SB units / s
ok = np.isfinite(v)
print('speed pct (units/s):', np.nanpercentile(v, [50, 90, 95, 98, 99]).round(1))
fast = np.where(ok & (v > 30))[0]
runs = []; 
for i in fast:
    if runs and i - runs[-1][1] <= 6: runs[-1][1] = i
    else: runs.append([i, i])
print(len(runs), 'fast runs')
for s, e in runs[:40]:
    seg = slice(max(0, s - 5), e + 6)
    print(a[s], a[e], 'len', e - s + 1, 'maxv %.0f' % np.nanmax(v[s:e + 1]),
          'x %.0f..%.0f y %.0f..%.0f' % (np.nanmin(x[seg]), np.nanmax(x[seg]), np.nanmin(y[seg]), np.nanmax(y[seg])))
