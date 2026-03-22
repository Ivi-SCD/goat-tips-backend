from pydantic import BaseModel
from typing import Optional
from app.schemas.match import MatchContext, NarrativeResponse, StatsTrend, LineupInfo
from app.schemas.analytics import H2HRecord, TeamForm
from app.schemas.prediction import ScorePredictionResponse


class FullMatchAnalysis(BaseModel):
    match: MatchContext
    narrative: NarrativeResponse
    prediction: Optional[ScorePredictionResponse] = None
    h2h: Optional[H2HRecord] = None
    stats_trend: Optional[StatsTrend] = None
    lineup: Optional[LineupInfo] = None
    home_form: Optional[TeamForm] = None
    away_form: Optional[TeamForm] = None
    goal_risk_score: Optional[float] = None
    card_risk_score: Optional[float] = None
    agent_steps: list[str] = []
