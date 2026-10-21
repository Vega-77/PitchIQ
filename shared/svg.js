// The one place this app makes an SVG element.
//
// This four-line factory was written six times over: `svgEl` in ui.js, and a
// private `el` in form-chart.js, heatmap.js, pass-map.js, pitch-backdrop.js and
// shot-map.js. Identical bodies -- but the signature had already drifted. Three
// of the six took `attrs` with no default, so `el('title')` threw on them and
// those call sites had to pass an empty object to a helper whose whole job was
// to save them the typing.
//
// That drift is the argument. The other half of it is recorded in
// form-chart.js, where the same copy-paste spread a broken `append(...)` chain
// through every chart in the repo and only one of them kept the bug.
// tests/test_duplicate_bodies.py holds the duplicate count at zero.
//
// Imported as `el` by the five drawing modules, where every second line calls
// it and the short name is the readable one. ui.js keeps the long name because
// nothing else in that file is about SVG.
//
// Nothing is imported here on purpose: this sits below pitch-backdrop.js, which
// had no imports at all before this and is loaded by every page with a header.

const SVG_NS = 'http://www.w3.org/2000/svg';

/** A namespaced SVG element with `attrs` applied. */
export function svgEl(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}
