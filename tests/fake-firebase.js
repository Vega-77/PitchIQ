// Firestore and Auth, in memory, in one file.
//
// The pages under test reach the network through exactly three CDN modules
// (`assets/db.js`, `assets/auth.js` and `assets/firebase-init.js` are the only
// files in the repo that import from gstatic). Replacing those three is what
// lets a page be loaded in `node --test` at all.
//
// This is not a Firestore. It has no rules, no ordering guarantees beyond
// insertion, no transactions, and no network — so it can never tell you a write
// would have been rejected. `tests/rules.test.js` and `tests/flow.test.js` run
// against the real emulator and remain the only things that can. What this
// gives is the other half: a page that renders, from documents that exist.

// ---------------------------------------------------------------- the store

const store = new Map();       // 'teams/t1/matches/m1' -> plain object
const listeners = new Set();
let autoId = 0;

/** Wipe every document and listener. Called between pages. */
export function reset() {
    store.clear();
    listeners.clear();
    authState.user = null;
    authState.callbacks.clear();
    autoId = 0;
}

/** Seed documents as a flat map of full paths to data. */
export function seed(documents) {
    for (const [path, data] of Object.entries(documents)) {
        store.set(path, structuredClone(data));
    }
}

export const snapshotOf = (path) => structuredClone(store.get(path) ?? null);

export const pathsUnder = (prefix) =>
    [...store.keys()].filter((p) => p.startsWith(prefix));

const parentOf = (path) => path.slice(0, path.lastIndexOf('/'));
const idOf = (path) => path.slice(path.lastIndexOf('/') + 1);

/** Direct children of a collection path — not descendants. */
function childrenOf(collectionPath) {
    const depth = collectionPath.split('/').length + 1;
    return [...store.keys()].filter((p) =>
        p.startsWith(`${collectionPath}/`) && p.split('/').length === depth);
}

function notify() {
    for (const fire of [...listeners]) fire();
}

// -------------------------------------------------------------- references

const isRef = (value) => !!value && typeof value === 'object' && 'path' in value
    && (value.kind === 'doc' || value.kind === 'coll');

export function doc(first, ...rest) {
    // doc(collectionRef) and doc(collectionRef, id) — the auto-id form is how
    // db.js creates a team, a player and a match.
    if (isRef(first) && first.kind === 'coll') {
        const id = rest[0] ?? `auto${++autoId}`;
        return { kind: 'doc', path: `${first.path}/${id}`, id: String(id) };
    }
    const path = rest.join('/');
    return { kind: 'doc', path, id: idOf(path) };
}

export function collection(first, ...rest) {
    if (isRef(first) && first.kind === 'doc') {
        return { kind: 'coll', path: `${first.path}/${rest.join('/')}`, id: rest[rest.length - 1] };
    }
    const path = rest.join('/');
    return { kind: 'coll', path, id: idOf(path) };
}

export function collectionGroup(_db, id) {
    return { kind: 'coll', group: id, path: `<group:${id}>`, id };
}

// ------------------------------------------------------------ query pieces

export const where = (field, op, value) => ({ constraint: 'where', field, op, value });
export const orderBy = (field, direction = 'asc') =>
    ({ constraint: 'orderBy', field, direction });
export const limit = (n) => ({ constraint: 'limit', n });
export const query = (ref, ...constraints) => ({ ...ref, constraints });

const readField = (data, field) =>
    field.split('.').reduce((value, key) => value?.[key], data);

