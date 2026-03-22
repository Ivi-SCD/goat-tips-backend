# Goat Tips — Premier League AI

> Análise narrativa e preditiva de partidas da Premier League em tempo real.

Combina dados ao vivo da BetsAPI, um modelo estatístico Poisson treinado em **4,495 jogos históricos** e LLM (Azure OpenAI GPT-4.1) para entregar interpretações contextualizadas — não apenas números.

**API ao vivo:** `http://4.157.187.122:8000` · **Docs:** `http://4.157.187.122:8000/docs`

---

## Arquitetura

```
Frontend (polling)
     │
     ▼
FastAPI — Azure Container Instance (goat-tips-api)
     ├── /matches      → BetsAPI: ao vivo, upcoming, H2H, stats, escalações
     ├── /predictions  → Modelo Poisson + Agente LangGraph + GPT-4.1
     └── /analytics    → Dataset histórico: 4,585 jogos, 86K stats, 229K eventos
          │
          ├── app/routers/          # Camada HTTP (rotas FastAPI)
          ├── app/services/         # Lógica de negócio
          │   ├── betsapi.py        # Cliente BetsAPI (async)
          │   ├── narrative.py      # Narrativa LLM em Português
          │   ├── predictor.py      # Modelo Poisson (Dixon-Coles)
          │   ├── analytics.py      # Analytics histórico
          │   └── llm_client.py     # Azure OpenAI client (singleton)
          ├── app/repositories/     # Acesso a dados
          │   ├── historical.py     # CSVs locais (in-memory pandas)
          │   └── database.py       # Supabase async (asyncpg)
          ├── app/agents/           # LangGraph orchestration
          │   ├── match_agent.py    # Graph builder + public API
          │   └── nodes.py          # AgentState + 3 nós do grafo
          └── app/schemas/          # Pydantic schemas por domínio
```

### Infraestrutura Azure

| Recurso | Tipo | Função |
|---|---|---|
| `goat-tips-api` | Container Instance | API FastAPI (porta 8000) |
| `goat-tips-azr-func-daily-update` | Function App (Flex) | Sync diário BetsAPI → Supabase |
| `goattips889c` | Storage Account | Artefatos do modelo (`models/` blob) |
| `goattipsacr` | Container Registry | Imagem Docker da API |
| `goat-tips-ai-frs` | Azure OpenAI | GPT-4.1 para narrativas |
| Supabase (us-west-2) | PostgreSQL | 4,585 jogos + 86K stats + 229K timeline |

---

## Instalação local

```bash
git clone <repo-url> && cd edscript
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure variáveis de ambiente
cp .env.example .env   # edite com suas chaves

# Treine o modelo (ou baixa do Azure Blob automaticamente)
python scripts/train_model.py

# Inicie o servidor
uvicorn app.main:app --reload --port 8000
```

Documentação interativa: **http://localhost:8000/docs**

---

## Endpoints

### `/matches` — Dados ao vivo (BetsAPI)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/matches/live` | Partidas ao vivo com odds e probabilidades |
| GET | `/matches/upcoming` | Próximas partidas com kick-off, árbitro, estádio |
| GET | `/matches/toplist` | Artilheiros e assistências da liga |
| GET | `/matches/{id}` | Contexto completo de uma partida |
| GET | `/matches/{id}/h2h` | Histórico de confrontos (BetsAPI) |
| GET | `/matches/{id}/stats-trend` | Momentum tático por período |
| GET | `/matches/{id}/lineup` | Escalações confirmadas |

### `/predictions` — Previsões (Poisson + LLM)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/predictions/?home=Arsenal&away=Chelsea` | Previsão Poisson por nome |
| GET | `/predictions/{id}` | Previsão Poisson via event_id ao vivo |
| GET | `/predictions/{id}/full-analysis` | **Análise completa (agente LangGraph)** |
| POST | `/predictions/{id}/narrative` | Narrativa LLM simples |
| POST | `/predictions/{id}/ask` | Pergunta livre sobre a partida |

### `/analytics` — Dataset histórico

| Método | Rota | Descrição |
|---|---|---|
| GET | `/analytics/teams` | Lista os 35 times do dataset |
| GET | `/analytics/teams/{name}/form` | Forma recente (últimos N jogos) |
| GET | `/analytics/teams/{name}/stats` | Win rate, clean sheets, BTTS |
| GET | `/analytics/h2h?home=X&away=Y` | H2H histórico (4,585 jogos) |
| GET | `/analytics/goal-patterns` | Distribuição de gols por minuto |
| GET | `/analytics/card-patterns` | Distribuição de cartões por minuto |
| GET | `/analytics/risk-scores` | Risk scores ao vivo (gol + cartão) |

