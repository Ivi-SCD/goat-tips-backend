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
import pandas as pd
from scipy.stats import poisson

logger = logging.getLogger(__name__)

MODEL_PKL = Path(__file__).parent.parent.parent / "models" / "poisson_model.pkl"
MAX_GOALS = 7
RHO = -0.05  # Dixon-Coles correction parameter (optimized via CV on 1000 test matches)


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

XG_BLEND = 0.4  # Weight for xG-based strengths when available (0.4 = 40% xG, 60% goals)


@dataclass
class TeamStrength:
    attack: float = 1.0
    defense: float = 1.0
    attack_home: Optional[float] = None
    attack_away: Optional[float] = None
    defense_home: Optional[float] = None
    defense_away: Optional[float] = None
    xg_attack_home: Optional[float] = None
    xg_defense_home: Optional[float] = None
    xg_attack_away: Optional[float] = None
    xg_defense_away: Optional[float] = None
    xg_matches: Optional[int] = None


@dataclass
class PoissonModel:
    team_strengths: dict[str, TeamStrength] = field(default_factory=dict)
    league_avg_home_goals: float = 1.54
    league_avg_away_goals: float = 1.26
    fitted: bool = False
    n_matches: int = 0


@dataclass
class HalfTimePrediction:
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_0_5_prob: float
    over_1_5_prob: float
    most_likely_score: str
    lambda_home: float
    lambda_away: float


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
    half_time: Optional["HalfTimePrediction"] = None
    weather_factor: float = 1.0
    weather_condition: Optional[str] = None


# ── Half-time prediction ──────────────────────────────────────────────────────

# Empirical goal fraction by half (from 9,448 timeline goals)
_FH_FRACTION = 0.4598  # 46% of goals occur in first 45 min
_SH_FRACTION = 0.5403  # 54% in second 45 min


def _compute_halftime(lh: float, la: float) -> HalfTimePrediction:
    """Compute half-time score distribution using first-half λ fractions."""
    lh_fh = lh * _FH_FRACTION
    la_fh = la * _FH_FRACTION
    n = 6
    hp = np.array([poisson.pmf(k, lh_fh) for k in range(n)])
    ap = np.array([poisson.pmf(k, la_fh) for k in range(n)])
    mat = np.outer(hp, ap)

    ht_home = float(np.sum(np.tril(mat, k=-1)))
    ht_draw = float(np.trace(mat))
    ht_away = float(np.sum(np.triu(mat, k=1)))
    ht_0_0 = float(mat[0, 0])
    ht_over_05 = 1.0 - ht_0_0
    ht_over_15 = float(1.0 - sum(mat[i, j] for i in range(n) for j in range(n) if i + j <= 1))

    best = np.unravel_index(np.argmax(mat), mat.shape)
    return HalfTimePrediction(
        home_win_prob=round(ht_home, 4),
        draw_prob=round(ht_draw, 4),
        away_win_prob=round(ht_away, 4),
        over_0_5_prob=round(ht_over_05, 4),
        over_1_5_prob=round(max(ht_over_15, 0), 4),
        most_likely_score=f"{best[0]}-{best[1]}",
        lambda_home=round(lh_fh, 3),
        lambda_away=round(la_fh, 3),
    )


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
            team: TeamStrength(
                attack=v["attack"],
                defense=v["defense"],
                attack_home=v.get("attack_home"),
                attack_away=v.get("attack_away"),
                defense_home=v.get("defense_home"),
                defense_away=v.get("defense_away"),
                xg_attack_home=v.get("xg_attack_home"),
                xg_defense_home=v.get("xg_defense_home"),
                xg_attack_away=v.get("xg_attack_away"),
                xg_defense_away=v.get("xg_defense_away"),
                xg_matches=v.get("xg_matches"),
            )
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


def _get_referee_goal_factor(referee_name: Optional[str]) -> float:
    """Compute how much a referee deviates from league-average goals.
    Returns a multiplier (1.0 = league average). Cached via lru_cache on model load."""
    if not referee_name:
        return 1.0
    model = get_model()
    factors = getattr(model, "_referee_factors", None)
    if factors is None:
        # Build referee factors from historical data
        try:
            from app.repositories.historical import load_events
            events = load_events()
            ended = events[events["time_status"] == 3].copy()
            ended["total_goals"] = pd.to_numeric(ended["home_score"], errors="coerce") + \
                                   pd.to_numeric(ended["away_score"], errors="coerce")
            league_avg = ended["total_goals"].mean()
            ref_agg = ended.groupby("referee_name").agg(
                matches=("event_id", "count"),
                avg_goals=("total_goals", "mean"),
            )
            # Only use referees with >= 20 matches for reliable factor
            ref_agg = ref_agg[ref_agg["matches"] >= 20]
            factors = (ref_agg["avg_goals"] / league_avg).to_dict()
        except Exception:
            factors = {}
        model._referee_factors = factors

    lower = referee_name.lower()
    for name, factor in factors.items():
        if name.lower() == lower or lower in name.lower():
            return float(factor)
    return 1.0


