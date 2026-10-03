"""Long straight paint segments (any colour) from every 2nd keyframe, in
reference-plane coordinates.  -> vsegs.npz (k, p0, p1 in ref plane, len_px)"""
import sys
import cv2, numpy as np
sys.path.insert(0, 'C:/Users/alexv/Desktop/Repos/PitchIQ')
from cv import lines as L
import vfeats
z = np.load('vmap.npz')
def apply(H, p):
    q = np.c_[p, np.ones(len(p))].dot(H.T); return q[:, :2] / q[:, 2:3]
cap = cv2.VideoCapture(vfeats.VIDEO)
K, A, B, N = [], [], [], []
for k in range(0, len(z['abs']), 2):
    if not z['ok'][k]: continue
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][k])); ok, im = cap.read()
    if not ok: continue
    fm = cv2.erode(L.field_mask(im), np.ones((21, 21), np.uint8))
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    # thin-line response: pixels differing from a local median (paint of any colour)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    d = cv2.absdiff(hsv, cv2.medianBlur(hsv, 21)).astype(np.int32)
    resp = ((d[..., 1] + d[..., 2]) > 45).astype(np.uint8) * 255
    resp[fm == 0] = 0
    s = cv2.HoughLinesP(resp, 1, np.pi / 720, 80, minLineLength=120, maxLineGap=15)
    if s is None: continue
    s = s.reshape(-1, 4).astype(np.float64)
    ln = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    s, ln = s[np.argsort(-ln)[:40]], np.sort(ln)[::-1][:40]
    A.append(apply(z['H'][k], s[:, :2])); B.append(apply(z['H'][k], s[:, 2:]))
    K.append(np.full(len(s), k)); N.append(ln)
np.savez('vsegs.npz', k=np.concatenate(K), p0=np.concatenate(A), p1=np.concatenate(B), len=np.concatenate(N))
print(len(np.concatenate(K)), 'segments from', len(K), 'keyframes')