function applyConstraints(rows, constraints = []) {
    let out = rows;
    for (const c of constraints) {
        if (c.constraint === 'where') {
            out = out.filter((row) => {
                const value = readField(row.data, c.field);
                if (c.op === '==') return value === c.value;
                if (c.op === '!=') return value !== c.value;
                if (c.op === 'in') return (c.value || []).includes(value);
                if (c.op === 'array-contains') return (value || []).includes(c.value);
                if (c.op === '>') return value > c.value;
                if (c.op === '>=') return value >= c.value;
                if (c.op === '<') return value < c.value;
                if (c.op === '<=') return value <= c.value;
                throw new Error(`fake firestore: unsupported operator ${c.op}`);
            });
        } else if (c.constraint === 'orderBy') {
            const sign = c.direction === 'desc' ? -1 : 1;
            out = [...out].sort((a, b) => {
                const x = readField(a.data, c.field);
                const y = readField(b.data, c.field);
                if (x === y) return 0;
                return (x > y ? 1 : -1) * sign;
            });
        } else if (c.constraint === 'limit') {
            out = out.slice(0, c.n);
        }
    }
    return out;
}

// --------------------------------------------------------------- snapshots

function docSnap(path) {
    const data = store.get(path);
    return {
        id: idOf(path),
        ref: { kind: 'doc', path, id: idOf(path) },
        exists: () => data !== undefined,
        data: () => (data === undefined ? undefined : structuredClone(data)),
        get: (field) => readField(data, field),
        // Nothing here is ever queued: every write in this fake is applied the
        // instant it is made. A page that renders differently while a write is
        // in flight cannot be tested here — that is what driving the real
        // emulator offline is for.
        metadata: { hasPendingWrites: false, fromCache: false },
    };
}

function collectionRows(ref) {
    const paths = ref.group
        ? [...store.keys()].filter((p) => parentOf(p).endsWith(`/${ref.group}`))
        : childrenOf(ref.path);
    return paths.map((path) => ({ path, data: store.get(path) }));
}

function querySnap(ref) {
    const rows = applyConstraints(collectionRows(ref), ref.constraints);
    const docs = rows.map((row) => docSnap(row.path));
    return {
        docs,
        size: docs.length,
        empty: docs.length === 0,
        forEach: (fn) => docs.forEach(fn),
        metadata: { fromCache: false, hasPendingWrites: false },
    };
}

// ------------------------------------------------------------------ writes

export const serverTimestamp = () => ({ __serverTimestamp: true });
export const deleteField = () => ({ __delete: true });
export const increment = (n) => ({ __increment: n });
export const arrayUnion = (...values) => ({ __arrayUnion: values });

/** Resolve the sentinel values Firestore would have resolved on the server. */
function settle(value, previous) {
    if (Array.isArray(value)) return value.map((v) => settle(v));
    if (value && typeof value === 'object') {
        if (value.__serverTimestamp) return new Date('2026-08-16T12:00:00Z');
        if (value.__increment) return (previous ?? 0) + value.__increment;
        if (value.__arrayUnion) {
            return [...new Set([...(previous ?? []), ...value.__arrayUnion])];
        }
        const out = {};
        for (const [k, v] of Object.entries(value)) {
            if (v?.__delete) continue;
            out[k] = settle(v, previous?.[k]);
        }
        return out;
    }
    return value;
}

function writeDoc(ref, data, options = {}) {
    const previous = store.get(ref.path);
    const settled = settle(data, previous);
    store.set(ref.path, options.merge ? { ...previous, ...settled } : settled);
}

export async function setDoc(ref, data, options) {
    writeDoc(ref, data, options);
    notify();
}

export async function updateDoc(ref, patch) {
    if (!store.has(ref.path)) {
        throw new Error(`fake firestore: no document to update at ${ref.path}`);
    }
    const previous = store.get(ref.path);
    const next = { ...previous };
    for (const [key, value] of Object.entries(patch)) {
        if (value?.__delete) delete next[key];
        else next[key] = settle(value, previous[key]);
    }
    store.set(ref.path, next);
    notify();
}

export async function deleteDoc(ref) {
    store.delete(ref.path);
    notify();
}

export async function getDoc(ref) { return docSnap(ref.path); }

export async function getDocs(ref) { return querySnap(ref); }

export const setLogLevel = () => {};

