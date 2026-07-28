from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db

router = APIRouter(prefix="/matches/{match_id}", tags=["substitutions"])


@router.post("/substitutions", response_model=schemas.SubstitutionOut, status_code=201)
def log_substitution(
    match_id: int, payload: schemas.SubstitutionCreate, db: Session = Depends(get_db)
):
    match = crud.get_match(db, match_id)
    if match is None:
        raise HTTPException(404, "Match not found")
    if payload.team_id not in (match.home_team_id, match.away_team_id):
        raise HTTPException(400, "That team is not playing in this match")

    sub, message = crud.apply_substitution(db, match_id, payload)
    if sub is None:
        raise HTTPException(400, message)
    return sub
