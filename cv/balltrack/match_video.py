"""Ball (Hybrid 90 + flight chain) and players (cv/pitchtrack + cv/linking) together.

  camera  team-coloured ring at each player's feet with their id, the ball ring
          and its last second (orange ring + drop line = in the air)
  map     everyone on the pitch, camera side down, with the camera's footprint;
          players out of the picture are cv/topdown's guesses, drawn as rings

Needs `sh hybrid.sh lo hi tag` and `python -m cv.experiments.player_lab extract
--tag tag --lo lo --n (hi-lo)` first. Writes a large H.264 master (~140 MB a
minute: Media Foundation ties the bitrate to pixels x fps); shrink it for a phone
with Windows' own transcoder, no ffmpeg needed:

    powershell -File shrink.ps1 -In match_<tag>_<lo>.mp4 -Out small.mp4 -Bitrate 3300000

    python match_video.py tag lo hi            -> match_<tag>_<lo>.mp4   (hi exclusive)
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); HG = os.path.join(HERE, 'h3d_hyb90')
sys.path.insert(0, HERE); sys.path.insert(0, HG)
os.chdir(HG)                                      # vovl and vflight read their maps from here
import render_vid as RV      # noqa: E402  (clock)
import vovl                  # noqa: E402
import vflight as V          # noqa: E402

WD = 720; CW, CH = 720, 405; TOP = 44
MS = 5.4                                      # map px per StatsBomb unit
OX = (WD - int(128 * MS)) // 2; OY = TOP + CH + 10
LEG = OY + int(88 * MS) + 6
HT = LEG + 34; HT += HT % 2
SC = CW / 1920.
FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE, GREY, BLACK = (255, 255, 255), (150, 150, 150), (0, 0, 0)
GREEN, ORANGE, CYAN = (80, 230, 80), (40, 140, 255), (230, 200, 60)
DARK, LIGHT = (60, 60, 235), (235, 170, 50)   # the darker kit drawn red, the lighter blue
KEEPER, REF, UNK = (0, 220, 255), (200, 90, 255), (200, 200, 200)


def mm(x, y):
    return (int(OX + (np.clip(x, -4, 124) + 4) * MS), int(OY + (84 - np.clip(y, -4, 84)) * MS))


def text(im, s, org, sc, col, th=1):
    cv2.putText(im, s, org, FONT, sc, BLACK, th + 2, cv2.LINE_AA)
    cv2.putText(im, s, org, FONT, sc, col, th, cv2.LINE_AA)


def ring(im, c, r, col):
    c = (int(c[0]), int(c[1]))
    cv2.circle(im, c, r, BLACK, 4, cv2.LINE_AA); cv2.circle(im, c, r, col, 2, cv2.LINE_AA)


def ball(tag, lo, hi):
    """Per frame: image point, StatsBomb point, kind, ground point under an air ball."""
    tr = V.Track(tag)
    a0 = int(tr.abs[0])
    B = np.load(os.path.join(HG, '%s_ball3dd.npz' % tag))
    P = np.load(os.path.join(HERE, 'alltests', 'p2d', 'hyb90', '%s.npz' % tag))
    n = hi - lo
    img = np.full((n, 2), np.nan); sb = np.full((n, 2), np.nan); gnd = np.full((n, 2), np.nan); kind = [''] * n
    for k in range(n):
        i = lo - a0 + k
        if str(B['state'][i]) == 'air' and np.isfinite(B['x'][i]):
            q = np.linalg.solve(tr.A, [B['x'][i], B['y'][i], 1.0]); uv = q[:2] / q[2]
            b_, g_ = tr.project(np.full(2, i), np.array([[uv[0], uv[1], B['z'][i]], [uv[0], uv[1], 0.0]]))
            img[k], gnd[k], kind[k] = b_, g_, 'air'
        elif P['src'][i] > 0 and np.isfinite(P['x'][i]):
            img[k] = P['x'][i], P['y'][i]; kind[k] = 'ground'
        if np.isfinite(B['x'][i]):
            sb[k] = B['x'][i], B['y'][i]
    return a0, img, sb, gnd, kind


def kit_name(lab) -> str:
    """A word for a median shirt colour (OpenCV 8-bit Lab): its hue when it has
    one, else how light it is."""
    L, a, b = float(lab[0]), float(lab[1]) - 128, float(lab[2]) - 128
    if np.hypot(a, b) >= 15:
        h = np.degrees(np.arctan2(b, a))
        for name, hi in (('blue', -45), ('purple', -10), ('red', 60), ('yellow', 100), ('green', 180)):
            if h <= hi:
                return name
    return 'black' if L < 110 else 'white'


def players(tag):
    from cv.experiments import player_lab as L

    det, meta = L.load_detections(tag)
    proj, pitch = L.projector(tag, meta['w'], meta['h'])
    _, res = L.run_variant('A+B+C', det, meta, proj, pitch)
    # which label is the black kit: the darker median shirt
    shade, shirt = {}, {}
    for t in ('team_a', 'team_b'):
        rows = np.concatenate([i.rows[i.rows >= 0] for i in res.identities if i.team == t and i.role == 'player'] or [[]]).astype(int)
        shirt[t] = np.nanmedian(det.desc[rows, :3], axis=0) if len(rows) else np.full(3, np.nan)
        shade[t] = float(shirt[t][0])
    dark = min(shade, key=lambda t: shade[t] if np.isfinite(shade[t]) else 1e9)
    light = 'team_b' if dark == 'team_a' else 'team_a'
    col = {dark: DARK, light: LIGHT}
    names = [kit_name(shirt[t]) if np.isfinite(shirt[t]).all() else w for t, w in ((dark, 'dark'), (light, 'light'))]
    if names[0] == names[1]:
        names = ['dark ' + names[0], 'light ' + names[1]]
    return res, col, {DARK: names[0], LIGHT: names[1]}


def footprint(H):
    """The ground the camera sees: up each side edge as far as it stays near the pitch."""
    def sb(u, v):
        q = H.dot([u, v, 1.0]); return q[:2] / q[2]

    def top(u):
        best = 1079
        for v in range(1079, -1, -20):
            q = H.dot([u, v, 1.0])
            if q[2] <= 0:
                break
            x, y = q[:2] / q[2]
            if not (-10 <= x <= 130 and -10 <= y <= 90):
                break
            best = v
        return best
    return [sb(0, 1079), sb(1919, 1079), sb(1919, top(1919)), sb(0, top(0))]


CH_M = 9.15 / V.R_CH                          # metres in one camera height


def pitch_map(tag, res, Hs, a0, lo, hi, settings=None):
    """cv/topdown on this clip: everyone's feet in metres, every frame, guesses while unseen."""
    from cv import topdown
    from cv.experiments import player_lab as L
    from cv.pitchtrack import PitchProjector

    _, pitch = L.projector(tag, 1920, 1080)
    D = np.diag([pitch.length_m / 120, pitch.width_m / 80, 1.0])
    proj = PitchProjector({f: D @ Hs[f - a0] for f in range(lo, hi)}, 1920, 1080)
    Ai = np.linalg.inv(V.Track(tag).A)

    def person_px(fs, boxes):
        """Pixel height of a 1.75 m person standing at each box's feet (the camera's 3D model)."""
        out = np.empty(len(fs))
        for n, (f, b) in enumerate(zip(fs, boxes)):
            M = Ai @ Hs[f - a0]
            g = M @ [(b[0] + b[2]) / 2, b[3], 1.0]
            q = np.linalg.inv(M) @ [g[0] / g[2], g[1] / g[2], 1 - 1.75 / CH_M]
            out[n] = abs(b[3] - q[1] / q[2])
        return out
    td = topdown.build(res, proj, lo, hi, pitch.length_m, pitch.width_m, settings, person_px=person_px)
    return td, (120 / pitch.length_m, 80 / pitch.width_m)


