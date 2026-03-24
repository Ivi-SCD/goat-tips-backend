# Goat Tips — Premier League AI

> Análise narrativa e preditiva de partidas da Premier League em tempo real.

Combina dados ao vivo da BetsAPI, um modelo estatístico Poisson treinado em **4,495 jogos históricos** e LLM (Groq — moonshotai/kimi-k2-instruct) para entregar interpretações contextualizadas em Português — não apenas números brutos.

**API ao vivo:** `https://goat-tips-backend-api.27s4ihbbhmjf.us-east.codeengine.appdomain.cloud` · **Docs interativos:** `.../docs`

---

## Arquitetura

```
Frontend (polling)
     │
     ▼
FastAPI — IBM Code Engine (goat-tips-backend-api, us-east)
     ├── /matches      → BetsAPI tempo real: ao vivo, upcoming, H2H, stats, lineup
     ├── /predictions  → Modelo Poisson + Agente LangGraph + Groq LLM
     ├── /analytics    → Dataset histórico: 4,585 jogos, 86K stats, 229K eventos
     └── /telegram     → Bot Telegram: webhook + histórico de conversa por usuário
          │
          ├── app/routers/          # Camada HTTP
          ├── app/services/
          │   ├── betsapi.py        # Cliente BetsAPI async (httpx)
          │   ├── predictor.py      # Modelo Poisson (Dixon-Coles) + fallback IBM COS
          │   ├── analytics.py      # Business logic histórico
          │   ├── narrative.py      # Geração de narrativa LLM
          │   ├── llm_client.py     # Groq client singleton (openai SDK)
          │   ├── conversation.py   # Histórico de sessão no Supabase (JSONB)
          │   ├── search.py         # Vertex AI Search (Google Discovery Engine)
          │   ├── telegram.py       # Cliente Telegram Bot API (httpx)
          │   └── tools.py          # 7 ferramentas LLM + dispatcher async
          ├── app/repositories/
          │   ├── historical.py     # CSVs → pandas in-memory (lru_cache)
          │   └── database.py       # Supabase async (asyncpg)
          ├── app/agents/
          │   ├── match_agent.py    # LangGraph graph builder + API pública
          │   └── nodes.py          # AgentState + 3 nós do grafo
          └── app/schemas/          # Pydantic schemas por domínio
```

![Arquitetura do Sistema](docs/diagrams/assets/01-system-architecture.svg)

![Arquitetura em Camadas](docs/diagrams/assets/04-layered-architecture.svg)

![Dependências entre Módulos](docs/diagrams/assets/03-module-dependencies.svg)

### Infraestrutura IBM Cloud

| Recurso | Tipo | Função |
|---|---|---|
| `goat-tips-backend-api` | Code Engine Application (us-east) | API FastAPI — HTTPS público |
| `goat-tips-daily-sync` | Code Engine Job | Sync diário BetsAPI → Supabase, `0 3 * * *` UTC |
| `goat-tips-retrain` | Code Engine Job | Retreinamento do modelo, `0 3 * * 1` (toda segunda) — usa a mesma imagem do backend |
| `icr.io/goat-tips-ns` | IBM Container Registry (global) | 2 imagens: `goat-tips-backend` (API + retrain bundled) e `goat-tips-daily-sync` |
| `goat-tips-bucket` | IBM Cloud Object Storage (us-south) | Artefato do modelo (`poisson_model.pkl`) |
| Groq API | LLM (moonshotai/kimi-k2-instruct) | Narrativas em Português — 131K contexto, 1T MoE |
| Supabase (us-west-2) | PostgreSQL | 4,585 jogos · 86K stats · 229K timeline · 20K odds · histórico de chat |

![Infraestrutura IBM Cloud](docs/diagrams/assets/02-infrastructure.svg)

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

O Supabase (PostgreSQL) armazena os dados históricos, os novos jogos sincronizados diariamente pelo CE Job e o histórico de conversas dos usuários. A camada `app/repositories/historical.py` é isolada como o único ponto de acesso aos dados históricos.

**Tabelas:**

