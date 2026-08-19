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

import { onUser, signOut, configWarning } from '../assets/auth.js?v=94';
import {
    myReports, seasonTotals, cvPlayerConfidence, knownMinutes,
} from '../assets/db.js?v=94';
import { CARD_COLOURS } from '../assets/events.js?v=94';
import { mountRail } from '../assets/rail.js?v=94';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=94';
import { renderHeatmap } from '../assets/heatmap.js?v=94';
import { renderShotMap, shotSummary } from '../assets/shot-map.js?v=94';
import {
    xgTrust, metresPerMinute, coverageNote, clockFromMatch, printStamp,
    playerWobbleNote,
    minutesNote, seasonGroups, matchLine,
} from '../assets/report.js?v=94';
import {
    seasonForms, formNote, MIN_FORM_POINTS, MIN_POINT_MINUTES,
} from '../assets/season.js?v=94';
import { renderForms } from '../assets/form-chart.js?v=94';
import {
    samplePlayerReport, sampleSeason, SAMPLE_NOTICE,
} from '../assets/sample-report.js?v=94';
import { renderMatchVideo } from '../assets/match-video.js?v=94';
import {
    byId, setText, toast, showOnly, clockText, statCard, figure, cardChips,
    plural, minutesChart, tally, coverageStrip,
} from '../assets/ui.js?v=94';

const VIEWS = ['view-empty', 'view-reports', 'view-match'];

// What the page is currently showing: the open match and its video handle so
// a moment can seek it, and the season totals the rail stands beside them.
// The rails read from here rather than from a captured argument — `mountRail`
// keeps its callback, so a closure over a parameter would pin the rail to
// whichever match was opened first.
const open = { report: null, video: null, season: null };

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
        // The minutes here are only the matches somebody kept a clock for. On a
        // season made entirely of untagged matches that total is zero, and
        // "0 minutes on the field" is the season-sized version of the sentence
        // this whole change exists to stop printing.
        if (!totals.minutes) return `${opening}.`;
        const short = totals.minutesUnknown
            ? ` from the ${totals.matches - totals.minutesUnknown} with a clock kept`
            : '';
        return `${opening}, ${totals.minutes} minutes on the field${short}.`;
    }

    const tally = [];
    if (totals.goals) tally.push(plural(totals.goals, 'goal'));
    if (totals.assists) tally.push(plural(totals.assists, 'assist'));

    // "in 7 of them" rather than "across 7 matches" — the sentence already
    // opened with a match count, and saying it twice reads as a mistake.
    const spread = scoring > 1 ? ` — involved in ${scoring} of them` : '';
    return `${opening}, ${tally.join(' and ')}${spread}.`;
}

/**
 * The season, in the groups `seasonGroups` decided on.
 *
 * The grouping lives in report.js because this same season is read on three
 * screens — here, the coach's view of this player, and the coach's roster — and
 * three copies of "which figures belong together" had already drifted into
 * disagreeing about what a tackle is called.
 *
 * Each group maps to a block, and a block with no group hides itself, which is
 * what takes the three video sections off the rail for a season nobody filmed.
 */
function renderSeason(reports) {
    const totals = seasonTotals(reports);

    setText('player-line', seasonLine(reports, totals));

    const groups = new Map(
        seasonGroups(reports, totals, { second: true }).map((g) => [g.id, g]),
    );
    fillGroup('season-totals-block', 'season-stats', groups.get('tagged'));
    fillGroup('season-ball-block', 'ball-stats', groups.get('ball'), 'ball-note');
    fillGroup('season-running-block', 'running-stats', groups.get('running'), 'running-note');
    fillGroup('season-defending-block', 'defending-stats', groups.get('defending'), 'defending-note');

    renderForm(reports);
}

