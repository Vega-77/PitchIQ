import sys, cv2, numpy as np, vfeats, vovl
z = np.load('vid1_ballsb.npz'); a = list(z['abs'])
cap = cv2.VideoCapture(vfeats.VIDEO); out = []
sys.path.insert(0, '..'); import render_vid as RV
by = RV.picks_for('vid1')
for f in map(int, sys.argv[1:]):
    i = a.index(f); H = z['H'][i]
    cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, im = cap.read()
    Hi = np.linalg.inv(H)
    for pl in vovl.polylines():
        q = np.c_[pl, np.ones(len(pl))].dot(Hi.T); q = q[:, :2] / q[:, 2:]
        cv2.polylines(im, [np.int32(q)], False, (255, 0, 255), 2, cv2.LINE_AA)
    r = by[f]
    if r.get('src'): cv2.circle(im, (int(r['x']), int(r['y'])), 22, (0, 255, 0), 3)
    cv2.putText(im, '%d  sb %.1f %.1f' % (f, z['x'][i], z['y'][i]), (20, 50), 0, 1.4, (255, 255, 255), 3)
    out.append(cv2.resize(im, (960, 540)))
cv2.imwrite('dbg.jpg', np.vstack([np.hstack(out[i:i + 2]) for i in range(0, len(out), 2)]))
