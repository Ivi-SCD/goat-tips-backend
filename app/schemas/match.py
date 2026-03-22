from pydantic import BaseModel
from typing import Optional


class TeamInfo(BaseModel):
    id: str
    name: str
    image_url: Optional[str] = None


class OddsSnapshot(BaseModel):
    home_win: float
    draw: float
    away_win: float
    over_2_5: Optional[float] = None
    btts: Optional[float] = None


class ImpliedProbabilities(BaseModel):
    home_win: float
    draw: float
    away_win: float
    market_margin: float


class MatchContext(BaseModel):
    event_id: str
    home: TeamInfo
    away: TeamInfo
    minute: Optional[int] = None
    score_home: int = 0
    score_away: int = 0
    status: str                         # "live" | "upcoming" | "ended"
    odds: Optional[OddsSnapshot] = None
    probabilities: Optional[ImpliedProbabilities] = None
    odds_shift_pct: Optional[float] = None
    kick_off_time: Optional[str] = None  # ISO 8601 UTC
    round: Optional[str] = None
    referee: Optional[str] = None
    stadium: Optional[str] = None
    bet365_id: Optional[str] = None


class NarrativeResponse(BaseModel):
    match_id: str
    headline: str
    analysis: str
    prediction: str
    momentum_signal: Optional[str] = None
    confidence_label: str               # "Alta" | "Média" | "Baixa"


class QuestionRequest(BaseModel):
    question: str


class PeriodStats(BaseModel):
    period: str
    home_shots: Optional[int] = None
    away_shots: Optional[int] = None
    home_shots_on_target: Optional[int] = None
    away_shots_on_target: Optional[int] = None
    home_corners: Optional[int] = None
    away_corners: Optional[int] = None
    home_possession: Optional[float] = None
    away_possession: Optional[float] = None
    home_dangerous_attacks: Optional[int] = None
    away_dangerous_attacks: Optional[int] = None
    home_attacks: Optional[int] = None
    away_attacks: Optional[int] = None


class StatsTrend(BaseModel):
    event_id: str
    periods: list[PeriodStats]
    momentum_score: Optional[float] = None
    momentum_label: Optional[str] = None


class PlayerInfo(BaseModel):
    id: Optional[str] = None
    name: str
    number: Optional[int] = None
    position: Optional[str] = None


class LineupTeam(BaseModel):
    team: TeamInfo
    formation: Optional[str] = None
    starting_xi: list[PlayerInfo]
    substitutes: list[PlayerInfo]


class LineupInfo(BaseModel):
    event_id: str
    home: Optional[LineupTeam] = None
    away: Optional[LineupTeam] = None


class TopPlayer(BaseModel):
    rank: int
    player_name: str
    team_name: str
    goals: Optional[int] = None
    assists: Optional[int] = None


class LeagueToplist(BaseModel):
    league_id: int
    top_scorers: list[TopPlayer]
    top_assists: list[TopPlayer]