/** One group into its block, and the block off when the group is not there. */
function fillGroup(blockId, gridId, group, noteId = null) {
    const block = byId(blockId);
    const grid = byId(gridId);
    if (!block || !grid) return;

    grid.innerHTML = '';
    block.classList.toggle('hidden', !group);
    if (!group) return;

    for (const row of group.rows) {
        grid.append(statCard(row.value, row.label, row.tone || '', row.confidence));
    }
    if (noteId) setText(noteId, group.note);
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
 * Which season trace belongs under which group of totals.
 *
 * The mapping is here rather than in season.js because it is a layout decision,
 * not an arithmetic one — the same four measures on the coach's screen are one
 * row under one heading, and that is right there.
 */
const FORM_GROUPS = [
    { host: 'ball-form', keys: ['touchesPerMin', 'passAccuracy'] },
    { host: 'running-form', keys: ['distancePerMin', 'topSpeed'] },
];

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
 *
 * The caveat block is separate from the traces and stands or falls with all of
 * them together, because it is one sentence about the same set of matches. Four
 * copies of it, one per group, would read as four separate problems with the
 * season rather than one fact about how much of it was filmed.
 */
function renderForm(reports) {
    const forms = seasonForms(reports);
    const byKey = new Map(forms.map((form) => [form.key, form]));

    let anyDrawn = false;
    for (const group of FORM_GROUPS) {
        const host = byId(group.host);
        if (!host) continue;
        const mine = group.keys.map((key) => byKey.get(key)).filter(Boolean);
        const drawn = renderForms(host, mine, { minPoints: MIN_FORM_POINTS });
        anyDrawn ||= Boolean(drawn);
    }

    const block = byId('form-block');
    if (!block) return;
    block.classList.toggle('hidden', !anyDrawn);
    if (!anyDrawn) return;

    setText('form-note', formNote(forms, { measured: 'were filmed' }));

    const strip = byId('coverage-strip');
    strip.innerHTML = '';
    strip.append(coverageStrip(reports, { thinBelow: MIN_POINT_MINUTES }));
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
            figure(knownMinutes(report) ? (report.minutesPlayed ?? 0) : '—', 'min'),
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

    // Only ever seen on paper, where the page has lost everything that said
    // whose report this is. See `printStamp`.
    setText('md-print-stamp', printStamp({
        subject: report.playerName || open.playerName || null,
        matchLine: `vs ${report.opponentName || '—'}`
            + (report.matchDate ? ` · ${report.matchDate}` : ''),
        estimated: Boolean(report.cvTrackedShare != null || report.cvDistanceM != null),
    }));

    renderMatchStats(report);
    renderVideo(report);
    renderTeam(report);
    // A player who was reading the shot map of one match and opens another
    // should land where every match opens, so the rail forgets first.
    matchRail?.reset();
    renderMatchRail();

    showOnly('view-match', VIEWS);
    window.scrollTo(0, 0);
}

function renderMatchStats(report) {
    const grid = byId('md-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(
            knownMinutes(report) ? (report.minutesPlayed ?? 0) : '—', 'Minutes',
            knownMinutes(report) ? '' : 'is-muted',
        ),
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

    // Their coach checked some of these against the video and changed them.
    // Worth saying on the player's own page too: without it, a figure that
    // differs from what a team-mate remembers has no explanation.
    const corrected = report.cvReviewed
        ? 'Your coach has checked some of these against the video and corrected them.'
        : '';

    // Why the Minutes card above is a dash. It sits here rather than beside the
    // card because it is a sentence about the match and not about the number,
    // and because this is where every other "what this rests on" line already
    // lives on this page.
    const clock = knownMinutes(report) ? '' : minutesNote(null, { second: true });

    // How precisely, after how much. `coverageNote` says which minutes of
    // the match were measured at all; this says how steady the measuring
    // was while it ran, and it is the only thing on the page that explains
    // a missing Bursts card. Read off this player's own track, not the
    // run's average — the pipeline withholds bursts per track.
    const wobble = playerWobbleNote(report.cvPositionNoiseM);

    const full = [clock, text, wobble, corrected].filter(Boolean).join(' ');

    note.textContent = full;
    note.classList.toggle('hidden', !full);
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
        + (sized ? ' Bigger circles were better chances.' : ''));
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
                      + 'video. Tap any of them to jump there, and the bar '
                      + 'follows along as it plays.'
                    : `${plural(moments.length, 'moment')}. Tap one to jump to `
                      + 'it, and the bar follows along as it plays.',
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
    renderMatches(reports);
    // Last, once every block above has decided whether it is on screen: the
    // rail lists what is there, and a season with no footage behind it has
    // three fewer sections than one with.
    renderSeasonRail(reports);
    showOnly('view-reports', VIEWS);
}