| Tabela | Linhas | Descrição |
|---|---|---|
| `teams` | 35 | Times com ID BetsAPI |
| `events` | 4,585 | Partidas com placar, árbitro, estádio |
| `match_stats` | 86,554 | Stats por métrica e período |
| `match_timeline` | 229,549 | Gols e cartões com minuto |
| `odds_snapshots` | 20,092 | Odds de fechamento por partida e mercado |
| `sync_log` | — | Histórico de execuções do CE Job diário |
| `conversation_sessions` | — | Histórico de chat por `(session_id, event_id)` — JSONB |
| `team_player_strength_snapshot` | 35 | FBref: attack/creation/defensive index por time e temporada |
| `team_style_snapshot_statsbomb` | 35 | StatsBomb: avg_goals, clean_sheet_rate, btts_rate por time |
| `player_absence_impact` | ~350 | Top-10 jogadores por time com impact_score (0–10) |

**Views úteis:**
- `v_matches` — join completo com nomes dos times
- `v_goal_timeline` — gols por minuto (filtra ruído: só linhas `N' - Goal...`)

![Diagrama ER — Banco de Dados](docs/diagrams/assets/15-database-er.svg)

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

**Melhorias implementadas (v0.5.0) — 8 features no total:**

| Código | Feature | Impacto |
|--------|---------|---------|
| P1 | **Dixon-Coles ρ=-0.05** (cross-validado em 1000 jogos) | Corrige subestimação dos placares 0-0, 1-0, 0-1, 1-1 |
| P2 | **Time-decay** (half-life 1 ano) | Jogos recentes têm mais peso exponencialmente |
| P3 | **Ataque/defesa separados por mando** | `attack_home`, `attack_away`, `defense_home`, `defense_away` por time |
| P4 | **xG blend (40%)** | 1,291 jogos com xG (2022–2026): `λ = 0.6 × goals_strength + 0.4 × xg_strength` |
| P5 | **Referee goal factor** | Multiplica λ pelo desvio histórico do árbitro em relação à média da liga |
| P6 | **In-play Non-Homogeneous Poisson** | Taxa de gol empírica por bucket de 15 min (9,448 gols) + suporte a cartão vermelho |
| P7 | **Matriz normalizada** | `mat /= mat.sum()` após correção Dixon-Coles |
| P8 | **Calibração com Brier Score** | Endpoint de backtesting com comparação predicted vs actual |
| P9 | **Weather adjustment** | Open-Meteo API: chuva/neve/vento reduzem λ em 4–15% por jogo |
| P10 | **Half-time prediction** | Previsão de intervalo embutida em toda resposta: HT win/draw/loss + Over 0.5/1.5 |

### Feature P4 — xG Blend (40%)

O xG ("expected goals") é uma métrica de qualidade de chute. Para times com dados suficientes (≥10 jogos com xG), blendamos as forças de ataque/defesa:

```python
XG_BLEND = 0.4  # 40% xG, 60% goals

home_attack = (1 - XG_BLEND) × goals_attack_home + XG_BLEND × xg_attack_home
away_defense = (1 - XG_BLEND) × goals_defense_away + XG_BLEND × xg_defense_away
```

**Por que 40%?** O xG é um melhor preditor de resultados futuros do que gols brutos (especialmente em jogos com pouco volume), mas não captura completamente a "finishing ability" de times como Arsenal e City. O blend 60/40 equilibra ambos.

**Cobertura:** 25 dos 35 times têm xG, cobrindo 1,291 jogos de 2022–2026. Times sem xG usam 100% das forças baseadas em gols.

### Feature P5 — Referee Goal Factor

Árbitros diferem significativamente em como conduzem as partidas. O fator de árbitro ajusta os λ multiplicativamente:

```python
ref_factor = avg_goals_with_referee / league_avg_goals

# Exemplo real — 3,715 jogos com árbitros identificados:
# Michael Oliver (279 jogos): factor ≈ 1.03 → +3% gols (árbitro permissivo)
# Stuart Attwell:  factor ≈ 0.96 → -4% gols (árbitro rigoroso)

lh *= ref_factor  # ajusta λ_home
la *= ref_factor  # ajusta λ_away
```

**Threshold de confiança:** apenas árbitros com ≥20 jogos no dataset recebem fator. Os demais usam `factor=1.0`. Cobertura: **82.6% dos jogos** (3,715 de 4,495).

**Ativação:** passe `?referee=Michael+Oliver` no endpoint de previsão ou use o `event_id` de um jogo ao vivo (o árbitro é extraído automaticamente da BetsAPI).

### Feature P6 — In-play Non-Homogeneous Poisson

A previsão pré-jogo torna-se obsoleta após o primeiro gol. O modelo in-play recalcula:

```
P(resultado final | placar atual, minuto, cartões vermelhos)
```

**Algoritmo:**
1. Computa λ_home e λ_away pré-jogo (incluindo xG, árbitro e clima)
2. Usa **taxa de gol empírica por bucket de 15 min** (não taxa constante) derivada de 9,448 gols:

```python
# Fração de gols por período — derivado de 229K timeline events
_BUCKET_WEIGHTS = [
    (1,  15, 0.1388),   # 13.9% dos gols
    (16, 30, 0.1586),   # 15.9%
    (31, 45, 0.1624),   # 16.2%
    (46, 60, 0.1745),   # 17.5%
    (61, 75, 0.1861),   # 18.6% — pico!
    (76, 95, 0.1797),   # 18.0% (inclui acréscimos)
]
```

3. Interpola linearmente dentro do bucket atual para precisão sub-minuto
4. Aplica penalidade de cartão vermelho: equipe com 10 jogadores → `λ_ataque × 0.72^N`, adversário `× (1/0.90)^N`
5. Constrói matriz de placar final deslocando pelo placar atual e normaliza

**Exemplo — Arsenal 1-0 Chelsea, Michael Oliver:**

| Momento | Home Win | Draw | Away Win |
|---------|----------|------|----------|
| Pré-jogo | 54.8% | 22.1% | 23.1% |
| 30' | 77.7% | 15.8% | 6.5% |
| 70' | 84.9% | 13.4% | 1.7% |
| 85' | 94.7% | 5.2% | 0.2% |

**Com cartão vermelho do Arsenal no 70':** home_win cai de 84.9% → ~71% (λ_rem × 0.72)

### Feature P9 — Weather Adjustment

O clima afeta o ritmo de jogo: chuva pesada reduz passes curtos, vento forte altera trajetórias. Integramos a **Open-Meteo API** (gratuita, sem chave) para ajustar os λ em tempo real.

```python
# Multiplicadores de goal factor por condição climática:
# clear/cloudy: 1.00 (sem impacto)
# drizzle:      0.96  (-4%)
# rain:         0.88–0.92 (-8% a -13%, proporcional à precipitação mm)
# snow:         0.88  (-12%)
# storm:        0.85  (-15%)
# wind > 40km/h: -5% adicional
# wind > 25km/h: -2% adicional
# floor: 0.75  (máximo -25%)

lh *= weather_factor  # aplicado após referee_factor
la *= weather_factor
```

**Cobertura:** 33 estádios da PL com coordenadas precisas + 25 cidades como fallback. Suporta previsão horária para o kick-off exato (`?match_hour_utc=15`).

**Ativação:** passe `?stadium=Emirates+Stadium` ou `?city=London` no endpoint de previsão. Também disponível isolado em `GET /analytics/weather`.

### Feature P10 — Half-time Prediction

Toda resposta de previsão agora inclui o objeto `half_time` com probabilidades para o **intervalo** (placar ao fim de 45 minutos):

```python
_FH_FRACTION = 0.4598  # 46% dos gols ocorrem no 1T (derivado de 9,448 gols)

lh_fh = lh_full × 0.4598  # λ do 1T
la_fh = la_full × 0.4598

# Retorna:
{
  "home_win_prob": ...,    # mandante vencendo no intervalo
  "draw_prob":     ...,    # empate no intervalo
  "away_win_prob": ...,    # visitante vencendo no intervalo
  "over_0_5_prob": ...,    # ao menos 1 gol no 1T
  "over_1_5_prob": ...,    # ao menos 2 gols no 1T
  "most_likely_score": "0-0",
  "lambda_home": ...,      # gols esperados do mandante no 1T
  "lambda_away": ...
}
```

Útil para mercados de **resultado ao intervalo** e **gols no 1T** em plataformas de apostas.

### Feature P8 — Calibração e Backtesting

O endpoint `GET /analytics/model/calibration?n=500` executa backtesting nos últimos N jogos encerrados:

**Resultados em 500 jogos (cross-validação leave-last-N-out):**

| Mercado | Brier Score | Calibração |
|---------|-------------|------------|
| Home Win | 0.2161 | Boa |
| Draw | 0.1886 | Boa |
| Away Win | 0.2003 | Boa |
| Over 2.5 | 0.2387 | Boa |
| BTTS | 0.2431 | Boa |

**Brier Score** = erro quadrático médio entre probabilidade prevista e resultado real (0 = perfeito, 0.25 = modelo sem discriminação, menor = melhor). Scores ≤0.24 indicam modelo com poder preditivo real.

### Ciclo de retreinamento

