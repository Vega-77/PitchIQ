from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas


def get_match(db: Session, match_id: int) -> models.Match | None:
    return db.get(models.Match, match_id)


def roster_for_match(
    db: Session, match_id: int, team_id: int | None = None
) -> list[models.MatchRosterEntry]:
    stmt = select(models.MatchRosterEntry).where(
        models.MatchRosterEntry.match_id == match_id
    )
    if team_id is not None:
        stmt = stmt.where(models.MatchRosterEntry.team_id == team_id)
    return list(db.scalars(stmt))


def set_lineup(
    db: Session, match_id: int, payload: schemas.LineupCreate
) -> list[models.MatchRosterEntry]:
    """Replace a team's roster for this match. Starters begin on the field."""
    existing = roster_for_match(db, match_id, payload.team_id)
    for entry in existing:
        db.delete(entry)
    db.flush()

    entries = []
    for item in payload.entries:
        entry = models.MatchRosterEntry(
            match_id=match_id,
            player_id=item.player_id,
            team_id=payload.team_id,
            is_starter=item.is_starter,
            is_active=item.is_starter,
            sub_in_ts=0.0 if item.is_starter else None,
        )
        db.add(entry)
        entries.append(entry)

    db.commit()
    for entry in entries:
        db.refresh(entry)
    return entries


def apply_substitution(
    db: Session, match_id: int, payload: schemas.SubstitutionCreate
) -> tuple[models.Substitution | None, str]:
    roster = {
        entry.player_id: entry
        for entry in roster_for_match(db, match_id, payload.team_id)
    }

    out_entry = roster.get(payload.player_out_id)
    in_entry = roster.get(payload.player_in_id)

    if out_entry is None or in_entry is None:
        return None, "Both players must be on this match's roster for that team."
    if not out_entry.is_active:
        return None, "The player coming off is not currently on the field."
    if in_entry.is_active:
        return None, "The player coming on is already on the field."

    out_entry.is_active = False
    out_entry.sub_out_ts = payload.match_clock_s
    in_entry.is_active = True
    in_entry.sub_in_ts = payload.match_clock_s

    sub = models.Substitution(
        match_id=match_id,
        team_id=payload.team_id,
        player_out_id=payload.player_out_id,
        player_in_id=payload.player_in_id,
        match_clock_s=payload.match_clock_s,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub, "ok"


def create_event(
    db: Session, match_id: int, payload: schemas.EventCreate
) -> models.Event:
    event = models.Event(
        match_id=match_id,
        team_id=payload.team_id,
        event_type=payload.event_type,
        match_clock_s=payload.match_clock_s,
        source=payload.source,
        detail=payload.detail,
    )
    db.add(event)

    if payload.event_type in schemas.PERIOD_EVENTS:
        match = db.get(models.Match, match_id)
        if match is not None:
            match.status = schemas.PERIOD_TO_STATUS[payload.event_type]

    db.commit()
    db.refresh(event)
    return event


def undo_last(db: Session, match_id: int) -> schemas.UndoResult:
    """Remove whichever of the last event or substitution was recorded most recently."""
    last_event = db.scalars(
        select(models.Event)
        .where(models.Event.match_id == match_id)
        .order_by(models.Event.created_at.desc(), models.Event.id.desc())
        .limit(1)
    ).first()

    last_sub = db.scalars(
        select(models.Substitution)
        .where(models.Substitution.match_id == match_id)
        .order_by(models.Substitution.created_at.desc(), models.Substitution.id.desc())
        .limit(1)
    ).first()

    if last_event is None and last_sub is None:
        return schemas.UndoResult(undone=False, description="Nothing to undo.")

    sub_is_newer = last_event is None or (
        last_sub is not None and last_sub.created_at >= last_event.created_at
    )

    if sub_is_newer:
        roster = {
            entry.player_id: entry
            for entry in roster_for_match(db, match_id, last_sub.team_id)
        }
        out_entry = roster.get(last_sub.player_out_id)
        in_entry = roster.get(last_sub.player_in_id)

        if out_entry is not None:
            out_entry.is_active = True
            out_entry.sub_out_ts = None
        if in_entry is not None:
            in_entry.is_active = False
            # A starter who was subbed off, then back on, keeps their original
            # kickoff sub_in_ts; anyone else reverts to never having entered.
            in_entry.sub_in_ts = 0.0 if in_entry.is_starter else None

        description = f"Undid substitution at {last_sub.match_clock_s:.0f}s"
        db.delete(last_sub)
        db.commit()
        return schemas.UndoResult(undone=True, description=description)

    if last_event.event_type in schemas.PERIOD_EVENTS:
        match = db.get(models.Match, match_id)
        if match is not None:
            prior = db.scalars(
                select(models.Event)
                .where(
                    models.Event.match_id == match_id,
                    models.Event.event_type.in_(schemas.PERIOD_EVENTS),
                    models.Event.id != last_event.id,
                )
                .order_by(models.Event.created_at.desc(), models.Event.id.desc())
                .limit(1)
            ).first()
            match.status = (
                schemas.PERIOD_TO_STATUS[prior.event_type] if prior else "scheduled"
            )

    description = (
        f"Undid {last_event.event_type.replace('_', ' ')} "
        f"at {last_event.match_clock_s:.0f}s"
    )
    db.delete(last_event)
    db.commit()
    return schemas.UndoResult(undone=True, description=description)


def match_log(db: Session, match_id: int) -> list[schemas.LogEntry]:
    entries: list[schemas.LogEntry] = []

    for event in db.scalars(
        select(models.Event).where(models.Event.match_id == match_id)
    ):
        entries.append(
            schemas.LogEntry(
                kind="event",
                id=event.id,
                match_clock_s=event.match_clock_s,
                label=event.event_type.replace("_", " "),
                team_id=event.team_id,
                created_at=event.created_at,
            )
        )

    for sub in db.scalars(
        select(models.Substitution).where(models.Substitution.match_id == match_id)
    ):
        entries.append(
            schemas.LogEntry(
                kind="substitution",
                id=sub.id,
                match_clock_s=sub.match_clock_s,
                label=f"sub: {sub.player_in.name} for {sub.player_out.name}",
                team_id=sub.team_id,
                created_at=sub.created_at,
            )
        )

    entries.sort(key=lambda e: e.match_clock_s)
    return entries
