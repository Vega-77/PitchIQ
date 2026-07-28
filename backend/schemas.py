from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

EventType = Literal[
    "out_of_bounds",
    "corner",
    "throw_in",
    "goal_kick",
    "free_kick",
    "foul",
    "card",
    "goal",
    "offside",
    "kickoff_1st",
    "halftime",
    "kickoff_2nd",
    "full_time",
]

PeriodType = Literal["kickoff_1st", "halftime", "kickoff_2nd", "full_time"]

EventSource = Literal["live_tag", "cv_candidate", "reviewer_confirmed"]

# Period markers define the match clock rather than occurring on it.
PERIOD_EVENTS: set[str] = {"kickoff_1st", "halftime", "kickoff_2nd", "full_time"}

PERIOD_TO_STATUS: dict[str, str] = {
    "kickoff_1st": "first_half",
    "halftime": "halftime",
    "kickoff_2nd": "second_half",
    "full_time": "full_time",
}


class TeamCreate(BaseModel):
    name: str


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PlayerCreate(BaseModel):
    name: str
    jersey_number: int | None = None


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: int
    name: str
    jersey_number: int | None


class TeamDetail(TeamOut):
    players: list[PlayerOut] = []


class MatchCreate(BaseModel):
    home_team_id: int
    away_team_id: int
    kickoff_date: str | None = None


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    home_team_id: int
    away_team_id: int
    kickoff_date: str | None
    status: str


class LineupEntry(BaseModel):
    player_id: int
    is_starter: bool = False


class LineupCreate(BaseModel):
    team_id: int
    entries: list[LineupEntry]


class RosterEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    player_id: int
    team_id: int
    is_starter: bool
    is_active: bool
    sub_in_ts: float | None
    sub_out_ts: float | None
    player: PlayerOut


class OnFieldOut(BaseModel):
    on_field: list[RosterEntryOut]
    available: list[RosterEntryOut]
    used: list[RosterEntryOut]


class PeriodCreate(BaseModel):
    period: PeriodType
    match_clock_s: float = 0.0


class EventCreate(BaseModel):
    event_type: EventType
    match_clock_s: float
    team_id: int | None = None
    detail: str | None = None
    source: EventSource = "live_tag"


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    team_id: int | None
    event_type: str
    match_clock_s: float
    source: str
    detail: str | None
    created_at: datetime


class SubstitutionCreate(BaseModel):
    team_id: int
    player_out_id: int
    player_in_id: int
    match_clock_s: float


class SubstitutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    team_id: int
    player_out_id: int
    player_in_id: int
    match_clock_s: float
    created_at: datetime


class LogEntry(BaseModel):
    kind: Literal["event", "substitution"]
    id: int
    match_clock_s: float
    label: str
    team_id: int | None
    created_at: datetime


class UndoResult(BaseModel):
    undone: bool
    description: str
