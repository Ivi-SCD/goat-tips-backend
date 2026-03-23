"""
Poisson Match Predictor
=======================
Loads a pre-trained model from models/poisson_model.pkl (produced by
scripts/train_model.py).  Falls back to fitting inline if the file is absent.

Algorithm:  Independent Poisson Goals (Dixon-Coles 1997 framework)
  λ_home = attack_home × defense_away × league_avg_home_goals
  λ_away = attack_away × defense_home × league_avg_away_goals

Improvements:
  P1: Dixon-Coles ρ correction for low-scoring cells (0-0, 1-0, 0-1, 1-1)
  P2: Time-decay weighting in _fit_inline() (half-life 1 year)
  P3: Separate home/away attack/defense in TeamStrength
  P6: Normalize matrix after Dixon-Coles correction
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
RHO = 0.04  # Dixon-Coles correction parameter


def _download_from_cos() -> bool:
    """Try to pull model.pkl from IBM Cloud Object Storage. Returns True on success."""
    access_key = os.getenv("IBM_COS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("IBM_COS_SECRET_ACCESS_KEY", "")
    endpoint   = os.getenv("IBM_COS_ENDPOINT", "https://s3.us-south.cloud-object-storage.appdomain.cloud")
    bucket     = os.getenv("IBM_COS_BUCKET", "goat-tips-bucket")
    key        = os.getenv("MODEL_BLOB_NAME", "poisson_model.pkl")
    if not (access_key and secret_key):
        return False
    try:
        import ibm_boto3
        cos = ibm_boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint,
        )
        MODEL_PKL.parent.mkdir(parents=True, exist_ok=True)
        cos.download_file(bucket, key, str(MODEL_PKL))
        logger.info("Predictor: downloaded model.pkl from IBM COS '%s/%s'", bucket, key)
        return True
    except Exception as exc:
        logger.warning("Predictor: IBM COS download failed — %s", exc)
        return False


# ── Domain objects ────────────────────────────────────────────────────────────

@dataclass
class TeamStrength:
    attack: float = 1.0
    defense: float = 1.0
    attack_home: Optional[float] = None
    attack_away: Optional[float] = None
    defense_home: Optional[float] = None
    defense_away: Optional[float] = None


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


# ── Dixon-Coles tau correction ────────────────────────────────────────────────

def _tau(h: int, a: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles low-score correction factor."""
    if h == 0 and a == 0: return 1 - lh * la * rho
    elif h == 0 and a == 1: return 1 + lh * rho
    elif h == 1 and a == 0: return 1 + la * rho
    elif h == 1 and a == 1: return 1 - rho
    return 1.0


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
    """Fallback: fit the model from raw CSV data with time-decay weighting and home/away split."""
    import pandas as pd
    data_path = Path(__file__).parent.parent.parent / "data" / "betsapi" / "premier_league_events.csv"
    df = pd.read_csv(data_path, low_memory=False)
    df = df[df["time_status"] == 3].copy()
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])

    # P2: Time-decay weights (half-life = 1 year)
    now_ts = float(pd.Timestamp.now().timestamp())
    df["time_unix"] = pd.to_numeric(df["time_unix"], errors="coerce").fillna(now_ts)
    df["weight"] = np.power(0.5, (now_ts - df["time_unix"].astype(float)) / (365.25 * 86400))

    n = len(df)

    # Weighted league averages
    total_weight = df["weight"].sum()
    avg_h = float((df["home_score"] * df["weight"]).sum() / total_weight)
    avg_a = float((df["away_score"] * df["weight"]).sum() / total_weight)

    # P3: Separate home/away attack/defense per team
    strengths = {}
    all_teams = set(df["home_team_name"].dropna().unique()) | set(df["away_team_name"].dropna().unique())

    for team in all_teams:
        home_rows = df[df["home_team_name"] == team]
        away_rows = df[df["away_team_name"] == team]

        # Home attack / defense
        if not home_rows.empty:
            w_home = home_rows["weight"].sum()
            attack_home = float((home_rows["home_score"] * home_rows["weight"]).sum() / w_home / avg_h) if avg_h > 0 else 1.0
            defense_home = float((home_rows["away_score"] * home_rows["weight"]).sum() / w_home / avg_a) if avg_a > 0 else 1.0
        else:
            attack_home = 1.0
            defense_home = 1.0

        # Away attack / defense
        if not away_rows.empty:
            w_away = away_rows["weight"].sum()
            attack_away = float((away_rows["away_score"] * away_rows["weight"]).sum() / w_away / avg_a) if avg_a > 0 else 1.0
            defense_away = float((away_rows["home_score"] * away_rows["weight"]).sum() / w_away / avg_h) if avg_h > 0 else 1.0
        else:
            attack_away = 1.0
            defense_away = 1.0

        # Combined (backward-compatible fallback)
        all_rows = pd.concat([home_rows, away_rows])
        w_total = all_rows["weight"].sum()
        if w_total > 0:
            scored = float((
                (home_rows["home_score"] * home_rows["weight"]).sum() +
                (away_rows["away_score"] * away_rows["weight"]).sum()
            ) / w_total)
            conceded = float((
                (home_rows["away_score"] * home_rows["weight"]).sum() +
                (away_rows["home_score"] * away_rows["weight"]).sum()
            ) / w_total)
            avg_tot = (avg_h + avg_a) / 2
            attack_combined = max(scored / avg_tot, 0.1) if avg_tot > 0 else 1.0
            defense_combined = max(conceded / avg_tot, 0.1) if avg_tot > 0 else 1.0
        else:
            attack_combined = 1.0
            defense_combined = 1.0

        strengths[team] = TeamStrength(
            attack=attack_combined,
            defense=defense_combined,
            attack_home=max(attack_home, 0.1),
            attack_away=max(attack_away, 0.1),
            defense_home=max(defense_home, 0.1),
            defense_away=max(defense_away, 0.1),
        )

    logger.info("Predictor: fitted inline on %d matches (time-decay, home/away split)", n)
    return PoissonModel(team_strengths=strengths, league_avg_home_goals=avg_h,
                        league_avg_away_goals=avg_a, fitted=True, n_matches=n)


@lru_cache(maxsize=1)
def get_model() -> PoissonModel:
    model = _load_from_pkl()
    if model:
        return model
    # Try IBM COS before falling back to inline fitting
    if _download_from_cos():
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

    # P3: Use home/away specific strengths with fallback to combined
    home_attack = home_str.attack_home or home_str.attack
    away_defense = away_str.defense_away or away_str.defense
    away_attack = away_str.attack_away or away_str.attack
    home_defense = home_str.defense_home or home_str.defense

    lh = home_attack * away_defense * model.league_avg_home_goals
    la = away_attack * home_defense * model.league_avg_away_goals

    n = MAX_GOALS
    hp = np.array([poisson.pmf(k, lh) for k in range(n)])
    ap = np.array([poisson.pmf(k, la) for k in range(n)])
    mat = np.outer(hp, ap)

    # P1: Dixon-Coles ρ correction for low-scoring cells
    for h_goals in range(2):
        for a_goals in range(2):
            mat[h_goals, a_goals] *= _tau(h_goals, a_goals, lh, la, RHO)

    # P6: Normalize after Dixon-Coles correction
    mat /= mat.sum()

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
    note = f"Modelo Poisson+DC — {model.n_matches} jogos PL 2014–2026."
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