O modelo é re-treinado toda segunda-feira às 03:00 UTC pelo **IBM Code Engine Job** (`goat-tips-retrain`). O job usa a **mesma imagem Docker da API** (`goat-tips-backend:latest`) — sem imagem separada, economizando 215 MB no ICR.

```
1. psycopg2 → Supabase → 4,495 jogos encerrados
2. query xG → match_stats WHERE metric='xg' (1,291 registros)
3. Treina forças home/away split por time
4. xG blend para times com ≥10 jogos com xG (25 times)
5. [NOVO v2.1] Carrega data/kaggle/players_data_2025_2026.csv (FBref)
   → computa por time: attack_index, creation_index, defensive_index, squad_depth
   → enriquece team_strengths no pkl
6. [NOVO v2.1] Upsert 3 snapshot tables no Supabase:
   → team_player_strength_snapshot (attack/creation/defensive index por temporada)
   → team_style_snapshot_statsbomb (avg_goals, clean_sheet_rate, btts_rate)
   → player_absence_impact (top-10 jogadores por time com impact_score 0–10)
7. Serializa com joblib compress=3 → poisson_model.pkl
8. ibm_boto3 → upload para IBM COS (goat-tips-bucket)
9. Na próxima requisição, predictor.py baixa o novo pkl via IBM COS
   Na próxima pergunta ao /ask, player_intel agent consulta os snapshots
```

**Kaggle/FBref player indices** — o que cada índice mede:
| Índice | Métricas FBref | O que representa |
|--------|---------------|-----------------|
| `attack_index` | `(Gls + xG)` ponderado por 90s | Poder ofensivo do elenco |
| `creation_index` | `(Ast + xAG + KP + PrgP)` ponderado | Criatividade e progressão |
| `defensive_index` | `(Tkl+Int + Blocks + Clr)` ponderado | Solidez defensiva coletiva |
| `squad_depth` | nº de jogadores com ≥5 90s | Profundidade de banco |

**Novo env var requerido:** `KAGGLE_PLAYERS_CSV=data/kaggle/players_data_2025_2026.csv`

### Fallback em cadeia do predictor

```
1. Carrega models/poisson_model.pkl local (mais rápido — <1ms)
2. Tenta download do IBM COS (quando não tem pkl local — ~2s)
3. Treina inline a partir do CSV com time-decay e split home/away (último recurso — ~5s)
```

![Carregamento do Modelo Poisson](docs/diagrams/assets/06-poisson-model-loading.svg)

![Sequência — Retreinamento Semanal](docs/diagrams/assets/12-sequence-model-retrain.svg)

---

## Agente LangGraph

O endpoint `/predictions/{id}/full-analysis` é o coração do produto. Orquestra um grafo de 3 nós:

```
START
  │
  ▼
fetch_context ─────────────────────────────────────────────────
  asyncio.gather (paralelo):
    ├── get_match_by_id(event_id)    → placar, odds, árbitro, estádio
    ├── get_h2h(event_id)            → histórico de confrontos BetsAPI
    ├── get_stats_trend(event_id)    → momentum tático por período
    └── get_lineup(event_id)         → escalações confirmadas
  │
  ├─[no_match]──────────────────────────────────────────► END
  │
  ▼ [ok]
fetch_historical ───────────────────────────────────────────────
  asyncio.to_thread (pandas, não bloqueia o event loop):
    ├── get_team_form(home, 10)      → últimos 10 jogos do mandante
    ├── get_team_form(away, 10)      → últimos 10 jogos do visitante
    ├── predict_from_match_context() → Poisson: λ, placares, probs
    ├── calculate_goal_risk_score()  → risco de gol nos próximos 15 min
    └── calculate_card_risk_score()  → risco de cartão
  │
  ├─[skip_narrative]────────────────────────────────────► END
  │
  ▼ [ok]
generate_narrative ─────────────────────────────────────────────
  Groq (moonshotai/kimi-k2-instruct — 131K contexto):
    → headline + analysis + prediction + momentum_signal
    → resposta sempre em Português
  │
  ▼
END
```

**Conditional edges (v0.5.0):**
- `fetch_context → "no_match" → END`: aborta se a partida não foi encontrada na BetsAPI
- `fetch_historical → "skip_narrative" → END`: pula narrativa se não há match nem prediction
- Evita chamadas desnecessárias ao LLM e erros em cascata

![Fluxo — Full Analysis](docs/diagrams/assets/05-full-analysis-flow.svg)

![Sequência — Full Analysis](docs/diagrams/assets/10-sequence-full-analysis.svg)

