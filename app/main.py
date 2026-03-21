from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.api.v1.endpoints.matches import router as matches_router

load_dotenv()

app = FastAPI(
    title="Scout — Premier League AI Assistant",
    description="Análise narrativa de partidas em tempo real. Dados → Interpretação → Insight.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches_router)


@app.get("/health")
async def health():
    return {"status": "ok", "league": "Premier League", "league_id": 535}