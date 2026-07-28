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
  apiKey: "AIzaSyCHY0t-88OPFzPgUut3Wbx7ui51YkaDEZ4",
  authDomain: "pitchiq-884db.firebaseapp.com",
  projectId: "pitchiq-884db",
  storageBucket: "pitchiq-884db.firebasestorage.app",
  messagingSenderId: "340675531579",
  appId: "1:340675531579:web:9466dd63337b5ec5acdd1e",
  measurementId: "G-42P3SKL0F5"
};

export const isConfigured = !firebaseConfig.projectId.startsWith('REPLACE_ME');

// Local development points at the emulators by default so it can never touch
// real data by accident. Append ?prod=1 once to test the live project from
// localhost — which matters because localhost is an authorized Firebase Auth
// domain and a bare LAN IP is not, so this is the only way to exercise real
// Google sign-in before the site is deployed. The choice sticks for the tab
// session so it survives navigating between pages.
const params = new URLSearchParams(location.search);
if (params.has('prod')) sessionStorage.setItem('pitchiq.useProd', '1');
if (params.has('emu')) sessionStorage.removeItem('pitchiq.useProd');

const forceProd = sessionStorage.getItem('pitchiq.useProd') === '1';
const isLocal =
    location.hostname === 'localhost' || location.hostname === '127.0.0.1';
const useEmulator = isLocal && !forceProd;

if (isLocal && forceProd) {
    console.warn('[PitchIQ] Using the LIVE Firebase project. Add ?emu=1 to go back to emulators.');
}

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
