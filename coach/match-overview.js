// The top and the spine of the match view: the team figures and the brief,
// the section rail, the estimate banner, and the player table.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { cvConfidence, cvStatsByPlayer, cvReportFields } from '../shared/db.js?v=152';
import { isSample } from '../shared/sample-report.js?v=152';
import {
    cvQualityNotes, roughDuration, hasVerdict, currentAnswers, groupStats, teamStatRows,
    taggedTeamRows, taggedCount, trackedCoverage, metresPerMinute, TRACKED_SHARE_FLOOR,
    shotLedger, headerCorrection, clockFromMatch, blindSplit, isKeeper, groupByPosition,
    minutesNote, FROM_LAST_TAG, matchBrief, cvReads,
} from '../shared/report.js?v=152';
import { CARD_COLOURS } from '../shared/events.js?v=152';
import { mountRail } from '../shared/rail.js?v=152';
import {
    byId, setText, clockText, plural, statGroup, cardChips, stackBar,
} from '../shared/ui.js?v=152';
import { activeCv, state, teamLabels } from './shell.js?v=152';

/**
 * The whole match as boxes, sorted into the questions they answer.
 *
 * Tagged counts and video-derived figures go into the same set of groups rather
 * than into a tagged block and a video block. A coach asking "did we pass it
 * forward" does not care which half of the system knew the answer, and the
 * confidence mark on the video rows is what says which is which — that
 * distinction belongs on the row, not in the page layout.
 */
export function renderTeamStats(stats) {
    const host = byId('team-stats');
    host.innerHTML = '';

    const cv = activeCv();
    const quality = cv?.quality || {};

    const tagged = taggedStatRows(stats, state.match?.log);
    const rows = [
        ...tagged,
        ...teamStatRows(cv, {
            events: cvConfidence(quality, 'events'),
            possession: cvConfidence(quality, 'possession'),
        }),
    ];

    // The first group open, the rest folded to their headings: five screens
    // of boxes were the one complaint about this page that every figure on it
    // could not answer. See `renderBrief` for what replaced the scrolling.
    groupStats(rows).forEach((group, i) => {
        host.append(statGroup(group, { folded: i > 0 }));
    });
    host.classList.toggle('has-folds', host.children.length > 1);
    renderBrief(rows);

    // Said once, here, rather than left as an absence a reader has to notice.
    // A filmed match nobody tagged is not a corner case — it is the ordinary
    // one — and the video rows are still real, so this cannot be the block's
    // empty state. The second clause only when there is something above to
    // point at; with no log and no footage there is nothing on this page at all
    // and the sentence has to say so on its own.
    setText('team-untagged', tagged.length
        ? ''
        : 'Nobody ran the tablet for this match, so there are no tapped counts '
            + '— no corners, no fouls, no cards.'
            + (host.children.length
                ? ' Everything above was measured from the video.'
                : ' Nothing was measured from video either.'));
    byId('team-untagged')?.classList.toggle('hidden', Boolean(tagged.length));

    // The key only earns its place once there are bars to key. Half the rows
    // here are ours alone — goals we scored, fouls we conceded — and a legend
    // over a column of single-sided cards would be pointing at nothing.
    const key = byId('tally-key');
    if (key) {
        const { usName, themName } = teamLabels();
        setText('tk-us', usName);
        setText('tk-them', themName);
        key.hidden = !host.querySelector('.tally');
    }
}

/** The lines at the top of the review. See `matchBrief`. */
function renderBrief(rows) {
    const block = byId('match-brief');
    const list = byId('brief-list');
    if (!block || !list) return;

    const match = state.match;
    const log = match?.log || [];
    const cv = activeCv();
    const lines = matchBrief({
        log,
        roster: match?.roster,
        counts: match?.stats?.counts,
        status: match?.status,
        rows,
        reads: cvReads(cv, {
            videoOffsetS: match?.videoOffsetS ?? 0,
            goals: !state.cvPreview && log.length
                ? (match?.stats?.counts.us.goal ?? 0) : undefined,
        }),
    });

    list.innerHTML = '';
    for (const line of lines) {
        const item = document.createElement('li');
        item.className = 'brief-line'
            + (line.tone ? ` is-${line.tone}` : '');
        const title = document.createElement('strong');
        title.textContent = line.title;
        item.append(title);
        if (line.detail) {
            const detail = document.createElement('span');
            detail.textContent = line.detail;
            item.append(detail);
        }
        if (line.estimated) {
            const mark = document.createElement('span');
            mark.className = 'brief-est';
            mark.textContent = isSample(cv) ? 'Sample data' : 'Estimated from video';
            item.append(mark);
        }
        list.append(item);
    }
    block.classList.toggle('hidden', !lines.length);
}

