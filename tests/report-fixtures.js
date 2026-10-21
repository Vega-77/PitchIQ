// Fixtures shared by the report suites: a two-player roster for the timeline,
// one tracked figure, and a squad whose stints cover every shape a player's
// time on the pitch can take.

export const ME = 'p1';
export const MATE = 'p2';

export const roster = [
    { id: ME, playerName: 'Amir Vance' },
    { id: MATE, playerName: 'Jordan Cho' },
];

export const ev = (over) => ({ kind: 'event', side: 'us', matchClockS: 0, ...over });

// One tracked figure, as the pipeline publishes it per cluster.
export const track = (id, over = {}) => ({
    cluster_id: id,
    touches: 10,
    passes_attempted: 8,
    passes_completed: 6,
    tackles: 1,
    distance_m: 1000,
    top_speed_kmh: 20,
    sprint_count: 2,
    minutes_tracked: 10,
    touch_times_s: [100, 200],
    ...over,
});

export const STINTS = {
    // On from kick-off, off at 30:00.
    starter: [{ inS: 0, outS: 1800 }],
    // On at 30:00 and never came off.
    sub: [{ inS: 1800, outS: null }],
    // On, off, and back on — the case a single in/out pair gets wrong.
    reentry: [{ inS: 0, outS: 600 }, { inS: 1200, outS: null }],
    never: [],
};

export const squad = () => [
    { id: 'starter', playerName: 'Ada', jerseyNumber: 4, stints: STINTS.starter },
    { id: 'sub', playerName: 'Bo', jerseyNumber: 9, stints: STINTS.sub },
    { id: 'reentry', playerName: 'Cy', jerseyNumber: 2, stints: STINTS.reentry },
    { id: 'never', playerName: 'Dee', jerseyNumber: 17, stints: STINTS.never },
];
