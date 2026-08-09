// A player's own season.
//
// This is the only page a player ever sees, and for most of them it is the
// whole product. So it tries to be worth opening more than once: not just the
// totals, but the shape of the season — whether their minutes are climbing,
// which match was their best, how the run of games has gone.
//
// Everything here comes from playerReports documents, which the coach writes at
// publish time. There is no live match data on this page by design; see the
// note on collection-group rules in firestore.rules.

import { onUser, signOut, configWarning } from '../assets/auth.js?v=43';
import { myReports, seasonTotals, cvPlayerConfidence } from '../assets/db.js?v=43';
import { CARD_COLOURS } from '../assets/events.js?v=43';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=43';
import { renderHeatmap } from '../assets/heatmap.js?v=43';
import { renderShotMap, shotSummary } from '../assets/shot-map.js?v=43';
import {
    xgTrust, metresPerMinute, coverageNote, clockFromMatch,
} from '../assets/report.js?v=43';
import { seasonForms, formNote, MIN_FORM_POINTS } from '../assets/season.js?v=43';
import { renderForms } from '../assets/form-chart.js?v=43';
import {
    samplePlayerReport, sampleSeason, SAMPLE_NOTICE,
} from '../assets/sample-report.js?v=43';
import { renderMatchVideo } from '../assets/match-video.js?v=43';
import {
    byId, setText, toast, showOnly, clockText, statCard, figure, cardChips,
    plural, minutesChart, tally,
} from '../assets/ui.js?v=43';

const VIEWS = ['view-empty', 'view-reports', 'view-match'];

// The match currently open, and its video handle, so a moment can seek it.
const open = { report: null, video: null };

// A full match, for working out whether someone played most of one.
const FULL_MATCH_MIN = 80;

const involvements = (r) => (r.goals || 0) + (r.assists || 0);

// ---------------------------------------------------------------- headline

/**
 * One sentence about the season, so the page opens by telling them something
 * rather than by handing them a grid to interpret.
 */
function seasonLine(reports, totals) {
    if (!reports.length) return '';

    const scoring = reports.filter((r) => involvements(r) > 0).length;
    const opening = `${plural(totals.matches, 'match', 'matches')} played`;

    if (!totals.goals && !totals.assists) {
        return `${opening}, ${totals.minutes} minutes on the field.`;
    }

    const tally = [];
    if (totals.goals) tally.push(plural(totals.goals, 'goal'));
    if (totals.assists) tally.push(plural(totals.assists, 'assist'));

    // "in 7 of them" rather than "across 7 matches" — the sentence already
    // opened with a match count, and saying it twice reads as a mistake.
    const spread = scoring > 1 ? ` — involved in ${scoring} of them` : '';
    return `${opening}, ${tally.join(' and ')}${spread}.`;
}

function renderSeason(reports) {
    const totals = seasonTotals(reports);

    setText('player-line', seasonLine(reports, totals));

    const grid = byId('season-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(totals.matches, 'Matches'),
        statCard(totals.minutes, 'Minutes played'),
        statCard(totals.goals, 'Goals', totals.goals ? 'is-good' : 'is-muted'),
        statCard(totals.assists, 'Assists', totals.assists ? 'is-good' : 'is-muted'),
    );

    const cards = totals.yellowCards + totals.redCards;
    grid.append(statCard(cards, 'Cards', cards ? 'is-warn' : 'is-muted'));

    // Goal involvements per 90 is the stat that survives uneven minutes, which
    // matters a lot for a squad player comparing themselves to a starter.
    if (totals.minutes >= 45) {
        const per90 = ((totals.goals + totals.assists) / totals.minutes * 90).toFixed(2);
        grid.append(statCard(per90, 'G+A per 90', 'is-muted'));
    }

    // Their best afternoon. Worth naming — it is the thing a player actually
    // wants to find on their own page.
    const best = reports.reduce(
        (a, b) => (involvements(b) > involvements(a) ? b : a), reports[0]
    );
    if (involvements(best) > 0) {
        grid.append(statCard(
            involvements(best),
            `Best · ${best.opponentName || 'opponent'}`,
            'is-good',
        ));
    }

    const full = reports.filter((r) => (r.minutesPlayed || 0) >= FULL_MATCH_MIN).length;
    if (full) grid.append(statCard(full, 'Full matches', 'is-muted'));

    grid.append(...videoCards(totals));
}

