// Who is who: naming the figures the video tracked, spotting the same
// player tracked twice, and the thumbnails that make either possible.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { clearThumbs, saveCvMapping } from '../shared/db.js?v=149';
import {
    NOT_A_PLAYER, rankRosterForCluster, sameFigureCandidates, SAME_KIT_CHROMA, roughDuration,
    currentAnswers,
} from '../shared/report.js?v=149';
import { byId, setText, toast, clockText, plural, figure } from '../shared/ui.js?v=149';
import { state } from './shell.js?v=149';
import { clockMap } from './review.js?v=149';
import { renderMatchRail, renderPlayerTable, mergeCvPlayers } from './match-overview.js?v=149';
import { renderShape, renderPassing } from './match-play.js?v=149';

// ---------------------------------------------------------------- who is who

/**
 * Put names to the figures the video tracked.
 *
 * The tracker cannot tell one player from another — it loses people behind
 * opponents and when the camera pans, and gives each reappearance a new
 * identity. pitchiq/identity.py stitches those fragments into clusters, but which
 * cluster is which teenager is not something footage at this distance can
 * answer: shirt numbers are a few pixels tall.
 *
 * So this is the step that turns tracked motion into a player's stats, and
 * nothing per-player is written until it is done. Read-only here: confirming a
 * mapping writes to cvStats, which every client is denied — the coach's choices
 * go back through pitchiq/publish_to_firestore.py. What this view does is make the mapping
 * possible to work out.
 */
export function renderClusterMapping() {
    const host = byId('cv-clusters');
    if (!host) return;

    const cv = state.match?.cv;
    const clusters = cv?.identity?.clusters || [];
    const section = byId('cv-clusters-block');

    if (!clusters.length) {
        if (section) section.classList.add('hidden');
        return;
    }
    if (section) section.classList.remove('hidden');

    const mapping = state.match.cvMapping || {};
    // Biggest first — a figure tracked through most of the match is both the
    // easiest to recognise and the one worth the coach's attention. The tail
    // of two-second fragments can be left unassigned without losing much.
    const ordered = [...clusters].sort((a, b) => (b.sightings || 0) - (a.sightings || 0));

    host.innerHTML = '';
    updateMappingNote();
    renderThumbControl();

    for (const cluster of ordered) {
        host.append(clusterRow(cluster, mapping));
    }
    restoreFocus();
}

/**
 * The control to put the cursor back on after the list is rebuilt.
 *
 * Naming a figure now rebuilds every row, because one answer changes what the
 * other rows can suggest. That is fine for a mouse and quietly hostile to a
 * keyboard: the `<select>` the coach just used no longer exists, focus falls
 * back to the body, and the next Tab starts again from the top of the page —
 * fifteen times over.
 */
let pendingFocus = null;

function restoreFocus() {
    if (!pendingFocus) return;
    const { clusterId, control } = pendingFocus;
    pendingFocus = null;
    byId('cv-clusters')
        ?.querySelector(`[data-cluster="${clusterId}"] ${control}`)
        ?.focus();
}

// A crop below this many pixels tall in the original footage has no face, no
// number and no hair in it. Matching pitchiq/player_thumbnails.py's own floor for storing one
// at all; this second, higher line is about how much to believe what you see.
const FAINT_THUMB_PX = 40;

/**
 * What a tracked figure looked like — the picture, or an honest substitute.
 *
 * This is the control the whole mapping step turns on. Everything else in the
 * row (a time span, a fragment count, a kit swatch) describes the figure
 * without answering the only question being asked, which is visual.
 *
 * The kit swatch survives as a strip along the bottom rather than being
 * replaced. On the wide framing this pipeline runs on, a crop is often too small
 * to read a shirt colour off, and which side somebody was on is the one thing
 * the clustering is genuinely confident about.
 */
function clusterFace(cluster) {
    const face = document.createElement('div');
    face.className = 'cluster-thumb';

    if (cluster.thumb) {
        const img = document.createElement('img');
        // Not from a URL: the crop is a data: URI written by the pipeline, so
        // there is no request, no storage bucket and no third party involved in
        // showing a photograph of a minor.
        img.src = cluster.thumb;
        img.alt = `Tracked figure ${cluster.cluster_id + 1}`;
        img.loading = 'lazy';
        face.append(img);

        const seen = cluster.thumb_height_px;
        if (seen != null && seen < FAINT_THUMB_PX) {
            face.classList.add('faint');
            face.title = `Only ${Math.round(seen)} pixels tall in the footage`;
        }
    } else {
        // Not an empty box: with no text, "the tracker never saw this figure
        // cleanly" and "the image has not loaded yet" look exactly the same.
        face.classList.add('none');
        face.textContent = 'no clear view';
    }

    const kit = document.createElement('span');
    kit.className = 'kit';
    if (cluster.colour) kit.style.background = labToCss(cluster.colour);
    else kit.style.background = 'var(--line)';
    face.append(kit);

    return face;
}