def colour(ident, col, team=None):
    if ident.role == 'referee':
        return REF
    if ident.role == 'keeper':
        return KEEPER
    return col.get(ident.team if team is None else team, UNK)


def group_colour(group, col):
    team, role = group
    return REF if role == 'referee' else KEEPER if role == 'keeper' else col.get(team, UNK)


def blend_dot(im, p, r, c, a, ring=False):
    """A dot drawn at strength a (0..1) over what is already there; ring: a hollow-looking guess."""
    x0, y0 = max(p[0] - r - 2, 0), max(p[1] - r - 2, 0)
    x1, y1 = min(p[0] + r + 3, im.shape[1]), min(p[1] + r + 3, im.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    roi = im[y0:y1, x0:x1]
    top = roi.copy()
    q = (p[0] - x0, p[1] - y0)
    if ring:
        cv2.circle(top, q, r, BLACK, 4, cv2.LINE_AA)
        cv2.circle(top, q, r, c, 2, cv2.LINE_AA)
    else:
        cv2.circle(top, q, r, c, -1, cv2.LINE_AA)
        cv2.circle(top, q, r, BLACK, 1, cv2.LINE_AA)
    cv2.addWeighted(top, a, roi, 1 - a, 0, dst=roi)


def main():
    import feat4
    tag, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    a0, bimg, bsb, bgnd, bkind = ball(tag, lo, hi)
    Hs = np.load(os.path.join(HG, '%s_smooth2.npz' % tag))['H']
    res, col, kits = players(tag)
    td, (kx, ky) = pitch_map(tag, res, Hs, a0, lo, hi)
    from cv import checks
    print('checks:', checks.map_report(td, 120 / kx, 80 / ky), flush=True)
    t0 = time.time()

    base = np.zeros((HT, WD, 3), np.uint8); base[:] = (24, 20, 16)
    cv2.rectangle(base, mm(-3, 83), mm(123, -3), (45, 105, 50), -1)
    for pl in vovl.polylines():
        cv2.polylines(base, [np.int32([mm(x, y) for x, y in pl])], False, (225, 235, 225), 1, cv2.LINE_AA)
    text(base, 'pitch, camera side down', (OX + 4, LEG - 10), 0.45, GREY)
    lx = 10
    for s, c in ((kits[DARK], DARK), (kits[LIGHT], LIGHT), ('unsure', UNK), ('keeper', KEEPER), ('referee', REF),
                 ('ball', GREEN), ('ball in air', ORANGE)):
        cv2.circle(base, (lx + 6, HT - 15), 6, c, -1, cv2.LINE_AA); text(base, s, (lx + 16, HT - 10), 0.45, (210, 210, 210))
        lx += 30 + 9 * len(s)
    blend_dot(base, (WD - 205, LEG - 14), 5, (200, 200, 200), 0.8, ring=True)
    text(base, 'ring = out of view, a guess', (WD - 195, LEG - 10), 0.4, GREY)

    out_p = os.path.join(HERE, 'match_%s_%d.mp4' % (tag, lo))
    vw = cv2.VideoWriter(out_p, cv2.CAP_MSMF, cv2.VideoWriter_fourcc(*'H264'), 30, (WD, HT))
    assert vw.isOpened(), 'no H.264 writer'
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    for k in range(hi - lo):
        ok, fr = cap.read()
        if not ok:
            break
        f = lo + k; i = f - a0
        im = base.copy()
        cam = cv2.resize(fr, (CW, CH), interpolation=cv2.INTER_AREA)
        here = res.at(f)
        dots = td.at(f)
        n = {DARK: 0, LIGHT: 0}
        # players: feet ring on the camera; far ones first
        for ident, b in sorted(here, key=lambda ib: ib[1][3]):
            if ident.role == 'off':
                continue
            c = colour(ident, col, res.team_at(ident, f))
            if c in n:
                n[c] += 1
            u, v = (b[0] + b[2]) / 2 * SC, b[3] * SC
            w = max(6, (b[2] - b[0]) * SC * 0.6)
            cv2.ellipse(cam, (int(u), int(v)), (int(w), int(w * 0.35)), 0, 0, 360, BLACK, 4, cv2.LINE_AA)
            cv2.ellipse(cam, (int(u), int(v)), (int(w), int(w * 0.35)), 0, 0, 360, c, 2, cv2.LINE_AA)
            text(cam, res.label(ident), (int(u) - 6, int(b[1] * SC) - 3), 0.38, c)
        # the map: guesses for the unseen first, faded, then everyone seen
        for d in dots:
            if not d.seen:
                blend_dot(im, mm(d.x * kx, d.y * ky), 5, group_colour(d.group, col), d.alpha, ring=True)
        for d in dots:
            if d.seen:
                c = colour(d.ident, col, res.team_at(d.ident, f))
                p = mm(d.x * kx, d.y * ky)
                cv2.circle(im, p, 7, BLACK, -1, cv2.LINE_AA); cv2.circle(im, p, 5, c, -1, cv2.LINE_AA)
                text(im, res.label(d.ident), (p[0] + 7, p[1] - 5), 0.36, WHITE)
        # ball: the last second of its path, then the ring
        Hc = np.linalg.inv(Hs[i]); prev = None
        for j in range(max(0, k - 30), k + 1):
            pt = None
            if np.isfinite(bimg[j, 0]):
                q = Hc.dot(Hs[lo - a0 + j].dot([bimg[j, 0], bimg[j, 1], 1.0])); pt = q[:2] / q[2] * SC
            if pt is not None and prev is not None:
                cv2.line(cam, tuple(np.int32(prev)), tuple(np.int32(pt)), WHITE, 2, cv2.LINE_AA)
            prev = pt
        kd = bkind[k]
        if kd == 'air':
            c, g = bimg[k] * SC, bgnd[k] * SC
            cv2.line(cam, tuple(np.int32(c)), tuple(np.int32(g)), ORANGE, 2, cv2.LINE_AA)
            ring(cam, c, 12, ORANGE)
        elif kd == 'ground':
            ring(cam, bimg[k] * SC, 11, GREEN)
        im[TOP:TOP + CH] = cam
        cv2.polylines(im, [np.int32([mm(*p) for p in footprint(Hs[i])])], True, (0, 220, 255), 1, cv2.LINE_AA)
        for j in range(max(1, k - 60), k + 1):
            if np.isfinite(bsb[j, 0]) and np.isfinite(bsb[j - 1, 0]):
                cv2.line(im, mm(*bsb[j - 1]), mm(*bsb[j]), CYAN if bkind[j] == 'air' else WHITE, 2, cv2.LINE_AA)
        if np.isfinite(bsb[k, 0]):
            cv2.circle(im, mm(*bsb[k]), 7, BLACK, -1, cv2.LINE_AA)
            cv2.circle(im, mm(*bsb[k]), 5, CYAN if kd == 'air' else WHITE, -1, cv2.LINE_AA)
        text(im, 'BALL + PLAYERS', (10, 30), 0.7, GREEN, 2)
        text(im, {'air': 'ball in the air', 'ground': 'ball tracked', '': 'no ball'}[kd], (215, 30), 0.5,
             GREY if kd == '' else WHITE)
        text(im, '%s %d  %s %d' % (kits[DARK], n[DARK], kits[LIGHT], n[LIGHT]), (390, 30), 0.5, WHITE)
        text(im, RV.clock(f)[:5], (WD - 72, 30), 0.65, WHITE, 2)
        vw.write(im)
    vw.release()
    print('%s  %dx%d  %.1f MiB  %.0f s' % (os.path.basename(out_p), WD, HT, os.path.getsize(out_p) / 2 ** 20, time.time() - t0))


if __name__ == '__main__':
    main()