def predict_match(home_team: str, away_team: str,
                  referee_name: Optional[str] = None,
                  weather_factor: float = 1.0,
                  weather_condition: Optional[str] = None) -> ScorePrediction:
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

    # xG blend: if both teams have xG data, blend xG-based strengths with goals-based
    if home_str.xg_attack_home is not None and away_str.xg_defense_away is not None:
        w = XG_BLEND
        home_attack = (1 - w) * home_attack + w * home_str.xg_attack_home
        away_defense = (1 - w) * away_defense + w * away_str.xg_defense_away
    if away_str.xg_attack_away is not None and home_str.xg_defense_home is not None:
        w = XG_BLEND
        away_attack = (1 - w) * away_attack + w * away_str.xg_attack_away
        home_defense = (1 - w) * home_defense + w * home_str.xg_defense_home

    lh = home_attack * away_defense * model.league_avg_home_goals
    la = away_attack * home_defense * model.league_avg_away_goals

    # Referee goal factor adjustment
    ref_factor = _get_referee_goal_factor(referee_name)
    if ref_factor != 1.0:
        lh *= ref_factor
        la *= ref_factor

    # Weather goal factor (rain/wind reduce scoring)
    if weather_factor != 1.0:
        lh *= weather_factor
        la *= weather_factor

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
        half_time=_compute_halftime(lh, la),
        weather_factor=round(weather_factor, 3),
        weather_condition=weather_condition,
    )


def predict_from_match_context(match) -> ScorePrediction:
    referee = getattr(match, "referee", None)
    return predict_match(match.home.name, match.away.name, referee_name=referee)


RED_CARD_ATTACK_PENALTY = 0.72  # 10-man team attack λ multiplier (~0.28 goals/match reduction)
RED_CARD_DEFENSE_BOOST  = 0.90  # 10-man team concedes slightly more (pressure relief for opponent)