/**
 * The figures somebody tapped on a tablet, or nothing at all.
 *
 * Nothing at all when the log is empty, and that is the whole point of this
 * function rather than a guard the caller could forget. `aggregateMatch`
 * initialises every type to zero, so a match nobody ran the tablet for came out
 * of here as nine boxes reading 0 — a full report of a match in which, it said,
 * nobody took a corner, gave away a foul or was booked. That is the same defect
 * as a whistle at second zero: absent read as measured, on the most ordinary
 * demo there is. See `whistleFrom`.
 *
 * The rows themselves come from `taggedTeamRows`, which the half-time page also
 * uses. They were two hand-written lists until 2026-08-16 and had already
 * drifted apart — see that function.
 */
function taggedStatRows(stats, log) {
    if (!(log || []).length) return [];
    return taggedTeamRows(stats.counts, { subs: stats.subs ?? 0 });
}

// ------------------------------------------------------------- the section rail
//
// A match report is about fifteen blocks. Stacked one to a row it ran to roughly
// ten screens on a laptop, which is where this page is actually read — a coach
// opens it at a desk the evening after the game, not on the touchline.
//
// The rail itself is `shared/rail.js`, shared with the player portal. What
// belongs here is the two things only this page can answer: which figures are
// worth standing at the top of the rail, and what is still outstanding in each
// section.

/** The standing figures beside a match report, whichever section is open. */
function railFacts() {
    const stats = state.match?.stats;
    if (!stats) return [];

    const cv = activeCv();
    const played = stats.players?.filter((p) => (p.minutesPlayed ?? 0) > 0).length || 0;

    // The same rule as the scoreline in the header: a match nobody tagged has
    // no score, not a 0–0 it never had.
    const log = state.match.log || [];
    const facts = [{
        label: 'Score',
        value: log.length
            ? `${taggedCount(stats.counts.us.goal, log)}–${taggedCount(stats.counts.them.goal, log)}`
            : null,
        tone: 'is-big',
    }];

    // Absent is not zero, in the rail too. A match nobody tagged has no clock,
    // and `0'` here would be the same lie the player portal had to stop telling
    // — that the game was played and lasted no time.
    const end = stats.matchEndS > 0 ? stats.matchEndS : null;
    facts.push({
        label: 'Tagged to',
        value: end == null ? null : clockText(end),
        note: end != null && stats.matchEndSource === FROM_LAST_TAG
            ? 'last tag, not the whistle'
            : null,
    });
    facts.push({ label: 'Players on', value: played || null });

    // Only when there is footage behind the report. A "filmed" row reading an
    // em dash on the eleven matches out of twelve nobody filmed would be a
    // standing reminder of the thing this page cannot do anything about.
    if (cv) {
        const share = cv.quality?.live_share;
        facts.push({
            label: 'Ball in play',
            value: share == null ? null : `${Math.round(share * 100)}%`,
            note: isSample(cv) ? 'sample data' : null,
        });
    }

    return facts;
}

/** Numbers worth carrying up to the rail — outstanding work, not totals. */
function railCounts() {
    const counts = {};

    // Figures still waiting for a name. The count that actually decides whether
    // the video columns in the table mean anything, and it lives eight screens
    // down the page.
    const clusters = state.match?.cv?.identity?.clusters || [];
    const answered = currentAnswers(
        clusters.map((cluster) => cluster.cluster_id), state.match?.cvMapping,
    ).filter(Boolean).length;
    const unnamed = clusters.length - answered;
    if (unnamed > 0) {
        counts['cv-clusters-block'] = {
            n: unnamed,
            title: `${plural(unnamed, 'tracked figure')} still unmatched`,
        };
    }

    const events = state.match?.cvEvents?.events || [];
    const checked = currentAnswers(
        events.map((event) => event.id), state.match?.cvReview?.byEvent,
    ).filter(hasVerdict).length;
    const unchecked = events.length - checked;
    if (unchecked > 0) {
        counts['cv-review-block'] = {
            n: unchecked,
            title: `${plural(unchecked, 'event')} not checked yet`,
        };
    }

    return counts;
}

