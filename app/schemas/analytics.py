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


class HalfGoals(BaseModel):
    first_half_avg: float
    second_half_avg: float
    first_half_pct: float


class TeamProfile(BaseModel):
    team_name: str
    sample_size: int
    avg_shots_on_target: float
    avg_goals_scored: float
    shot_efficiency: float          # goals per shot on target (0.0–1.0)
    avg_xg: float                   # avg xG per game (if available)
    goals_by_half: HalfGoals
    home_win_rate: float
    away_win_rate: float
    home_goals_avg: float
    away_goals_avg: float


class RefereeStats(BaseModel):
    referee_name: str
    matches: int
    avg_yellow_cards: float
    avg_red_cards: float
    avg_fouls: float
    home_win_rate: float            # % of matches where home team won