function clusterRow(cluster, mapping) {
    const row = document.createElement('div');
    row.className = 'list-item cluster-row';
    row.dataset.cluster = String(cluster.cluster_id);
    row.innerHTML = `
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <div class="figures"></div>
        <label class="cluster-pick">
            <span class="sr-only">Who is this?</span>
            <select></select>
        </label>`;
    row.prepend(clusterFace(cluster));

    const clock = clockMap();
    const fromS = clock.toClock(cluster.first_seen_s ?? 0).clockS;
    const toS = clock.toClock(cluster.last_seen_s ?? 0).clockS;

    row.querySelector('.title').textContent = `Figure ${cluster.cluster_id + 1}`;
    row.querySelector('.sub').textContent = [
        cluster.team === 'unknown' ? 'kit unclear' : cluster.team.replace('_', ' '),
        plural(cluster.track_ids?.length || 1, 'fragment'),
        // On the match clock, not the video's, so it can be read against the
        // substitutions below it.
        `${clockText(Math.max(0, fromS))}–${clockText(Math.max(0, toS))}`,
    ].join(' · ');

    row.querySelector('.figures').append(figure(cluster.sightings ?? 0, 'frames'));

    const select = row.querySelector('select');
    const key = String(cluster.cluster_id);

    fillClusterPicker(select, cluster, clock);

    select.value = mapping[key] || '';

    select.addEventListener('change', () => {
        const next = { ...(state.match.cvMapping || {}) };
        if (select.value) next[key] = select.value;
        else delete next[key];
        state.match.cvMapping = next;

        // The suggestions are about the player just named, so a change of name
        // is a change of question. Opening the strip on the row the coach is
        // working on is also the only moment it is worth their attention.
        sameAsOpen = select.value && select.value !== NOT_A_PLAYER
            ? cluster.cluster_id
            : null;

        pendingFocus = { clusterId: cluster.cluster_id, control: 'select' };
        updateMappingNote();
        queueMappingSave();
        renderClusterMapping();
    });

    row.classList.toggle('unmatched', !select.value);

    const strip = sameAsStrip(cluster, mapping);
    if (strip) row.append(strip);

    return row;
}

/**
 * The picker's options: nobody, not a player, then the roster split by who
 * was on the pitch while `cluster` was on screen.
 */
function fillClusterPicker(select, cluster, clock) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Not matched';
    select.append(blank);

    const notPlayer = document.createElement('option');
    notPlayer.value = NOT_A_PLAYER;
    notPlayer.textContent = 'Not a player (referee, bench)';
    select.append(notPlayer);

    // Grouped by who was on the pitch while this figure was on screen, and
    // never filtered by it. The offset relating the two clocks is the fiddliest
    // number in the app, and if it is wrong then so is every overlap here —
    // hiding the players who do not fit would hide the right answer.
    //
    // Every rostered player stays pickable however many times they have been
    // used. A player who left the frame and came back is genuinely several
    // figures; pitchiq/identity.py only rejoins fragments seconds apart.
    const ranked = rankRosterForCluster(state.match.roster, cluster, {
        clock,
        matchEndS: state.match.stats?.matchEndS ?? 0,
    });

    const onPitch = document.createElement('optgroup');
    onPitch.label = 'On the pitch then';
    const others = document.createElement('optgroup');
    others.label = 'Everyone else';

    for (const { entry, overlapS, overlapShare } of ranked) {
        const option = document.createElement('option');
        option.value = entry.id;
        const name = entry.jerseyNumber != null
            ? `${entry.jerseyNumber} · ${entry.playerName}`
            : entry.playerName;
        option.textContent = overlapS > 0
            ? `${name} — on for ${Math.round(overlapShare * 100)}% of it`
            : name;
        (overlapS > 0 ? onPitch : others).append(option);
    }

    if (onPitch.children.length) select.append(onPitch);
    if (others.children.length) select.append(others);
}

