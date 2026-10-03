"""Draw the fitted pitch back onto keyframes.  python vovl.py abs1 abs2 ..."""
import sys, cv2, numpy as np, vfeats
z = np.load('vmap.npz'); t = np.load('vtop.npz'); pz = np.load('vpitch.npz')
L, W = float(pz['L']), float(pz['W'])
sx, sy = 120 / L, 80 / W                      # R units -> StatsBomb


def polylines():
    """Pitch paint in StatsBomb coords (x along length, y across)."""
    P = []
    seg = lambda a, b, n=40: [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]
    P.append(seg((0, 0), (120, 0)) + seg((120, 0), (120, 80))[1:] + seg((120, 80), (0, 80))[1:] + seg((0, 80), (0, 0))[1:])
    P.append(seg((60, 0), (60, 80)))
    P.append([(60 + sx * np.sin(a), 40 + sy * np.cos(a)) for a in np.linspace(0, 2 * np.pi, 80)])
    for g in (0, 120):
        d = 1 if g == 0 else -1
        for depth, hw in ((1.8, 2.2), (0.6, 1.0)):
            x1 = g + d * depth * sx
            P.append(seg((g, 40 - hw * sy), (x1, 40 - hw * sy)) + seg((x1, 40 - hw * sy), (x1, 40 + hw * sy))[1:] + seg((x1, 40 + hw * sy), (g, 40 + hw * sy))[1:])
    return [np.array(p, np.float64) for p in P]


def frame_to_sb(k):
    return pz['P'].dot(t['T']).dot(t['B']).dot(np.linalg.inv(t['K'])).dot(z['H'][k])


if __name__ == '__main__':
    cap = cv2.VideoCapture(vfeats.VIDEO); out = []
    for ab in map(int, sys.argv[1:]):
        k = int(np.argmin(abs(z['abs'] - ab)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
        Hi = np.linalg.inv(frame_to_sb(k))
        for p in polylines():
            q = np.c_[p, np.ones(len(p))].dot(Hi.T)
            good = q[:, 2] > 0
            q = q[:, :2] / q[:, 2:]
            for a, b, ga, gb in zip(q[:-1], q[1:], good[:-1], good[1:]):
                if ga and gb and abs(a).max() < 5000 and abs(b).max() < 5000:
                    cv2.line(im, tuple(np.int32(a)), tuple(np.int32(b)), (255, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(im, 'abs %d' % z['abs'][k], (20, 40), 0, 1.2, (255, 255, 255), 3)
        out.append(cv2.resize(im, (960, 540)))
    while len(out) % 2: out.append(np.zeros_like(out[0]))
    cv2.imwrite('ovl.jpg', np.vstack([np.hstack(out[i:i + 2]) for i in range(0, len(out), 2)]))
