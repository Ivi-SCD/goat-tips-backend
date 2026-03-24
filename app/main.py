from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.routers import matches_router, predictions_router, analytics_router, telegram_router

load_dotenv()

app = FastAPI(
    title="Goat Tips — Premier League AI",
    description=(
        "Análise narrativa e preditiva de partidas da Premier League em tempo real.\n\n"
        "## Módulos\n"
        "| Prefixo | Responsabilidade |\n"
        "|---|---|\n"
        "| `/matches` | Partidas ao vivo, futuras, H2H, stats, escalações |\n"
        "| `/predictions` | Modelo Poisson + análise completa do agente LangGraph |\n"
        "| `/analytics` | Dataset histórico: forma, padrões de gol/cartão, risco |\n"
        "| `/telegram` | Integração Telegram: webhook, configuração e respostas via bot |\n"
    ),
    version="0.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches_router)
app.include_router(predictions_router)
app.include_router(analytics_router)
app.include_router(telegram_router)


@app.get("/health", tags=["Sistema"])
async def health():
    return {
        "status": "ok",
        "app": "Goat Tips",
        "version": "0.3.0",
        "league": "Premier League",
        "betsapi_league_id": 94,
        "modules": {
            "matches": "/matches",
            "predictions": "/predictions",
            "analytics": "/analytics",
        },
    }
