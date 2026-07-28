from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("", response_model=schemas.MatchOut, status_code=201)
def create_match(payload: schemas.MatchCreate, db: Session = Depends(get_db)):
    for team_id in (payload.home_team_id, payload.away_team_id):
        if db.get(models.Team, team_id) is None:
            raise HTTPException(404, f"Team {team_id} not found")
    if payload.home_team_id == payload.away_team_id:
        raise HTTPException(400, "A team cannot play itself")

    match = models.Match(
        home_team_id=payload.home_team_id,
        away_team_id=payload.away_team_id,
        kickoff_date=payload.kickoff_date,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


@router.get("", response_model=list[schemas.MatchOut])
def list_matches(db: Session = Depends(get_db)):
    return list(db.scalars(select(models.Match).order_by(models.Match.id.desc())))


@router.get("/{match_id}", response_model=schemas.MatchOut)
def get_match(match_id: int, db: Session = Depends(get_db)):
    match = crud.get_match(db, match_id)
    if match is None:
        raise HTTPException(404, "Match not found")
    return match


@router.post("/{match_id}/lineup", response_model=list[schemas.RosterEntryOut])
def set_lineup(
    match_id: int, payload: schemas.LineupCreate, db: Session = Depends(get_db)
):
    match = crud.get_match(db, match_id)
    if match is None:
        raise HTTPException(404, "Match not found")
    if payload.team_id not in (match.home_team_id, match.away_team_id):
        raise HTTPException(400, "That team is not playing in this match")

    return crud.set_lineup(db, match_id, payload)


@router.get("/{match_id}/onfield", response_model=schemas.OnFieldOut)
def on_field(match_id: int, team_id: int, db: Session = Depends(get_db)):
    if crud.get_match(db, match_id) is None:
        raise HTTPException(404, "Match not found")

    roster = crud.roster_for_match(db, match_id, team_id)
    return schemas.OnFieldOut(
        on_field=[e for e in roster if e.is_active],
        available=[e for e in roster if not e.is_active and e.sub_out_ts is None],
        used=[e for e in roster if not e.is_active and e.sub_out_ts is not None],
    )


@router.post("/{match_id}/period", response_model=schemas.EventOut, status_code=201)
def mark_period(
    match_id: int, payload: schemas.PeriodCreate, db: Session = Depends(get_db)
):
    if crud.get_match(db, match_id) is None:
        raise HTTPException(404, "Match not found")

    return crud.create_event(
        db,
        match_id,
        schemas.EventCreate(
            event_type=payload.period, match_clock_s=payload.match_clock_s
        ),
    )


@router.get("/{match_id}/log", response_model=list[schemas.LogEntry])
def match_log(match_id: int, db: Session = Depends(get_db)):
    if crud.get_match(db, match_id) is None:
        raise HTTPException(404, "Match not found")
    return crud.match_log(db, match_id)