/**
 * The rail, built once and re-rendered whenever the report changes.
 *
 * Lazily, because `openMatch` is the first thing that has a body worth wiring
 * and the page is also opened on views that have no report at all.
 */
export let matchRail = null;
export function renderMatchRail() {
    matchRail ||= mountRail({
        body: byId('match-body'),
        rail: byId('match-rail'),
        facts: railFacts,
        counts: railCounts,
    });
    matchRail.render();
}

/** Put the quality banner back, from whatever the state now says. */
export function redrawCvNote() {
    document.querySelector('.cv-note')?.remove();
    document.querySelector('.cv-warnings')?.remove();

    const note = cvNote();
    if (note) byId('team-stats').before(note);
    // Above the note, because the two say different things. The note is what
    // these numbers rest on and is always true; a warning is something that
    // went wrong in this particular run.
    const warnings = cvWarnings();
    if (warnings) (note || byId('team-stats')).before(warnings);
}

/**
 * What the pipeline itself flagged about this run, or null.
 *
 * `report_json` has published a `warnings` list since the pipeline existed and
 * **nothing has ever drawn it**. `trustworthy` is defined as `not warnings`
 * (pitchiq/report_export.py), so every one of them was a judgement the pipeline made
 * about its own output that no coach could see — which also means the rule
 * "a warning is only ever about data quality, never a cosmetic limit" was being
 * enforced against a reader who did not exist.
 *
 * Separate from the quality note on purpose. The note is a standing set of
 * caveats about how these figures are made; this is a list of things that were
 * wrong with this run and might be fixable — an untagged restart, a goal the
 * two records disagree about, a window that ran through half-time.
 */
function cvWarnings() {
    const list = activeCv()?.warnings || [];
    if (!list.length) return null;

    const block = document.createElement('div');
    block.className = 'cv-warnings';
    block.innerHTML = '<strong></strong><ul></ul>';
    block.querySelector('strong').textContent = list.length === 1
        ? 'One thing to know about this run'
        : `${list.length} things to know about this run`;

    const ul = block.querySelector('ul');
    for (const text of list) {
        const li = document.createElement('li');
        // textContent, not innerHTML: these strings are assembled in Python and
        // interpolate a source filename among other things.
        li.textContent = text;
        ul.append(li);
    }
    return block;
}

/** The banner over the estimated section, naming what limited it. */
function cvNote() {
    const cv = activeCv();
    if (!cv) return null;

    const quality = cv.quality || {};
    const note = document.createElement('div');
    note.className = 'cv-note';

    const bits = cvNoteBits(cv, quality);

    // "Measured from video" is a claim, and on the preview it is false. The
    // banner is the one element guaranteed to sit above every estimated block,
    // which makes it the right place to carry the correction.
    const sampled = isSample(cv);
    if (sampled) note.classList.add('is-sample');

    // One plain sentence up front; the list of what limited the run is there
    // for whoever wants it, folded, because as a paragraph it buried the one
    // thing everybody needs — these are estimates.
    note.innerHTML = '<span></span>';
    note.querySelector('span').textContent = sampled
        // The label beside it already says "Sample", so not the notice's own
        // "Sample data —" a second time.
        ? 'Made-up numbers, so you can see the layout before there is footage. '
            + 'Nothing here was measured.'
        : 'Measured from video, so treat these as close rather than exact.';
    if (bits.length) {
        const why = document.createElement('details');
        why.className = 'cv-why';
        why.innerHTML = '<summary></summary><ul></ul>';
        why.querySelector('summary').textContent = sampled
            ? 'What a real run would be limited by'
            : 'What limits these numbers';
        for (const bit of bits) {
            const li = document.createElement('li');
            li.textContent = bit.charAt(0).toUpperCase() + bit.slice(1) + '.';
            why.querySelector('ul').append(li);
        }
        note.append(why);
    }

    const strong = document.createElement('strong');
    strong.textContent = sampled ? 'Sample ' : 'Estimated ';
    note.prepend(strong);

    const bar = blindBar(quality);
    if (bar) note.append(bar);
    return note;
}

