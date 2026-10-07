// Team colours.
//
// A team picks two colours, the shirt and the trim, and every page that
// belongs to that team is painted in them: the top bar, the season header,
// the match-day button, the band across the top of every figure. Pages that
// belong to nobody yet wear the house kit.
//
// A school's colours are whatever the school chose decades ago, and plenty of
// them are unreadable as text — gold on white, light blue on white, black on
// navy. So nothing here uses the chosen colours as given. Every token is the
// chosen colour where that is legible, and the nearest thing to it that is
// legible where it is not:
//
//   --kit, --kit-hi    the shirt, and a lighter panel of it
//   --kit-on           lettering on the shirt: white or near-black, whichever
//                      reads better
//   --kit-2            the trim, as chosen; only ever a fill
//   --kit-2-on         lettering on the trim: the shirt if that reads on it,
//                      otherwise white or near-black
//   --kit-2-ink        the trim as lettering on the shirt, or the shirt's own
//                      ink if the two are too close to read
//   --accent, ...      the shirt as lettering on the page: darkened until it
//                      reads on the palest grey the page uses
//
// The thresholds are WCAG 2's: 4.5 for text. tests/kit.test.js runs every
// preset and a sweep of awkward colours through it and holds each pair to
// that.

export const HOUSE_KIT = Object.freeze({ primary: '#13294b', secondary: '#f2b632' });

// Common school pairings, so most coaches pick rather than mix. Names are the
// colours, not anyone's team.
export const KIT_PRESETS = Object.freeze([
    { name: 'Navy and gold', primary: '#13294b', secondary: '#f2b632' },
    { name: 'Maroon and white', primary: '#7a1f2b', secondary: '#ffffff' },
    { name: 'Royal and white', primary: '#1f4fbf', secondary: '#ffffff' },
    { name: 'Forest and gold', primary: '#1d5b37', secondary: '#f2c230' },
    { name: 'Red and black', primary: '#c8102e', secondary: '#111111' },
    { name: 'Purple and gold', primary: '#4b2a83', secondary: '#f2b632' },
    { name: 'Black and orange', primary: '#141414', secondary: '#f57c1f' },
    { name: 'Sky and navy', primary: '#6cace4', secondary: '#13294b' },
]);

const INK_LIGHT = '#ffffff';
const INK_DARK = '#141820';
// The palest ground anything coloured in the accent sits on (--surface-hi).
const PALEST_GROUND = '#e9ecf0';
const TEXT = 4.5;

const HEX = /^#[0-9a-f]{6}$/;

/** `#ABC`, `abcdef` or `#AbCdEf` → `#abcdef`, or null if it is not a colour. */
export function normaliseHex(value) {
    if (typeof value !== 'string') return null;
    let v = value.trim().toLowerCase();
    if (!v.startsWith('#')) v = '#' + v;
    if (/^#[0-9a-f]{3}$/.test(v)) v = '#' + [...v.slice(1)].map((c) => c + c).join('');
    return HEX.test(v) ? v : null;
}

function rgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function hex([r, g, b]) {
    return '#' + [r, g, b]
        .map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, '0'))
        .join('');
}

