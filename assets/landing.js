import {
    signIn, signOut, onUser, resolveAccess, pendingInvites, claimInvite,
    configWarning,
} from './auth.js';
import { mountPitchBackdrop } from './pitch-backdrop.js';

const $ = (id) => document.getElementById(id);

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

function show(id) {
    for (const v of ['view-marketing', 'view-nowhere', 'view-routes']) {
        $(v).classList.toggle('hidden', v !== id);
    }
    $('loading').classList.add('hidden');
}

// signIn() must be reached synchronously from the click or iPad Safari blocks
// the popup — so no awaits before it here.
function attachSignIn(button) {
    button.addEventListener('click', () => {
        signIn().catch((err) => {
            const msg = err?.code === 'auth/popup-blocked'
                ? 'Your browser blocked the sign-in popup. Allow popups and try again.'
                : err?.code === 'auth/popup-closed-by-user'
                    ? 'Sign-in was cancelled.'
                    : err?.message || 'Sign-in failed.';
            toast(msg, true);
        });
    });
}

function route(label, sub, href) {
    const a = document.createElement('a');
    a.className = 'list-item';
    a.href = href;
    a.innerHTML = `<div class="grow"><div class="title"></div><div class="sub"></div></div><span class="muted">&rsaquo;</span>`;
    a.querySelector('.title').textContent = label;
    a.querySelector('.sub').textContent = sub;
    return a;
}

function renderInvites(user, invites) {
    const slot = $('invite-slot');
    slot.innerHTML = '';

    if (!invites.length) {
        $('nowhere-msg').textContent =
            `Nothing is linked to ${user.email} yet. If you're a player, ask your ` +
            'coach to add that address to the roster and invite you. If you\'re a ' +
            'coach setting up for the first time, start below.';
        return;
    }

    $('nowhere-msg').textContent = 'You have an invitation:';

    for (const invite of invites) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <button class="btn small primary">Accept</button>`;
        row.querySelector('.title').textContent = invite.teamName || 'A team';
        row.querySelector('.sub').textContent =
            `${invite.coachName || 'Your coach'} invited you to join`;

        // Explicit accept rather than auto-claiming on sign-in: an invite can
        // name any address, so the person should see who sent it first.
        row.querySelector('button').addEventListener('click', async (e) => {
            e.target.disabled = true;
            try {
                await claimInvite(user, invite);
                toast('Joined — loading your reports');
                setTimeout(() => { location.href = 'player/'; }, 700);
            } catch (err) {
                e.target.disabled = false;
                toast(err.message || 'Could not accept the invitation.', true);
            }
        });

        slot.appendChild(row);
    }
}

async function onSignedIn(user) {
    $('loading').classList.remove('hidden');
    $('btn-signin').classList.add('hidden');
    $('btn-signout').classList.remove('hidden');

    const chip = $('user-chip');
    chip.textContent = user.email;
    chip.classList.remove('hidden');

    const access = await resolveAccess(user);

    if (access.role === 'coach') {
        $('welcome').textContent = `Welcome back, ${user.displayName || 'Coach'}`;
        const routes = $('routes');
        routes.innerHTML = '';
        for (const team of access.teams) {
            routes.appendChild(route(
                team.name, 'Roster, matches and reports',
                `coach/?team=${encodeURIComponent(team.id)}`
            ));
        }
        routes.appendChild(route(
            'Live tagging', 'Tag a match from the sideline', 'live-tagging/'
        ));
        show('view-routes');
        return;
    }

    if (access.role === 'player') {
        location.href = 'player/';
        return;
    }

    const invites = await pendingInvites(user);
    renderInvites(user, invites);
    show('view-nowhere');
}

function onSignedOut() {
    $('btn-signin').classList.remove('hidden');
    $('btn-signout').classList.add('hidden');
    $('user-chip').classList.add('hidden');
    show('view-marketing');
}

function init() {
    const warning = configWarning();
    if (warning) $('config-slot').appendChild(warning);

    mountPitchBackdrop($('landing-hero'), { opacity: 0.2 });

    attachSignIn($('btn-signin'));
    attachSignIn($('btn-hero-signin'));
    $('btn-signout').addEventListener('click', () => signOut());

    onUser((user) => {
        if (user) {
            onSignedIn(user).catch((err) => {
                toast(err.message || 'Could not load your account.', true);
                show('view-marketing');
            });
        } else {
            onSignedOut();
        }
    });
}

init();