/** What limited this run, as `cvQualityNotes` words it for the banner. */
function cvNoteBits(cv, quality) {
    return cvQualityNotes(quality, {
        calibrated: cv.calibrated,
        // So the xG caveat appears only once there is an xG row to caveat.
        shots: cv.teams?.team_a?.shots,
        // Same idea for the line height, which is absent on most runs: it needs
        // a calibration and somebody to have said which goal each side defends.
        lineHeight: cv.teams?.team_a?.shape?.line_m,
        calibrationErrorM: cv.calibrationErrorM,
        // So the keeper caveat appears only once there are keeping rows to
        // caveat. One keeper found is enough: `keeperStatRows` keeps a row on
        // one side alone, and the caveat covers whichever side got one.
        keepers: (cv.keepers || []).length,
        reconciliation: cv.reconciliation,
        // Which half, and what decided it. Every pitch picture below this
        // banner is drawn from the answer, and a wrong one mirrors all of them
        // without changing how any of them look.
        period: cv.period,
        periodSource: cv.periodSource,
        // So the foot-shot caveat stops claiming to be unfixed on a match where
        // the coach has already fixed it.
        headersTagged: headerCorrection(shotLedger(
            state.match?.cvEvents?.events || [], state.match?.cvReview,
        ))?.headers || 0,
    });
}

/**
 * What the run never saw, drawn rather than described. The banner's sentence
 * can carry one figure out of the split; the bar is the only thing that
 * shows the proportion, which is the whole reason the split exists — a
 * coach needs to know at a glance whether the missing time was stoppages
 * or football.
 */
function blindBar(quality) {
    const blind = blindSplit(quality);
    if (!blind?.segments?.length) return null;
    // The worst single stretch beside the total, because the same lost
    // minutes as one blackout and as a hundred flickers are different
    // failures and only one of them is worth scrubbing to. In video time,
    // which is what somebody would type into the player.
    const worst = blind.worst
        ? ` · longest ${roughDuration(blind.worst.durationS)} `
            + `at ${clockText(blind.worst.startS)}`
        : '';
    return stackBar(
        blind.segments.map((part) => ({
            ...part, text: roughDuration(part.seconds),
        })),
        { label: `No ball found — ${roughDuration(blind.totalS)} in total${worst}` },
    );
}

export function renderPlayerTable(players) {
    const body = byId('player-table').querySelector('tbody');
    body.innerHTML = '';

    // The *last* header row. There are two now — the upper one groups the
    // columns by where their numbers came from — and counting every th in the
    // head would make this colspan two columns too wide.
    const columns = byId('player-table')
        .querySelectorAll('thead tr:last-child th').length;
    if (!players.length) {
        body.innerHTML =
            `<tr><td colspan="${columns}" class="muted">No lineup was set for this match.</td></tr>`;
        return;
    }

    // Most involved first — a coach scanning this wants the standouts on top.
    // Kept as the order *within* a line rather than replaced by it, so the
    // grouping below adds a heading without taking the ranking away.
    // The `?? 0` is load-bearing on an untagged match: subtracting two nulls
    // gives NaN, a comparator that returns NaN is not an ordering, and the
    // table would come out in whatever order the sort happened to leave it.
    const compare = (a, b) => (b.goals + b.assists) - (a.goals + a.assists)
        || (b.minutesPlayed ?? 0) - (a.minutesPlayed ?? 0);

    // A position is a fact about the squad, not about this match: nothing in
    // this system records what a player actually played on a given day, so a
    // report opened today groups by where they play *now*. Snapshotting it into
    // the match roster would look more careful and would be worse — it is
    // written when the lineup is set, and most positions get filled in long
    // afterwards, so the snapshot would be empty for every match that mattered.
    const squad = new Map((state.players || []).map((p) => [p.id, p.position]));
    const ordered = players.map((p) => ({ ...p, position: squad.get(p.id) ?? null }));

    // What the minutes bar is a share of. The match's own length, not the
    // longest shift — a squad nobody rotated would otherwise draw itself with
    // eleven full bars and one stub, which is a claim about rotation rather
    // than about minutes. Falls back to the longest shift only when nothing
    // knows how long the match ran, where the two are the same thing anyway.
    const full = Math.max(
        (state.match?.stats?.matchEndS || 0) / 60,
        ...players.map((p) => p.minutesPlayed || 0),
    );

    for (const group of groupByPosition(ordered, compare)) {
        // No heading at all until somebody has set a position — `groupByPosition`
        // returns a single untitled group, and the table is exactly the one that
        // was there before.
        if (group.title) body.append(positionHeadRow(group, columns));
        for (const player of group.players) body.append(playerTableRow(player, full));
    }

    setText('player-table-note', [
        // First, because it is about the leftmost column and it is the reason
        // the column has dashes in it.
        minutesNote(state.match?.stats?.matchEndSource, {
            over: state.match?.status === 'full_time' || state.match?.finalized === true,
        }),
        coverageSummary(players),
        keeperNote(ordered),
    ].filter(Boolean).join(' '));
}