function luminance(color) {
    const [r, g, b] = rgb(color).map((c) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

/** `a` moved `t` of the way toward `b`, in sRGB. */
function mix(a, b, t) {
    const [x, y] = [rgb(a), rgb(b)];
    return hex(x.map((c, i) => c + (y[i] - c) * t));
}

/**
 * White or near-black, whichever reads better on `ground`. A mid-grey sits
 * where neither quite reaches 4.5, and only true black does.
 */
function inkFor(ground) {
    const light = contrast(ground, INK_LIGHT);
    const dark = contrast(ground, INK_DARK);
    if (Math.max(light, dark) >= TEXT) return light >= dark ? INK_LIGHT : INK_DARK;
    return light >= contrast(ground, '#000000') ? INK_LIGHT : '#000000';
}

/** `color`, darkened in small steps until it reaches `ratio` against `ground`. */
function darkenUntil(color, ground, ratio) {
    let c = color;
    for (let i = 0; i < 40 && contrast(c, ground) < ratio; i++) c = mix(c, '#000000', 0.06);
    return c;
}

function alpha(color, a) {
    const [r, g, b] = rgb(color);
    return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/**
 * The custom properties for a kit. Anything that is not two colours falls
 * back to the house kit, so a half-written team document still renders.
 */
export function kitTokens(kit) {
    const primary = normaliseHex(kit?.primary) ?? HOUSE_KIT.primary;
    const secondary = normaliseHex(kit?.secondary) ?? HOUSE_KIT.secondary;

    const kitOn = inkFor(primary);
    const trimReads = contrast(secondary, primary) >= TEXT;
    // A brighter panel of the shirt, for the glow in a header's corner. Each
    // channel scaled up rather than mixed toward white, which would wash navy
    // out to slate; a near-black shirt has nothing to scale and is lifted a
    // little toward grey instead. As bright as half again, and less if more
    // would stop the lettering reading across it: purple and gold pass on the
    // shirt and fail much brighter.
    const legibleOn = (ground) => contrast(kitOn, ground) >= TEXT
        && (!trimReads || contrast(secondary, ground) >= TEXT);
    const brighten = (k) => mix(hex(rgb(primary).map((c) => c * (1 + k))), '#ffffff', luminance(primary) < 0.01 ? k / 5 : 0);
    let kitHi = primary;
    // A pale shirt is already near the top: a little brighter is a glow, half
    // again clips to cyan.
    const most = kitOn === INK_LIGHT ? 0.5 : 0.15;
    for (let k = most; k > 0.001; k -= most / 5) {
        const candidate = brighten(k);
        if (legibleOn(candidate)) { kitHi = candidate; break; }
    }

    const kit2On = trimReads ? primary : inkFor(secondary);
    const kit2Ink = trimReads ? secondary : kitOn;

    const accent = darkenUntil(primary, PALEST_GROUND, TEXT);
    const lighter = mix(accent, '#ffffff', 0.14);
    const accentHi = contrast(lighter, PALEST_GROUND) >= TEXT ? lighter : mix(accent, '#000000', 0.2);

    return {
        '--kit': primary,
        '--kit-hi': kitHi,
        '--kit-on': kitOn,
        '--kit-2': secondary,
        '--kit-2-on': kit2On,
        '--kit-2-ink': kit2Ink,
        '--accent': accent,
        '--accent-hi': accentHi,
        '--accent-dim': alpha(accent, 0.08),
        '--on-accent': inkFor(accent),
    };
}

/**
 * What a coach should hear about a pair before saving it. Nothing here stops
 * them: it is their school's kit. But a trim the same shade as the shirt
 * vanishes from every band and button, and that is worth a sentence.
 */
export function kitWarnings(kit) {
    const primary = normaliseHex(kit?.primary);
    const secondary = normaliseHex(kit?.secondary);
    if (!primary || !secondary) return ['Pick two colours.'];
    const out = [];
    if (contrast(primary, secondary) < 1.6) {
        out.push('These two are close in shade, so the trim will barely show against the shirt.');
    }
    return out;
}

const STORE = 'piq-kit';

/**
 * Paint the page in a kit. `remember` keeps it for the next page load, which
 * assets/kit-boot.js applies before anything draws, so a team's pages do not
 * open in navy and then change colour.
 */
export function applyKit(kit, { root = globalThis.document?.documentElement, remember = true } = {}) {
    const tokens = kitTokens(kit);
    if (root?.style) {
        for (const [name, value] of Object.entries(tokens)) root.style.setProperty(name, value);
    }
    if (remember) {
        try {
            globalThis.localStorage?.setItem(STORE, JSON.stringify(tokens));
        } catch {
            // Private windows and blocked storage: the page is still painted.
        }
    }
    return tokens;
}

/** Back to the house kit, and forget the last team's. */
export function clearKit({ root = globalThis.document?.documentElement } = {}) {
    if (root?.style) {
        for (const name of Object.keys(kitTokens(HOUSE_KIT))) root.style.removeProperty(name);
    }
    try {
        globalThis.localStorage?.removeItem(STORE);
    } catch {
        // As above.
    }
}
