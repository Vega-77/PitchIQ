// The Staff tab: the squad's kit colours, and the coaches who share the
// squad.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { updateTeamKit, getTeam, listStaff, inviteCoach, removeCoach } from '../shared/db.js?v=154';
import { HOUSE_KIT, applyKit } from '../shared/kit.js?v=154';
import { byId, toast, kitPicker } from '../shared/ui.js?v=154';
import { state } from './shell.js?v=154';
import { renderHero } from './season.js?v=154';

// ---------------------------------------------------------------- staff

/**
 * The squad's colours, on the staff tab. The page tries a pair on as it is
 * picked; leaving the tab without saving puts the saved pair back.
 */
function renderKit() {
    const saved = state.team.kit ?? HOUSE_KIT;
    const button = byId('btn-save-kit');
    const slot = byId('kit-team-slot');
    slot.innerHTML = '';
    teamKit = kitPicker({
        kit: saved,
        onChange: (kit) => {
            applyKit(kit, { remember: false });
            button.disabled = kit.primary === saved.primary && kit.secondary === saved.secondary;
        },
    });
    slot.append(teamKit);
    button.disabled = true;
}

/** The picker on the staff tab. */
let teamKit = null;

function openKit() {
    byId('kit-panel').classList.remove('hidden');
    byId('btn-kit-open').setAttribute('aria-expanded', 'true');
    byId('kit-panel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/** Put the panel away. Colours tried on and not saved come off with it. */
export function closeKit() {
    if (state.team) applyKit(state.team.kit ?? HOUSE_KIT);
    renderKit();
    byId('kit-panel').classList.add('hidden');
    byId('btn-kit-open').setAttribute('aria-expanded', 'false');
}

export function toggleKit() {
    if (byId('kit-panel').classList.contains('hidden')) openKit();
    else closeKit();
}

export async function doSaveKit() {
    const kit = teamKit?.value();
    if (!kit) return;
    const button = byId('btn-save-kit');
    button.disabled = true;
    try {
        await updateTeamKit(state.team.id, kit);
        state.team = { ...state.team, kit };
        state.teams = state.teams.map((t) => (t.id === state.team.id ? state.team : t));
        applyKit(kit);
        closeKit();
        toast('Colours saved');
    } catch (err) {
        button.disabled = false;
        toast(err.message || 'Could not save the colours.', true);
    }
}

export function renderStaff() {
    const list = byId('staff-list');
    list.innerHTML = '';

    // Only the coach who created the squad can remove anyone — the rules say so
    // too, so hiding the button just keeps the UI from offering a dead action.
    const isOwner = state.team.createdBy === state.user.uid;

    for (const member of state.staff) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <span class="staff-initial"></span>
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="pill"></span>
            <button class="btn small danger" data-act="remove">Remove</button>`;

        const isMe = member.uid === state.user.uid;
        // Not every account carries a name: a Google profile can leave it
        // blank, and the row read "undefined (you)". The address is the next
        // best thing to call someone by, and then it is not repeated beneath.
        const label = member.displayName?.trim()
            || (isMe ? state.user.displayName?.trim() : '')
            || member.emailLower
            || 'Unnamed coach';
        const name = isMe ? `${label} (you)` : label;

        row.querySelector('.staff-initial').textContent =
            label.trim()[0]?.toUpperCase() ?? '?';
        row.querySelector('.title').textContent = name;
        row.querySelector('.sub').textContent = member.unknown
            ? "Hasn't opened the dashboard yet"
            : (label === member.emailLower ? '' : member.emailLower);

        const pill = row.querySelector('.pill');
        const isTeamOwner = member.uid === state.team.createdBy;
        pill.textContent = isTeamOwner ? 'Head coach' : 'Coach';
        if (isTeamOwner) pill.classList.add('done');

        const remove = row.querySelector('[data-act="remove"]');
        if (!isOwner || isMe || isTeamOwner) {
            remove.remove();
        } else {
            remove.addEventListener('click', () => doRemoveCoach(member));
        }

        list.append(row);
    }
}

export async function doInviteCoach() {
    const input = byId('input-coach-email');
    const email = input.value.trim().toLowerCase();
    if (!email) return toast('Enter their email address', true);

    const button = byId('btn-invite-coach');
    button.disabled = true;
    try {
        await inviteCoach(state.user, state.team, email);
        input.value = '';
        toast(`Invitation sent to ${email}`);
    } catch (err) {
        toast(
            err?.code === 'permission-denied'
                ? "That address doesn't look like a valid email."
                : err.message || 'Could not send the invitation.',
            true,
        );
    } finally {
        button.disabled = false;
    }
}

async function doRemoveCoach(member) {
    const name = member.displayName || member.emailLower || 'this coach';
    if (!confirm(
        `Remove ${name} from ${state.team.name}?\n\n`
        + 'They lose access to the roster, matches and reports for this squad.'
    )) return;

    try {
        await removeCoach(state.team, member.uid);
        state.team = await getTeam(state.team.id);
        state.teams = state.teams.map((t) => (t.id === state.team.id ? state.team : t));
        state.staff = await listStaff(state.team);
        renderStaff();
        renderHero();
        toast(`${name} removed`);
    } catch (err) {
        toast(err.message || 'Could not remove that coach.', true);
    }
}
