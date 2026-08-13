import {
    signIn, signOut, onUser, resolveAccess, pendingInvites, claimInvite,
    configWarning,
} from './auth.js?v=62';
import { mountPitchBackdrop } from './pitch-backdrop.js?v=62';
import { listMatches, seasonSummary } from './db.js?v=62';
import { byId, setText, toast, showOnly, figure, signed, plural } from './ui.js?v=62';

const VIEWS = ['view-marketing', 'view-nowhere', 'view-routes'];

// signIn() must be reached synchronously from the click or iPad Safari blocks
// the popup — so no awaits before it here.
function attachSignIn(button) {
    button.addEventListener('click', () => {
        signIn().catch((err) => {
            const message = err?.code === 'auth/popup-blocked'
                ? 'Your browser blocked the sign-in popup. Allow popups and try again.'
                : err?.code === 'auth/popup-closed-by-user'
                    ? 'Sign-in was cancelled.'
                    : err?.message || 'Sign-in failed.';
            toast(message, true);
        });
    });
}

/** A team, with enough of its season on it to be worth reading. */
function teamCard(team, summary) {
    const card = document.createElement('a');
    card.className = 'team-card';
    card.href = `coach/?team=${encodeURIComponent(team.id)}`;
    card.innerHTML = `
        <div class="team-card-top">
            <span class="team-card-name"></span>
            <span class="team-card-go">&rsaquo;</span>
        </div>
        <div class="team-card-body"></div>`;

    card.querySelector('.team-card-name').textContent = team.name;
    const body = card.querySelector('.team-card-body');

    if (!summary || summary.played === 0) {
        const note = document.createElement('div');
        note.className = 'team-card-empty';
        note.textContent = summary?.upcoming
            ? `${plural(summary.upcoming, 'match', 'matches')} set up, none played yet`
            : 'No matches yet — open the team to add your squad';
        body.append(note);
        return card;
    }

    const gd = summary.goalDifference;
    const stats = document.createElement('div');
    stats.className = 'figures';
    stats.append(
        figure(summary.record, 'W–D–L'),
        figure(signed(gd), 'Goal diff', gd > 0 ? 'pos' : gd < 0 ? 'neg' : 'dim'),
        figure(String(summary.played), 'Played', 'dim'),
    );

    body.append(stats);
    return card;
}

function quickLink(icon, title, sub, href, accent = false) {
    const link = document.createElement('a');
    link.className = `quick-link ${accent ? 'accent' : ''}`.trim();
    link.href = href;
    link.innerHTML = `
        <span class="ql-icon"></span>
        <span class="grow">
            <span class="ql-title"></span>
            <div class="ql-sub"></div>
        </span>`;
    link.querySelector('.ql-icon').textContent = icon;
    link.querySelector('.ql-title').textContent = title;
    link.querySelector('.ql-sub').textContent = sub;
    return link;
}

/** "Good morning" beats a bare date, and tells them the app knows the time. */
function greeting() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
}

function renderInvites(user, invites) {
    const slot = byId('invite-slot');
    slot.innerHTML = '';

    if (!invites.length) {
        setText('nowhere-msg',
            `Nothing is linked to ${user.email} yet. If you're a player, ask your `
            + 'coach to add that address to the roster and invite you. If you\'re a '
            + 'coach setting up for the first time, start below.');
        return;
    }

    setText('nowhere-msg', invites.length === 1
        ? 'You have an invitation:'
        : 'You have invitations:');

    for (const invite of invites) {
        const isCoach = invite.role === 'coach';

        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="pill"></span>
            <button class="btn small primary">Accept</button>`;

        row.querySelector('.title').textContent = invite.teamName || 'A team';
        // Saying which of the two it is matters: accepting a coaching invite
        // hands over the roster, including every player's email address.
        row.querySelector('.sub').textContent = isCoach
            ? `${invite.coachName || 'A coach'} invited you to help coach this squad`
            : `${invite.coachName || 'Your coach'} invited you to join the roster`;
        row.querySelector('.pill').textContent = isCoach ? 'Coach' : 'Player';

        // Explicit accept rather than auto-claiming on sign-in: an invite can
        // name any address, so the person should see who sent it first.
        row.querySelector('button').addEventListener('click', async (e) => {
            e.target.disabled = true;
            try {
                const role = await claimInvite(user, invite);
                const destination = role === 'coach' ? 'coach/' : 'player/';
                toast(role === 'coach'
                    ? 'Joined — opening the dashboard'
                    : 'Joined — loading your reports');
                setTimeout(() => { location.href = destination; }, 700);
            } catch (err) {
                e.target.disabled = false;
                toast(err.message || 'Could not accept the invitation.', true);
            }
        });

        slot.append(row);
    }
}

async function renderCoachWelcome(user, teams) {
    mountPitchBackdrop(byId('welcome-hero'), { opacity: 0.16 });

    const firstName = (user.displayName || '').split(' ')[0];
    setText('welcome', `${greeting()}${firstName ? `, ${firstName}` : ''}`);
    setText('welcome-date', new Date().toLocaleDateString(undefined, {
        weekday: 'long', month: 'long', day: 'numeric',
    }));

    // Season summaries are one extra read per team; a coach has one or two, so
    // this is cheap and makes the card worth looking at.
    const summaries = await Promise.all(teams.map(async (team) => {
        try {
            return seasonSummary(await listMatches(team.id));
        } catch {
            return null;
        }
    }));

    const cards = byId('routes');
    cards.innerHTML = '';
    teams.forEach((team, i) => cards.append(teamCard(team, summaries[i])));

    const untagged = summaries.reduce((n, s) => n + (s?.upcoming ?? 0), 0);
    setText('welcome-sub', untagged
        ? `${plural(untagged, 'match', 'matches')} waiting to be tagged.`
        : 'Everything is up to date.');

    const quick = byId('quick-links');
    quick.innerHTML = '';
    quick.append(
        quickLink('●', 'Tag a match live', 'Record events from the touchline',
            'live-tagging/', true),
        quickLink('▦', 'Calibrate a camera', 'Map footage onto the pitch',
            'calibrate/'),
        quickLink('△', 'xG sandbox', 'Try the shot-quality model',
            'xg-sandbox/'),
    );

    showOnly('view-routes', VIEWS);
}

async function onSignedIn(user) {
    byId('loading').classList.remove('hidden');
    byId('btn-signin').classList.add('hidden');
    byId('btn-signout').classList.remove('hidden');

    const chip = byId('user-chip');
    chip.textContent = user.email;
    chip.classList.remove('hidden');

    const access = await resolveAccess(user);

    if (access.role === 'coach') {
        await renderCoachWelcome(user, access.teams);
        return;
    }
    if (access.role === 'player') {
        location.href = 'player/';
        return;
    }

    renderInvites(user, await pendingInvites(user));
    showOnly('view-nowhere', VIEWS);
}

function onSignedOut() {
    byId('btn-signin').classList.remove('hidden');
    byId('btn-signout').classList.add('hidden');
    byId('user-chip').classList.add('hidden');
    showOnly('view-marketing', VIEWS);
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    mountPitchBackdrop(byId('landing-hero'), { opacity: 0.2 });

    attachSignIn(byId('btn-signin'));
    attachSignIn(byId('btn-hero-signin'));
    byId('btn-signout').addEventListener('click', () => signOut());

    onUser((user) => {
        if (!user) { onSignedOut(); return; }
        onSignedIn(user).catch((err) => {
            toast(err.message || 'Could not load your account.', true);
            showOnly('view-marketing', VIEWS);
        });
    });
}

init();