def predict_inplay(
    home_team: str,
    away_team: str,
    current_home_goals: int,
    current_away_goals: int,
    minute: int,
    referee_name: Optional[str] = None,
    home_red_cards: int = 0,
    away_red_cards: int = 0,
) -> ScorePrediction:
    """In-play Bayesian update: P(final score | current score, minute).

    Uses non-homogeneous Poisson (empirical goal rate by time bucket) scaled by
    remaining time fraction, adjusted for red cards and referee tendency.
    """
    model = get_model()
    home_key = _find_team(home_team, model)
    away_key = _find_team(away_team, model)
    home_str = model.team_strengths.get(home_key, TeamStrength()) if home_key else TeamStrength()
    away_str = model.team_strengths.get(away_key, TeamStrength()) if away_key else TeamStrength()

    # Compute full-match lambdas (same as predict_match)
    home_attack = home_str.attack_home or home_str.attack
    away_defense = away_str.defense_away or away_str.defense
    away_attack = away_str.attack_away or away_str.attack
    home_defense = home_str.defense_home or home_str.defense

    if home_str.xg_attack_home is not None and away_str.xg_defense_away is not None:
        w = XG_BLEND
        home_attack = (1 - w) * home_attack + w * home_str.xg_attack_home
        away_defense = (1 - w) * away_defense + w * away_str.xg_defense_away
    if away_str.xg_attack_away is not None and home_str.xg_defense_home is not None:
        w = XG_BLEND
        away_attack = (1 - w) * away_attack + w * away_str.xg_attack_away
        home_defense = (1 - w) * home_defense + w * home_str.xg_defense_home

    lh_full = home_attack * away_defense * model.league_avg_home_goals
    la_full = away_attack * home_defense * model.league_avg_away_goals

    ref_factor = _get_referee_goal_factor(referee_name)
    lh_full *= ref_factor
    la_full *= ref_factor

    # Non-Homogeneous Poisson: scale λ by fraction of goals expected in remaining time.
    # Weights derived from 9,448 goals in 229K timeline events (empirical goal rate per bucket).
    # Goal rate accelerates mid-match (46-75 = x1.05-x1.12 of avg), decelerates early/late.
    _BUCKET_WEIGHTS = [
        (1,  15, 0.1388),
        (16, 30, 0.1586),
        (31, 45, 0.1624),
        (46, 60, 0.1745),
        (61, 75, 0.1861),
        (76, 95, 0.1797),  # includes injury time bucket
    ]
    minute = max(1, min(minute, 90))
    remaining_fraction = 0.0
    for lo, hi, w in _BUCKET_WEIGHTS:
        if minute >= hi:
            continue  # bucket fully elapsed
        if minute <= lo:
            remaining_fraction += w  # bucket fully remaining
        else:
            # partially elapsed: linear interpolation within bucket
            remaining_fraction += w * (hi - minute) / (hi - lo)

    remaining_fraction = max(remaining_fraction, 0.01)
    lh_rem = lh_full * remaining_fraction
    la_rem = la_full * remaining_fraction

    # Red card adjustment: 10-man team attacks less, opponent attacks more
    if home_red_cards > 0:
        lh_rem *= RED_CARD_ATTACK_PENALTY ** home_red_cards
        la_rem *= (1 / RED_CARD_DEFENSE_BOOST) ** home_red_cards
    if away_red_cards > 0:
        la_rem *= RED_CARD_ATTACK_PENALTY ** away_red_cards
        lh_rem *= (1 / RED_CARD_DEFENSE_BOOST) ** away_red_cards

    # Distribution of remaining goals
    n = MAX_GOALS
    max_rem = max(n - current_home_goals, 1)
    max_rem_a = max(n - current_away_goals, 1)
    hp_rem = np.array([poisson.pmf(k, lh_rem) for k in range(max_rem)])
    ap_rem = np.array([poisson.pmf(k, la_rem) for k in range(max_rem_a)])

    # Build final score matrix: final_home = current_home + rem_home
    mat = np.zeros((n, n))
    for rh in range(len(hp_rem)):
        for ra in range(len(ap_rem)):
            fh = current_home_goals + rh
            fa = current_away_goals + ra
            if fh < n and fa < n:
                mat[fh, fa] += hp_rem[rh] * ap_rem[ra]

    # Normalize
    total = mat.sum()
    if total > 0:
        mat /= total

    home_win = float(np.sum(np.tril(mat, k=-1)))
    draw = float(np.trace(mat))
    away_win = float(np.sum(np.triu(mat, k=1)))
    over_2_5 = float(sum(mat[i, j] for i in range(n) for j in range(n) if i + j > 2))
    btts = float(sum(mat[i, j] for i in range(n) for j in range(n) if i > 0 and j > 0))

    best = np.unravel_index(np.argmax(mat), mat.shape)
    flat = sorted(
        [(i, j, mat[i, j]) for i in range(n) for j in range(n)],
        key=lambda x: -x[2],
    )
    top5 = [(f"{i}-{j}", round(float(p), 4)) for i, j, p in flat[:5]]

    unknown = []
    if not home_key:
        unknown.append(f"'{home_team}' fora do dataset")
    if not away_key:
        unknown.append(f"'{away_team}' fora do dataset")
    confidence = "Alta" if (home_key and away_key) else ("Média" if (home_key or away_key) else "Baixa")
    red_note = ""
    if home_red_cards:
        red_note += f" 🟥{home_team}(x{home_red_cards})"
    if away_red_cards:
        red_note += f" 🟥{away_team}(x{away_red_cards})"
    note = (
        f"In-play (Non-Homogeneous Poisson) — {minute}' ({current_home_goals}-{current_away_goals}){red_note}. "
        f"λ restante: home={lh_rem:.2f}, away={la_rem:.2f} ({remaining_fraction:.1%} do jogo). "
        f"Base: {model.n_matches} jogos PL."
    )
    if unknown:
        note += " Atenção: " + "; ".join(unknown)

    return ScorePrediction(
        home_team=home_team, away_team=away_team,
        lambda_home=round(lh_rem, 3), lambda_away=round(la_rem, 3),
        home_win_prob=round(home_win, 4), draw_prob=round(draw, 4),
        away_win_prob=round(away_win, 4),
        over_2_5_prob=round(over_2_5, 4), btts_prob=round(btts, 4),
        most_likely_score=f"{best[0]}-{best[1]}",
        most_likely_score_prob=round(float(mat[best]), 4),
        top_scores=top5,
        score_matrix=[[round(float(mat[i, j]), 5) for j in range(n)] for i in range(n)],
        confidence=confidence, model_note=note,
    )
