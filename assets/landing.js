import {
    signIn, signOut, onUser, resolveAccess, pendingInvites, claimInvite,
    configWarning,
} from './auth.js?v=91';
import { mountPitchBackdrop } from './pitch-backdrop.js?v=91';
import { listMatches, listPlayers, seasonSummary } from './db.js?v=91';
import {
    formGuide, nextFixture, whenLabel, seasonJobs,
} from './report.js?v=91';
import { byId, setText, toast, showOnly, figure, signed, plural } from './ui.js?v=91';

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

/** Today where this browser is, as the YYYY-MM-DD a match document holds. */
function localDate() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A team, with enough of its season on it to be worth reading. */
function teamCard(team, summary, matches = []) {
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

    // The same five pills as the squad page's sidebar. A record is the season
    // summed into two numbers, and three wins and two defeats reads very
    // differently depending on which three.
    const guide = formGuide(matches, 5);
    if (guide.length > 1) {
        const strip = document.createElement('div');
        strip.className = 'form-guide';
        for (const played of guide) {
            const pill = document.createElement('span');
            pill.className = `form-pill is-${played.result.toLowerCase()}`;
            pill.textContent = played.result;
            pill.title = `${played.date || 'no date'} · ${played.opponentName} `
                + `${played.scoreUs}–${played.scoreThem}`;
            strip.append(pill);
        }
        body.append(strip);
    }

    return card;
}

/**
 * The next game, with the one action it wants.
 *
 * Above the team cards because this page is opened on the morning of a match
 * more often than on any other day, and until now the most useful thing it
 * could say was the name of the team.
 */
function renderNext(teams, matchesByTeam, today) {
    const block = byId('next-block');
    const host = byId('next-fixture');
    if (!block || !host) return null;

    let soonest = null;
    for (const team of teams) {
        const found = nextFixture(matchesByTeam.get(team.id) || [], today);
        if (found && (!soonest || found.daysAway < soonest.daysAway)) {
            soonest = { ...found, team };
        }
    }

    host.innerHTML = '';
    block.classList.toggle('hidden', !soonest);
    if (!soonest) return null;

    const row = document.createElement('a');
    row.className = 'next-card';
    // Straight to the tool it needs. The team is in the link because a coach
    // with two squads should not have to pick the one the page just named.
    row.href = `live-tagging/?team=${encodeURIComponent(soonest.team.id)}`;
    row.innerHTML = `
        <div class="grow">
            <div class="next-when"></div>
            <div class="next-opp"></div>
            <div class="next-team"></div>
        </div>
        <span class="next-go">Tag it live &rsaquo;</span>`;

    row.querySelector('.next-when').textContent =
        `${whenLabel(soonest.daysAway)} · ${soonest.match.date}`;
    row.querySelector('.next-opp').textContent =
        `vs ${soonest.match.opponentName || 'an opponent'}`;
    row.querySelector('.next-team').textContent = soonest.team.name;
    // The nearest fixture is the loud one; anything a week out is a diary entry.
    row.classList.toggle('is-soon', soonest.daysAway <= 1);

    host.append(row);
    return soonest;
}

/** The outstanding work, pooled across every squad this account coaches. */
function renderHomeJobs(teams, matchesByTeam, playersByTeam, today) {
    const block = byId('home-jobs-block');
    const host = byId('home-jobs');
    if (!block || !host) return [];

    const jobs = [];
    for (const team of teams) {
        for (const job of seasonJobs({
            matches: matchesByTeam.get(team.id) || [],
            players: playersByTeam.get(team.id) || [],
            today,
        })) {
            jobs.push({ ...job, team });
        }
    }

    host.innerHTML = '';
    block.classList.toggle('hidden', !jobs.length);

    for (const job of jobs) {
        const row = document.createElement('a');
        row.className = 'job-row';
        row.href = `coach/?team=${encodeURIComponent(job.team.id)}`;
        row.innerHTML = `
            <span class="job-count"></span>
            <span class="grow">
                <span class="job-title"></span>
                <span class="job-note"></span>
            </span>
            <span class="job-go">&rsaquo;</span>`;
        row.querySelector('.job-count').textContent = job.count;
        row.querySelector('.job-title').textContent = job.title;
        // Which squad, when there is more than one. A count with no team on it
        // is a job you cannot go and do.
        row.querySelector('.job-note').textContent =
            teams.length > 1 ? `${job.team.name} — ${job.note}` : job.note;
        host.append(row);
    }

    return jobs;
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

    // Two queries per team — the fixtures and the squad. A coach has one or two
    // squads, so this is a handful of reads for a page that can then say what
    // today actually needs rather than only which teams exist.
    const today = localDate();
    const matchesByTeam = new Map();
    const playersByTeam = new Map();

    await Promise.all(teams.map(async (team) => {
        const [matches, players] = await Promise.all([
            listMatches(team.id).catch(() => null),
            listPlayers(team.id).catch(() => []),
        ]);
        if (matches) matchesByTeam.set(team.id, matches);
        playersByTeam.set(team.id, players);
    }));

    const cards = byId('routes');
    cards.innerHTML = '';
    for (const team of teams) {
        const matches = matchesByTeam.get(team.id);
        cards.append(teamCard(
            team,
            matches ? seasonSummary(matches) : null,
            matches || [],
        ));
    }

    const soonest = renderNext(teams, matchesByTeam, today);
    const jobs = renderHomeJobs(teams, matchesByTeam, playersByTeam, today);

    // The one sentence a coach reads before deciding whether to open anything,
    // and the order it is chosen in is the whole point.
    //
    // A match today outranks everything: nothing on this page matters as much
    // as the game that is happening, and the card for it is directly below.
    // Only then the outstanding work, and only then the quiet answer.
    //
    // It used to count every fixture that was not finalized as "waiting to be
    // tagged", which meant a match set up for Saturday was reported as overdue
    // work on the Tuesday before it — a coach who is well organised got told
    // off for being organised. A fixture in the future is news, not a debt.
    const outstanding = jobs.reduce((n, job) => n + job.count, 0);
    const opponent = soonest?.match?.opponentName || 'A match';
    setText('welcome-sub',
        soonest && soonest.daysAway <= 1
            ? `${opponent} ${whenLabel(soonest.daysAway)}.`
            : outstanding
                ? `${plural(outstanding, 'thing', 'things')} to pick up.`
                : soonest
                    ? `Nothing outstanding. Next match ${whenLabel(soonest.daysAway)}.`
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