**Por que `asyncio.gather` dentro de um único nó e não nós paralelos no LangGraph?**
O LangGraph suporta fan-out nativo mas adiciona overhead de serialização de estado. Para 4 chamadas I/O que retornam em ~2s, `asyncio.gather` dentro do nó é mais simples e igualmente eficiente.

---

## Agente Ask — Supervisor Multi-Agente

Os endpoints `/predictions/ask` e `/predictions/{id}/ask` usam um **pipeline LangGraph de 6 agentes** (substituiu o loop de ferramentas em v0.6.0). Cada agente tem foco único, timeout de 1.8s e retorna um `AgentArtifact` com `confidence`, `payload` e `citations`.

```
intent_router
    │
    ▼
parallel_gather ─────────────────────────────────────────────────
  asyncio.gather (3 sub-agentes em paralelo, timeout 1.8s cada):
    ├── live_context     → BetsAPI: ao vivo, upcoming, odds
    ├── historical_stats → Supabase/CSV: form, H2H, stats
    └── player_intel     → Supabase snapshots (FBref): attack_index,
                           creation_index, player_absence_impact
    │
    ▼
quant_agent ─────────────────────────────────────────────────────
  → Modelo Poisson: λ_home, λ_away, probabilidades, placar
    │
    ▼
narrative_verifier ──────────────────────────────────────────────
  Groq LLM: sintetiza todos os artifacts em Português
  → headline + analysis + prediction + momentum_signal + confidence_label
    │
    ▼
END
```

**SLAs de resiliência:**
- Per-agent timeout: **1.8s** — agente falho retorna artifact vazio com `confidence="low"`
- Total graph budget: **6.5s** (asyncio.wait_for)
- Degraded mode: `partial_context=True` quando qualquer agente falha/timeout — resposta ainda é entregue com fontes disponíveis

**Roteamento por intenção (IntentRouterAgent):**

| Intent | Gatilho | Sub-agentes priorizados |
|--------|---------|------------------------|
| `FORM_ODDS` | "forma recente", "odds", "mercado" | live_context + historical_stats |
| `INJURIES` | "lesão", "desfalque", "suspenso" | player_intel + live_context |
| `HISTORICAL` | "H2H", "histórico", "confronto" | historical_stats |
| `PLAYER` | "jogador", "artilheiro", stats individuais | player_intel |
| `TACTICAL` | "tática", "formação", "estilo" | historical_stats + player_intel |
| `PREDICTION` | "prever", "placar", "probabilidade" | quant_agent |
| `GENERAL` | qualquer outra coisa | todos |

**Observabilidade no response** — todos os campos opcionais, backward-compatible:
```json
{
  "confidence_score": 0.87,
  "data_sources": ["live_context", "historical_stats", "player_intel", "quant"],
  "partial_context": false,
  "agent_trace_id": "a3f2e1..."
}
```

### Ferramentas disponíveis (8)

O `quant_agent` e o `narrative_verifier` também têm acesso às ferramentas abaixo para chamadas ad-hoc:

| Ferramenta | Fonte | Quando usa |
|---|---|---|
| `web_search` | Vertex AI Search (Google Cloud) | Árbitros, lesões, notícias, previews |
| `get_team_form` | Dataset histórico CSV | Forma recente de um time |
| `get_team_stats` | Dataset histórico CSV | Win rate, clean sheets, BTTS |
| `get_h2h_stats` | Dataset histórico CSV | Histórico de confrontos diretos |
| `get_upcoming_odds` | BetsAPI tempo real | Próximos jogos + odds |
| `get_team_profile` | Stats + Timeline CSV | Shot efficiency, xG, gols por half |
| `get_referee_stats` | Events + Stats CSV | Média de cartões e faltas por árbitro |
| `get_player_intel` | Supabase snapshots (FBref) | `attack_index`, `creation_index`, `squad_depth`, ausências |

O sistema de aliases (`_normalize()`) mapeia automaticamente nomes como "Manchester City" → "Man City", "Nottingham Forest" → "Nottm Forest", garantindo consultas corretas ao dataset.

---

## Bot Telegram

A integração Telegram expõe o pipeline `/ask` diretamente no chat. Qualquer mensagem enviada ao bot é processada pelo `answer_general_question` (mesmo agente do endpoint `POST /predictions/ask`) e a resposta é devolvida formatada em HTML.

### Fluxo

```
Usuário → Telegram → POST /telegram/webhook
                          │
                          ▼ (background task — retorna 200 imediatamente)
                    answer_general_question(texto, histórico)
                          │
                          ▼
                    sendMessage(chat_id, resposta HTML)
                          │
                          ▼
                    Usuário recebe resposta
```

