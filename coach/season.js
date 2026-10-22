// The season at the top of the dashboard: the hero, the jobs still waiting
// on the coach, the form guide, and the getting-started panel.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { seasonSummary } from '../shared/db.js?v=151';
import { formGuide, seasonJobs } from '../shared/report.js?v=151';
import { HOUSE_KIT, applyKit } from '../shared/kit.js?v=151';
import { mountPitchBackdrop } from '../shared/pitch-backdrop.js?v=151';
import { byId, setText, signed, plural, localDate, statCard } from '../shared/ui.js?v=151';
import { state, showTab } from './shell.js?v=151';

// ---------------------------------------------------------------- season hero

export function renderHero() {
    applyKit(state.team.kit ?? HOUSE_KIT);
    mountPitchBackdrop(byId('team-hero'), { opacity: 0.16 });

    const summary = seasonSummary(state.matches);
    setText('hero-team', state.team.name);
    setText('hero-record', summary.record);

    const gd = byId('hero-gd');
    gd.textContent = signed(summary.goalDifference);
    gd.classList.toggle('pos', summary.goalDifference > 0);
    gd.classList.toggle('neg', summary.goalDifference < 0);

    const grid = byId('hero-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(summary.played, 'Played'),
        statCard(summary.scored, 'Scored', summary.scored ? 'is-good' : 'is-muted'),
        statCard(summary.conceded, 'Conceded', 'is-muted'),
        statCard(state.players.length, 'Squad', 'is-muted'),
    );

    setText('count-matches', state.matches.length);
    setText('count-roster', state.players.length);
    setText('count-staff', state.staff.length);

    const open = state.matches.filter((m) => !m.finalized).length;
    setText('action-tag-sub', open
        ? `${plural(open, 'match', 'matches')} ready to tag`
        : 'Create a match first');

    renderGettingStarted();
    renderJobs();
    renderFormGuide(summary);
}

/**
 * What is still waiting on this coach, and one press away from being done.
 *
 * The arithmetic is `seasonJobs` in report.js, where it is tested; this draws
 * it. Each row switches to the tab the work is on rather than only naming it —
 * a to-do list that tells you about a job and then leaves you to find it is
 * half a feature.
 */
function renderJobs() {
    const block = byId('jobs-block');
    const list = byId('jobs-list');
    if (!block || !list) return;

    const jobs = seasonJobs({
        matches: state.matches,
        players: state.players,
        // Local, not UTC — see localDate.
        today: localDate(),
    });

    list.innerHTML = '';
    block.classList.toggle('hidden', !jobs.length);
    if (!jobs.length) return;

    for (const job of jobs) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'job-row';
        row.addEventListener('click', () => showTab(job.tab));

        const badge = document.createElement('span');
        badge.className = 'job-count';
        badge.textContent = job.count;

        const body = document.createElement('span');
        body.className = 'grow';
        const title = document.createElement('span');
        title.className = 'job-title';
        title.textContent = job.title;
        const note = document.createElement('span');
        note.className = 'job-note';
        note.textContent = job.note;
        body.append(title, note);

        const go = document.createElement('span');
        go.className = 'job-go';
        go.textContent = '›';

        row.append(badge, body, go);
        list.append(row);
    }
}

/**
 * The last five results, and where the goals went.
 *
 * The hero above already says the record and the goal difference, which are the
 * season summed into two numbers. This is the same season with its shape left
 * in: three wins and two defeats reads very differently depending on which
 * three, and a record cannot say.
 */
function renderFormGuide(summary) {
    const block = byId('form-guide-block');
    const strip = byId('form-guide');
    const bars = byId('goal-bars');
    if (!block || !strip || !bars) return;

    const guide = formGuide(state.matches, 5);
    block.classList.toggle('hidden', !guide.length);
    if (!guide.length) return;

    strip.innerHTML = '';
    for (const match of guide) {
        const pill = document.createElement('span');
        pill.className = `form-pill is-${match.result.toLowerCase()}`;
        pill.textContent = match.result;
        // The result alone is three letters and no memory attached. The title
        // is what turns the strip back into matches a coach was at.
        pill.title = `${match.date || 'no date'} · ${match.opponentName} `
            + `${match.scoreUs}–${match.scoreThem}`;
        strip.append(pill);
    }

    // Two bars against the larger of the pair, so the longer one is always
    // full and the shorter is read as a fraction of it. Scaling each to its own
    // maximum would draw two full bars and say nothing.
    const most = Math.max(summary.scored, summary.conceded, 1);
    bars.innerHTML = '';
    bars.append(
        goalBar('Scored', summary.scored, most, 'is-good'),
        goalBar('Conceded', summary.conceded, most, 'is-bad'),
    );
}

function goalBar(label, value, most, tone) {
    const row = document.createElement('div');
    row.className = 'goal-row';

    const key = document.createElement('span');
    key.className = 'goal-key';
    key.textContent = label;

    const track = document.createElement('span');
    track.className = 'goal-track';
    const bar = document.createElement('span');
    bar.className = `goal-bar ${tone}`;
    bar.style.width = `${Math.round((value / most) * 100)}%`;
    track.append(bar);

    const num = document.createElement('span');
    num.className = 'goal-value';
    num.textContent = value;

    row.append(key, track, num);
    return row;
}

/** Tick off setup steps as they're actually completed, and hide once done. */
function renderGettingStarted() {
    const done = {
        'step-players': state.players.length > 0,
        'step-invite': state.players.some((p) => p.linkedUid),
        'step-match': state.matches.length > 0,
        'step-tag': state.matches.some((m) => m.status && m.status !== 'scheduled'),
    };

    for (const [id, complete] of Object.entries(done)) {
        byId(id)?.classList.toggle('done', complete);
    }

    // Once every step is done the checklist is clutter, so it goes away.
    const allDone = Object.values(done).every(Boolean);
    byId('getting-started').classList.toggle('hidden', allDone);
}
