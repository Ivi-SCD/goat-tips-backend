# Goat Tips — Premier League AI

> Análise narrativa e preditiva de partidas da Premier League em tempo real.

Combina dados ao vivo da BetsAPI, um modelo estatístico Poisson treinado em **4,495 jogos históricos** e LLM (Azure OpenAI GPT-4.1) para entregar interpretações contextualizadas em Português — não apenas números brutos.

**API ao vivo:** `http://4.157.187.122:8000` · **Docs interativos:** `http://4.157.187.122:8000/docs`

---

## Arquitetura

```
Frontend (polling)
     │
     ▼
FastAPI — Azure Container Instance (goat-tips-api)
     ├── /matches      → BetsAPI tempo real: ao vivo, upcoming, H2H, stats, lineup
     ├── /predictions  → Modelo Poisson + Agente LangGraph + GPT-4.1
     └── /analytics    → Dataset histórico: 4,585 jogos, 86K stats, 229K eventos
          │
          ├── app/routers/          # Camada HTTP
          ├── app/services/
          │   ├── betsapi.py        # Cliente BetsAPI async (httpx)
          │   ├── predictor.py      # Modelo Poisson (Dixon-Coles)
          │   ├── analytics.py      # Business logic histórico
          │   ├── narrative.py      # Geração de narrativa LLM
          │   └── llm_client.py     # Azure OpenAI client singleton
          ├── app/repositories/
          │   ├── historical.py     # CSVs → pandas in-memory (lru_cache)
          │   └── database.py       # Supabase async (asyncpg)
          ├── app/agents/
          │   ├── match_agent.py    # LangGraph graph builder + API pública
          │   └── nodes.py          # AgentState + 3 nós do grafo
          └── app/schemas/          # Pydantic schemas por domínio
```

### Infraestrutura Azure

| Recurso | Tipo | Função |
|---|---|---|
| `goat-tips-api` | Container Instance | API FastAPI — porta 8000 |
| `goat-tips-azr-func-daily-update` | Function App Flex | Sync diário BetsAPI → Supabase, 03:00 UTC |
| `goattips889c` | Storage Account | Artefato do modelo (`models/poisson_model.pkl`) |
| `goattipsacr` | Container Registry | Imagem Docker `goattips-backend-api:latest` |
| `goat-tips-ai-frs` | Azure OpenAI | GPT-4.1 — narrativas em Português |
| Supabase (us-west-2) | PostgreSQL | 4,585 jogos · 86K stats · 229K timeline · 20K odds |

---

## Decisões de Dados

### Por que BetsAPI?

A BetsAPI é nossa fonte de dados em tempo real. Ela entrega eventos ao vivo com placar, minuto, odds e estatísticas táticas por requisição. Os endpoints principais usados:

| Endpoint BetsAPI | Uso |
|---|---|
| `/v1/events/inplay` | Partidas ao vivo |
| `/v1/events/upcoming` | Próximas partidas |
| `/v1/event/view` | Contexto completo de uma partida |
| `/v1/event/history` | H2H entre dois times |
| `/v1/event/stats_trend` | Momentum tático por período |
| `/v1/event/lineup` | Escalações |
| `/v2/event/odds/summary` | Odds pré e ao vivo |
| `/league/toplist` | Artilheiros da liga |

As odds são buscadas em paralelo via `asyncio.gather` — sem isso, 50 partidas × 1 requisição = timeout garantido.

### Dataset histórico

Exportamos **4,585 jogos da Premier League (2014–2026)** da BetsAPI para CSV. Esse dataset é a base de treino do modelo Poisson e do módulo de analytics histórico.

| Arquivo | Linhas | Uso |
|---|---|---|
| `premier_league_events.csv` | 4,585 | Base de treino + analytics |
| `premier_league_stats.csv` | 86,554 | Estatísticas por métrica e período |
| `premier_league_timeline.csv` | 229,549 | Eventos de gol/cartão por minuto |
| `premier_league_odds.csv` | 4.5M | **Não migrado** — ficou local (563 MB) |

### Por que não migramos os 4.5M de odds para o Supabase?

Os 4.5M de linhas são uma série temporal de odds (cada mudança de cotação ao longo do tempo). O que importa para o modelo é o **snapshot final** — a odd de fechamento antes do apito. Migramos apenas os **20,092 snapshots finais** (1 por partida × mercado) para a tabela `odds_snapshots`. A série temporal completa fica local para análise eventual.

### Supabase como camada de persistência

O Supabase (PostgreSQL) armazena os dados históricos e os novos jogos sincronizados diariamente pelo Azure Function. A camada `app/repositories/historical.py` é isolada como o único ponto de acesso aos dados históricos — para migrar de CSV para Supabase basta substituir as funções nesse arquivo sem tocar em services ou routers.

**Tabelas:**

