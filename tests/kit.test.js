// Team colours: every token a kit produces has to be legible where it is used,
// whatever two colours a school happens to have.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    HOUSE_KIT, KIT_PRESETS, applyKit, clearKit, contrast, kitTokens, kitWarnings, normaliseHex,
} from '../assets/kit.js';

// The page grounds from app.css that accent-coloured text sits on.
const GROUNDS = ['#f2f3f5', '#f8f9fa', '#ffffff', '#e9ecf0'];

// The presets, plus pairs chosen to be awkward: pale shirts, a trim the same
// as the shirt, pure black and pure white, two mid-tones.
const AWKWARD = [
    { primary: '#ffffff', secondary: '#13294b' },
    { primary: '#f2b632', secondary: '#ffffff' },
    { primary: '#ffff00', secondary: '#000000' },
    { primary: '#000000', secondary: '#000000' },
    { primary: '#13294b', secondary: '#13294b' },
    { primary: '#808080', secondary: '#7f7f7f' },
    { primary: '#767676', secondary: '#757575' },
    { primary: '#7a7a7a', secondary: '#ffffff' },
    { primary: '#6cace4', secondary: '#ffffff' },
    { primary: '#ff69b4', secondary: '#00ff00' },
];
const KITS = [...KIT_PRESETS, ...AWKWARD];

for (const kit of KITS) {
    const name = kit.name ?? `${kit.primary} / ${kit.secondary}`;

    test(`${name}: lettering on the shirt reads`, () => {
        const t = kitTokens(kit);
        assert.ok(contrast(t['--kit-on'], t['--kit']) >= 4.5);
        assert.ok(contrast(t['--kit-on'], t['--kit-hi']) >= 4.5);
        assert.ok(contrast(t['--kit-2-ink'], t['--kit']) >= 4.5);
        assert.ok(contrast(t['--kit-2-ink'], t['--kit-hi']) >= 4.5);
    });

    test(`${name}: lettering on the trim reads`, () => {
        const t = kitTokens(kit);
        assert.ok(contrast(t['--kit-2-on'], t['--kit-2']) >= 4.5);
    });

    test(`${name}: the accent reads on every page ground`, () => {
        const t = kitTokens(kit);
        for (const ground of GROUNDS) {
            assert.ok(contrast(t['--accent'], ground) >= 4.5, `${t['--accent']} on ${ground}`);
            assert.ok(contrast(t['--accent-hi'], ground) >= 4.5, `${t['--accent-hi']} on ${ground}`);
        }
        assert.ok(contrast(t['--on-accent'], t['--accent']) >= 4.5);
    });

    test(`${name}: the chosen colours are kept where they read`, () => {
        const t = kitTokens(kit);
        assert.equal(t['--kit'], normaliseHex(kit.primary));
        assert.equal(t['--kit-2'], normaliseHex(kit.secondary));
    });
}

test('the house kit is the one app.css is written in', async () => {
    const { readFile } = await import('node:fs/promises');
    const css = await readFile(new URL('../assets/app.css', import.meta.url), 'utf8');
    const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')));
    for (const [name, value] of Object.entries(kitTokens(HOUSE_KIT))) {
        const m = root.match(new RegExp(`\\s${name}:\\s*([^;]+);`));
        assert.ok(m, `${name} is missing from :root`);
        const norm = (v) => v.replace(/\s+/g, '').toLowerCase().replace(/([(,])0\./g, '$1.');
        assert.equal(norm(m[1]), norm(value), name);
    }
});

test('a kit that is not two colours falls back to the house kit', () => {
    assert.deepEqual(kitTokens(undefined), kitTokens(HOUSE_KIT));
    assert.deepEqual(kitTokens({ primary: 'red', secondary: 12 }), kitTokens(HOUSE_KIT));
});

test('hex is normalised and anything else refused', () => {
    assert.equal(normaliseHex('#ABC'), '#aabbcc');
    assert.equal(normaliseHex('13294B'), '#13294b');
    assert.equal(normaliseHex('#13294bff'), null);
    assert.equal(normaliseHex('navy'), null);
    assert.equal(normaliseHex(null), null);
});

test('a trim the shade of the shirt gets a warning, a good pair none', () => {
    assert.equal(kitWarnings(HOUSE_KIT).length, 0);
    assert.equal(kitWarnings({ primary: '#13294b', secondary: '#1a2f50' }).length, 1);
    assert.deepEqual(kitWarnings({ primary: '#13294b' }), ['Pick two colours.']);
});

test('applyKit paints the root and remembers; clearKit undoes both', () => {
    const set = new Map();
    const root = {
        style: {
            setProperty: (k, v) => set.set(k, v),
            removeProperty: (k) => set.delete(k),
        },
    };
    const store = new Map();
    globalThis.localStorage = {
        setItem: (k, v) => store.set(k, v),
        getItem: (k) => store.get(k) ?? null,
        removeItem: (k) => store.delete(k),
    };
    try {
        applyKit(KIT_PRESETS[1], { root });
        assert.equal(set.get('--kit'), '#7a1f2b');
        assert.deepEqual(JSON.parse(store.get('piq-kit')), kitTokens(KIT_PRESETS[1]));
        clearKit({ root });
        assert.equal(set.size, 0);
        assert.equal(store.has('piq-kit'), false);
    } finally {
        delete globalThis.localStorage;
    }
});

test('applyKit still paints when storage throws', () => {
    const set = new Map();
    const root = { style: { setProperty: (k, v) => set.set(k, v) } };
    globalThis.localStorage = { setItem: () => { throw new Error('blocked'); } };
    try {
        applyKit(HOUSE_KIT, { root });
        assert.equal(set.get('--kit'), HOUSE_KIT.primary);
    } finally {
        delete globalThis.localStorage;
    }
});
