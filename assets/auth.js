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

import { auth, db, isConfigured } from './firebase-init.js?v=87';

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

    // A coach commonly holds more than one squad — varsity and JV under one
    // person is the normal case, not the exception. Sorted by name so the
    // switcher does not reshuffle itself between visits.
    const coaching = teams
        .filter((t) => (t.coachUids || []).includes(user.uid))
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
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
    // Invites written before staff invites existed carry no role at all.
    return snap.docs.map((d) => ({ role: 'player', teamId: d.id, ...d.data() }));
}

/**
 * Accept an invitation. Deliberately requires an explicit user action rather
 * than auto-claiming on sign-in — an invite can name any address, so the person
 * should see who invited them before accepting.
 *
 * Returns 'coach' or 'player', which is where the caller should send them.
 */
export async function claimInvite(user, invite) {
    if (invite.role === 'coach') {
        await claimCoachSeat(user, invite.teamId);
        return 'coach';
    }

    await updateDoc(
        doc(db, 'teams', invite.teamId, 'players', invite.playerId),
        { linkedUid: user.uid }
    );

    await saveHint(user, {
        lastPlayerRef: { teamId: invite.teamId, playerId: invite.playerId },
    });
    return 'player';
}

/**
 * Add this account to a team's coaching staff.
 *
 * The rules require the new array to be exactly the old one with this uid
 * appended, which means reading the team first — a pending invitee is granted
 * `get` on the team document for precisely this reason. Writing the array
 * explicitly rather than with arrayUnion keeps what the rule checks and what
 * the client sends identical.
 */
async function claimCoachSeat(user, teamId) {
    const ref = doc(db, 'teams', teamId);
    const snap = await getDoc(ref);
    if (!snap.exists()) throw new Error('That team no longer exists.');

    const coachUids = snap.data().coachUids || [];
    if (!coachUids.includes(user.uid)) {
        await updateDoc(ref, { coachUids: [...coachUids, user.uid] });
    }

    await saveStaffProfile(user, teamId);
    await rememberTeam(user, teamId);
}

/**
 * This account's entry in a team's staff directory: a display name for a uid.
 *
 * Carries no authority — coachUids remains the only proof of who coaches a
 * team. It exists because one coach cannot read another's users/{uid} document,
 * so without it the staff list would be a column of opaque uids. Safe to call
 * repeatedly; the dashboard uses that to backfill teams created before the
 * directory existed.
 */
export function saveStaffProfile(user, teamId, role = 'coach') {
    return setDoc(doc(db, 'teams', teamId, 'staff', user.uid), {
        displayName: user.displayName || emailOf(user),
        emailLower: emailOf(user),
        role,
        joinedAt: serverTimestamp(),
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