| Tabela | Linhas | Descrição |
|---|---|---|
| `teams` | 35 | Times com ID BetsAPI |
| `events` | 4,585 | Partidas com placar, árbitro, estádio |
| `match_stats` | 86,554 | Stats por métrica e período |
| `match_timeline` | 229,549 | Gols e cartões com minuto |
| `odds_snapshots` | 20,092 | Odds de fechamento por partida e mercado |
| `sync_log` | — | Histórico de execuções do Azure Function |

**Views úteis:**
- `v_matches` — join completo com nomes dos times
- `v_goal_timeline` — gols por minuto (filtra ruído: só linhas `N' - Goal...`)

---

## Decisões de Modelagem

### Por que Poisson e não Machine Learning?

Cogitamos usar gradient boosting (XGBoost/LightGBM) mas o dataset de 4,495 jogos é relativamente pequeno para features ricas. O modelo Poisson tem três vantagens práticas:

1. **Interpretabilidade** — os parâmetros `attack` e `defense` de cada time são intuitivos e explicáveis ao usuário
2. **Generalização** — funciona bem com times recém-promovidos que têm poucos jogos
3. **Saída probabilística natural** — a distribuição Poisson entrega probabilidade de cada placar diretamente, sem calibração adicional

### Como o modelo funciona (Dixon-Coles 1997)

```
λ_home = attack_home × defense_away × league_avg_home_goals
λ_away = attack_away × defense_home × league_avg_away_goals

P(placar = i-j) = Poisson(λ_home, i) × Poisson(λ_away, j)
```

1. Para cada time, calculamos força de ataque e defesa normalizadas pela média da liga
2. Os λ (gols esperados) são calculados cruzando ataque do mandante com defesa do visitante
3. Geramos uma matriz 7×7 de probabilidades de placar (0–6 gols por time)
4. Da matriz extraímos: placar mais provável, top 5 placares, Over 2.5, BTTS, vitória/empate/derrota

**Limitações conhecidas:**
- Não aplica correção de baixo placar de Dixon-Coles (simplificação consciente)
- Não pondera jogos recentes com mais peso (todos os jogos valem igual)
- Não modela desfalques ou lesões

### Ciclo de retreinamento

O modelo é re-treinado toda segunda-feira às 03:00 UTC por um **Azure Container Apps Job** (`retrain/`). O job:
1. Puxa os jogos encerrados do Supabase (sempre atualizado pelo Azure Function)
2. Re-calcula as forças de ataque e defesa de todos os times
3. Serializa com `joblib` e faz upload para Azure Blob Storage (`goattips889c/models/poisson_model.pkl`)
4. Na próxima requisição, o `predictor.py` baixa o novo pkl automaticamente

### Fallback em cadeia do predictor

```
1. Carrega models/poisson_model.pkl local (mais rápido)
2. Tenta download do Azure Blob Storage (quando não tem pkl local)
3. Treina inline a partir do CSV (último recurso)
```

---

## Agente LangGraph

O endpoint `/predictions/{id}/full-analysis` é o coração do produto. Orquestra um grafo de 3 nós:

```
fetch_context ─────────────────────────────────────────────────
  asyncio.gather (paralelo):
    ├── get_match_by_id(event_id)    → placar, odds, árbitro, estádio
    ├── get_h2h(event_id)            → histórico de confrontos BetsAPI
    ├── get_stats_trend(event_id)    → momentum tático por período
    └── get_lineup(event_id)         → escalações confirmadas
                    │
fetch_historical ───────────────────────────────────────────────
  asyncio.to_thread (pandas, não bloqueia o event loop):
    ├── get_team_form(home, 10)      → últimos 10 jogos do mandante
    ├── get_team_form(away, 10)      → últimos 10 jogos do visitante
    ├── predict_from_match_context() → Poisson: λ, placares, probs
    ├── calculate_goal_risk_score()  → risco de gol nos próximos 15 min
    └── calculate_card_risk_score()  → risco de cartão
                    │
generate_narrative ─────────────────────────────────────────────
  Azure OpenAI GPT-4.1 (~600 tokens de contexto):
    → headline + analysis + prediction + momentum_signal
    → resposta sempre em Português
```

**Por que `asyncio.gather` dentro de um único nó e não nós paralelos no LangGraph?**
O LangGraph suporta fan-out nativo mas adiciona overhead de serialização de estado. Para 4 chamadas I/O que retornam em ~2s, `asyncio.gather` dentro do nó é mais simples e igualmente eficiente.

---

## Instalação local

```bash
git clone <repo-url> && cd edscript
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure variáveis de ambiente
cp .env.example .env
# BETSAPI_TOKEN, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
# SUPABASE_DB_URL, AZURE_STORAGE_CONNECTION_STRING

# Treina o modelo (ou baixa do Blob automaticamente na 1ª requisição)
python scripts/train_model.py

# Servidor local
uvicorn app.main:app --reload --port 8000
```

---

## Deploy (CLI)

### API (Container Instance)

