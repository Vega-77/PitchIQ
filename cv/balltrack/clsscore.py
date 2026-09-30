"""The crop classifier over EVERY candidate of a clip, streamed from video.

crops.py stores pixels for a sample of frames, which is what training needs.
Tracking needs a classifier score on every candidate of every frame, and
storing 48x48 crops for 1200 frames x 40 candidates is ~330 MB a clip for
numbers that are used once.  So this cuts the same crops (crops.cut, the
same context vector from crops.context) and scores them in batches as the
video goes past, keeping only (f, j, p).

    python clsscore.py <model.pt> <tag> [<tag> ...]    # -> <tag>cls.npz
"""
import os
import sys
import time

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S2)

import crops as CR                              # noqa: E402
import dfree                                    # noqa: E402

SIZE = 48
BATCH_FRAMES = 32


def run(model, tag):
    import cv2
    import feat4

    t0 = time.time()
    frames = dfree.load(tag)
    ppl = CR.load_ppl(tag)
    has = ppl is not None
    N = sum(len(fr['s']) for fr in frames)
    F = np.empty(N, np.int32)
    J = np.empty(N, np.int16)
    P = np.empty(N, np.float32)
    buf_c, buf_k, buf_i = [], [], []
    k = 0

    def flush():
        if not buf_c:
            return
        c = np.concatenate(buf_c)
        x = np.concatenate(buf_k)
        i = np.concatenate(buf_i)
        P[i] = model.score(c, x, ctx_names=CR.CTX_NAMES)
        buf_c.clear()
        buf_k.clear()
        buf_i.clear()

    cap = cv2.VideoCapture(feat4.VIDEO)
    assert cap.isOpened(), 'cannot open video'
    pos = frames[0]['abs']
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    for q, fr in enumerate(frames):
        ab = fr['abs']
        while pos < ab:
            if not cap.grab():
                break
            pos += 1
        ok, img = cap.read()
        assert ok and pos == ab, 'read failed at %d' % ab
        pos += 1
        n = len(fr['s'])
        c = np.empty((n, SIZE, SIZE, 3), np.uint8)
        for j in range(n):
            CR.cut(img, fr['x'][j], fr['y'][j], SIZE, c[j])
        F[k:k + n] = ab
        J[k:k + n] = np.arange(n)
        buf_c.append(c)
        buf_k.append(CR.context(fr['x'], fr['y'], fr['s'],
                                ppl.get(ab) if has else None, has))
        buf_i.append(np.arange(k, k + n))
        k += n
        if (q + 1) % BATCH_FRAMES == 0:
            flush()
    flush()
    cap.release()
    np.savez_compressed(os.path.join(S2, '%scls.npz' % tag),
                        f=F[:k], j=J[:k], p=P[:k])
    el = time.time() - t0
    print('%s  %d frames  %d candidates  %.1f s  %.1f fps  ppl %s'
          % (tag, len(frames), k, el, len(frames) / el, has), flush=True)


def main():
    from train_cls import load_model
    model = load_model(sys.argv[1])
    for tag in sys.argv[2:]:
        run(model, tag)


if __name__ == '__main__':
    main()
