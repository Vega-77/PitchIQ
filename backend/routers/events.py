from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/matches/{match_id}", tags=["events"])


@router.post("/events", response_model=schemas.EventOut, status_code=201)
def log_event(
    match_id: int, payload: schemas.EventCreate, db: Session = Depends(get_db)
):
    match = crud.get_match(db, match_id)
    if match is None:
        raise HTTPException(404, "Match not found")
    if payload.team_id is not None and payload.team_id not in (
        match.home_team_id,
        match.away_team_id,
    ):
        raise HTTPException(400, "That team is not playing in this match")

    return crud.create_event(db, match_id, payload)


@router.get("/events", response_model=list[schemas.EventOut])
def list_events(match_id: int, db: Session = Depends(get_db)):
    if crud.get_match(db, match_id) is None:
        raise HTTPException(404, "Match not found")

    return list(
        db.scalars(
            select(models.Event)
            .where(models.Event.match_id == match_id)
            .order_by(models.Event.match_clock_s)
        )
    )


@router.post("/undo", response_model=schemas.UndoResult)
def undo_last(match_id: int, db: Session = Depends(get_db)):
    if crud.get_match(db, match_id) is None:
        raise HTTPException(404, "Match not found")
    return crud.undo_last(db, match_id)
