// Sign-in, role resolution, and the roster claim flow.

import {
    GoogleAuthProvider,
    signInWithPopup,
    signOut as fbSignOut,
    onAuthStateChanged,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js';
import {
    doc, getDoc, setDoc, updateDoc, collection, getDocs, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';

import { auth, db, isConfigured } from './firebase-init.js';

const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: 'select_account' });

/**
 * Popup, never redirect. signInWithRedirect needs cross-origin storage between
 * github.io and firebaseapp.com, which current Chrome, Firefox and Safari all
 * block — and both documented workarounds need a server that GitHub Pages
 * doesn't provide.
 *
 * Must be called synchronously from a click handler: any `await` before this
 * runs and iPad Safari's popup blocker kills it.
 */
export function signIn() {
    return signInWithPopup(auth, provider);
}

export function signOut() {
    return fbSignOut(auth);
}

export function onUser(callback) {
    return onAuthStateChanged(auth, callback);
}

export const emailOf = (user) => (user?.email || '').toLowerCase();

/**
 * Work out what this account can actually do, by reading authoritative
 * documents — never a self-written profile.
 *
 * Returns { role, teams, player } where role is 'coach' | 'player' | 'none'.
 */
export async function resolveAccess(user) {
    if (!user) return { role: 'none', teams: [], player: null };

    const hint = await getDoc(doc(db, 'users', user.uid)).catch(() => null);
    const teamIds = hint?.exists() ? (hint.data().teamIds || []) : [];

    // The hint is untrusted; each get() is still gated by the rules, so a forged
    // teamIds entry simply fails to read.
    const teams = [];
    for (const id of teamIds) {
        const snap = await getDoc(doc(db, 'teams', id)).catch(() => null);
        if (snap?.exists()) teams.push({ id: snap.id, ...snap.data() });
    }

    const coaching = teams.filter((t) => (t.coachUids || []).includes(user.uid));
    if (coaching.length) return { role: 'coach', teams: coaching, player: null };

    const lastRef = hint?.exists() ? hint.data().lastPlayerRef : null;
    if (lastRef?.teamId && lastRef?.playerId) {
        const snap = await getDoc(
            doc(db, 'teams', lastRef.teamId, 'players', lastRef.playerId)
        ).catch(() => null);
        if (snap?.exists() && snap.data().linkedUid === user.uid) {
            return {
                role: 'player',
                teams: [],
                player: { teamId: lastRef.teamId, id: snap.id, ...snap.data() },
            };
        }
    }

    return { role: 'none', teams: [], player: null };
}

/** Invites addressed to this account. A pointer only — claiming re-verifies. */
export async function pendingInvites(user) {
    const email = emailOf(user);
    if (!email) return [];

    const snap = await getDocs(collection(db, 'invites', email, 'from')).catch(
        () => null
    );
    if (!snap) return [];
    return snap.docs.map((d) => ({ teamId: d.id, ...d.data() }));
}

/**
 * Bind this account to a roster slot. Deliberately requires an explicit user
 * action rather than auto-claiming on sign-in — an invite can name any address,
 * so the person should see who invited them before accepting.
 */
export async function claimInvite(user, invite) {
    await updateDoc(
        doc(db, 'teams', invite.teamId, 'players', invite.playerId),
        { linkedUid: user.uid }
    );

    await saveHint(user, {
        lastPlayerRef: { teamId: invite.teamId, playerId: invite.playerId },
    });
}

/** users/{uid} is a convenience cache with no authority. */
export async function saveHint(user, patch) {
    const ref = doc(db, 'users', user.uid);
    const existing = await getDoc(ref).catch(() => null);
    const current = existing?.exists() ? existing.data() : {};

    await setDoc(ref, {
        displayName: user.displayName || '',
        emailLower: emailOf(user),
        teamIds: current.teamIds || [],
        lastPlayerRef: current.lastPlayerRef ?? null,
        ...patch,
        updatedAt: serverTimestamp(),
    });
}

export async function rememberTeam(user, teamId) {
    const ref = doc(db, 'users', user.uid);
    const existing = await getDoc(ref).catch(() => null);
    const teamIds = new Set(existing?.exists() ? existing.data().teamIds || [] : []);
    teamIds.add(teamId);
    await saveHint(user, { teamIds: [...teamIds].slice(0, 10) });
}

/** Shown when firebase-init.js still has placeholder config. */
export function configWarning() {
    if (isConfigured) return null;
    const el = document.createElement('div');
    el.className = 'banner warn';
    el.innerHTML =
        '<h3>Firebase not configured</h3>' +
        '<p class="muted">Add your project config to <code>assets/firebase-init.js</code>. ' +
        'See <code>FIREBASE_SETUP.md</code> for the console steps.</p>';
    return el;
}
