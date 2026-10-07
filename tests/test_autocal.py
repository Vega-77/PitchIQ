"""The camera-height cue in cv/autocal: people seen from a known camera."""
import numpy as np

from cv import autocal as A


def _look_at(C, target):
    """World -> camera rotation for a camera at C looking at target, image y down, no roll."""
    z = target - C; z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0]); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.array([x, y, z])


def _people(C, R, f, pitch, n=400, tall=0.19, seed=0):
    """Boxes of n upright people `tall` R high, standing at random on the pitch."""
    rng = np.random.default_rng(seed)
    G = np.c_[rng.uniform(0.5, pitch.L - 0.5, n), rng.uniform(0.5, pitch.W - 0.5, n)]
    K = np.array([[f, 0, A.CENTRE[0]], [0, f, A.CENTRE[1]], [0, 0, 1.0]])

    def px(X):
        q = (X - C) @ R.T @ K.T
        return q[:, :2] / q[:, 2:]
    feet = px(np.c_[G, np.zeros(n)]); head = px(np.c_[G, np.full(n, tall)])
    w = 0.3 * (feet[:, 1] - head[:, 1])
    box = np.c_[feet[:, 0] - w, head[:, 1], feet[:, 0] + w, feet[:, 1]]
    keep = (box[:, 0] > 0) & (box[:, 2] < 1920) & (box[:, 1] > 4) & (box[:, 3] < 1075)
    return box[keep]


def test_far_near_ratio_finds_the_camera_height():
    pitch = A.PitchModel(L=12.31, W=7.47)
    C = np.array([6.2, -0.74, 0.56])
    R = _look_at(C, np.array([6.2, 3.0, 0.0]))
    f = np.array([900.0])
    boxes = {0: _people(C, R, f[0], pitch)}
    chain = np.eye(3)[None]
    ratio, med = A.far_near_ratio(chain, f, C, R, pitch, boxes)
    assert abs(ratio - 1.0) < 0.02 and abs(med - 0.19) < 0.005
    # the same picture from a guessed camera too low: far people come out taller than near ones
    low = C * [1, 1, 0.7]
    assert A.far_near_ratio(chain, f, low, _look_at(low, np.array([6.2, 3.0, 0.0])), pitch, boxes)[0] > 1.05


def test_line_paint_keeps_lines_and_drops_foliage():
    rng = np.random.default_rng(1)
    m = np.zeros((300, 600), bool)
    m[150:153, 50:550] = True                                   # a painted line
    blobs = rng.integers(0, 2, (25, 150)).repeat(4, 0).repeat(4, 1).astype(bool)   # speckle, 4 px blocks
    m[:100, :] |= blobs[:100]
    keep = A.line_paint(m)
    assert keep[150:153, 60:540].mean() > 0.95
    assert keep[:100].sum() < 0.1 * m[:100].sum()


def test_white_paint_skips_a_fence_rail_above_the_grass():
    # trees, then a fence with a white top rail, then grass with a white touchline
    im = np.zeros((1080, 1920, 3), np.uint8)
    im[:400] = (40, 60, 45)
    im[400:500] = (95, 80, 90)
    im[500:] = (150, 160, 150)             # hazy turf, as at the away field
    im[420:424] = (215, 220, 220)          # the rail
    im[540:544] = (215, 220, 220)          # the touchline
    m = A.paint_mask(im, colour='white')
    assert m[205:217].sum() == 0
    assert m[265:277, 100:860].mean() > 0.1
