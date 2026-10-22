// How a match was played: where each player stood, how the ball moved, how
// long the press lasted, and the football either side of a substitution.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { cvStatsByPlayer } from '../shared/db.js?v=151';
import {
    samplePassEvents, samplePassMapping, sampleShapeGrids, sampleSubRoster, sampleSubEvents,
    sampleSubClock,
} from '../shared/sample-report.js?v=151';
import {
    playersByTrack, passingNetwork, foldEdges, strongestLink, networkNote,
} from '../shared/passing.js?v=151';
import { renderPassMap } from '../shared/pass-map.js?v=151';
import { mergeHeatmaps, orientedCentroid } from '../shared/heatmap.js?v=151';
import {
    NOT_A_PLAYER, pressingTrend, pressingNote, pressingRead, substitutionWindows,
    substitutionRead, substitutionNote, CHANGE_REASONS, matchClockMap, groupByPosition,
    positionalPlay, MAX_BAND_M,
} from '../shared/report.js?v=151';
import { PITCH_LENGTH_M } from '../shared/pitch-backdrop.js?v=151';
import { byId, setText, clockText, plural } from '../shared/ui.js?v=151';
import { activeCv, state } from './shell.js?v=151';
import { clockMap } from './review.js?v=151';

// ------------------------------------------------ where each player played
//
// The second piece of the tactical catalog, and the only block on this page
// that compares two records the coach already has instead of measuring a new
// thing. The video says where somebody averaged. The team sheet says which
// line they were picked in. Nothing here invents a template of where a
// defender "should" stand — the claim is only ever that two records disagree
// by more than the measurement could explain.
//
// The arithmetic lives in `positionalPlay` (shared/report.js) and the
// orientation in `orientedCentroid` (shared/heatmap.js), which owns the pitch
// dimensions. What is left here is layout, and the layout is the feature: one
// shared axis, eleven bars, grouped by the line each player was picked in. A
// defender whose bar ends past the whole midfield block is visible before any
// sentence about it — and a coach who was going to disagree with the sentence
// can see what it was made of.
//
// The rows are always drawn and the remarks often are not. That asymmetry is
// deliberate: where somebody played is a measurement that stands on its own,
// and "played out of position" is a verdict that needs the camera and the
// tracking to have been good enough to carry it.

/**
 * Every mapped player's average position, oriented, ready for `positionalPlay`.
 *
 * Under the preview these are grids synthesised from the sample fixture, for
 * the same reason the passing network gets invented events: this block writes
 * nothing back, and it is the only way anybody sees the feature before there is
 * footage.
 *
 * On the real path a player is often several tracked figures, so the grids are
 * merged by minutes before a centroid is taken — an eight-second fragment at
 * the edge of frame must not weigh the same as a half. `orientedCentroid`
 * returns null without an attacking end, which is what silently empties this
 * list on an uncalibrated run rather than drawing eleven bars in whichever
 * direction the camera happened to face.
 */
function shapeSource() {
    if (state.cvPreview) {
        return sampleShapeGrids().map((row) => ({
            playerId: row.playerId,
            name: row.name,
            position: row.position,
            minutesTracked: row.minutesTracked,
            ...orientedCentroid(row.grid, 'right'),
        }));
    }

    const match = state.match;
    if (!match) return [];

    const stats = cvStatsByPlayer(match.cv?.identity?.tracks, match.cvMapping, {
        clusters: match.cv?.identity?.clusters,
    });
    const attackingEnd = match.cv?.teams?.team_a?.attacking_end;
    // The same join `renderPlayerTable` makes, and for the reason spelled out
    // there: a position is a fact about the squad, not about this match. The
    // match roster is a snapshot taken when the lineup was set, and it has
    // never carried one — so reading `player.position` off it would leave this
    // block saying "give these players a position" on a squad whose positions
    // were all filled in months ago.
    const squad = new Map((state.players || []).map((p) => [p.id, p.position]));

    return (match.roster || []).map((player) => {
        const middle = orientedCentroid(
            mergeHeatmaps(stats[player.id]?.heatmaps), attackingEnd,
        );
        if (!middle) return null;
        return {
            playerId: player.id,
            name: player.playerName,
            position: squad.get(player.id) ?? null,
            minutesTracked: stats[player.id]?.minutes_tracked,
            ...middle,
        };
    }).filter(Boolean);
}

/** A distance up the pitch as a percentage of the axis. */
function shapePct(metres) {
    return `${Math.max(0, Math.min(100, (metres / PITCH_LENGTH_M) * 100))}%`;
}