/**
 * What the video column is worth across the roster, in one line.
 *
 * Per-player coverage is four figures a coach has no reason to read one by one.
 * What they need to know before comparing the last column is whether it rests on
 * most of each player's match or on scraps of it, and which players are the
 * scraps — so the ones below the floor are named, and nobody else is mentioned.
 */
function coverageSummary(players) {
    const measured = players.filter((p) => p.cvTrackedShare != null);
    if (!measured.length) return '';

    // A number that moved between two visits looks like a bug unless something
    // says a person moved it. Named as a count of players rather than of
    // events, because the table is a list of players.
    const corrected = measured.filter((p) => p.cvReviewed).length;
    const reviewed = corrected
        ? ` Your review has adjusted the video columns for `
            + `${plural(corrected, 'player')}.`
        : '';

    const thin = measured
        .filter((p) => p.cvTrackedShare < TRACKED_SHARE_FLOOR)
        .sort((a, b) => a.cvTrackedShare - b.cvTrackedShare);

    const head = `Metres a minute is measured over the time the video held each`
        + ` player, not the time they played — ${measured.length} of`
        + ` ${players.length} were matched to a tracked figure at all.`;

    if (!thin.length) return head + reviewed;

    // Named rather than counted. "Four players are thin" sends a coach hunting;
    // the names are what makes the caveat actionable, and past three it becomes
    // a list nobody reads, so the rest are a count.
    const names = thin.slice(0, 3).map((p) => p.playerName).join(', ');
    const rest = thin.length > 3 ? ` and ${thin.length - 3} more` : '';
    const pct = Math.round(TRACKED_SHARE_FLOOR * 100);
    return `${head} It held ${names}${rest} for under ${pct}% of their filmed`
        + ' minutes, so those rows are a sample of the match rather than the'
        + ' whole of it.' + reviewed;
}

/** A line of the team, as a full-width row across the table. */
function positionHeadRow(group, columns) {
    const tr = document.createElement('tr');
    tr.className = 'pos-head';
    const th = document.createElement('th');
    th.colSpan = columns;
    th.scope = 'rowgroup';
    // In a span so the label can stay in view while a phone scrolls the
    // table sideways; the cell itself is as wide as the table and cannot.
    const label = document.createElement('span');
    label.className = 'pos-head-label';
    label.textContent = group.title;
    th.append(label);
    tr.append(th);
    return tr;
}

/**
 * Why a keeper's row is not to be read down the column with the rest.
 *
 * This is the whole reason the position field exists. A goalkeeper covers
 * roughly a third the ground of a midfielder, so in a single ranked table their
 * metres a minute reads as the least mobile player on the pitch — which is not
 * a finding, it is the job. The grouping above already separates them; this
 * says out loud why they are separated, once, and only when there is a keeper
 * on the sheet to separate.
 */
function keeperNote(players) {
    if (!players.some(isKeeper)) return '';
    return 'Goalkeepers are listed on their own line: a keeper covers a fraction'
        + ' of the ground an outfielder does, so metres a minute compares within'
        + ' a line and not across them.';
}

