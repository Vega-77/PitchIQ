// The match viewer on the match page: the video with the top-down map beside it, the
// stats so far and each moment's why, all in step (shared/match-viewer.js).
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.
//
// The run publishes the viewer's documents to cvViewer: a small index, the moments, and the
// map in five-minute chunks, about 8 MB a game. Opening a match reads only the index, to
// know whether there is a viewer. The moments are read when the coach starts it, and each
// chunk as the video reaches it.
//
// The video. The map's times are the times in the file the run processed, and that file
// is on the coach's computer, so playing it from there keeps the map in step to the frame.
// Nothing is uploaded: the browser reads the file where it is. The match's video link
// works too when it is the same recording, uncut. A trimmed upload would put every dot
// the trim's length out, which is why the link is the second choice.

import { readCvViewerDoc } from '../shared/db.js?v=156';
import { mount, mountLocalFile, videoKind } from '../shared/video.js?v=156';
import { mountViewer } from '../shared/match-viewer.js?v=156';
import { playersByTrack } from '../shared/passing.js?v=156';
import { NOT_A_PLAYER } from '../shared/report.js?v=156';
import { byId, setText, plural } from '../shared/ui.js?v=156';
import { state, teamLabels } from './shell.js?v=156';

let viewer = null;
let opening = 0; // bumped by every start and every redraw, so a slow read can't land late

/** Stop the viewer: the video, the map's drawing loop and its reads. */
export function leaveViewer() {
    opening += 1;
    viewer?.destroy();
    viewer = null;
}

function status(text) {
    const line = byId('cv-viewer-status');
    line.textContent = text;
    line.classList.toggle('hidden', !text);
}

/** A figure's name once the coach has matched it to a player, else whose side it is on. */
function namer() {
    const byTrack = playersByTrack(
        state.match?.cv?.identity?.clusters || [],
        state.match?.cvMapping || {},
        NOT_A_PLAYER,
    );
    const names = new Map((state.match?.roster || []).map((p) => [p.id, p.playerName]));
    return (id, team) => {
        const name = names.get(byTrack.get(id));
        if (name) return name;
        if (team === 'a') return 'one of ours';
        if (team === 'b') return 'one of theirs';
        return 'a figure';
    };
}

export function renderViewerBlock() {
    leaveViewer();
    const block = byId('cv-viewer-block');
    if (!block) return;
    const index = state.match?.cvViewer;
    block.classList.toggle('hidden', !index);
    byId('cv-viewer').innerHTML = '';
    status('');
    if (!index) return;

    const counts = index.counts || {};
    const halves = (index.halves || []).length;
    setText(
        'cv-viewer-note',
        `The video with the map beside it, in step: everyone the camera tracked, the ball, `
        + `the stats so far and why each moment went to its player. `
        + `${plural(halves, 'half', 'halves')}, ${plural(counts.moments || 0, 'moment')}. `
        + `Choose the video file the run processed, from this computer: it keeps the map in `
        + `step to the frame, and nothing is uploaded.`,
    );
    const link = state.match?.videoUrl || '';
    const linkButton = byId('btn-viewer-link');
    linkButton.classList.toggle('hidden', !videoKind(link));
    linkButton.title = 'Only if the link is the same recording as the file the run processed, uncut';
}

async function start(mountVideo) {
    leaveViewer();
    const index = state.match?.cvViewer;
    if (!index) return;
    const mine = opening;
    const teamId = state.team.id;
    const matchId = state.match.id;
    status('Loading the moments…');
    let moments;
    try {
        moments = (await readCvViewerDoc(teamId, matchId, 'moments')).moments || [];
    } catch (err) {
        if (mine === opening) status(err.message || 'Could not load the viewer.');
        return;
    }
    if (mine !== opening) return; // another match was opened, or the coach left
    status('');
    const { usName, themName } = teamLabels();
    viewer = mountViewer(byId('cv-viewer'), {
        index: { ...index, moments },
        loadChunk: (id) => readCvViewerDoc(teamId, matchId, id),
        mountVideo,
        nameOf: namer(),
        teamNames: { a: usName, b: themName },
    });
    // From kick-off: the recording usually starts well before it.
    const first = (index.halves || [])[0];
    if (first) viewer.seek(first.f0 / first.fps);
}

/** The buttons, wired once by `coach.js`. */
export function wireViewer() {
    const input = byId('input-viewer-file');
    byId('btn-viewer-file').addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
        const file = input.files?.[0];
        input.value = '';
        if (file) start((slot) => mountLocalFile(slot, file));
    });
    byId('btn-viewer-link').addEventListener('click', () => {
        const link = state.match?.videoUrl;
        if (videoKind(link || '')) start((slot) => mount(slot, link));
    });
}
