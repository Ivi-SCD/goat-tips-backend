from typing import Optional
from pydantic import BaseModel


class HalfTimePrediction(BaseModel):
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_0_5_prob: float       # any goal by HT
    over_1_5_prob: float
    most_likely_score: str
    lambda_home: float
    lambda_away: float


class ScorePredictionResponse(BaseModel):
    home_team: str
    away_team: str
    lambda_home: float
    lambda_away: float
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_2_5_prob: float
    btts_prob: float
    most_likely_score: str
    most_likely_score_prob: float
    top_scores: list[tuple[str, float]]
    score_matrix: list[list[float]]
    confidence: str                     # "Alta" | "Média" | "Baixa"
    model_note: str
    half_time: Optional[HalfTimePrediction] = None
    weather_factor: float = 1.0
    weather_condition: Optional[str] = None