/**
 * The numbers that come from footage rather than from the coach's tablet.
 *
 * Marked, every one of them. A player comparing themselves to a teammate
 * deserves to know which figures were watched by a person and which were
 * worked out by a machine from a video where the ball is visible about two
 * thirds of the time.
 *
 * Only shown for filmed matches, and averaged over those rather than over the
 * season — dividing by matches nobody filmed would quietly halve everything.
 */
function videoCards(totals) {
    if (!totals.cvMatches) return [];

    const cards = [
        statCard(totals.cvTouches, 'Touches', 'is-muted', 'medium'),
        statCard(totals.cvTackles, 'Tackles won', 'is-muted', 'medium'),
    ];

    if (totals.cvPassesAttempted) {
        const accuracy = Math.round(
            (totals.cvPassesCompleted / totals.cvPassesAttempted) * 100,
        );
        cards.push(statCard(`${accuracy}%`, 'Pass accuracy', 'is-muted', 'medium'));
    }
    if (totals.cvDistanceM) {
        cards.push(statCard(
            (totals.cvDistanceM / 1000).toFixed(1), 'km covered', 'is-muted', 'medium',
        ));
    }
    if (totals.cvTopSpeedKmh) {
        cards.push(statCard(
            totals.cvTopSpeedKmh.toFixed(1), 'Top speed km/h', 'is-muted', 'medium',
        ));
    }
    if (totals.cvSprintCount) {
        cards.push(statCard(totals.cvSprintCount, 'Sprints', 'is-muted', 'medium'));
    }
    if (totals.cvAccelerations) {
        cards.push(statCard(totals.cvAccelerations, 'Bursts', 'is-muted', 'medium'));
    }

    return cards;
}

// ---------------------------------------------------------------- the season

/** The season as a shape. The chart itself is shared with the coach's view. */
function renderChart(reports) {
    const host = byId('minutes-chart');
    host.innerHTML = '';
    host.append(minutesChart(reports));

    const scored = reports.filter((r) => involvements(r)).length;
    setText('chart-note', scored
        ? 'The line is 90 minutes, oldest match on the left. Highlighted bars are '
          + `matches you scored or assisted in — ${plural(scored, 'match', 'matches')}.`
        : 'The line is 90 minutes, oldest match on the left.');
}

/**
 * The video-derived figures as rates across the season, not as one pile.
 *
 * `seasonTotals` adds these up, and for the headline counts above that is
 * right. For anything you would compare — is she covering more ground than she
 * was — a sum is the wrong shape: it hides which matches it came from, and it
 * treats a match tracked for six minutes as equal evidence to one tracked for
 * seventy. Every arithmetic decision behind this is in assets/season.js.
 *
 * Hidden entirely below three placed matches. Two dots with a line through them
 * is a much stronger claim than the two numbers it is made of.
 */
function renderForm(reports) {
    const block = byId('form-block');
    if (!block) return;

    const forms = seasonForms(reports);
    const drawn = renderForms(byId('form-charts'), forms, {
        minPoints: MIN_FORM_POINTS,
    });
    block.classList.toggle('hidden', !drawn);
    if (!drawn) return;

    setText('form-note', formNote(forms, { measured: 'were filmed' }));
}

// ---------------------------------------------------------------- matches