// ------------------------------------------------ the same player, seen twice
//
// `pitchiq/identity.py` will not bridge an absence longer than two seconds, so a
// player who goes off, or who leaves frame while the camera pans, comes back as
// a second figure. Naming both is already the whole fix — the picker is
// many-to-one and `cvStatsByPlayer` sums across every cluster mapped to a name.
//
// What was missing is the saying-so. The list is ordered by how long each
// figure was tracked, so the two halves of one player's match sit nowhere near
// each other, and finding the second one means recognising a face in a forty-row
// list you have already scrolled past. This puts the shortlist under the row
// you are on, with the reasoning shown rather than implied.

/** The row whose suggestions are open. One at a time; a wall of them is noise. */
let sameAsOpen = null;

/** Close the open strip, for opening another match. */
export function closeSameAs() {
    sameAsOpen = null;
}

/**
 * What to say about a candidate, in the order the objections matter.
 *
 * A ruled-out row keeps its reason on it. It is there to be seen and dismissed
 * — an empty space where a figure used to be reads as a bug, and the coach who
 * wonders "why isn't Figure 12 offered?" deserves the answer on screen.
 */
function sameAsWhy(row, clock) {
    if (row.overlapS > 0 && row.ruledOut) {
        return `on screen at the same time for ${roughDuration(row.overlapS)}`;
    }
    if (row.sameTeam === false) return 'the other team’s kit';

    const bits = [];
    const at = clock.toClock(row.cluster.first_seen_s ?? 0).clockS;
    bits.push(row.gapS > 0
        ? `${roughDuration(row.gapS)} later, from ${clockText(Math.max(0, at))}`
        : `from ${clockText(Math.max(0, at))}`);
    if (row.kitS != null && row.kitS > SAME_KIT_CHROMA) bits.push('a different shirt');
    // Zero is the case worth naming: the player this would join was on the
    // bench for every second of it, so either the suggestion or the sub log is
    // wrong. A partial share is ordinary — figures straddle a substitution.
    // Set off with a dash rather than another dot, because it is an objection
    // rather than one more fact in the row.
    return bits.join(' · ')
        + (row.playedShare === 0 ? ' — but they were off the pitch' : '');
}

function sameAsCard(row, clusterId, player, playerName) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'same-as-card';
    card.dataset.same = String(row.cluster.cluster_id);
    const joined = row.takenBy === null
        && (state.match.cvMapping || {})[String(row.cluster.cluster_id)] === player;
    card.classList.toggle('joined', joined);
    card.classList.toggle('ruled-out', !!row.ruledOut);
    // Not merely dimmed. Two figures on screen together are two people, and
    // that is the one thing here that is a certainty rather than evidence — so
    // it is the one thing the control refuses to let a coach do by mistake.
    card.disabled = !!row.ruledOut;

    card.append(clusterFace(row.cluster));

    const text = document.createElement('span');
    text.className = 'same-as-text';
    const title = document.createElement('strong');
    title.textContent = `Figure ${row.cluster.cluster_id + 1}`;
    const why = document.createElement('small');
    why.textContent = row.takenBy
        ? `already ${nameOf(row.takenBy)}`
        : sameAsWhy(row, clockMap());
    text.append(title, why);
    card.append(text);

    const verb = document.createElement('span');
    verb.className = 'same-as-verb';
    verb.textContent = joined ? '✓ joined' : 'Same player';
    card.append(verb);

    card.addEventListener('click', () => {
        const next = { ...(state.match.cvMapping || {}) };
        const at = String(row.cluster.cluster_id);
        if (joined) delete next[at];
        else next[at] = player;
        state.match.cvMapping = next;
        toast(joined
            ? `Figure ${row.cluster.cluster_id + 1} is no longer ${playerName}.`
            : `Figure ${row.cluster.cluster_id + 1} counts towards ${playerName} too.`);
        // Back on the card just pressed, not on the row it belongs to: joining
        // two fragments is often three cards in a row, and the answer to "was
        // that right?" is the card changing under the cursor.
        pendingFocus = { clusterId, control: `[data-same="${row.cluster.cluster_id}"]` };
        updateMappingNote();
        queueMappingSave();
        renderClusterMapping();
    });

    return card;
}

function nameOf(playerId) {
    if (playerId === NOT_A_PLAYER) return 'ruled out';
    const entry = (state.match.roster || []).find((r) => r.id === playerId);
    return entry?.playerName || 'someone else';
}

