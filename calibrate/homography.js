// A plane homography from pinned landmarks, and how far off it is, in metres.
//
// This is only the page's check. The calibration the pipeline uses is still
// fitted in Python (`python -m pitchiq.experiments.calibrate`), from the file this
// page saves. The page fits its own so a person can see, while still clicking,
// whether the outline lands on the paint, and which pin is the bad one.

/** Solve the symmetric n x n matrix's eigenvector of the smallest eigenvalue (Jacobi). */
function smallestEigenvector(S) {
    const n = S.length;
    const A = S.map((row) => row.slice());
    const V = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));
    for (let sweep = 0; sweep < 100; sweep++) {
        let off = 0;
        for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) off += A[p][q] * A[p][q];
        if (off < 1e-22) break;
        for (let p = 0; p < n; p++) {
            for (let q = p + 1; q < n; q++) {
                if (Math.abs(A[p][q]) < 1e-30) continue;
                const theta = (A[q][q] - A[p][p]) / (2 * A[p][q]);
                const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
                const c = 1 / Math.sqrt(t * t + 1), s = t * c;
                for (let k = 0; k < n; k++) {
                    const akp = A[k][p], akq = A[k][q];
                    A[k][p] = c * akp - s * akq;
                    A[k][q] = s * akp + c * akq;
                }
                for (let k = 0; k < n; k++) {
                    const apk = A[p][k], aqk = A[q][k];
                    A[p][k] = c * apk - s * aqk;
                    A[q][k] = s * apk + c * aqk;
                }
                for (let k = 0; k < n; k++) {
                    const vkp = V[k][p], vkq = V[k][q];
                    V[k][p] = c * vkp - s * vkq;
                    V[k][q] = s * vkp + c * vkq;
                }
            }
        }
    }
    let best = 0;
    for (let i = 1; i < n; i++) if (A[i][i] < A[best][best]) best = i;
    return V.map((row) => row[best]);
}

/** Hartley normalisation: centroid to the origin, mean distance sqrt(2). */
function normaliser(pts) {
    const n = pts.length;
    const mx = pts.reduce((a, p) => a + p[0], 0) / n;
    const my = pts.reduce((a, p) => a + p[1], 0) / n;
    const d = pts.reduce((a, p) => a + Math.hypot(p[0] - mx, p[1] - my), 0) / n || 1;
    const s = Math.SQRT2 / d;
    return [[s, 0, -s * mx], [0, s, -s * my], [0, 0, 1]];
}

function mul(A, B) {
    return A.map((row) => B[0].map((_, j) => row.reduce((acc, a, k) => acc + a * B[k][j], 0)));
}

function invert(H) {
    const [[a, b, c], [d, e, f], [g, h, i]] = H;
    const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    if (!det) return null;
    return [
        [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
        [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
        [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
    ];
}

/** Apply H to a point; null when it lands at or behind infinity. */
export function apply(H, [x, y]) {
    const w = H[2][0] * x + H[2][1] * y + H[2][2];
    if (!(Math.abs(w) > 1e-12)) return null;
    return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w];
}

/** w of a point under H, whose sign says which side of the horizon it is on. */
export function depth(H, [x, y]) {
    return H[2][0] * x + H[2][1] * y + H[2][2];
}

/** Least-squares (normalised DLT) homography taking src[i] to dst[i]; null under 4 pairs. */
function fitHomography(src, dst) {
    if (src.length < 4) return null;
    const Ts = normaliser(src), Td = normaliser(dst);
    const ns = src.map((p) => apply(Ts, p)), nd = dst.map((p) => apply(Td, p));
    const S = Array.from({ length: 9 }, () => new Array(9).fill(0));
    for (let i = 0; i < ns.length; i++) {
        const [x, y] = ns[i], [u, v] = nd[i];
        for (const r of [[-x, -y, -1, 0, 0, 0, u * x, u * y, u], [0, 0, 0, -x, -y, -1, v * x, v * y, v]]) {
            for (let j = 0; j < 9; j++) for (let k = 0; k < 9; k++) S[j][k] += r[j] * r[k];
        }
    }
    const h = smallestEigenvector(S);
    const Hn = [h.slice(0, 3), h.slice(3, 6), h.slice(6, 9)];
    const Tdi = invert(Td);
    if (!Tdi) return null;
    const H = mul(mul(Tdi, Hn), Ts);
    const scale = H[2][2] || 1;
    return H.map((row) => row.map((v) => v / scale));
}

/**
 * Fit image -> pitch from pins, and say how good it is.
 *
 * `pins` is [{name, px: [x, y], m: [x, y]}]. Returns null under four pins, or
 * {H (image -> metres), Hinv (metres -> image), each: [{name, err, loo}], mean,
 * max, looMean, looMax}. `err` is how far the fit puts the pin from its
 * landmark, in metres. `loo` is the same with that pin left out of the fit
 * (five pins or more): the honest number, the one that finds a mis-click,
 * because a fit always bends towards the point it was given.
 */
export function assess(pins) {
    if (pins.length < 4) return null;
    const H = fitHomography(pins.map((p) => p.px), pins.map((p) => p.m));
    const Hinv = H && invert(H);
    if (!Hinv) return null;
    const miss = (h, p) => {
        const q = h && apply(h, p.px);
        return q ? Math.hypot(q[0] - p.m[0], q[1] - p.m[1]) : Infinity;
    };
    const each = pins.map((p, i) => {
        let loo = null;
        if (pins.length >= 5) {
            const rest = pins.filter((_, j) => j !== i);
            loo = miss(fitHomography(rest.map((r) => r.px), rest.map((r) => r.m)), p);
        }
        return { name: p.name, err: miss(H, p), loo };
    });
    const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
    const loos = each.map((e) => e.loo).filter((v) => v !== null);
    return {
        H, Hinv, each,
        mean: mean(each.map((e) => e.err)),
        max: Math.max(...each.map((e) => e.err)),
        looMean: loos.length ? mean(loos) : null,
        looMax: loos.length ? Math.max(...loos) : null,
    };
}