function renderMatches(reports) {
    const list = byId('match-list');
    list.innerHTML = '';

    for (const report of reports) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'list-item match-row';
        row.addEventListener('click', () => openMatch(report));
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div class="figures"></div>`;

        row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

        const sub = row.querySelector('.sub');
        sub.textContent = report.matchDate || '';

        const chips = cardChips(report.yellowCards, report.redCards, CARD_COLOURS);
        if (chips.length) {
            const wrap = document.createElement('span');
            wrap.className = 'match-cards';
            wrap.append(...chips);
            sub.append(wrap);
        }

        row.querySelector('.figures').append(
            figure(report.minutesPlayed ?? 0, 'min'),
            figure(report.goals ?? 0, 'goals'),
            figure(report.assists ?? 0, 'assists'),
        );

        list.append(row);
    }
}

// ---------------------------------------------------------------- one match

/**
 * One match, from this player's side of it.
 *
 * Everything here was written into their own report when the coach published,
 * so opening it costs no reads and can expose nothing the season list could
 * not already. Reports published before this existed have no timeline and no
 * score; the view degrades to what is there rather than erroring.
 */
function openMatch(report) {
    open.video?.destroy();
    open.video = null;
    open.report = report;

    setText('md-date', report.matchDate || '');
    setText('md-title', `vs ${report.opponentName || '—'}`);
    setText('md-score-us', report.scoreUs ?? '—');
    setText('md-score-them', report.scoreThem ?? '—');
    setText('md-line', matchLine(report));

    renderMatchStats(report);
    renderVideo(report);
    renderTeam(report);

    showOnly('view-match', VIEWS);
    window.scrollTo(0, 0);
}

/** One sentence about their afternoon, so the page opens by saying something. */
function matchLine(report) {
    const bits = [];
    if (report.goals) bits.push(plural(report.goals, 'goal'));
    if (report.assists) bits.push(plural(report.assists, 'assist'));

    const minutes = report.minutesPlayed ?? 0;
    const played = minutes ? `${minutes} minutes` : 'an unused substitute';

    if (report.scoreUs == null) return bits.length ? bits.join(' and ') : played;

    const result = report.scoreUs > report.scoreThem ? 'Won'
        : report.scoreUs < report.scoreThem ? 'Lost' : 'Drew';
    return bits.length
        ? `${result}. You played ${played} and got ${bits.join(' and ')}.`
        : `${result}. You played ${played}.`;
}

function renderMatchStats(report) {
    const grid = byId('md-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(report.minutesPlayed ?? 0, 'Minutes'),
        statCard(report.goals ?? 0, 'Goals', report.goals ? 'is-good' : 'is-muted'),
        statCard(report.assists ?? 0, 'Assists', report.assists ? 'is-good' : 'is-muted'),
        statCard(report.fouls ?? 0, 'Fouls', 'is-muted'),
    );

    const cards = (report.yellowCards ?? 0) + (report.redCards ?? 0);
    if (cards) grid.append(statCard(cards, 'Cards', 'is-warn'));

    // Video-derived, and marked as such. The confidence comes from how many
    // tracked fragments this player had to be assembled from — every join is
    // a place the tracker could have picked up somebody else.
    const trust = cvPlayerConfidence(report.cvClusterCount);
    if (report.cvTouches != null) {
        grid.append(statCard(report.cvTouches, 'Touches', 'is-muted', trust));
    }
    if (report.cvDistanceM != null) {
        grid.append(statCard(
            (report.cvDistanceM / 1000).toFixed(2), 'km covered', 'is-muted', trust,
        ));
    }
    // Beside the total rather than instead of it. The kilometres are the figure
    // a player wants and the one that shrinks when the tracker loses them; the
    // rate is the one that survives, and the one that can be compared against a
    // team-mate who played a different number of minutes.
    const rate = metresPerMinute(report.cvDistanceM, report.cvMinutesTracked);
    if (rate != null) {
        grid.append(statCard(Math.round(rate), 'Metres a minute', 'is-muted', trust));
    }
    if (report.cvTopSpeedKmh != null) {
        grid.append(statCard(
            report.cvTopSpeedKmh.toFixed(1), 'Top speed km/h', 'is-muted', trust,
        ));
    }
    // How many times they went from walking to running hard. Absent, not zero,
    // when the tracking was too jittery to tell one from the wobble — see
    // `position_noise_m` in cv/metrics.py, and the note under this grid.
    if (report.cvAccelerations != null) {
        grid.append(statCard(report.cvAccelerations, 'Bursts', 'is-muted', trust));
    }
    if (report.cvTackles != null) {
        grid.append(statCard(report.cvTackles, 'Tackles won', 'is-muted', trust));
    }

    renderCoverageNote(report);
    renderPlayerHeatmap(report);
    renderPlayerShots(report);
}

/**
 * How much of this player's match the video actually measured.
 *
 * The card grid puts "Minutes 71" next to "km covered 1.9" and those have never
 * had the same denominator — the first is the sub log, the second is however
 * much of it the tracker held on to. Side by side and unlabelled they read as
 * one claim, and a player comparing their kilometres to a team-mate's is mostly
 * comparing who the tracker followed.
 *
 * Written out rather than shown as a confidence mark: three pips can say "trust
 * this less", but they cannot say *why*, and the why here is a specific number
 * of minutes the player can check against their own memory of the game.
 */
function renderCoverageNote(report) {
    const note = byId('md-stats-note');
    const text = coverageNote(
        {
            trackedS: report.cvMinutesTracked == null ? null : report.cvMinutesTracked * 60,
            onPitchS: report.cvMinutesOnPitch == null ? null : report.cvMinutesOnPitch * 60,
            watchedS: report.cvMinutesFilmed == null ? null : report.cvMinutesFilmed * 60,
            share: report.cvTrackedShare ?? null,
        },
        { second: true },
    );

    note.textContent = text || '';
    note.classList.toggle('hidden', !text);
}

/**
 * This player's shots, placed on the half they were attacking.
 *
 * Every shot is a button that seeks the video to it, which is the point — a
 * player is not going to scrub ninety minutes to find the one they hit wide,
 * and being able to watch it is the difference between a statistic and
 * something they learn from.
 */
function renderPlayerShots(report) {
    const block = byId('md-shots-block');
    const marks = report.cvShotMap || [];
    // The same band the coach's page applies, from the error published onto
    // this report. A player comparing their map to what their coach was shown
    // has to be looking at the same claim.
    const trust = xgTrust(report.cvCalibrationErrorM);
    const sized = trust === 'shot';

    const drawn = renderShotMap(byId('md-shots'), marks, {
        onPick: (mark) => seekTo(clock.toClock(mark.video_s || 0).clockS, clock),
        label: sized
            ? 'Your shots, placed on the pitch and sized by how good a chance each was'
            : 'Your shots, placed on the pitch',
        xgTrust: trust,
    });

    block.classList.toggle('hidden', !drawn);
    if (!drawn) return;

    const totals = shotSummary(marks, trust);
    setText('md-shots-note',
        `${plural(totals.shots, 'shot')}, ${totals.onTarget} on target`
        + (totals.goals ? `, ${plural(totals.goals, 'goal')}` : '')
        + (totals.xg != null
            ? ` — worth about ${totals.xg.toFixed(2)} expected goals.`
            : '.')
        + (sized ? ' Bigger circles were better chances.' : '')
        + ' Tap one to watch it.');
}

/**
 * Where this player spent the match.
 *
 * One report is one player, so there is nothing to merge here — the pipeline
 * already combined their clusters, weighted by how long each was tracked. The
 * merge function still runs, because handing it a single grid is how it
 * normalises and validates one.
 *
 * The caption carries the caveat rather than a confidence mark: a heatmap
 * assembled from nine fragments is a weaker claim than one tracked cleanly, and
 * that is a sentence rather than three pips.
 */
function renderPlayerHeatmap(report) {
    const block = byId('md-heatmap-block');
    const drawn = renderHeatmap(
        byId('md-heatmap'),
        [{ grid: report.cvHeatmap, minutes: report.cvMinutesTracked }],
        {
            attackingEnd: report.cvAttackingEnd || null,
            label: 'A pitch, shaded where you spent most of your time',
        },
    );

    block.classList.toggle('hidden', !drawn);
    if (!drawn) return;

    const fragments = report.cvClusterCount || 0;
    setText('md-heatmap-note',
        'Shaded where you spent the most time.'
        + (report.cvMinutesTracked
            ? ` From ${Math.round(report.cvMinutesTracked)} minutes the video tracked you.`
            : '')
        + (fragments > 2
            ? ` The video lost and refound you ${fragments} times, so this is`
                + ' rougher than it looks.'
            : ''));
}

// ---------------------------------------------------------------- the video

/**
 * The match video with this player's moments marked along it.
 *
 * The marks are the point. A player is not going to scrub a 90-minute video
 * looking for the twelve seconds they were involved in, so every moment is a
 * button that seeks straight to it.
 *
 * Touches from the CV pipeline slot into the same strip once they exist — they
 * are the same shape as a tagged moment, a clock reading and a label. Until
 * then this shows what a human tagged, which is real today.
 */
function renderVideo(report) {
    const block = byId('md-video-block');
    const moments = (report.timeline || []).filter((e) => e.mine);
    // Written by cv/publish.py, and only for a player whose cluster a coach
    // has confirmed — an unconfirmed one would put somebody else's touches on
    // this player's video.
    const touches = report.cvTouchTimes || [];

    // Nothing to show at all: no video and nothing to mark on one.
    if (!report.videoUrl && !moments.length) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    const clock = clockFromMatch(report);
    const marks = marksFor(report, moments, touches);

    open.video = renderMatchVideo(
        {
            video: byId('md-video'),
            strip: byId('md-scrubber'),
            list: byId('md-moments'),
            note: byId('md-video-note'),
        },
        {
            url: report.videoUrl,
            clock,
            marks,
            clockText,
            // So a mark tagged in stoppage time does not fall off the end of a
            // bar drawn to ninety minutes.
            extraTimes: (report.timeline || []).map((e) => e.clockS || 0),
            emptyText: 'Nothing was tagged for you in this match.',
            // Taken over rather than left to the module: a tap with no video
            // should say why, and the video wants scrolling into view.
            onSeek: (clockS) => seekTo(clockS, clock),
            notes: {
                embed: touches.length
                    ? `${plural(moments.length, 'tagged moment')} and `
                      + `${plural(touches.length, 'touch', 'touches')} found in the `
                      + 'video. Tap any of them to jump there.'
                    : `${plural(moments.length, 'moment')}. Tap one to jump to it.`,
                link: 'That video link cannot be played inside PitchIQ, so the '
                    + 'times below are match-clock readings.',
                none: 'No video for this match yet — ask your coach to add one. '
                    + 'The times below are match-clock readings.',
            },
        },
    );
}

/**
 * Everything markable in this match, as the shared timeline's mark shape.
 *
 * Touches first so a tagged moment paints over one — a goal and the touch that
 * scored it land on the same pixel, and the goal is the one worth seeing.
 */
function marksFor(report, moments, touches) {
    return [
        ...touches.map((clockS) => ({ clockS, type: 'touch', label: 'Touch' })),
        ...moments.map((m) => ({ clockS: m.clockS, type: m.type, label: m.label })),
    ];
}

function seekTo(clockS, clock) {
    if (!open.video) {
        toast('No playable video for this match yet.');
        return;
    }
    open.video.seek(clock.toVideo(clockS));
    byId('md-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ---------------------------------------------------------------- the team

/**
 * How the team did, without naming anyone.
 *
 * Goalscorers appear on the timeline above because they are announced out loud
 * at the match anyway. Everything else about a teammate — minutes, fouls, and
 * above all cards — stays with the coach.
 */
function renderTeam(report) {
    const list = byId('md-team');
    list.innerHTML = '';

    const counts = report.teamCounts;
    if (!counts) {
        list.innerHTML =
            '<div class="empty">Team numbers were not saved for this match.</div>';
        return;
    }

    const rows = [
        ['Goals', counts.us.goal, counts.them.goal, 'high'],
        ['Corners', counts.us.corner, counts.them.corner, 'high'],
        ['Free kicks won', counts.us.free_kick, counts.them.free_kick, 'high'],
        ['Fouls committed', counts.us.foul, counts.them.foul, 'low'],
        ['Offside', counts.us.offside, counts.them.offside, 'low'],
        ['Cards', counts.us.card, counts.them.card, 'low'],
    ];

    for (const [label, us, them, better] of rows) {
        if (!us && !them) continue;
        list.append(tally(label, us ?? 0, them ?? 0, better));
    }

    if (!list.children.length) {
        list.innerHTML = '<div class="empty">Nothing beyond the restarts was tagged.</div>';
    }
}

// ---------------------------------------------------------------- load

function showEmpty(message) {
    mountPitchBackdrop(byId('empty-hero'), { opacity: 0.16 });
    if (message) setText('empty-msg', message);
    renderEmptySample();
    showOnly('view-empty', VIEWS);
}

/**
 * The two plots a filmed match adds, drawn from the sample fixture.
 *
 * Through the same two renderers the real report uses, which is the only
 * arrangement worth having — a bespoke preview would prove the preview works.
 *
 * Safe here in a way it would not be on a real report: this view only exists
 * when the player has nothing published, so there is no genuine figure on the
 * page for an invented one to be confused with. The plots carry the dashed
 * sample border anyway, because a screenshot loses the heading above them.
 */
function renderEmptySample() {
    const report = samplePlayerReport();

    renderHeatmap(
        byId('empty-sample-heatmap'),
        [{ grid: report.cvHeatmap, minutes: report.cvMinutesTracked }],
        {
            attackingEnd: report.cvAttackingEnd,
            label: 'Example heatmap: a pitch shaded where a player spent their time',
        },
    );

    renderShotMap(byId('empty-sample-shots'), report.cvShotMap, {
        label: 'Example shot map: two shots placed on the pitch',
        xgTrust: xgTrust(report.cvCalibrationErrorM),
    });

    // A season, not a match. The other two plots answer "what does one filmed
    // match give me"; this one answers "and what is it for", which is the
    // question a player with an empty page is actually asking.
    const forms = seasonForms(sampleSeason());
    renderForms(byId('empty-sample-form'), forms, { minPoints: MIN_FORM_POINTS });
    setText('empty-sample-form-note', formNote(forms, { measured: 'were filmed' }));

    setText('empty-sample-note', SAMPLE_NOTICE);
}

async function loadReports(user) {
    setText('who', user.email);

    const reports = await myReports(user.uid);
    if (!reports.length) return showEmpty();

    const latest = reports[0];
    mountPitchBackdrop(byId('player-hero'), { opacity: 0.16 });
    setText('player-name', latest.playerName || 'My season');
    setText('player-number', latest.jerseyNumber ?? '—');
    setText('player-sub', latest.teamName || 'Your season');

    renderSeason(reports);
    renderChart(reports);
    renderForm(reports);
    renderMatches(reports);
    showOnly('view-reports', VIEWS);
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    byId('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));

    byId('btn-back').addEventListener('click', () => {
        // Tear the player down on the way out, or a YouTube iframe keeps
        // playing behind the season list.
        open.video?.destroy();
        open.video = null;
        showOnly('view-reports', VIEWS);
    });

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        loadReports(user).catch((err) => {
            // A missing composite index surfaces here first, so name it rather
            // than showing a generic failure.
            const message = err?.code === 'failed-precondition'
                ? 'Still getting things ready. Please try again in a minute.'
                : err.message || 'Could not load your reports.';
            toast(message, true);
            showEmpty(message);
        });
    });
}

init();
