from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/teams", tags=["teams"])


@router.post("", response_model=schemas.TeamOut, status_code=201)
def create_team(payload: schemas.TeamCreate, db: Session = Depends(get_db)):
    team = models.Team(name=payload.name)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("", response_model=list[schemas.TeamOut])
def list_teams(db: Session = Depends(get_db)):
    return list(db.scalars(select(models.Team).order_by(models.Team.name)))


@router.get("/{team_id}", response_model=schemas.TeamDetail)
def get_team(team_id: int, db: Session = Depends(get_db)):
    team = db.get(models.Team, team_id)
    if team is None:
        raise HTTPException(404, "Team not found")
    return team


@router.post("/{team_id}/players", response_model=schemas.PlayerOut, status_code=201)
def add_player(
    team_id: int, payload: schemas.PlayerCreate, db: Session = Depends(get_db)
):
    if db.get(models.Team, team_id) is None:
        raise HTTPException(404, "Team not found")

    player = models.Player(
        team_id=team_id, name=payload.name, jersey_number=payload.jersey_number
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player
