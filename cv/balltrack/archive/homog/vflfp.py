import numpy as np
T = np.load('airtruth.npz'); tru = T['truth']; A0 = int(T['abs'][0])
F = np.load('vid1_flights.npz')['F']
tot = {'inside': 0, 'edge': 0, 'false': 0}
rows = []
for r in F:
    a, b = int(np.floor(r[0])) - A0, int(np.ceil(r[1])) - A0
    seg = tru[a:b + 1]; vis = seg != 'hidden'
    fp = ((seg == 'ground') & vis).sum()
    if not (seg == 'air').any():
        tot['false'] += fp; kind = 'FALSE'
    else:
        air = np.where(seg == 'air')[0]
        inner = ((seg[air[0]:air[-1] + 1] == 'ground')).sum()
        tot['inside'] += inner; tot['edge'] += fp - inner; kind = 'gnd-inside %d edge %d' % (inner, fp - inner)
    if fp: rows.append('%d-%d %.1fs apex %.2f rms %.1f  ground %d  %s' % (r[0], r[1], (r[1]-r[0])/30, r[6], r[7], fp, kind))
print('\n'.join(rows)); print(tot)