### Histórico por usuário

O bot mantém histórico de conversa individual usando `tg_{user_id}` como `session_id`, reutilizando a mesma tabela `conversation_sessions` do Supabase. O usuário pode limpar seu histórico com o comando `/clear`.

### Comandos disponíveis

| Comando | Descrição |
|---------|-----------|
| `/start` | Mensagem de boas-vindas com exemplos de uso |
| `/help` | Lista de comandos disponíveis |
| `/clear` | Limpa o histórico de conversa do usuário |

### Configuração após deploy

1. Obtenha o token do bot com o [@BotFather](https://t.me/BotFather) no Telegram
2. Adicione `TELEGRAM_TOKEN=<token>` ao `.env` e às variáveis de ambiente do Code Engine
3. Após o deploy, registre o webhook uma vez:

```bash
POST /telegram/set-webhook?url=https://<seu-dominio>/telegram/webhook
```

4. Verifique o status com `GET /telegram/webhook/info`

> **Nota:** o webhook exige HTTPS. Em desenvolvimento local, use [ngrok](https://ngrok.com/) ou outro túnel para expor o servidor.

---

## Histórico de Conversa

O endpoint `/predictions/{id}/ask` suporta histórico de conversa por sessão, armazenado no Supabase.

**Como funciona:**
1. O frontend gera um UUID como `session_id` e o reutiliza entre perguntas do mesmo jogo
2. Cada pergunta/resposta é armazenada como par `user/assistant` em `conversation_sessions`
3. Os últimos **6 pares** (12 mensagens) são injetados no contexto do LLM a cada nova pergunta
4. Para limpar o histórico, use `DELETE /predictions/{id}/ask/history?session_id=<uuid>`

**Por que 6 pares?**
Típico de uma sessão de análise de partida (< 10 perguntas). A janela deslizante descarta pares mais antigos silenciosamente — sem sumarização necessária para esse volume.

![Fluxo — Histórico de Conversa](docs/diagrams/assets/07-conversation-flow.svg)

![Sequência — Pergunta com Histórico](docs/diagrams/assets/11-sequence-ask-question.svg)

---

## Diagramas de Arquitetura

Os diagramas estão em `docs/` e podem ser abertos no [draw.io](https://app.diagrams.net/):

| Arquivo | Conteúdo |
|---|---|
| `docs/architecture-general.drawio` | Visão geral: IBM Cloud, BetsAPI, Groq, Vertex AI Search, Supabase |
| `docs/architecture-ml-agents.drawio` | Fluxo detalhado: Poisson+DC, LangGraph, Tool-Calling Loop, Retrain |

---

## Instalação local

```bash
git clone <repo-url> && cd edscript
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure variáveis de ambiente
cp .env.example .env
# BETSAPI_TOKEN, GROQ_API_KEY, GROQ_MODEL,
# SUPABASE_DB_URL, SUPABASE_DB_URL_ASYNC,
# IBM_COS_ACCESS_KEY_ID, IBM_COS_SECRET_ACCESS_KEY,
# IBM_COS_ENDPOINT, IBM_COS_BUCKET,
# TELEGRAM_TOKEN  (opcional — necessário apenas para o bot Telegram)

# Treina o modelo (ou baixa do IBM COS automaticamente na 1ª requisição)
python scripts/train_model.py

# Servidor local
uvicorn app.main:app --reload --port 8000
```

---

## Deploy (CLI)

### API (IBM Code Engine)

```bash
# 1. Login e target
ibmcloud login
ibmcloud target -r us-east -g Default
ibmcloud ce project select --name goat-tips-proj

# 2. Build e push da imagem
ibmcloud cr login
docker build --network host -t icr.io/goat-tips-ns/goat-tips-backend:latest .
docker push icr.io/goat-tips-ns/goat-tips-backend:latest

# 3. Criar ou atualizar a aplicação
ibmcloud ce application update --name goat-tips-backend-api \
  --image icr.io/goat-tips-ns/goat-tips-backend:latest \
  --registry-secret icr-global-secret \
  --env BETSAPI_TOKEN=<token> \
  --env GROQ_API_KEY=<key> \
  --env GROQ_MODEL=moonshotai/kimi-k2-instruct \
  --env SUPABASE_DB_URL=<url> \
  --env SUPABASE_DB_URL_ASYNC=<async_url> \
  --env IBM_COS_ACCESS_KEY_ID=<key_id> \
  --env IBM_COS_SECRET_ACCESS_KEY=<secret> \
  --env IBM_COS_ENDPOINT=https://s3.us-south.cloud-object-storage.appdomain.cloud \
  --env IBM_COS_BUCKET=goat-tips-bucket \
  --env TELEGRAM_TOKEN=<token>
```

Após o deploy, registre o webhook do Telegram uma vez:

```bash
curl -X POST "https://goat-tips-backend-api.27s4ihbbhmjf.us-east.codeengine.appdomain.cloud/telegram/set-webhook?url=https://goat-tips-backend-api.27s4ihbbhmjf.us-east.codeengine.appdomain.cloud/telegram/webhook"
```

### CE Jobs (daily sync + retrain)

```bash
# Daily sync (já criado — atualizar imagem se necessário)
docker build --network host -t icr.io/goat-tips-ns/goat-tips-daily-sync:latest jobs/daily_sync/
docker push icr.io/goat-tips-ns/goat-tips-daily-sync:latest
ibmcloud ce job update --name goat-tips-daily-sync \
  --image icr.io/goat-tips-ns/goat-tips-daily-sync:latest

# Retrain — usa a mesma imagem do backend (retrain.py está bundled)
# Não requer build/push separado. Apenas atualize o job após atualizar o backend:
ibmcloud ce job update --name goat-tips-retrain \
  --image icr.io/goat-tips-ns/goat-tips-backend:latest \
  --argument "python" --argument "retrain.py"

# Execução manual
ibmcloud ce jobrun submit --job goat-tips-daily-sync
ibmcloud ce jobrun submit --job goat-tips-retrain

# Ver logs de uma execução
ibmcloud ce jobrun logs --name <jobrun-name>
```

### Migração de dados

```bash
# Aplique o schema no Supabase SQL editor:
sql/schema.sql
sql/conversation_sessions.sql

# Migre os CSVs para o Supabase (idempotente):
python scripts/migrate_csv_to_db.py

# Retreine o modelo manualmente:
python scripts/train_model.py
```

---

## Endpoints

![Casos de Uso — Visão Geral](docs/diagrams/assets/09-use-cases.svg)

![Casos de Uso — /matches](docs/diagrams/assets/16-use-cases-matches.svg)

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

![Casos de Uso — /predictions](docs/diagrams/assets/17-use-cases-predictions.svg)

### `/predictions` — Modelo + LLM

| Método | Rota | Descrição |
|---|---|---|
| GET | `/predictions/?home=Arsenal&away=Chelsea` | Previsão Poisson pré-jogo (aceita `?referee=`, `?stadium=`, `?city=`, `?match_hour_utc=`) |
| GET | `/predictions/?home=X&away=Y&referee=Z&stadium=Emirates+Stadium` | Previsão com árbitro + ajuste climático |
| GET | `/predictions/inplay?home=X&away=Y&home_goals=N&away_goals=N&minute=N` | **In-play Non-Homogeneous Poisson** (aceita `?home_red=N&away_red=N`) |
| GET | `/predictions/{id}` | Previsão Poisson via event_id |
| GET | `/predictions/{id}/inplay` | **In-play automático** — busca placar/minuto da BetsAPI |
| GET | `/predictions/{id}/full-analysis` | **Análise completa — agente LangGraph** |
| POST | `/predictions/{id}/narrative` | Narrativa LLM simples |
| POST | `/predictions/{id}/ask` | Pergunta livre — aceita `?session_id=` para histórico |
| POST | `/predictions/ask` | Pergunta geral sobre a Premier League (sem event_id) |
| DELETE | `/predictions/{id}/ask/history` | Limpa histórico de sessão (`?session_id=` obrigatório) |

### `/telegram` — Bot Telegram

| Método | Rota | Descrição |
|---|---|---|
| POST | `/telegram/webhook` | Recebe updates do Telegram — configurar como webhook no BotFather |
| POST | `/telegram/set-webhook?url=<url>` | Registra a URL do webhook no Telegram (executar uma vez após deploy) |
| DELETE | `/telegram/webhook` | Remove o webhook registrado |
| GET | `/telegram/webhook/info` | Status e URL do webhook atual |

### `/analytics` — Dataset histórico

| Método | Rota | Descrição |
|---|---|---|
| GET | `/analytics/teams` | Lista os 35 times do dataset |
| GET | `/analytics/teams/{name}/form` | Forma recente (últimos N jogos) |
| GET | `/analytics/teams/{name}/stats` | Win rate, clean sheets, BTTS |
| GET | `/analytics/teams/{name}/profile` | Perfil avançado: shot efficiency, xG, gols por half, home vs away |
| GET | `/analytics/h2h?home=X&away=Y` | H2H histórico (4,495 jogos) |
| GET | `/analytics/goal-patterns` | Distribuição de gols por minuto (9,508 gols) |
| GET | `/analytics/card-patterns` | Distribuição de cartões por minuto (11,391 cartões) |
| GET | `/analytics/risk-scores` | Risk scores ao vivo (gol + cartão) |
| GET | `/analytics/referees` | Lista todos os árbitros do dataset |
| GET | `/analytics/referees/{name}/stats` | Estatísticas do árbitro: cartões/jogo, faltas, home win rate |
| GET | `/analytics/model/calibration?n=500` | **Backtesting do modelo** com Brier scores em N jogos recentes |
| GET | `/analytics/weather?stadium=X&city=Y&match_hour_utc=N` | **Clima em tempo real** para estádio — retorna `goal_factor` |

### CE Jobs — Automação

![Casos de Uso — Jobs](docs/diagrams/assets/19-use-cases-jobs.svg)

---

## Estrutura de arquivos

```
edscript/
├── app/
│   ├── agents/
│   │   ├── match_agent.py   # Graph LangGraph 3-nós + run_full_analysis()
│   │   ├── nodes.py         # AgentState TypedDict + 3 nós (full-analysis)
│   │   ├── ask_agent.py     # Supervisor 6-agentes LangGraph (ask pipeline)
│   │   └── ask_nodes.py     # AskState, 6 node funcs, AgentArtifact contract
│   ├── core/settings.py     # Pydantic Settings — todas as env vars
│   ├── db/
│   │   ├── connection.py    # Pool asyncpg
│   │   └── models.py        # SQLAlchemy ORM
│   ├── repositories/
│   │   ├── historical.py    # CSVs → pandas (lru_cache, in-memory)
│   │   └── database.py      # Supabase upsert bulk (asyncpg)
│   ├── routers/             # matches / predictions / analytics / telegram
│   ├── schemas/             # match / prediction / analytics / agent
│   └── services/
│       ├── betsapi.py       # Cliente BetsAPI async — todos os endpoints
│       ├── llm_client.py    # Groq client singleton + SYSTEM_PROMPT
│       ├── narrative.py     # Contexto enriquecido + chamada LLM
│       ├── predictor.py     # Poisson + fallback IBM COS + fallback inline
│       ├── analytics.py     # Lógica analytics histórico
│       ├── conversation.py  # Histórico de chat por sessão (Supabase JSONB)
│       ├── search.py        # Vertex AI Search (Google Discovery Engine)
│       ├── telegram.py      # Cliente Telegram Bot API (send_message, set_webhook)
│       └── tools.py         # 7 ferramentas LLM + dispatcher async
├── jobs/
│   └── daily_sync/          # CE Job — sync diário BetsAPI → Supabase
│       ├── daily_sync.py    # Entrypoint IBM Code Engine
│       ├── sync_logic.py    # Fetch BetsAPI + upsert Supabase (psycopg2)
│       ├── Dockerfile
│       └── requirements.txt
├── retrain/                 # CE Job — retreinamento semanal
│   ├── retrain.py           # Puxa Supabase → treina → upload IBM COS
│   ├── Dockerfile
│   └── requirements.txt
├── scripts/
│   ├── train_model.py       # Treina e serializa o modelo localmente
│   ├── migrate_csv_to_db.py # Migra CSVs → Supabase (idempotente)
│   ├── deploy-code-engine.sh # Deploy completo da API no Code Engine
│   └── test_routes.py       # Testa todos os endpoints com rich output
├── sql/
│   ├── schema.sql                   # Schema Supabase completo (6 tabelas + views)
│   ├── conversation_sessions.sql    # Tabela de histórico de chat
│   └── feature_snapshots.sql        # 3 tabelas de snapshots FBref/StatsBomb
├── data/kaggle/                     # FBref + StatsBomb CSVs (não versionados)
│   ├── players_data_2025_2026.csv   # Stats por jogador (Kaggle/FBref export)
│   └── statsbomb_premier_league_matches.csv # Partidas StatsBomb
├── docs/guia-api.md         # Guia de consumo da API em Português
├── Dockerfile               # Imagem da API principal
├── .dockerignore            # Exclui odds timeseries (563 MB)
└── requirements.txt
```