function playerTableRow(player, fullMinutes) {
    const tr = document.createElement('tr');

    // Tagged columns first, then the video ones, so the trustworthy numbers are
    // the ones a coach reads without scrolling sideways on a phone.
    const tagged = [player.minutesPlayed, player.goals, player.assists, player.fouls];
    const rate = metresPerMinute(player.cvDistanceM, player.cvMinutesTracked);
    const derived = [
        player.cvTouches, player.cvPassesCompleted, player.cvTackles,
        rate == null ? null : Math.round(rate),
    ];

    tr.innerHTML = `
        <td class="num"></td>
        <td></td>
        ${'<td class="num"></td>'.repeat(tagged.length)}
        <td><span class="cards-cell"></span></td>
        ${'<td class="num cv"></td>'.repeat(derived.length)}`;

    const cells = tr.querySelectorAll('td');
    cells[0].textContent = player.jerseyNumber ?? '—';
    cells[1].textContent = player.playerName;

    tagged.forEach((value, i) => {
        const cell = cells[i + 2];
        // Null is "nobody can say" and gets the same dash as a missing shirt
        // number two columns left. Without this it rendered the string "null",
        // and before that a 0 that read as a player who never came on.
        cell.textContent = value == null ? '—' : value;
        // Zeroes recede so the meaningful numbers carry the eye. An unknown
        // recedes with them: it is not a figure worth the reader's attention.
        if (!value) cell.classList.add('zero');
    });

    // Minutes, drawn as well as written. The table is sorted by goals and
    // assists, so nothing in the row order says who actually played the match —
    // and a column of two-digit numbers is exactly where a length reads faster
    // than a figure. Only the minutes: a bar behind every column would be a
    // wall, and these are the only ones that share a scale.
    // No bar for minutes nobody counted. A zero-length bar in a column of full
    // ones is the most emphatic way there is to say a player did not play.
    if (fullMinutes > 0 && player.minutesPlayed != null) {
        const cell = cells[2];
        cell.classList.add('bar-cell');
        const bar = document.createElement('i');
        bar.className = 'cell-bar';
        bar.style.setProperty(
            '--w', Math.min(1, player.minutesPlayed / fullMinutes).toFixed(3),
        );
        cell.append(bar);
    }

    const cardsCell = tr.querySelector('.cards-cell');
    const chips = cardChips(player.yellowCards, player.redCards, CARD_COLOURS);
    if (chips.length) cardsCell.append(...chips);
    else cardsCell.innerHTML = '<span class="none">—</span>';

    const cvCells = tr.querySelectorAll('td.cv');
    cvCells[0]?.classList.add('cv-first');
    derived.forEach((value, i) => {
        const cell = cvCells[i];
        // An em dash, not a zero: this player's video stats do not exist until
        // a coach has confirmed which tracked figure on the pitch was them.
        cell.textContent = value == null ? '—' : value;
        if (value == null || !Number(value)) cell.classList.add('zero');
    });

    // The rate is the only column whose meaning changes with coverage: the
    // counts beside it are undercounts either way, but a rate over a fifth of
    // someone's minutes is a claim about a fifth of their match while looking
    // exactly like a claim about all of it.
    const share = player.cvTrackedShare;
    if (share != null && share < TRACKED_SHARE_FLOOR && derived[3] != null) {
        const cell = cvCells[3];
        cell.classList.add('cv-partial');
        cell.title = `Measured over ${Math.round(share * 100)}% of the minutes`
            + ' this player was on screen for';
    }

    return tr;
}

/**
 * Attach the video's numbers to the players a coach matched them to.
 *
 * Recomputed from scratch each time rather than patched, so unmatching a
 * figure actually clears the row it was feeding — otherwise a mistake would
 * stay on screen until the page was reloaded.
 */
export function mergeCvPlayers(players, match, matchEndS = null) {
    // The coach's own verdicts go in here, not just onto the scorecard. Until
    // they did, a coach could reject thirty phantom passes, watch precision
    // fall, and still hand the player a report crediting them with all thirty.
    const stats = cvStatsByPlayer(match.cv?.identity?.tracks, match.cvMapping, {
        events: match.cvEvents?.events,
        review: match.cvReview,
        clusters: match.cv?.identity?.clusters,
    });
    const context = {
        window: match.cv?.window,
        clock: clockFromMatch(match),
        matchEndS,
    };

    for (const player of players) {
        for (const key of Object.keys(player)) {
            if (key.startsWith('cv')) delete player[key];
        }
        Object.assign(player, cvReportFields(
            stats[player.id],
            trackedCoverage(stats[player.id]?.minutes_tracked, player.stints, context),
        ));
    }
}