/**
 * The strip of other figures that could be this same player.
 *
 * Ruled-out rows are kept and shown last rather than dropped. Only one thing
 * here is a certainty — two figures on screen together are two people — and
 * everything else is evidence; hiding a poor fit on evidence would hide the
 * right answer on the day the kit colour or the video offset is wrong, which is
 * the same rule the picker above already follows.
 */
function sameAsStrip(cluster, mapping) {
    const player = mapping[String(cluster.cluster_id)];
    if (!player || player === NOT_A_PLAYER) return null;

    const clusters = state.match?.cv?.identity?.clusters || [];
    const rows = sameFigureCandidates(clusters, cluster, {
        mapping,
        player,
        roster: state.match.roster || [],
        clock: clockMap(),
        matchEndS: state.match.stats?.matchEndS ?? 0,
    });
    if (!rows.length) return null;

    const playerName = nameOf(player);
    const open = sameAsOpen === cluster.cluster_id;
    const offered = rows.filter((r) => !r.ruledOut && !r.takenBy).length;
    const joined = rows.filter(
        (r) => !r.takenBy && mapping[String(r.cluster.cluster_id)] === player,
    ).length;

    const wrap = document.createElement('div');
    wrap.className = 'same-as';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'same-as-toggle';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = joined
        ? `${playerName} is ${plural(joined + 1, 'figure')} · change`
        : `${offered ? plural(offered, 'other figure') : 'No other figure'} `
          + `could also be ${playerName}`;
    toggle.addEventListener('click', () => {
        sameAsOpen = open ? null : cluster.cluster_id;
        renderClusterMapping();
    });
    wrap.append(toggle);

    if (!open) return wrap;

    const list = document.createElement('div');
    list.className = 'same-as-list';
    for (const row of rows) {
        list.append(sameAsCard(row, cluster.cluster_id, player, playerName));
    }
    wrap.append(list);

    const note = document.createElement('p');
    note.className = 'same-as-note';
    note.textContent = 'A player who left the frame and came back is genuinely '
        + 'two figures, and both count towards them. The ones greyed out were on '
        + 'screen at the same time, so they are somebody else.';
    wrap.append(note);

    return wrap;
}

function updateMappingNote() {
    const clusters = state.match?.cv?.identity?.clusters || [];
    const answers = currentAnswers(
        clusters.map((cluster) => cluster.cluster_id), state.match?.cvMapping,
    );
    // Ruled out is an answer, but it is not a player, and counting it as one
    // would tell a coach their stats are further along than they are.
    const matched = answers.filter((id) => id && id !== NOT_A_PLAYER).length;
    const ruledOut = answers.filter((id) => id === NOT_A_PLAYER).length;
    const tail = ruledOut ? ` ${plural(ruledOut, 'figure')} ruled out.` : '';

    renderMatchRail();

    setText('cv-clusters-note', matched
        ? `${matched} of ${clusters.length} tracked figures matched.${tail} `
          + 'Publish the reports again to send these numbers to the players.'
        : `The video tracked ${clusters.length} figures and cannot tell who is `
          + 'who — shirt numbers are a few pixels tall at this distance. Match '
          + 'the big ones to a player and their stats appear. A figure left '
          + 'unmatched is simply not counted.');
}

// ------------------------------------------------- the pictures, and dropping them
//
// `pitchiq/player_thumbnails.py` cuts a crop of each tracked figure out of the footage so a
// coach can look at a row and say which of their players it is. They are
// photographs of children, and they exist for that one job.
//
// Once the mapping is done the job is finished, and what is left is pictures of
// minors in a database with no remaining purpose. Nobody could remove them:
// they rode inside `cvStats/identity` next to every per-track statistic, and
// `cvStats` is `allow write: if false` to every client. Now they have their own
// document, a coach may delete that one document, and deleting it costs no
// number at all.
//
// Not done automatically on the last name being picked. A coach may reasonably
// want to check their work the next morning, and a control that quietly
// destroyed evidence the moment it judged you finished would be worse than one
// that waits to be pressed.

