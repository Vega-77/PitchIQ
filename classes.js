class Vector {
    x = 0;
    y = 0;

    constructor(x, y) {
        this.x = x;
        this.y = y;
    }

    // Static methods (return new Vector)
    static add(v, w)  { return new Vector(v.x + w.x, v.y + w.y); }
    static sub(v, w)  { return new Vector(v.x - w.x, v.y - w.y); }
    static mul(v, s)  { return new Vector(v.x * s,   v.y * s);   }
    static div(v, s)  { return new Vector(v.x / s,   v.y / s);   }
    static dist(v, w) { return Math.sqrt((v.x - w.x) ** 2 + (v.y - w.y) ** 2); }

    // Instance methods (mutate in place)
    add(v) { this.x += v.x; this.y += v.y; return this; }
    sub(v) { this.x -= v.x; this.y -= v.y; return this; }
    mul(s) { this.x *= s;   this.y *= s;   return this; }
}


class Player {
    position   = new Vector(0, 0);
    goalkeeper = false;
    red        = false;
    isDragging = false;

    constructor(position, isGoalkeeper, isRed) {
        this.position   = position;
        this.goalkeeper = isGoalkeeper;
        this.red        = isRed;
    }
}