---

## Modelo Preditivo

**Algoritmo:** Poisson Independente (inspirado em Dixon-Coles 1997)

```
λ_home = attack_home × defense_away × league_avg_home_goals
λ_away = attack_away × defense_home × league_avg_away_goals
P(score = i-j) = Poisson(λ_home, i) × Poisson(λ_away, j)
```

**Treinamento:** 4,495 jogos Premier League (março 2014 – março 2026)

**Outputs:** gols esperados · vitória/empate/derrota · placar mais provável · top 5 placares · Over 2.5 · BTTS · matriz 7×7

**Artefato:** `models/poisson_model.pkl` — armazenado no Azure Blob Storage (`goattips889c/models/`)

**Retreinamento semanal:** Azure Container Apps Job (`retrain/`) — toda segunda-feira 03:00 UTC, puxa dados do Supabase, treina e sobrescreve o blob.

---

## Agente LangGraph

O endpoint `/predictions/{id}/full-analysis` orquestra um grafo de 3 nós:

```
fetch_context      → asyncio.gather: match + h2h + stats_trend + lineup (paralelo)
fetch_historical   → asyncio.to_thread: team_form × 2 + Poisson + risk scores
generate_narrative → Azure OpenAI GPT-4.1 com contexto completo (~600 tokens)
```

Nós implementados em `app/agents/nodes.py`. Grafo e API pública em `app/agents/match_agent.py`.

---

## Supabase

Banco de dados PostgreSQL (us-west-2) com 6 tabelas:

| Tabela | Linhas |
|---|---|
| teams | 35 |
| events | 4,585 |
| match_stats | 86,554 |
| match_timeline | 229,549 |
| odds_snapshots | 20,092 |
| sync_log | — |

Views úteis: `v_matches` (join completo), `v_goal_timeline` (gols por minuto).

Azure Function `goat-tips-azr-func-daily-update` sincroniza novos jogos encerrados todo dia às 03:00 UTC.

---

## Dataset histórico

- **Fonte:** BetsAPI export histórico
- **Volume:** 4,585 jogos · 86K stats · 229K timeline events
- **Período:** 2014–2026
- **CSVs não são versionados** (ver `.gitignore`) — dados ao vivo via Supabase

---

## CI/CD — GitHub Actions

| Workflow | Trigger | Ação |
|---|---|---|
| `deploy-api.yml` | push `main` (app/ ou Dockerfile) | Build → push ACR → restart Container Instance |
| `deploy-azure-functions.yml` | push `main` (azure_functions/) | Deploy Flex Consumption via `functions-action` |

### Secrets necessários no GitHub

```
AZURE_CREDENTIALS              # az ad sp create-for-rbac output (JSON)
ACR_USERNAME                   # goattipsacr
ACR_PASSWORD                   # az acr credential show
BETSAPI_TOKEN
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
SUPABASE_DB_URL
AZURE_STORAGE_CONNECTION_STRING
```

Para gerar `AZURE_CREDENTIALS`:
```bash
az ad sp create-for-rbac \
  --name "scout-github-actions" \
  --role contributor \
  --scopes /subscriptions/<subscription-id>/resourceGroups/goat-tips \
  --sdk-auth
```

---

## Estrutura de arquivos

```
edscript/
├── app/
│   ├── agents/
│   │   ├── match_agent.py   # Graph builder + run_full_analysis()
│   │   └── nodes.py         # AgentState + fetch/narrative nodes
│   ├── core/settings.py
│   ├── repositories/
│   │   ├── historical.py    # CSVs → pandas (in-memory)
│   │   └── database.py      # Supabase async (asyncpg)
│   ├── routers/             # matches / predictions / analytics
│   ├── schemas/             # match / prediction / analytics / agent
│   └── services/
│       ├── betsapi.py
│       ├── llm_client.py    # Azure OpenAI singleton
│       ├── narrative.py
│       ├── predictor.py     # Poisson + Azure Blob fallback
│       └── analytics.py
├── azure_functions/         # Daily sync (Flex Consumption)
├── retrain/                 # Weekly retrain (Container Apps Job)
│   ├── retrain.py
│   ├── Dockerfile
│   └── deploy.sh
├── sql/schema.sql           # Supabase schema (6 tables + views)
├── scripts/
│   ├── train_model.py
│   └── migrate_csv_to_db.py
├── Dockerfile               # Main API image
├── .dockerignore
├── requirements.txt
└── docs/guia-api.md         # Guia completo em Português
```