function renderThumbControl() {
    const host = byId('cv-thumb-control');
    if (!host) return;

    const clusters = state.match?.cv?.identity?.clusters || [];
    const withPictures = clusters.filter((c) => c.thumb).length;

    host.innerHTML = '';
    if (!withPictures) {
        // Deliberately says the pictures are gone rather than saying nothing.
        // "There are no photographs of your players stored" is the reassuring
        // half of this feature, and it only reassures if it is stated.
        host.textContent = clusters.length
            ? 'No pictures of the tracked figures are stored for this match.'
            : '';
        return;
    }

    const line = document.createElement('p');
    line.className = 'thumb-note';
    line.textContent = `${plural(withPictures, 'picture')} of your players, cut `
        + 'out of the footage so you could match the figures to names. Once the '
        + 'matching is done they are not needed.';
    host.append(line);

    const button = document.createElement('button');
    button.className = 'btn small';
    button.textContent = 'Delete the pictures';
    button.title = 'Deletes only the pictures. Every figure, statistic and name '
        + 'you have matched stays exactly as it is.';
    button.addEventListener('click', () => clearMatchThumbs(button));
    host.append(button);
}

async function clearMatchThumbs(button) {
    if (!confirm(
        'Delete the pictures cut from the footage?\n\n'
        + 'The figures, their statistics and the names you have matched are all '
        + 'kept — only the photographs go. Running the pipeline on this match '
        + 'again would produce them a second time.'
    )) return;

    button.disabled = true;
    try {
        await clearThumbs(state.team.id, state.match.id);
        // Dropped from what is already on screen rather than re-fetched: the
        // rows redraw as "no clear view", which is the same thing they show for
        // a figure the tracker never saw cleanly, and is what they will show on
        // every future load.
        for (const cluster of state.match.cv?.identity?.clusters || []) {
            delete cluster.thumb;
            delete cluster.thumb_height_px;
        }
        renderClusterMapping();
        toast('The pictures have been deleted. Everything else is untouched.');
    } catch (err) {
        toast(err.message || 'Could not delete the pictures.', true);
        button.disabled = false;
    }
}

/**
 * Save shortly after the coach stops changing things.
 *
 * Mapping fifteen figures is fifteen changes in quick succession, and writing
 * on each one would be fifteen round trips racing each other to be last.
 */
let mappingSaveTimer = null;
function queueMappingSave() {
    // The screen changes now; only the write waits. These three were redrawn
    // inside `saveMappingNow` until this line existed, which meant they lagged
    // the coach by 600 ms and did not redraw at all when the write failed —
    // leaving the passing map drawing lines to a player who had just been
    // unmapped, and the shape bars standing under a name nobody had claimed.
    redrawMappedViews();
    clearTimeout(mappingSaveTimer);
    mappingSaveTimer = setTimeout(saveMappingNow, 600);
}

/**
 * Everything drawn from the coach's cluster mapping, and nothing else.
 *
 * A smaller list than `redrawShotViews`, on purpose: these blocks do not read
 * the review, so putting them on that redraw would say they went stale in
 * places they cannot.
 */
function redrawMappedViews() {
    if (state.match?.stats?.players) {
        mergeCvPlayers(
            state.match.stats.players, state.match, state.match.stats.matchEndS,
        );
        renderPlayerTable(state.match.stats.players);
    }
    renderPassing();
    renderShape();
}

async function saveMappingNow() {
    const badge = byId('cv-save-state');
    try {
        if (badge) badge.textContent = 'Saving…';
        await saveCvMapping(
            state.user, state.team.id, state.match.id, state.match.cvMapping,
        );
        if (badge) badge.textContent = 'Saved';
    } catch (err) {
        if (badge) badge.textContent = '';
        toast(err.message || 'Could not save that match-up.', true);
    }
}

/**
 * A Lab colour from the pipeline to something a browser can paint.
 *
 * Approximate on purpose: this is a swatch to help a coach recognise a kit at a
 * glance, not a colour-managed reproduction. Lightness is fixed at mid-grey
 * because the clustering drops it — two sightings of one shirt in sun and shade
 * differ in lightness far more than two different kits differ in chroma.
 */
function labToCss(lab) {
    const [, a = 0, b = 0] = lab;
    const y = 0.6;
    const r = Math.max(0, Math.min(255, Math.round(255 * (y + a / 128 * 0.5))));
    const g = Math.max(0, Math.min(255, Math.round(255 * (y - a / 256 - b / 256))));
    const bl = Math.max(0, Math.min(255, Math.round(255 * (y - b / 128 * 0.5))));
    return `rgb(${r}, ${g}, ${bl})`;
}
