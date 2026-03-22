from pydantic import BaseModel


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
