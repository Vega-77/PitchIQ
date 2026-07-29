// The value types the sandbox works in.
//
// Positions are normalised: x runs 0-1 across the width of the half, y runs
// from 0 at the goal line to 1 at the halfway line. Keeping them unitless means
// the canvas can be any size and the conversion to model space happens in
// exactly one place (xg-model.js).

export class Vector {
    constructor(x = 0, y = 0) {
        this.x = x;
        this.y = y;
    }

    static dist(v, w) { return Math.hypot(v.x - w.x, v.y - w.y); }

    /** A copy pulled back inside the pitch, for dragging. */
    clamped() {
        return new Vector(
            Math.max(0, Math.min(1, this.x)),
            Math.max(0, Math.min(1, this.y)),
        );
    }
}

export class Player {
    /** `team` is 'attack' or 'defence' — defence is the side being shot at. */
    constructor(position, team, isGoalkeeper = false) {
        this.position = position;
        this.team = team;
        this.isGoalkeeper = isGoalkeeper;
        this.isDragging = false;
    }

    get isDefending() { return this.team === 'defence'; }
}