// ------------------------------------------------------------------- the rails
//
// Both views get one, and it is the same component the coach's match report
// uses — see assets/rail.js for why that matters more than it looks like it
// should. The facts are the difference: a coach's rail stands the scoreline and
// how much of the match was tagged; a player's stands their own season.

let seasonRail = null;
// Read through `open.season` for the same reason the match rail reads
// `open.report`: mountRail keeps the callback, so a closure over the
// argument would freeze the first set of totals it ever saw.
function renderSeasonRail(reports) {
    open.season = seasonTotals(reports);
    seasonRail ||= mountRail({
        body: byId('season-body'),
        rail: byId('season-rail'),
        heading: 'Your season',
        facts: () => {
            const totals = open.season;
            if (!totals) return [];
            return [
                { label: 'Matches', value: totals.matches, tone: 'is-big' },
                {
                    label: 'Minutes',
                    value: totals.minutes || null,
                    // Named rather than left to be inferred from a number that
                    // is quietly short. The alternative is a player dividing by
                    // a total that is missing two matches and never knowing.
                    note: totals.minutesUnknown
                        ? `${totals.minutesUnknown} without a clock`
                        : null,
                },
                {
                    label: 'Goals + assists',
                    value: totals.goals + totals.assists,
                    tone: totals.goals + totals.assists ? 'is-good' : '',
                },
                {
                    label: 'Filmed',
                    value: totals.cvMatches
                        ? `${totals.cvMatches} of ${totals.matches}`
                        : 'none yet',
                },
            ];
        },
    });
    seasonRail.render();
}

/**
 * The rail beside one match.
 *
 * The facts read `open.report` rather than a parameter, and that is not a
 * detail: `mountRail` runs once and keeps the callback, so a closure over the
 * argument would have pinned the rail to whichever match was opened first and
 * shown its scoreline over every match after it.
 */
let matchRail = null;
function renderMatchRail() {
    matchRail ||= mountRail({
        body: byId('md-body'),
        rail: byId('md-rail'),
        heading: 'This match',
        facts: () => {
            const report = open.report;
            if (!report) return [];
            const minutes = knownMinutes(report) ? (report.minutesPlayed ?? 0) : null;
            const facts = [];
            if (report.scoreUs != null) {
                facts.push({
                    label: 'Score',
                    value: `${report.scoreUs}–${report.scoreThem ?? 0}`,
                    tone: 'is-big',
                });
            }
            facts.push({ label: 'Minutes', value: minutes });
            facts.push({
                label: 'Goals + assists',
                value: (report.goals || 0) + (report.assists || 0),
                tone: (report.goals || 0) + (report.assists || 0) ? 'is-good' : '',
            });
            // Only where there is footage. On the eleven matches in twelve
            // nobody filmed, a "Followed for" row reading an em dash would be a
            // standing note about an absence the player cannot do anything
            // about.
            if (report.cvMinutesTracked != null) {
                facts.push({
                    label: 'Followed for',
                    value: `${Math.round(report.cvMinutesTracked)}′`,
                    note: 'of video',
                });
            }
            return facts;
        },
    });
    matchRail.render();
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    byId('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));

    byId('btn-print').addEventListener('click', () => window.print());
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
