// Firebase bootstrap. Plain ES modules from the CDN — no bundler, no build step,
// matching how the rest of this site is built.
//
// The values below are NOT secrets. A web API key is a public project
// identifier; firestore.rules is the actual security boundary. It is fine that
// this file is committed and served publicly.
//
// SETUP: replace the placeholders with your project's config from
// Firebase console -> Project settings -> Your apps -> Web app.

import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js';
import {
    getAuth,
    connectAuthEmulator,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js';
import {
    initializeFirestore,
    persistentLocalCache,
    persistentMultipleTabManager,
    connectFirestoreEmulator,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';

export const firebaseConfig = {
    apiKey: 'REPLACE_ME',
    authDomain: 'REPLACE_ME.firebaseapp.com',
    projectId: 'REPLACE_ME',
    storageBucket: 'REPLACE_ME.firebasestorage.app',
    messagingSenderId: 'REPLACE_ME',
    appId: 'REPLACE_ME',
};

export const isConfigured = !firebaseConfig.projectId.startsWith('REPLACE_ME');

const useEmulator =
    location.hostname === 'localhost' || location.hostname === '127.0.0.1';

export const app = initializeApp(
    isConfigured ? firebaseConfig : { ...firebaseConfig, projectId: 'demo-pitchiq' }
);

export const auth = getAuth(app);

// Offline persistence is not on by default on web. The tablet tags matches at a
// field where connectivity is unreliable, so queued-and-replayed writes are the
// difference between a usable tool and a broken one.
export const db = initializeFirestore(app, {
    localCache: persistentLocalCache({
        tabManager: persistentMultipleTabManager(),
    }),
});

if (useEmulator) {
    connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
    connectFirestoreEmulator(db, '127.0.0.1', 8085);
}
