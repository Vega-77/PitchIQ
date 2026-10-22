// The Roster tab: the squad list, adding a player, and what removing one
// is going to do.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import {
    listPlayers, addPlayer, invitePlayer, setPlayerActive, setPlayerPosition, playerFootprint,
    erasePlayer,
} from '../shared/db.js?v=154';
import { erasureNote, POSITIONS, positionOf } from '../shared/report.js?v=154';
import { byId, toast } from '../shared/ui.js?v=154';
import { state } from './shell.js?v=154';
import { openPlayer } from './player-view.js?v=154';

// ---------------------------------------------------------------- roster

export function renderRoster() {
    const list = byId('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<div class="empty">No players yet. Add them with the Add a player form.</div>';
        return;
    }

    // Anyone who has left the team goes to the bottom rather than out of the
    // list. They keep their reports, and a coach who took the wrong row off
    // needs somewhere to find them again.
    const ordered = [...state.players].sort(
        (a, b) => (a.active === false ? 1 : 0) - (b.active === false ? 1 : 0),
    );
    for (const player of ordered) {
        list.append(rosterRow(player));
    }
}

/**
 * The four lines of a team, plus the honest blank.
 *
 * The blank reads "Position" rather than "None" or "—": an empty select on a
 * roster is a question nobody has answered yet, and both of those words look
 * like answers.
 */
function fillPositionSelect(select, chosen) {
    select.innerHTML = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'Position';
    select.append(none);

    for (const pos of POSITIONS) {
        const option = document.createElement('option');
        option.value = pos.id;
        option.textContent = pos.label;
        select.append(option);
    }
    select.value = positionOf(chosen) || '';
}

function rosterRow(player) {
    const row = document.createElement('div');
    row.className = 'list-item roster-row';
    row.innerHTML = `
        <span class="jersey"></span>
        <button class="grow open-player">
            <div class="title"></div>
            <div class="sub"></div>
        </button>
        <label class="pos-pick">
            <span class="sr-only"></span>
            <select></select>
        </label>
        <span class="pill"></span>
        <button class="btn small" data-act="invite">Invite</button>
        <button class="btn small danger" data-act="remove">Remove</button>
        <span class="open-hint">&rsaquo;</span>`;

    row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
    row.querySelector('.title').textContent = player.name;
    row.querySelector('.sub').textContent = player.emailLower
        || 'No email yet — they cannot see their report without one';

    const gone = player.active === false;
    row.classList.toggle('has-left', gone);

    const linked = Boolean(player.linkedUid);
    const pill = row.querySelector('.pill');
    // "Left the team" outranks whether they can see reports, because it is the
    // reason the row is greyed and the other pill would read as the cause.
    pill.textContent = gone
        ? 'Left the team'
        : (linked ? 'Can see reports' : 'Not joined yet');
    pill.classList.toggle('done', linked && !gone);

    const remove = row.querySelector('[data-act="remove"]');
    if (gone) {
        remove.textContent = 'Back on the roster';
        remove.classList.remove('danger');
    }

    // Saved on change, with no confirm and no undo, because there is nothing to
    // undo: a position is a heading over figures rather than an input to one,
    // so getting it wrong costs a wrong word and never a wrong number. That is
    // also why it can be filled in months later, or never.
    const pick = row.querySelector('.pos-pick select');
    fillPositionSelect(pick, player.position);
    row.querySelector('.pos-pick .sr-only').textContent = `Position for ${player.name}`;
    pick.addEventListener('change', async () => {
        const chosen = pick.value || null;
        try {
            await setPlayerPosition(state.team.id, player.id, chosen);
            player.position = chosen;
        } catch (err) {
            fillPositionSelect(pick, player.position);
            toast(err.message || 'Could not save the position.', true);
        }
    });

    row.querySelector('.open-player').addEventListener('click', () => openPlayer(player));

    row.querySelector('[data-act="invite"]').addEventListener('click', async (e) => {
        e.target.disabled = true;
        try {
            await invitePlayer(state.user, state.team, player);
            toast(`Invitation sent to ${player.emailLower}`);
        } catch (err) {
            toast(err.message || 'Could not send the invitation.', true);
        } finally {
            e.target.disabled = false;
        }
    });

    remove.addEventListener('click', async () => {
        if (gone) {
            try {
                await setPlayerActive(state.team.id, player.id, true);
                player.active = true;
                renderRoster();
                toast(`${player.name} is back on the roster.`);
            } catch (err) {
                toast(err.message || 'Could not put them back.', true);
            }
            return;
        }
        openRemoveChoice(row, player);
    });

    return row;
}

// ------------------------------------------------ what "remove" is going to do
//
// It used to be one `confirm()` and one deleted document — the squad entry —
// while the student's name, shirt number, minutes, distance and published
// report stayed in every match they had played, and their email address stayed
// on as an invite key. The coach was told "Player removed".
//
// Two intentions wear that word. *They left the team* has to keep the match
// reports, because a report is a record of a match that happened and deleting
// it would change the team's own results. *A guardian asked* has to keep
// nothing. One button could not do both, and the one that existed did neither
// — so the choice is put to the coach in the words of the consequence rather
// than the words of the action.

/** Only one open at a time; two irreversible choices on screen is one too many. */
let removeChoiceFor = null;