/**
 * One player's bar.
 *
 * Three things on one track, in the order they matter. The fill runs from the
 * goal they were defending to where they averaged. The band over it is how far
 * either way that average could honestly sit — drawn rather than written down,
 * because two bars whose bands overlap are two bars that should not be compared
 * and no caption makes that as obvious as the picture does. The pin is the
 * average itself, kept visible so the band cannot swallow it.
 *
 * A row with no band is a row with no verdict attached, and says so under the
 * bar rather than looking like a row whose band happened to be narrow.
 */
function shapeRow(row, remark) {
    const el = document.createElement('div');
    el.className = 'shape-row';
    if (remark) el.classList.add('is-flagged');

    const name = document.createElement('span');
    name.className = 'shape-name';
    name.textContent = row.name || 'a player';
    el.append(name);

    const track = document.createElement('span');
    track.className = 'shape-track';

    const fill = document.createElement('span');
    fill.className = 'shape-fill';
    fill.style.width = shapePct(row.forwardM);
    track.append(fill);

    if (row.bandM != null) {
        const lo = Math.max(0, row.forwardM - row.bandM);
        const hi = Math.min(PITCH_LENGTH_M, row.forwardM + row.bandM);
        const band = document.createElement('span');
        band.className = 'shape-band';
        band.style.left = shapePct(lo);
        band.style.width = shapePct(hi - lo);
        track.append(band);
    }

    const pin = document.createElement('span');
    pin.className = 'shape-pin';
    pin.style.left = shapePct(row.forwardM);
    track.append(pin);
    el.append(track);

    const value = document.createElement('span');
    value.className = 'shape-value';
    value.textContent = `${Math.round(row.forwardM)} m`;
    el.append(value);

    const sub = document.createElement('span');
    sub.className = 'shape-sub';
    sub.textContent = shapeSub(row);
    el.append(sub);
    return el;
}

/** What the bar cannot say by itself. */
function shapeSub(row) {
    const held = row.spreadM > 0
        ? `ranged over ${Math.round(row.spreadM * 2)} m`
        : null;
    const tracked = row.minutesTracked > 0
        ? `${Math.round(row.minutesTracked)} min tracked`
        : 'barely tracked';
    // Named for what it is rather than for the threshold it failed. A coach
    // reading "too wide to compare" learns something about the player; one
    // reading "band 9.2 m > 8 m" learns something about this file.
    const wide = row.bandM == null || row.bandM > MAX_BAND_M
        ? 'too loose to compare'
        : `±${row.bandM.toFixed(1)} m`;
    return [held, tracked, wide].filter(Boolean).join(' · ');
}

/** The scale the bars are read against, drawn once above them. */
function shapeScale() {
    const el = document.createElement('div');
    el.className = 'shape-scale';
    for (const [label, at] of [['Own goal', 0], ['Halfway', 52.5], ['Their goal', 105]]) {
        const tick = document.createElement('span');
        tick.style.left = shapePct(at);
        tick.textContent = label;
        el.append(tick);
    }
    return el;
}

export function renderShape() {
    const block = byId('cv-shape-block');
    if (!block) return;

    const play = positionalPlay(shapeSource(), {
        coverage: activeCv()?.quality?.pitch_coverage,
    });
    block.classList.toggle('hidden', !play.rows.length);
    if (!play.rows.length) return;

    const byPlayer = new Map(play.remarks.map((r) => [r.playerId, r]));
    const host = byId('cv-shape-rows');
    host.innerHTML = '';
    host.classList.toggle('is-sample-plot', state.cvPreview);
    host.append(shapeScale());

    // Sorted by how far up the pitch they averaged, inside each line. That is
    // the same order `positionalPlay` returns, so a line whose players are out
    // of order relative to the line ahead of it reads as one break in the
    // sequence rather than as eleven unrelated bars.
    for (const group of groupByPosition(play.rows)) {
        if (group.title) {
            const head = document.createElement('h4');
            head.className = 'shape-line';
            head.textContent = group.title;
            host.append(head);
        }
        for (const row of group.players) host.append(shapeRow(row, byPlayer.get(row.playerId)));
    }

    const remarks = byId('cv-shape-remarks');
    remarks.innerHTML = '';
    for (const remark of play.remarks) {
        const li = document.createElement('li');
        li.textContent = `${remark.name} ${remark.text} — by `
            + `${Math.round(remark.gapM)} m at the closest.`;
        remarks.append(li);
    }
    remarks.classList.toggle('hidden', !play.remarks.length);

    setText('cv-shape-lede',
        'Each bar is where a player averaged over the minutes the video tracked '
        + 'them, measured up the pitch from the goal they were defending. The '
        + 'pale band is how firmly that average is pinned down — two bars '
        + 'whose bands overlap are two players who played the same height.');

    setText('cv-shape-note', [
        play.note,
        // Said every time, not only when a remark appears. The absence of a
        // remark is the common case and reads as "nothing to see" unless the
        // reader knows what was and was not being looked for.
        !play.withheld && !play.remarks.length
            ? 'Nobody played far enough out of their line for it to mean '
                + 'anything, which is the usual answer and a real one.'
            : null,
        'Sides are not checked. A position here is a line, not a wing, so a '
        + 'right-back who spent the match on the left is something to spot in '
        + 'the bars rather than something this can flag.',
    ].filter(Boolean).join(' '));
}