```bash
# 1. Build e push da imagem
docker build -t goattipsacr.azurecr.io/goattips-backend-api:latest .
az acr login --name goattipsacr
docker push goattipsacr.azurecr.io/goattips-backend-api:latest

# 2. Recriar o container com a nova imagem
az container delete --name goat-tips-api --resource-group goat-tips --yes
az container create \
  --name goat-tips-api \
  --resource-group goat-tips \
  --image goattipsacr.azurecr.io/goattips-backend-api:latest \
  --registry-login-server goattipsacr.azurecr.io \
  --registry-username goattipsacr \
  --registry-password <ACR_PASSWORD> \
  --cpu 1 --memory 2 \
  --ports 8000 --ip-address Public \
  --environment-variables \
    BETSAPI_TOKEN=<token> \
    AZURE_OPENAI_ENDPOINT=<endpoint> \
    AZURE_OPENAI_API_KEY=<key> \
    AZURE_OPENAI_MODEL=gpt-4.1 \
    SUPABASE_DB_URL=<url> \
    AZURE_STORAGE_CONTAINER=models \
    MODEL_BLOB_NAME=poisson_model.pkl \
    PREMIER_LEAGUE_ID=94 \
  --secure-environment-variables \
    AZURE_STORAGE_CONNECTION_STRING=<conn_str>
```

### Azure Function (daily sync)

```bash
cd azure_functions
zip -r ../azure_functions.zip . \
  --exclude "*.pyc" --exclude "*/__pycache__/*" \
  --exclude "local.settings.json" --exclude "*.zip"

# Deploy via Flex Consumption (requer func CLI v4+)
func azure functionapp publish goat-tips-azr-func-daily-update
```

### Migração de dados

```bash
# Aplique o schema no Supabase SQL editor:
sql/schema.sql

# Migre os CSVs para o Supabase (idempotente):
python scripts/migrate_csv_to_db.py

# Retreine o modelo manualmente:
python scripts/train_model.py
```

---

## Endpoints

### `/matches` — Tempo real

| Método | Rota | Descrição |
|---|---|---|
| GET | `/matches/live` | Partidas ao vivo com odds e probabilidades |
| GET | `/matches/upcoming` | Próximas partidas com kick-off, árbitro, estádio |
| GET | `/matches/toplist` | Artilheiros e assistências da liga |
| GET | `/matches/{id}` | Contexto completo de uma partida |
| GET | `/matches/{id}/h2h` | Histórico H2H via BetsAPI |
| GET | `/matches/{id}/stats-trend` | Momentum tático por período |
| GET | `/matches/{id}/lineup` | Escalações confirmadas |

### `/predictions` — Modelo + LLM

| Método | Rota | Descrição |
|---|---|---|
| GET | `/predictions/?home=Arsenal&away=Chelsea` | Previsão Poisson por nome (sem partida ao vivo) |
| GET | `/predictions/{id}` | Previsão Poisson via event_id |
| GET | `/predictions/{id}/full-analysis` | **Análise completa — agente LangGraph** |
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

## Estrutura de arquivos

```
edscript/
├── app/
│   ├── agents/
│   │   ├── match_agent.py   # Graph LangGraph + run_full_analysis()
│   │   └── nodes.py         # AgentState TypedDict + 3 nós
│   ├── core/settings.py     # Pydantic Settings — todas as env vars
│   ├── db/
│   │   ├── connection.py    # Pool asyncpg
│   │   └── models.py        # SQLAlchemy ORM
│   ├── repositories/
│   │   ├── historical.py    # CSVs → pandas (lru_cache, in-memory)
│   │   └── database.py      # Supabase upsert bulk (asyncpg)
│   ├── routers/             # matches / predictions / analytics
│   ├── schemas/             # match / prediction / analytics / agent
│   └── services/
│       ├── betsapi.py       # Cliente BetsAPI async — todos os endpoints
│       ├── llm_client.py    # Azure OpenAI singleton + SYSTEM_PROMPT
│       ├── narrative.py     # Contexto enriquecido + chamada LLM
│       ├── predictor.py     # Poisson + fallback Blob + fallback inline
│       └── analytics.py     # Lógica analytics histórico
├── azure_functions/         # Daily sync — Flex Consumption
│   ├── function_app.py      # Python v2: timer + HTTP trigger
│   └── sync_logic.py        # Fetch BetsAPI + upsert Supabase (psycopg2)
├── retrain/                 # Retreinamento semanal — Container Apps Job
│   ├── retrain.py           # Puxa Supabase → treina → upload Blob
│   ├── Dockerfile
│   └── deploy.sh            # az containerapp job create
├── scripts/
│   ├── train_model.py       # Treina e serializa o modelo localmente
│   ├── migrate_csv_to_db.py # Migra CSVs → Supabase (idempotente)
│   └── test_routes.py       # Testa todos os endpoints com rich output
├── sql/schema.sql           # Schema Supabase completo (6 tabelas + views)
├── docs/guia-api.md         # Guia de consumo da API em Português
├── Dockerfile               # Imagem da API principal
├── .dockerignore            # Exclui odds timeseries (563 MB)
└── requirements.txt
```
