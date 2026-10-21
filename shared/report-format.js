// Small helpers the other report-*.js modules share. None of them is part
// of `report.js`'s public face, so nothing re-exports them.

export const num = (value) => (
    typeof value === 'number' && Number.isFinite(value) ? value : null
);

export const share = (value) => (value == null ? null : `${Math.round(value * 100)}%`);

// `ui.js` has a `plural`, and this module imports nothing on purpose so that
// the pure test suites can load it. Two words is a cheaper duplication than an
// import that would take those suites' reason for existing with it.
export const count = (n, word, plural = `${word}s`) => `${n} ${n === 1 ? word : plural}`;