function openRemoveChoice(row, player) {
    if (removeChoiceFor === player.id) return closeRemoveChoice();
    closeRemoveChoice();
    removeChoiceFor = player.id;

    const panel = document.createElement('div');
    panel.className = 'remove-choice';
    panel.innerHTML = `
        <p class="rc-lead"></p>
        <div class="rc-options">
            <button class="btn small" data-act="left">They left the team</button>
            <button class="btn small danger" data-act="erase">Erase everything</button>
            <button class="btn small ghost" data-act="cancel">Cancel</button>
        </div>
        <div class="rc-detail"></div>`;
    row.append(panel);

    panel.querySelector('.rc-lead').textContent =
        `${player.name} — what should happen to what we hold about them?`;

    panel.querySelector('[data-act="cancel"]').addEventListener('click', closeRemoveChoice);

    panel.querySelector('[data-act="left"]').addEventListener('click', async () => {
        try {
            await setPlayerActive(state.team.id, player.id, false);
            player.active = false;
            closeRemoveChoice();
            renderRoster();
            toast(`${player.name} has left the team. Their reports are kept.`);
        } catch (err) {
            toast(err.message || 'Could not update the roster.', true);
        }
    });

    const erase = panel.querySelector('[data-act="erase"]');
    const detail = panel.querySelector('.rc-detail');

    erase.addEventListener('click', async () => {
        // Read now rather than guessed, because the coach is about to be shown
        // a number and asked to act on it. Reading it only on the press keeps a
        // season of gets off every roster render.
        erase.disabled = true;
        erase.textContent = 'Checking\u2026';
        let footprint;
        try {
            footprint = await playerFootprint(state.team.id, player.id);
        } catch (err) {
            toast(err.message || 'Could not check what is stored.', true);
            erase.disabled = false;
            erase.textContent = 'Erase everything';
            return;
        }
        if (removeChoiceFor !== player.id) return;   // cancelled while reading
        erase.classList.add('hidden');
        renderErasePlan(detail, player, footprint);
    });
}

function renderErasePlan(host, player, footprint) {
    const { lines } = erasureNote(footprint);

    host.innerHTML = '';
    const list = document.createElement('ul');
    list.className = 'rc-lines';
    for (const line of lines) {
        const li = document.createElement('li');
        li.textContent = line;
        list.append(li);
    }
    host.append(list);

    const confirmRow = document.createElement('div');
    confirmRow.className = 'rc-confirm';
    confirmRow.innerHTML = `
        <label class="rc-type">
            <span>Type <strong>ERASE</strong> to confirm. This cannot be undone.</span>
            <input type="text" autocomplete="off" spellcheck="false">
        </label>
        <button class="btn small danger" disabled></button>`;
    host.append(confirmRow);

    const input = confirmRow.querySelector('input');
    const go = confirmRow.querySelector('button');
    go.textContent = `Erase ${player.name}`;

    // Typed, not clicked. Every other destructive control in this app is one
    // press and every other one can be undone by entering again what was lost.
    // This one cannot, and the difference is worth four seconds of a coach's
    // time.
    input.addEventListener('input', () => {
        go.disabled = input.value.trim().toUpperCase() !== 'ERASE';
    });

    go.addEventListener('click', async () => {
        go.disabled = true;
        go.textContent = 'Erasing\u2026';
        try {
            await erasePlayer(state.user, state.team.id, player.id, footprint);
            state.players = state.players.filter((p) => p.id !== player.id);
            closeRemoveChoice();
            renderRoster();
            toast(`Everything about ${player.name} has been deleted.`);
        } catch (err) {
            // Deliberately not "nothing happened". The erase runs a batch per
            // match, so a failure partway leaves whole matches done and whole
            // matches not, and a coach who is told it failed and assumes the
            // data is intact has been told the opposite of the truth.
            toast(err.message || 'That did not finish. Some matches may already '
                + 'be cleared — open this again to see what is left.', true);
            go.disabled = false;
            go.textContent = `Erase ${player.name}`;
        }
    });
}

function closeRemoveChoice() {
    removeChoiceFor = null;
    document.querySelectorAll('.remove-choice').forEach((el) => el.remove());
}

export async function doAddPlayer() {
    const name = byId('input-player-name').value.trim();
    const email = byId('input-player-email').value.trim().toLowerCase();
    const numberRaw = byId('input-player-number').value.trim();

    if (!name) return toast('Enter a name', true);
    if (!email) return toast('Enter the school email — it links them to their report', true);

    try {
        await addPlayer(state.team.id, {
            name,
            jerseyNumber: numberRaw ? Number(numberRaw) : null,
            email,
            position: byId('input-player-position').value || null,
        });
        for (const id of ['input-player-name', 'input-player-number', 'input-player-email']) {
            byId(id).value = '';
        }
        // The position is *not* cleared. A coach adding a squad works through it
        // in lines — four defenders, then the midfield — and re-picking the same
        // answer eleven times is the kind of small friction that ends with the
        // field left empty for the whole roster.
        state.players = await listPlayers(state.team.id);
        renderRoster();
        toast(`${name} added`);
    } catch (err) {
        toast(err.message || 'Could not add the player.', true);
    }
}
