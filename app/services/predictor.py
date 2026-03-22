"""
Poisson Match Predictor
=======================
Loads a pre-trained model from models/poisson_model.pkl (produced by
scripts/train_model.py).  Falls back to fitting inline if the file is absent.

Algorithm:  Independent Poisson Goals (Dixon-Coles 1997 framework)
  λ_home = attack_home × defense_away × league_avg_home_goals
  λ_away = attack_away × defense_home × league_avg_away_goals
"""

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.stats import poisson

logger = logging.getLogger(__name__)

MODEL_PKL = Path(__file__).parent.parent.parent / "models" / "poisson_model.pkl"
MAX_GOALS = 7


def _download_from_blob() -> bool:
    """Try to pull model.pkl from Azure Blob Storage. Returns True on success."""
    conn_str  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.getenv("AZURE_STORAGE_CONTAINER", "models")
    blob_name = os.getenv("MODEL_BLOB_NAME", "poisson_model.pkl")
    if not conn_str:
        return False
    try:
        from azure.storage.blob import BlobServiceClient
        blob_client = BlobServiceClient.from_connection_string(conn_str) \
                          .get_blob_client(container=container, blob=blob_name)
        MODEL_PKL.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PKL, "wb") as f:
            f.write(blob_client.download_blob().readall())
        logger.info("Predictor: downloaded model.pkl from Azure Blob '%s/%s'", container, blob_name)
        return True
    except Exception as exc:
        logger.warning("Predictor: blob download failed — %s", exc)
        return False


# ── Domain objects ────────────────────────────────────────────────────────────

@dataclass
class TeamStrength:
    attack: float = 1.0
    defense: float = 1.0


@dataclass
class PoissonModel:
    team_strengths: dict[str, TeamStrength] = field(default_factory=dict)
    league_avg_home_goals: float = 1.54
    league_avg_away_goals: float = 1.26
    fitted: bool = False
    n_matches: int = 0


@dataclass
class ScorePrediction:
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
    confidence: str
    model_note: str


# ── Model loading ─────────────────────────────────────────────────────────────

def _load_from_pkl() -> Optional[PoissonModel]:
    if not MODEL_PKL.exists():
        return None
    try:
        import joblib
        data = joblib.load(MODEL_PKL)
        strengths = {
            team: TeamStrength(attack=v["attack"], defense=v["defense"])
            for team, v in data["team_strengths"].items()
        }
        model = PoissonModel(
            team_strengths=strengths,
            league_avg_home_goals=data["league_avg_home_goals"],
            league_avg_away_goals=data["league_avg_away_goals"],
            fitted=True,
            n_matches=data["n_matches"],
        )
        logger.info("Predictor: loaded model.pkl (%d teams, %d matches)",
                    len(strengths), model.n_matches)
        return model
    except Exception as exc:
        logger.warning("Predictor: failed to load model.pkl — %s", exc)
        return None


def _fit_inline() -> PoissonModel:
    """Fallback: fit the model from raw CSV data."""
    import pandas as pd
    data_path = Path(__file__).parent.parent.parent / "data" / "betsapi" / "premier_league_events.csv"
    df = pd.read_csv(data_path, low_memory=False)
    df = df[df["time_status"] == 3].copy()
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])

    n = len(df)
    avg_h = df["home_score"].mean()
    avg_a = df["away_score"].mean()
    avg_tot = (avg_h + avg_a) / 2

    home_stats = df.groupby("home_team_name").agg(
        hs=("home_score", "sum"), hc=("away_score", "sum"), hm=("home_score", "count"))
    away_stats = df.groupby("away_team_name").agg(
        as_=("away_score", "sum"), ac=("home_score", "sum"), am=("away_score", "count"))

    strengths = {}
    for team in set(home_stats.index) | set(away_stats.index):
        scored = conceded = matches = 0
        if team in home_stats.index:
            r = home_stats.loc[team]
            scored += r["hs"]; conceded += r["hc"]; matches += r["hm"]
        if team in away_stats.index:
            r = away_stats.loc[team]
            scored += r["as_"]; conceded += r["ac"]; matches += r["am"]
        if matches:
            strengths[team] = TeamStrength(
                attack=max(scored / matches / avg_tot, 0.1),
                defense=max(conceded / matches / avg_tot, 0.1),
            )
        else:
            strengths[team] = TeamStrength()

    logger.info("Predictor: fitted inline on %d matches", n)
    return PoissonModel(team_strengths=strengths, league_avg_home_goals=avg_h,
                        league_avg_away_goals=avg_a, fitted=True, n_matches=n)


