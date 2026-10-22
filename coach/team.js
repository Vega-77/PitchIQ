// The team itself: creating one, loading one, and switching between the
// squads a coach runs.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { rememberTeam, saveStaffProfile } from '../shared/auth.js?v=154';
import { createTeam, getTeam, listPlayers, listMatches, listStaff } from '../shared/db.js?v=154';
import { HOUSE_KIT, applyKit } from '../shared/kit.js?v=154';
import { byId, setText, toast, kitPicker } from '../shared/ui.js?v=154';
import { show, state } from './shell.js?v=154';
import { renderHero } from './season.js?v=154';
import { closeKit, renderStaff } from './staff.js?v=154';
import { renderMatches } from './matches.js?v=154';
import { renderRoster } from './roster.js?v=154';

// ---------------------------------------------------------------- team setup

/** Show the create form, either on first run or for an extra squad. */
export function showCreateTeam() {
    const hasTeams = state.teams.length > 0;
    setText('noteam-title', hasTeams ? 'Create another squad' : 'Create your team');
    setText('noteam-lede', hasTeams
        ? 'A separate squad keeps its own roster, matches and player reports. '
          + 'You stay the coach of both and can switch between them at any time.'
        : 'Set this up once, then add your players.');

    byId('btn-cancel-team').classList.toggle('hidden', !hasTeams);
    byId('input-team-name').value = '';

    // The page wears the colours while they are being picked, which is the
    // quickest way to see whether a pair works.
    const slot = byId('kit-new-slot');
    slot.innerHTML = '';
    newKit = kitPicker({
        kit: HOUSE_KIT,
        onChange: (kit) => applyKit(kit, { remember: false }),
    });
    slot.append(newKit);
    applyKit(HOUSE_KIT, { remember: false });
    show('view-noteam');
}

/** The picker on the create form, while it is open. */
let newKit = null;

/** Back from the create form without creating: the current squad's colours. */
export function cancelCreateTeam() {
    applyKit(state.team?.kit ?? HOUSE_KIT);
    show('view-main');
}

export async function doCreateTeam() {
    const name = byId('input-team-name').value.trim();
    if (!name) return toast('Give your team a name', true);

    const button = byId('btn-create-team');
    button.disabled = true;
    try {
        const teamId = await createTeam(state.user, name, newKit?.value() ?? null);
        await rememberTeam(state.user, teamId);
        await saveStaffProfile(state.user, teamId).catch(() => {});

        state.team = await getTeam(teamId);
        state.teams = [...state.teams, state.team]
            .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        history.replaceState(null, '', `?team=${encodeURIComponent(teamId)}`);

        await loadTeamData();
        show('view-main');
        toast(`${name} created`);
    } catch (err) {
        toast(
            err?.code === 'permission-denied'
                ? 'This Google account is not approved to create a team yet. Email alexvega20137@gmail.com from this address to be added, then try again.'
                : err.message || 'Could not create the team.',
            true,
        );
    } finally {
        button.disabled = false;
    }
}

export async function loadTeamData() {
    renderTeamSwitcher();

    [state.players, state.matches, state.staff] = await Promise.all([
        listPlayers(state.team.id),
        listMatches(state.team.id),
        listStaff(state.team),
    ]);

    renderHero();
    renderRoster();
    renderMatches();
    renderStaff();
    closeKit();

    // Teams created before the staff directory existed have no entry for their
    // own coach, which would show them to a new assistant as an unnamed uid.
    // Writing it on load backfills that without a migration.
    if (!state.staff.some((s) => s.uid === state.user.uid && !s.unknown)) {
        saveStaffProfile(state.user, state.team.id)
            .then(async () => { state.staff = await listStaff(state.team); renderStaff(); })
            .catch(() => { /* display only — never worth interrupting the page */ });
    }
}

/**
 * The squad picker. Hidden entirely for a coach with one team, so the common
 * case carries no extra chrome.
 */
function renderTeamSwitcher() {
    const wrap = byId('team-switch-wrap');
    const select = byId('team-switch');
    const single = state.teams.length < 2;

    wrap.classList.toggle('hidden', single);
    byId('team-name').classList.toggle('hidden', !single);

    if (single) {
        setText('team-name', state.team.name);
        return;
    }

    select.innerHTML = '';
    for (const team of state.teams) {
        const option = document.createElement('option');
        option.value = team.id;
        option.textContent = team.name;
        option.selected = team.id === state.team.id;
        select.appendChild(option);
    }
}

export async function switchTeam(teamId) {
    const team = state.teams.find((t) => t.id === teamId);
    if (!team || team.id === state.team.id) return;

    state.team = team;
    // Keep the URL honest so a reload, or a bookmark, lands on the same squad.
    history.replaceState(null, '', `?team=${encodeURIComponent(team.id)}`);

    byId('loading').classList.remove('hidden');
    try {
        await loadTeamData();
        show('view-main');
        toast(`Switched to ${team.name}`);
    } catch (err) {
        toast(err.message || 'Could not load that squad.', true);
        show('view-main');
    }
}
