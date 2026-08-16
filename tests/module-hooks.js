// Make `import 'https://www.gstatic.com/firebasejs/…'` resolvable under Node.
//
// The frontend has no build step on purpose, so the Firebase SDK arrives as a
// bare CDN URL in three files. Node cannot load an https specifier, which is the
// single reason no page module in this repo could ever be imported by a test.
//
// `node:module`'s synchronous hooks fix that in about thirty lines and add no
// dependency. The stub they serve exports exactly the names the repo imports —
// generated from `MODULES` rather than hand-listed, so a new Firestore function
// used in `db.js` fails here with "not exported" instead of silently arriving as
// undefined and being called an hour later.

import { registerHooks } from 'node:module';
import { MODULES } from './fake-firebase.js';

const CDN = 'https://www.gstatic.com/firebasejs/';
const FAKE = new URL('./fake-firebase.js', import.meta.url).href;

registerHooks({
    resolve(specifier, context, nextResolve) {
        if (specifier.startsWith(CDN)) return { url: specifier, shortCircuit: true };
        return nextResolve(specifier, context);
    },

    load(url, context, nextLoad) {
        if (!url.startsWith(CDN)) return nextLoad(url, context);

        const file = url.split('/').pop();
        const names = MODULES[file];
        if (!names) throw new Error(`No fake for the Firebase module ${file}`);

        return {
            format: 'module',
            shortCircuit: true,
            source: [
                `import * as impl from ${JSON.stringify(FAKE)};`,
                ...names.map((name) => `export const ${name} = impl.${name};`),
            ].join('\n'),
        };
    },
});