// ------------------------------------------------------ how the ball moved
//
// Everything else video-derived on this page is a total. This is the first
// thing that is a shape: who the ball actually travelled between, and which
// pairs never connected at all.
//
// It reads the event list rather than the summary document, which puts two
// limits on it that the note underneath has to carry. Only players a coach has
// named can appear — a line drawn to an unnamed figure looks exactly like a
// fact — and only a calibrated run has the metres to place anyone.

/**
 * Which team's network to draw, and who each figure is.
 *
 * Under the preview this hands back invented events and an invented mapping,
 * because a diagram writes nothing back and this is the only way anyone sees
 * the feature before there is footage. The review tool and the shot log stay
 * empty under the same preview for the opposite reason: they write.
 */
function passingSource() {
    if (state.cvPreview) {
        const { byTrack, nameOf } = samplePassMapping();
        return { events: samplePassEvents(), byTrack, nameOf, truncated: false };
    }

    const byTrack = playersByTrack(
        state.match?.cv?.identity?.clusters || [],
        state.match?.cvMapping || {},
        NOT_A_PLAYER,
    );
    const nameById = new Map(
        (state.match?.roster || []).map((p) => [p.id, p.playerName]),
    );
    return {
        events: state.match?.cvEvents?.events || [],
        byTrack,
        nameOf: (id) => nameById.get(id) || 'a player',
        truncated: Boolean(state.match?.cvEvents?.truncated),
    };
}

export function renderPassing() {
    const block = byId('cv-passing-block');
    if (!block) return;

    const { events, byTrack, nameOf, truncated } = passingSource();
    const cv = activeCv();
    const network = passingNetwork(events, {
        byTrack,
        team: 'team_a',
        // The same flip the shot maps do, so our side always plays left to
        // right and two matches can be compared without re-reading which end.
        attackingEnd: cv?.teams?.team_a?.attacking_end || 'right',
    });

    const drawn = renderPassMap(byId('cv-passing'), network, {
        nameOf,
        label: 'Our players, placed where they passed from, and the passes between them',
    });
    block.classList.toggle('hidden', !drawn);
    byId('cv-passing').classList.toggle('is-sample-plot', drawn && state.cvPreview);
    if (!drawn) return;

    setText('cv-passing-note', networkNote(network, { truncated }));
    setText('cv-passing-link',
        strongestLink(foldEdges(network.edges), nameOf) || '');
}

// ------------------------------------------------- how long the press lasted
//
// The first thing on this page that is about *time*. Every other video block
// answers "how much" over the whole window, and a press is the statistic where
// that hides the most: pressing is exhausting, so the question is never whether
// a side pressed but how long they managed it for.
//
// The arithmetic and every refusal in it are in shared/report.js; the pipeline
// has already decided which blocks have a denominator worth dividing by. What
// this does is lay them out so the two kinds of silence stay distinguishable —
// a block where nobody challenged at all is the loudest thing the chart can
// say, and it looks identical to missing data unless it is labelled.

function pressRow(block) {
    const row = document.createElement('div');
    row.className = 'press-row';
    if (block.unchallenged) row.classList.add('is-unchallenged');

    const when = block.startMin == null ? '' : `${block.startMin}–${block.endMin}'`;
    const counts = `${plural(block.allowed, 'pass', 'passes')} allowed, `
        + `${plural(block.actions, 'challenge')}`;

    // A bar only where there is a ratio behind it. The two blocks that get no
    // bar say why in words rather than showing an empty track the eye reads as
    // a small number.
    const bar = block.share == null
        ? `<div class="press-none">${block.unchallenged
            ? 'never challenged' : 'too few to divide'}</div>`
        : `<div class="press-bar"><span style="width:${
            Math.round(block.share * 100)}%"></span></div>`;

    row.innerHTML = `
        <div class="press-when">${when}</div>
        ${bar}
        <div class="press-value">${
            block.ppda == null ? '—' : block.ppda.toFixed(1)}</div>
        <div class="press-counts">${counts}</div>`;
    return row;
}

