import {
    signIn, signOut, onUser, resolveAccess, pendingInvites, claimInvite,
    configWarning,
} from './auth.js';
import { mountPitchBackdrop } from './pitch-backdrop.js';
import { listMatches, seasonSummary } from './db.js';

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

/** A team, with enough of its season on it to be worth reading. */
function teamCard(team, summary) {
    const a = document.createElement('a');
    a.className = 'team-card';
    a.href = `coach/?team=${encodeURIComponent(team.id)}`;
    a.innerHTML = `
        <div class="team-card-top">
            <span class="team-card-name"></span>
            <span class="team-card-go">&rsaquo;</span>
        </div>
        <div class="team-card-body"></div>`;

    a.querySelector('.team-card-name').textContent = team.name;
    const body = a.querySelector('.team-card-body');

    if (!summary || summary.played === 0) {
        const note = document.createElement('div');
        note.className = 'team-card-empty';
        note.textContent = summary && summary.upcoming
            ? `${summary.upcoming} match${summary.upcoming === 1 ? '' : 'es'} set up, none played yet`
            : 'No matches yet — open the team to add your squad';
        body.appendChild(note);
        return a;
    }

    const stats = document.createElement('div');
    stats.className = 'team-card-stats';

    const gd = summary.goalDifference;
    const figures = [
        [summary.record, 'W–D–L', ''],
        [gd > 0 ? `+${gd}` : String(gd), 'Goal diff', gd > 0 ? 'pos' : gd < 0 ? 'neg' : 'dim'],
        [String(summary.played), 'Played', 'dim'],
    ];

    for (const [value, label, tone] of figures) {
        const el = document.createElement('div');
        el.className = 'tcs';
        el.innerHTML = `<div class="n"></div><div class="k"></div>`;
        const n = el.querySelector('.n');
        n.textContent = value;
        if (tone) n.classList.add(tone);
        el.querySelector('.k').textContent = label;
        stats.appendChild(el);
    }

    body.appendChild(stats);
    return a;
}

function quickLink(icon, title, sub, href, accent = false) {
    const a = document.createElement('a');
    a.className = `quick-link ${accent ? 'accent' : ''}`;
    a.href = href;
    a.innerHTML = `
        <span class="ql-icon"></span>
        <span class="grow">
            <span class="ql-title"></span>
            <div class="ql-sub"></div>
        </span>`;
    a.querySelector('.ql-icon').textContent = icon;
    a.querySelector('.ql-title').textContent = title;
    a.querySelector('.ql-sub').textContent = sub;
    return a;
}

/** "Good morning" beats a bare date, and tells them the app knows the time. */
function greeting() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
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
        mountPitchBackdrop($('welcome-hero'), { opacity: 0.16 });

        const firstName = (user.displayName || '').split(' ')[0];
        $('welcome').textContent = `${greeting()}${firstName ? `, ${firstName}` : ''}`;
        $('welcome-date').textContent = new Date().toLocaleDateString(undefined, {
            weekday: 'long', month: 'long', day: 'numeric',
        });

        const routes = $('routes');
        routes.innerHTML = '';

        // Season summaries are one extra read per team; a coach has one or two,
        // so this is cheap and makes the card worth looking at.
        const summaries = await Promise.all(access.teams.map(async (team) => {
            try {
                return seasonSummary(await listMatches(team.id));
            } catch {
                return null;
            }
        }));

        access.teams.forEach((team, i) => {
            routes.appendChild(teamCard(team, summaries[i]));
        });

        const openMatches = summaries.reduce((n, s) => n + (s?.upcoming ?? 0), 0);
        $('welcome-sub').textContent = openMatches
            ? `${openMatches} match${openMatches === 1 ? '' : 'es'} waiting to be tagged.`
            : 'Everything is up to date.';

        const quick = $('quick-links');
        quick.innerHTML = '';
        quick.appendChild(quickLink(
            '●', 'Tag a match live', 'Record events from the touchline',
            'live-tagging/', true,
        ));
        quick.appendChild(quickLink(
            '▦', 'Calibrate a camera', 'Map footage onto the pitch', 'calibrate/',
        ));
        quick.appendChild(quickLink(
            '△', 'xG sandbox', 'Try the shot-quality model', 'demo/',
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
