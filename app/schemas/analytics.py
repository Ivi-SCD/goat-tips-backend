from pydantic import BaseModel
from typing import Optional


class H2HMatch(BaseModel):
    event_id: str
    date: Optional[str] = None
    home_team: str
    away_team: str
    score_home: int
    score_away: int
    winner: str   # "home" | "away" | "draw"


class H2HRecord(BaseModel):
    home_team: str
    away_team: str
    total_matches: int
    home_wins: int
    away_wins: int
    draws: int
    home_goals_avg: float
    away_goals_avg: float
    last_matches: list[H2HMatch]


class MatchResult(BaseModel):
    event_id: str
    date: str
    opponent: str
    home_or_away: str   # "home" | "away"
    goals_scored: int
    goals_conceded: int
    result: str         # "W" | "D" | "L"


class TeamForm(BaseModel):
    team_name: str
    last_n_matches: int
    matches: list[MatchResult]
    wins: int
    draws: int
    losses: int
    goals_scored: int
    goals_conceded: int
    form_string: str
    avg_goals_scored: float
    avg_goals_conceded: float


class GoalMinuteBucket(BaseModel):
    minute_range: str
    goals: int
    pct_of_total: float


class GoalPatterns(BaseModel):
    total_goals: int
    buckets: list[GoalMinuteBucket]
    peak_minute_range: str
    avg_goals_per_match: float


class CardPattern(BaseModel):
    minute_range: str
    yellow_cards: int
    red_cards: int
    pct_of_total: float


class CardPatterns(BaseModel):
    total_yellows: int
    total_reds: int
    buckets: list[CardPattern]
    peak_minute_range: str