export function renderPressing() {
    const block = byId('cv-pressing-block');
    if (!block) return;

    const cv = activeCv();
    const trend = pressingTrend(cv?.teams?.team_a?.pressing_segments, {
        clock: clockMap(),
    });
    block.classList.toggle('hidden', !trend);
    if (!trend) return;

    const host = byId('cv-pressing');
    host.innerHTML = '';
    host.classList.toggle('is-sample-plot', state.cvPreview);
    for (const row of trend.blocks) host.appendChild(pressRow(row));

    // The lede carries the direction of the bars, because a reader who takes
    // "longer is worse" from the chart alone will take it wrong.
    const lede = ['Passes the opposition strung together for every challenge you '
        + 'made in their half, quarter-hour by quarter-hour. A longer bar is a '
        + 'softer press.'];
    const faded = pressingRead(trend);
    if (faded) lede.push(faded.detail);
    setText('cv-pressing-lede', lede.join(' '));
    setText('cv-pressing-note', pressingNote(trend));
}

// ------------------------------------------------ the football around a change

/**
 * Where the changes, the football and the clock come from.
 *
 * Under the preview all three come from the same fixture, and they have to:
 * mixing a real roster's stints with invented events would put real players'
 * names over invented possession, which is the one kind of sample data worth
 * refusing. Reading writes nothing, so previewing this is safe in the way the
 * passing network is and the review tool is not.
 */
function subsSource() {
    if (state.cvPreview) {
        const sample = sampleSubClock();
        return {
            roster: sampleSubRoster(),
            events: sampleSubEvents(),
            clock: matchClockMap(sample),
            matchEndS: sample.matchEndS,
            window: sample.window,
            period: null,
            truncated: false,
        };
    }
    const cv = state.match?.cv;
    return {
        roster: state.match?.roster || [],
        events: state.match?.cvEvents?.events || [],
        clock: clockMap(),
        matchEndS: state.match?.stats?.matchEndS ?? null,
        window: cv?.window || null,
        period: cv?.period || null,
        truncated: Boolean(state.match?.cvEvents?.truncated),
    };
}

function subRow(row) {
    const el = document.createElement('div');
    el.className = `sub-row${row.scored ? '' : ' is-bare'}`;

    const on = row.on.join(' and ');
    const off = row.off.join(' and ');
    // Three real shapes, because a change is not always a swap: a red card
    // leaves somebody coming off with nobody replacing them, and a returning
    // player comes on with nobody going off.
    const who = on && off ? `${on} on for ${off}`
        : (on ? `${on} on` : `${off} off`);

    const legs = row.scored ? `
        <div class="sub-legs">
            ${subLeg('Before', row.shareBefore, row.before)}
            ${subLeg('After', row.shareAfter, row.after)}
        </div>
        <div class="sub-meta">${
            Math.round(row.spanS / 60)} minutes either side${
            row.tentative ? ' · swing is within chance' : ''}</div>`
        : `<div class="sub-why">${CHANGE_REASONS[row.reason] || 'not measured'}</div>`;

    el.innerHTML = `
        <div class="sub-when">${clockText(row.clockS)}</div>
        <div class="sub-who"></div>
        ${legs}`;
    // Names go in as text, never as markup. Everything else on this row is a
    // number this file worked out; a player's name is a string somebody typed.
    el.querySelector('.sub-who').textContent = who;
    return el;
}

/** One half of a comparison: the share as a bar, and the shots beside it. */
function subLeg(label, share, tally) {
    const pct = share == null ? null : Math.round(share * 100);
    // "1&ndash;2 shots" reads as a range rather than as a scoreline, and this
    // is the one figure here nobody should have to look twice at.
    const shots = tally.shots + tally.shotsAgainst
        ? `shots ${tally.shots}&ndash;${tally.shotsAgainst}` : 'no shots';
    return `
        <div class="sub-leg">
            <span class="sub-leg-k">${label}</span>
            <span class="sub-bar"><span style="width:${pct ?? 0}%"></span></span>
            <span class="sub-leg-v">${pct == null ? '—' : `${pct}%`}</span>
            <span class="sub-leg-n">${shots}</span>
        </div>`;
}

export function renderSubs() {
    const block = byId('cv-subs-block');
    if (!block) return;

    const src = subsSource();
    const result = substitutionWindows(src.roster, src.events, {
        clock: src.clock,
        matchEndS: src.matchEndS,
        period: src.period,
        window: src.window,
        truncated: src.truncated,
    });
    block.classList.toggle('hidden', !result);
    if (!result) return;

    const host = byId('cv-subs');
    host.innerHTML = '';
    host.classList.toggle('is-sample-plot', state.cvPreview);
    for (const row of result.rows) host.append(subRow(row));

    // The lede says what the bars are before the first one is read. "Share of
    // the ball" would be a claim about possession; these are the on-ball events
    // the video found, which is a proxy for it and not the same measurement.
    const lede = ['Each bar is your share of the on-ball events the video found '
        + 'in that window, which is as close to possession as an event list '
        + 'gets.'];
    const read = substitutionRead(result, clockText);
    if (read) lede.push(read.detail);
    setText('cv-subs-lede', lede.join(' '));
    setText('cv-subs-note', substitutionNote(result));
}