export function writeBatch() {
    const queued = [];
    return {
        set(ref, data, options) { queued.push(() => writeDoc(ref, data, options)); return this; },
        update(ref, patch) { queued.push(() => updateDoc(ref, patch)); return this; },
        delete(ref) { queued.push(() => store.delete(ref.path)); return this; },
        async commit() {
            for (const apply of queued) await apply();
            notify();
        },
    };
}

export async function runTransaction(_db, fn) {
    return fn({
        get: async (ref) => docSnap(ref.path),
        set: (ref, data, options) => writeDoc(ref, data, options),
        update: (ref, patch) => updateDoc(ref, patch),
        delete: (ref) => store.delete(ref.path),
    });
}

/**
 * `onSnapshot(ref, cb, err)` and `onSnapshot(ref, options, cb, err)` — the
 * four-argument form is the one `watchSync` uses, and dropping it would leave
 * the sync indicator's callback holding an options object.
 */
export function onSnapshot(ref, ...rest) {
    const args = typeof rest[0] === 'function' ? rest : rest.slice(1);
    const [onNext] = args;
    const fire = () => onNext(ref.kind === 'doc' ? docSnap(ref.path) : querySnap(ref));
    listeners.add(fire);
    fire();
    return () => listeners.delete(fire);
}

export const enableNetwork = async () => {};
export const disableNetwork = async () => {};
export const waitForPendingWrites = async () => {};

export const initializeFirestore = () => ({ __fakeFirestore: true });
export const getFirestore = initializeFirestore;
export const persistentLocalCache = (options) => ({ ...options });
export const persistentMultipleTabManager = () => ({});
export const connectFirestoreEmulator = () => {};
export const Timestamp = {
    now: () => ({ toDate: () => new Date('2026-08-16T12:00:00Z') }),
    fromDate: (date) => ({ toDate: () => date }),
};

// -------------------------------------------------------------------- auth

const authState = { user: null, callbacks: new Set() };

export const initializeApp = (config) => ({ options: config });
export const getApp = () => ({});
export const getAuth = () => ({ __fakeAuth: true, get currentUser() { return authState.user; } });
export const connectAuthEmulator = () => {};

export class GoogleAuthProvider {
    setCustomParameters() {}
    static credential(token) { return { token }; }
}

export function onAuthStateChanged(_auth, callback) {
    authState.callbacks.add(callback);
    // Asynchronous, like the real one. A page that renders correctly only
    // because its auth callback ran before the rest of `init()` would pass a
    // synchronous fake and fail in a browser.
    queueMicrotask(() => { if (authState.callbacks.has(callback)) callback(authState.user); });
    return () => authState.callbacks.delete(callback);
}

export async function signInWithPopup() { return { user: authState.user }; }
export async function signInWithCredential() { return { user: authState.user }; }
export async function signOut() { return signInAs(null); }

/** Drive the auth callbacks the way a real sign-in would, and wait for them. */
export async function signInAs(user) {
    authState.user = user;
    const done = [...authState.callbacks].map((cb) => cb(user));
    await Promise.all(done);
    await new Promise((resolve) => setImmediate(resolve));
}

/** Every module the pages import from the CDN, and what each one provides. */
export const MODULES = {
    'firebase-app.js': ['initializeApp', 'getApp'],
    'firebase-auth.js': [
        'getAuth', 'connectAuthEmulator', 'GoogleAuthProvider', 'signInWithPopup',
        'signInWithCredential', 'signOut', 'onAuthStateChanged',
    ],
    'firebase-firestore.js': [
        'doc', 'collection', 'collectionGroup', 'getDoc', 'getDocs', 'setDoc',
        'updateDoc', 'deleteDoc', 'onSnapshot', 'query', 'where', 'orderBy',
        'limit', 'writeBatch', 'runTransaction', 'serverTimestamp', 'deleteField',
        'increment', 'arrayUnion', 'initializeFirestore', 'getFirestore',
        'persistentLocalCache', 'persistentMultipleTabManager',
        'connectFirestoreEmulator', 'enableNetwork', 'disableNetwork',
        'waitForPendingWrites', 'setLogLevel', 'Timestamp',
    ],
};