@lru_cache(maxsize=1)
def get_model() -> PoissonModel:
    model = _load_from_pkl()
    if model:
        return model
    # Try Azure Blob before falling back to inline fitting
    if _download_from_blob():
        model = _load_from_pkl()
        if model:
            return model
    return _fit_inline()


# ── Prediction ────────────────────────────────────────────────────────────────

def _find_team(name: str, model: PoissonModel) -> Optional[str]:
    lower = name.lower()
    for k in model.team_strengths:
        if k.lower() == lower:
            return k
    for k in model.team_strengths:
        if lower in k.lower() or k.lower() in lower:
            return k
    return None


def predict_match(home_team: str, away_team: str) -> ScorePrediction:
    model = get_model()
    home_key = _find_team(home_team, model)
    away_key = _find_team(away_team, model)
    home_str = model.team_strengths.get(home_key, TeamStrength()) if home_key else TeamStrength()
    away_str = model.team_strengths.get(away_key, TeamStrength()) if away_key else TeamStrength()

    lh = home_str.attack * away_str.defense * model.league_avg_home_goals
    la = away_str.attack * home_str.defense * model.league_avg_away_goals

    n = MAX_GOALS
    hp = np.array([poisson.pmf(k, lh) for k in range(n)])
    ap = np.array([poisson.pmf(k, la) for k in range(n)])
    mat = np.outer(hp, ap)

    home_win = float(np.sum(np.tril(mat, k=-1)))
    draw = float(np.trace(mat))
    away_win = float(np.sum(np.triu(mat, k=1)))
    over_2_5 = float(sum(mat[i, j] for i in range(n) for j in range(n) if i + j > 2))
    btts = float(1 - hp[0] - ap[0] + mat[0, 0])

    best = np.unravel_index(np.argmax(mat), mat.shape)
    flat = sorted([(i, j, mat[i, j]) for i in range(n) for j in range(n)], key=lambda x: -x[2])
    top5 = [(f"{i}-{j}", round(float(p), 4)) for i, j, p in flat[:5]]

    unknown = []
    if not home_key:
        unknown.append(f"'{home_team}' fora do dataset")
    if not away_key:
        unknown.append(f"'{away_team}' fora do dataset")
    confidence = "Alta" if (home_key and away_key) else ("Média" if (home_key or away_key) else "Baixa")
    note = f"Modelo Poisson — {model.n_matches} jogos PL 2014–2026."
    if unknown:
        note += " Atenção: " + "; ".join(unknown) + " — usando médias da liga."

    return ScorePrediction(
        home_team=home_team, away_team=away_team,
        lambda_home=round(lh, 3), lambda_away=round(la, 3),
        home_win_prob=round(home_win, 4), draw_prob=round(draw, 4), away_win_prob=round(away_win, 4),
        over_2_5_prob=round(over_2_5, 4), btts_prob=round(btts, 4),
        most_likely_score=f"{best[0]}-{best[1]}",
        most_likely_score_prob=round(float(mat[best]), 4),
        top_scores=top5,
        score_matrix=[[round(float(mat[i, j]), 5) for j in range(n)] for i in range(n)],
        confidence=confidence, model_note=note,
    )


def predict_from_match_context(match) -> ScorePrediction:
    return predict_match(match.home.name, match.away.name)
