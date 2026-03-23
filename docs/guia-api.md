# Guia de Uso da API — Goat Tips Premier League AI

> Versão 0.5.0 | Base URL: `https://goat-tips-backend-api.27s4ihbbhmjf.us-east.codeengine.appdomain.cloud`
> Docs interativos: `.../docs`

Este guia explica como consumir cada endpoint da Goat Tips API, com exemplos de requisição, resposta e casos de uso para o frontend.

---

## Índice

1. [Visão Geral](#visão-geral)
2. [Autenticação](#autenticação)
3. [Módulo `/matches` — Dados ao Vivo](#módulo-matches)
4. [Módulo `/predictions` — Previsões](#módulo-predictions)
5. [Módulo `/analytics` — Dataset Histórico](#módulo-analytics)
6. [Polling — Como Atualizar o Frontend](#polling)
7. [Fluxo Recomendado para o Frontend](#fluxo-recomendado)
8. [Tratamento de Erros](#tratamento-de-erros)
9. [Dicionário de Campos](#dicionário-de-campos)

---

## Visão Geral

A Goat Tips API possui três módulos:

| Prefixo | Dados | Latência típica |
|---------|-------|-----------------|
| `/matches` | BetsAPI (real-time) | 0.3–5 s |
| `/predictions` | Modelo Poisson + LLM (Groq) | 1–15 s |
| `/analytics` | Dataset histórico (4,585 jogos) | <50 ms (cacheado) |

---

## Autenticação

Nenhuma autenticação é necessária para consumir a API. As chaves de terceiros (BetsAPI, Groq) ficam no servidor.

---

## Módulo `/matches`

### `GET /matches/live`

Retorna todas as partidas da Premier League ao vivo.

**Quando usar:** Para o painel principal "ao vivo". Faça polling a cada **30 segundos**.

```bash
BASE=https://goat-tips-backend-api.27s4ihbbhmjf.us-east.codeengine.appdomain.cloud
curl $BASE/matches/live
```

**Resposta:**
```json
[
  {
    "event_id": "12345678",
    "home": { "id": "42", "name": "Arsenal", "image_url": "https://..." },
    "away": { "id": "44", "name": "Chelsea", "image_url": "https://..." },
    "minute": 67,
    "score_home": 1,
    "score_away": 0,
    "status": "live",
    "odds": { "home_win": 1.40, "draw": 4.20, "away_win": 7.50 },
    "probabilities": { "home_win": 0.682, "draw": 0.218, "away_win": 0.100, "market_margin": 0.052 },
    "kick_off_time": "2026-03-22T12:00:00Z",
    "round": "31",
    "referee": "Anthony Taylor",
    "stadium": "Emirates Stadium, London"
  }
]
```

**Campos importantes:**
- `minute`: minuto atual do jogo (null se ainda não começou)
- `probabilities.home_win`: probabilidade real sem margem da casa (0–1)
- `kick_off_time`: horário de início em UTC (exibir como horário local no frontend)
- `stadium` e `referee`: contexto adicional para enriquecer a UI

---

### `GET /matches/upcoming`

Retorna as próximas partidas da liga.

**Quando usar:** Para a tela de "próximos jogos". Atualize a cada **5 minutos**.

```bash
curl $BASE/matches/upcoming
```

**Resposta (mesmo formato do live, com diferenças):**
```json
[
  {
    "event_id": "11545080",
    "home": { "name": "Newcastle" },
    "away": { "name": "Sunderland" },
    "minute": null,
    "score_home": 0,
    "score_away": 0,
    "status": "upcoming",
    "kick_off_time": "2026-03-22T12:00:00Z",
    "round": "31",
    "referee": "Anthony Taylor",
    "stadium": "St. James Park, Newcastle upon Tyne",
    "odds": { "home_win": 1.85, "draw": 3.60, "away_win": 4.20 }
  }
]
```

**Dica:** Use `kick_off_time` para montar um countdown no frontend. Converta para o fuso local do usuário via `Intl.DateTimeFormat`.

---

### `GET /matches/{event_id}`

Contexto completo de uma partida específica.

```bash
curl $BASE/matches/11545080
```

---

### `GET /matches/{event_id}/h2h`

Histórico de confrontos diretos via BetsAPI.

```bash
curl $BASE/matches/11545080/h2h
```

**Resposta:**
```json
{
  "home_team": "Newcastle",
  "away_team": "Sunderland",
  "total_matches": 6,
  "home_wins": 2,
  "away_wins": 3,
  "draws": 1,
  "home_goals_avg": 1.17,
  "away_goals_avg": 1.50,
  "last_matches": [
    {
      "event_id": "9876543",
      "date": "2024-10-15",
      "home_team": "Sunderland",
      "away_team": "Newcastle",
      "score_home": 2,
      "score_away": 0,
      "winner": "home"
    }
  ]
}
```

---

### `GET /matches/{event_id}/stats-trend`

Estatísticas táticas por período, com score de momentum.

```bash
curl $BASE/matches/12345678/stats-trend
```

**Resposta:**
```json
{
  "event_id": "12345678",
  "periods": [
    {
      "period": "1st_half",
      "home_shots": 4, "away_shots": 2,
      "home_corners": 3, "away_corners": 1,
      "home_dangerous_attacks": 18, "away_dangerous_attacks": 9,
      "home_possession": 58.0, "away_possession": 42.0
    }
  ],
  "momentum_score": 0.42,
  "momentum_label": "Domínio do Mandante"
}
```

**Momentum score:** -1 (domínio total do visitante) a +1 (domínio total do mandante). Use para um medidor visual no card da partida.

---

### `GET /matches/{event_id}/lineup`

Escalações confirmadas dos dois times.

```bash
curl $BASE/matches/12345678/lineup
```

**Resposta:**
```json
{
  "event_id": "12345678",
  "home": {
    "team": { "name": "Arsenal" },
    "formation": "4-3-3",
    "starting_xi": [
      { "name": "Raya", "number": 22, "position": "GK" },
      { "name": "White", "number": 4, "position": "DEF" }
    ],
    "substitutes": [
      { "name": "Nketiah", "number": 14, "position": "FWD" }
    ]
  },
  "away": { ... }
}
```

---

### `GET /matches/toplist`

Artilheiros e garçons da liga.

```bash
curl $BASE/matches/toplist
```

**Use para:** Contexto narrativo ("Salah, artilheiro da liga, está em campo hoje").

---

## Módulo `/predictions`

### `GET /predictions/?home=Arsenal&away=Chelsea`

Previsão estatística por nome dos times. **Não requer partida ao vivo.**

Aceita `?referee=` para ajuste de árbitro e `?stadium=`/`?city=` para ajuste climático via Open-Meteo:

```bash
# Previsão básica
curl "$BASE/predictions/?home=Arsenal&away=Chelsea"

# Com árbitro + clima (kick-off às 15h UTC no Emirates)
curl "$BASE/predictions/?home=Arsenal&away=Chelsea&referee=Michael+Oliver&stadium=Emirates+Stadium&match_hour_utc=15"
```

**Query params:**
| Param | Tipo | Descrição |
|---|---|---|
| `home` | string | Time mandante |
| `away` | string | Time visitante |
| `referee` | string | Árbitro (opcional) — ajuste ±8% em λ |
| `stadium` | string | Nome do estádio (opcional) — ajuste climático via Open-Meteo |
| `city` | string | Cidade (fallback quando estádio não encontrado) |
| `match_hour_utc` | int 0–23 | Hora do kick-off em UTC para previsão climática horária |

**Campos adicionais na resposta:**
- `weather_factor` (float): multiplicador aplicado em λ (1.0 = sem impacto, 0.88 = chuva forte)
- `weather_condition` (string): label climático ("clear", "rain", "storm", etc.)
- `half_time` (object): previsão para o intervalo — ver seção abaixo

**Resposta:**
```json
{
  "home_team": "Arsenal",
  "away_team": "Chelsea",
  "lambda_home": 1.671,
  "lambda_away": 1.191,
  "home_win_prob": 0.4844,
  "draw_prob": 0.2423,
  "away_win_prob": 0.2733,
  "over_2_5_prob": 0.5432,
  "btts_prob": 0.5652,
  "most_likely_score": "1-1",
  "most_likely_score_prob": 0.1137,
  "top_scores": [
    ["1-1", 0.1137],
    ["1-0", 0.0955],
    ["2-1", 0.0950],
    ["2-0", 0.0799],
    ["0-1", 0.0675]
  ],
  "score_matrix": [[0.05, 0.06, ...], ...],
  "confidence": "Alta",
  "model_note": "Modelo Poisson — 4585 jogos PL 2014–2026."
}
```

**Como usar no frontend:**
- `lambda_home/away`: mostrar como "gols esperados" (ex: Arsenal 1.7 gols esperados)
- `home_win_prob`: barra de probabilidade (Arsenal 48% | Empate 24% | Chelsea 27%)
- `top_scores`: ranking de placares mais prováveis
- `score_matrix`: heatmap de probabilidades de placar (7×7)
- `over_2_5_prob`: "mais de 2.5 gols: 54% de chance"
- `btts_prob`: "ambos marcam: 57% de chance"

---

---

### `GET /predictions/inplay`

**Previsão Bayesiana in-play** — recalcula probabilidades de resultado final dado o estado atual da partida.

```bash
# Arsenal 1-0 Chelsea, minuto 70
curl "$BASE/predictions/inplay?home=Arsenal&away=Chelsea&home_goals=1&away_goals=0&minute=70"

# Com árbitro
curl "$BASE/predictions/inplay?home=Arsenal&away=Chelsea&home_goals=1&away_goals=0&minute=70&referee=Michael+Oliver"
```

**Query params:**
| Param | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `home` | string | Sim | Time mandante |
| `away` | string | Sim | Time visitante |
| `home_goals` | int (≥0) | Sim | Gols atuais do mandante |
| `away_goals` | int (≥0) | Sim | Gols atuais do visitante |
| `minute` | int (1–90) | Sim | Minuto atual do jogo |
| `referee` | string | Não | Nome do árbitro para ajuste de λ |
| `home_red` | int (0–2) | Não | Cartões vermelhos do mandante (reduz λ_ataque × 0.72^N) |
| `away_red` | int (0–2) | Não | Cartões vermelhos do visitante (idem) |

**Retorna o mesmo schema do `GET /predictions/`**, com `lambda_home`/`lambda_away` = **gols esperados no tempo restante**.

**Como funciona (Non-Homogeneous Poisson):**
1. Calcula λ pré-jogo (com xG, árbitro e clima)
2. Usa **taxa empírica de gols por bucket de 15 min** (derivada de 9,448 gols PL) — não taxa constante
3. Interpola linearmente dentro do bucket para sub-minuto preciso
4. Aplica penalidade de cartão vermelho: `λ_ataque × 0.72^N` (time com N jogadores a menos)
5. Distribui gols adicionais com Poisson(λ_rem), desloca pelo placar atual
6. `model_note` detalha: minuto, placar, λ restante e fração de jogo usada

**Exemplo de resposta (Arsenal 1-0 Chelsea, 70', Arsenal com 10 jogadores):**
```bash
curl "$BASE/predictions/inplay?home=Arsenal&away=Chelsea&home_goals=1&away_goals=0&minute=70&home_red=1"
```
```json
{
  "home_team": "Arsenal",
  "away_team": "Chelsea",
  "lambda_home": 0.375,
  "lambda_away": 0.430,
  "home_win_prob": 0.7147,
  "draw_prob": 0.2022,
  "away_win_prob": 0.0831,
  "most_likely_score": "1-0",
  "model_note": "In-play (Non-Homogeneous Poisson) — 70' (1-0) 🟥Arsenal(x1). λ restante: home=0.38, away=0.43 (28.2% do jogo). Base: 4495 jogos PL."
}
```

**Evolução das probabilidades (Arsenal 1-0 Chelsea):**
| Minuto | Home Win | Draw | Away Win |
|--------|----------|------|----------|
| Pré-jogo | 54.8% | 22.1% | 23.1% |
| 30' | 77.7% | 15.8% | 6.5% |
| 70' | 84.9% | 13.4% | 1.7% |
| 85' | 94.7% | 5.2% | 0.2% |

**Quando usar:** Widget "probabilidades ao vivo" no card de partida. Atualizar a cada ~30s sincronizado com o polling de placar.

---

### `GET /predictions/{event_id}/inplay`

**In-play automático** — obtém placar e minuto diretamente da BetsAPI, sem precisar passar os valores manualmente.

```bash
curl $BASE/predictions/12345678/inplay
```

- Se a partida estiver **ao vivo**: usa `score_home`, `score_away`, `minute` e `referee` da BetsAPI
- Se ainda **não iniciou**: retorna a previsão pré-jogo normal (fallback automático)
- `model_note` indica qual modo foi usado

---

### `GET /predictions/{event_id}`

Previsão usando o ID da BetsAPI (resolve os nomes automaticamente).

```bash
curl $BASE/predictions/11545080
```

---

### `GET /predictions/{event_id}/full-analysis`

**O endpoint principal do produto.** Agente LangGraph que combina todas as fontes em 3 nós sequenciais:
1. `fetch_context` — placar, odds, H2H e escalações em paralelo via BetsAPI
2. `fetch_historical` — forma dos times, previsão Poisson e risk scores do dataset local
3. `generate_narrative` — Groq (moonshotai/kimi-k2-instruct) gera headline + análise + previsão em Português

```bash
curl $BASE/predictions/12345678/full-analysis
```

**Tempo esperado:** 5–15 segundos (I/O paralelo + LLM). Não usar em polling automático — acionar sob demanda.

**Resposta:**
```json
{
  "match": { ... },
  "narrative": {
    "match_id": "12345678",
    "headline": "Arsenal domina, mas Chelsea resiste",
    "analysis": "O Arsenal controla o jogo com 58% de posse e superioridade nos ataques perigosos (18 a 9). Apesar do placar aberto, o mercado ainda vê risco real de reação do Chelsea.",
    "prediction": "Grandes chances de o Arsenal ampliar o placar nos próximos 15 minutos, com alta pressão e um adversário desgastado.",
    "momentum_signal": "A odd do Arsenal recuou 12% desde o início — mercado confiante na vitória do mandante.",
    "confidence_label": "Alta"
  },
  "prediction": {
    "lambda_home": 1.671,
    "most_likely_score": "1-1",
    "home_win_prob": 0.4844
  },
  "h2h": { "total_matches": 10, "home_wins": 6, ... },
  "stats_trend": { "momentum_score": 0.42, "momentum_label": "Domínio do Mandante", ... },
  "lineup": { "home": { "formation": "4-3-3", ... }, ... },
  "home_form": { "form_string": "WWWWDDWWLD", "avg_goals_scored": 2.1, ... },
  "away_form": { "form_string": "WDLWWLDDW", ... },
  "goal_risk_score": 7.8,
  "card_risk_score": 5.2,
  "agent_steps": ["fetch_context", "fetch_historical", "generate_narrative"]
}
```

**Como usar:**
- `narrative.headline`: título do card de análise
- `narrative.analysis`: parágrafo central da experiência
- `narrative.prediction`: seção "o que pode acontecer"
- `goal_risk_score`: medidor visual (0–10, onde 7+ = ALTO)
- `card_risk_score`: medidor visual de risco de cartão
- `agent_steps`: útil para debug / loading states no frontend

---

### `POST /predictions/{event_id}/ask`

Perguntas em linguagem natural sobre a partida, com suporte a **histórico de conversa por sessão**.

```bash
# Pergunta simples (sem histórico)
curl -X POST $BASE/predictions/12345678/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Por que o time visitante está sofrendo tanto?"}'

# Com histórico de sessão (passe o mesmo session_id nas perguntas seguintes)
curl -X POST "$BASE/predictions/12345678/ask?session_id=550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"question": "Tem risco de virada?"}'
```

**Query params:**
| Param | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `session_id` | string (UUID) | Não | ID de sessão gerado pelo frontend. Reutilize entre perguntas do mesmo jogo para manter contexto. Omitir = pergunta isolada sem histórico. |

**Como funciona o histórico:**
- Gere um UUID no frontend (`crypto.randomUUID()`) na abertura do chat de uma partida
- Reutilize o mesmo `session_id` em todas as perguntas subsequentes sobre aquela partida
- O servidor armazena os pares pergunta/resposta no Supabase e injeta os **últimos 6** no contexto do LLM
- O histórico persiste entre sessões de browser (até você limpá-lo)

**Resposta:** Mesmo formato de `NarrativeResponse` (headline, analysis, prediction, confidence_label).

**Casos de uso no frontend:**
- Chatbot integrado ao card da partida
- Botões de perguntas rápidas ("O que mudou no 2º tempo?", "Tem risco de virada?")
- Conversa contínua — o LLM lembra do contexto das perguntas anteriores

---

### `DELETE /predictions/{event_id}/ask/history`

Remove o histórico de conversa de uma sessão específica.

```bash
curl -X DELETE "$BASE/predictions/12345678/ask/history?session_id=550e8400-e29b-41d4-a716-446655440000"
```

**Query params:**
| Param | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `session_id` | string (UUID) | **Sim** | ID da sessão a limpar |

**Resposta:**
```json
{ "cleared": true, "session_id": "550e8400-...", "event_id": "12345678" }
```

**Quando usar:** Botão "Limpar conversa" no frontend, ou ao iniciar uma nova análise da mesma partida.

---

### `POST /predictions/ask` (sem event_id)

Perguntas gerais sobre a Premier League — sem precisar de uma partida específica.

```bash
curl -X POST $BASE/predictions/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual árbitro aplica mais cartões amarelos esta temporada?"}'
```

**Como funciona:** O LLM usa as mesmas 7 ferramentas do endpoint com `event_id`, mas sem contexto de partida específica. Ideal para chatbots de pré-jogo, consultas sobre a liga em geral, ou quando o usuário ainda não selecionou uma partida.

**Suporta `?session_id=` igual ao endpoint com `event_id`.**

---

### `POST /predictions/{event_id}/narrative`

Narrativa simples sem o agente completo. Mais rápida, menos contexto.

```bash
curl -X POST $BASE/predictions/12345678/narrative
```

---

## Módulo `/analytics`

### `GET /analytics/teams`

Lista todos os times do dataset histórico.

```bash
curl $BASE/analytics/teams
```

**Resposta:** `{"teams": [{"id": "17230", "name": "Arsenal"}, ...], "total": 35}`

---

### `GET /analytics/teams/{team_name}/form?n=10`

Forma recente do time.

```bash
curl "$BASE/analytics/teams/Arsenal/form?n=10"
```

**Resposta:**
```json
{
  "team_name": "Arsenal",
  "last_n_matches": 10,
  "form_string": "WWWWDDWWLD",
  "wins": 6, "draws": 3, "losses": 1,
  "avg_goals_scored": 2.1,
  "avg_goals_conceded": 0.9,
  "matches": [
    {
      "event_id": "12345",
      "date": "2026-03-15",
      "opponent": "Chelsea",
      "home_or_away": "home",
      "goals_scored": 3,
      "goals_conceded": 1,
      "result": "W"
    }
  ]
}
```

---

### `GET /analytics/teams/{team_name}/stats`

Estatísticas agregadas (últimas 50 partidas).

```bash
curl $BASE/analytics/teams/Arsenal/stats
```

**Resposta:**
```json
{
  "team_name": "Arsenal",
  "sample_size": 50,
  "win_rate": 0.56,
  "draw_rate": 0.22,
  "avg_goals_scored": 2.06,
  "avg_goals_conceded": 0.94,
  "clean_sheet_rate": 0.38,
  "btts_rate": 0.44
}
```

---

### `GET /analytics/h2h?home=Arsenal&away=Chelsea&n=10`

H2H histórico extraído do CSV local (complementa o H2H da BetsAPI).

```bash
curl "$BASE/analytics/h2h?home=Arsenal&away=Chelsea&n=10"
```

---

### `GET /analytics/goal-patterns`

Distribuição de 9,448 gols por intervalos de 15 minutos.

```bash
curl $BASE/analytics/goal-patterns
```

**Resposta:**
```json
{
  "total_goals": 9448,
  "avg_goals_per_match": 2.1,
  "peak_minute_range": "31-45+",
  "buckets": [
    { "minute_range": "0-15",   "goals": 1311, "pct_of_total": 0.1388 },
    { "minute_range": "16-30",  "goals": 1498, "pct_of_total": 0.1585 },
    { "minute_range": "31-45+", "goals": 2046, "pct_of_total": 0.2165 },
    { "minute_range": "46-60",  "goals": 1649, "pct_of_total": 0.1745 },
    { "minute_range": "61-75",  "goals": 1758, "pct_of_total": 0.1860 },
    { "minute_range": "76-90+", "goals": 1698, "pct_of_total": 0.1797 }
  ]
}
```

**Como usar:** Timeline visual de risco de gol. Mostre qual período está ativo e destaque o `peak_minute_range`.

---

### `GET /analytics/card-patterns`

Distribuição de cartões por intervalo de 15 minutos.

```bash
curl $BASE/analytics/card-patterns
```

**Campos:** `total_yellows`, `total_reds`, `peak_minute_range`, `buckets[]`.

---

### `GET /analytics/risk-scores?minute=75&score_diff=-1`

Scores de risco ao vivo calculados por heurística + padrão histórico.

```bash
curl "$BASE/analytics/risk-scores?minute=75&score_diff=-1"
```

**Resposta:**
```json
{
  "minute": 75,
  "score_diff": -1,
  "goal_risk":  { "score": 8.5, "label": "Alto" },
  "card_risk":  { "score": 6.2, "label": "Médio" }
}
```

**`score_diff`:** mandante − visitante. `-1` = mandante perdendo por 1 gol.

---

### `GET /analytics/referees`

Lista todos os árbitros presentes no dataset histórico.

```bash
curl $BASE/analytics/referees
```

**Resposta:** `{"referees": ["Andre Marriner", "Anthony Taylor", "Michael Oliver", ...], "total": 22}`

---

### `GET /analytics/referees/{referee_name}/stats`

Estatísticas históricas de um árbitro extraídas do dataset (2014–2026).

```bash
curl "$BASE/analytics/referees/Michael Oliver/stats"
```

**Resposta:**
```json
{
  "referee_name": "Michael Oliver",
  "matches": 279,
  "avg_yellow_cards": 3.26,
  "avg_red_cards": 0.13,
  "avg_fouls": 19.59,
  "home_win_rate": 0.441
}
```

**Quando usar:** Para enriquecer análises pré-jogo com perfil de rigor do árbitro designado.

---

### `GET /analytics/teams/{team_name}/profile`

Perfil avançado do time com eficiência ofensiva, distribuição de gols e desempenho por mando.

```bash
curl "$BASE/analytics/teams/Arsenal/profile"
```

**Resposta:**
```json
{
  "team_name": "Arsenal",
  "sample_size": 451,
  "avg_shots_on_target": 5.09,
  "avg_goals_scored": 1.88,
  "shot_efficiency": 0.369,
  "avg_xg": 1.78,
  "goals_by_half": {
    "first_half_avg": 1.52,
    "second_half_avg": 1.98,
    "first_half_pct": 0.434
  },
  "home_win_rate": 0.668,
  "away_win_rate": 0.423,
  "home_goals_avg": 2.11,
  "away_goals_avg": 1.63
}
```

**Campos:**
- `shot_efficiency`: gols marcados por chute no alvo (Arsenal = 36.9%)
- `avg_xg`: xG médio por jogo baseado nos dados históricos
- `goals_by_half.first_half_pct`: proporção de gols marcados no 1T (Arsenal = 43.4% no 1T)
- `home_win_rate` vs `away_win_rate`: contraste entre desempenho em casa e fora

**Quando usar:** Para análises de tendência ofensiva, apostas em Over/Under por half, e enriquecer contexto do LLM.

---

### `GET /analytics/weather`

**Condições climáticas em tempo real ou previstas** para estádios da Premier League.

```bash
# Clima atual no Emirates
curl "$BASE/analytics/weather?stadium=Emirates+Stadium"

# Previsão para kick-off às 15h UTC
curl "$BASE/analytics/weather?stadium=Anfield&match_hour_utc=15"

# Por cidade (fallback)
curl "$BASE/analytics/weather?city=London&match_hour_utc=20"
```

**Query params:**
| Param | Tipo | Descrição |
|---|---|---|
| `stadium` | string | Nome do estádio (33 estádios PL com coordenadas) |
| `city` | string | Cidade fallback (25 cidades com coordenadas) |
| `match_hour_utc` | int 0–23 | Hora do kick-off em UTC para previsão horária |

**Resposta:**
```json
{
  "stadium": "Emirates Stadium",
  "city": null,
  "weather_code": 61,
  "condition": "rain",
  "description": "Chuva",
  "precipitation_mm": 2.4,
  "wind_speed_kmh": 18.2,
  "temperature_c": 11.3,
  "goal_factor": 0.920,
  "source": "stadium",
  "impact": "leve"
}
```

**Campos:**
- `goal_factor`: multiplicador para λ (integrado automaticamente em `/predictions/` se `?stadium=` for passado)
- `impact`: "neutro" (≥0.99), "leve" (≥0.94), "moderado" (≥0.87), "severo" (<0.87)
- `source`: "stadium" (coordenada exata) ou "city" (coordenada de cidade)

**Tabela de impacto climático:**
| Condição | `goal_factor` | Redução de gols |
|----------|--------------|-----------------|
| Céu limpo / Nublado | 1.00 | 0% |
| Garoa | 0.96 | -4% |
| Chuva leve | 0.92 | -8% |
| Chuva forte | 0.88–0.92 | -8% a -13% |
| Neve | 0.88 | -12% |
| Trovoada | 0.85 | -15% |
| Vento > 40 km/h | -0.05 adicional | |

**Fonte:** Open-Meteo API (gratuita, sem chave). Latência: ~200–500 ms.

---

### `GET /analytics/model/calibration?n=500`

**Backtesting do modelo Poisson** — avalia a qualidade das probabilidades nos últimos N jogos encerrados.

```bash
curl "$BASE/analytics/model/calibration?n=500"
```

**Query params:**
| Param | Tipo | Default | Descrição |
|---|---|---|---|
| `n` | int (50–4000) | 500 | Número de jogos recentes para backtest |

**Como funciona:**
1. Pega os últimos N jogos encerrados do dataset
2. Para cada jogo, faz a previsão **sem usar esse jogo no treino** (leave-last-N-out)
3. Compara a probabilidade prevista com o resultado real
4. Calcula Brier Score por mercado e bins de calibração

**Resposta:**
```json
{
  "n_matches": 500,
  "markets": {
    "home_win":  { "brier_score": 0.2161, "avg_predicted": 0.452, "avg_actual": 0.460 },
    "draw":      { "brier_score": 0.1886, "avg_predicted": 0.248, "avg_actual": 0.241 },
    "away_win":  { "brier_score": 0.2003, "avg_predicted": 0.300, "avg_actual": 0.299 },
    "over_2_5":  { "brier_score": 0.2387, "avg_predicted": 0.537, "avg_actual": 0.528 },
    "btts":      { "brier_score": 0.2431, "avg_predicted": 0.523, "avg_actual": 0.510 }
  },
  "calibration_bins": {
    "home_win": [
      { "bin": "0.0-0.1", "avg_predicted": 0.07, "avg_actual": 0.06, "n": 12 },
      { "bin": "0.4-0.5", "avg_predicted": 0.45, "avg_actual": 0.47, "n": 89 }
    ]
  }
}
```

**Interpretando o Brier Score:**
- `0.0` = perfeito (impossível na prática)
- `0.25` = modelo sem poder discriminativo (equivale a prever 50% sempre)
- `< 0.22` = bom modelo preditivo ← **nossos resultados estão nessa faixa**

**Quando usar:** Dashboard interno de monitoramento do modelo, validação após retreinamento.

**Nota:** Primeira chamada leva ~10s (cálculo não cacheado por padrão). Para produção, considere cache de 24h.

---

## Polling — Como Atualizar o Frontend

| Dado | Endpoint | Intervalo recomendado |
|------|----------|-----------------------|
| Placar / minuto ao vivo | `GET /matches/live` | 30 s |
| Odds ao vivo | `GET /matches/{id}` | 60 s |
| Momentum | `GET /matches/{id}/stats-trend` | 2 min |
| Próximos jogos | `GET /matches/upcoming` | 5 min |
| Risk scores | `GET /analytics/risk-scores` | Calculado localmente com o minuto atual |
| Perfil do árbitro | `GET /analytics/referees/{name}/stats` | Uma vez antes do jogo |
| Perfil do time | `GET /analytics/teams/{name}/profile` | Uma vez por sessão |
| Probabilidades in-play | `GET /predictions/{id}/inplay` | 30 s (sincronizar com polling de placar; passar `?home_red=N` se houver cartão vermelho) |
| Clima do estádio | `GET /analytics/weather?stadium=X` | Uma vez por dia (ou na abertura do card da partida) |
| Narrativa completa | `GET /predictions/{id}/full-analysis` | Sob demanda (botão) |
| Calibração do modelo | `GET /analytics/model/calibration` | Uma vez por semana / após retrain |

> **Dica:** O `full-analysis` não deve ser chamado no polling — é caro (LLM). Acione sob demanda ou uma vez por jogo.

---

## Fluxo Recomendado para o Frontend

### Tela: "Partidas ao Vivo"

```
1. GET /matches/live                     → lista de cards com placar + odds
2. Para cada card: exibir momentum_score se disponível
3. Ao clicar em um card: GET /predictions/{id}/full-analysis
4. Exibir narrative.headline + analysis + goal_risk_score + card_risk_score
5. Polling /matches/live a cada 30s para atualizar placar
```

### Tela: "Pré-jogo / Upcoming"

```
1. GET /matches/upcoming                 → lista com kick_off_time
2. Ao selecionar um jogo: GET /predictions/?home=X&away=Y
3. Exibir: barra de probabilidades, top 5 placares, lambda_home/away
4. GET /analytics/h2h?home=X&away=Y     → histórico de confrontos
5. GET /analytics/teams/{X}/form?n=5    → forma recente dos dois times
6. GET /analytics/goal-patterns         → timeline de risco de gol
```

### Widget: "Risk Meter" (ao vivo)

```
Obtém do full-analysis:
  - goal_risk_score (0–10) → barra de progresso colorida
  - card_risk_score (0–10) → barra de progresso colorida
  - momentum_score (-1 a +1) → medidor central

Ou calcula dinamicamente:
  GET /analytics/risk-scores?minute={minuto_atual}&score_diff={h-a}
```

### Widget: "Probabilidades ao Vivo"

```
1. GET /predictions/{id}/inplay               → probabilidades atualizadas pelo placar
2. Exibir barra home_win / draw / away_win    → atualiza a cada 30s com o polling de placar
3. Exibir model_note para contexto (ex: "In-play Bayesian — 70' (1-0)")
4. Fallback: se jogo ainda não iniciou, exibir previsão pré-jogo normal
```

### Widget: "Chat da Partida"

```
1. Na abertura do card: gerar session_id = crypto.randomUUID()
2. POST /predictions/{id}/ask?session_id={uuid}  → primeira pergunta
3. Reutilizar o mesmo session_id nas perguntas seguintes
4. Ao fechar/resetar o chat: DELETE /predictions/{id}/ask/history?session_id={uuid}
```

---

## Tratamento de Erros

| Código | Significado | O que fazer |
|--------|-------------|-------------|
| `200` | Sucesso | Normal |
| `404` | Partida/time não encontrado | Mostrar mensagem "Dados não disponíveis" |
| `500` | Erro interno no agente | Log + fallback para narrativa simples |

**Nota importante sobre timeouts BetsAPI:**
Os endpoints `/matches/live` e `/matches/upcoming` nunca retornam 5xx por timeout — em caso de falha de rede ou timeout (30 s), retornam `200` com lista vazia `[]`. Isso protege o frontend de crashes; trate a lista vazia exibindo um estado de "sem dados disponíveis" em vez de erro.

**Todos os erros de domínio retornam:**
```json
{ "detail": "mensagem descritiva do erro" }
```

---

## Dicionário de Campos

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `event_id` | string | ID único da BetsAPI para a partida |
| `kick_off_time` | string ISO 8601 | Horário de início em UTC |
| `status` | string | `"live"` \| `"upcoming"` \| `"ended"` |
| `minute` | int \| null | Minuto atual (null se não iniciou) |
| `probabilities.home_win` | float 0–1 | Prob. de vitória do mandante sem margem da casa |
| `market_margin` | float | Margem da casa removida (ex: 0.052 = 5.2%) |
| `momentum_score` | float -1–1 | -1 = visitante domina, +1 = mandante domina |
| `lambda_home` | float | Gols esperados do mandante (Modelo Poisson) |
| `over_2_5_prob` | float 0–1 | Prob. de mais de 2.5 gols no total |
| `btts_prob` | float 0–1 | Prob. de ambos os times marcarem |
| `goal_risk_score` | float 0–10 | Risco de gol nos próximos 15 min |
| `card_risk_score` | float 0–10 | Risco de cartão nos próximos 15 min |
| `confidence_label` | string | `"Alta"` \| `"Média"` \| `"Baixa"` |
| `form_string` | string | Sequência de resultados ex: `"WWDLW"` |
| `agent_steps` | string[] | Nós do grafo LangGraph executados |
| `session_id` | string (UUID) | ID de sessão para histórico de chat — gerado pelo frontend |
| `shot_efficiency` | float 0–1 | Gols por chute no alvo do time |
| `avg_xg` | float | xG médio por jogo do time |
| `first_half_pct` | float 0–1 | Proporção de gols marcados no 1º tempo |
| `avg_yellow_cards` | float | Média de cartões amarelos/jogo do árbitro |
| `home_win_rate` (árbitro) | float 0–1 | Taxa de vitória do mandante com este árbitro |
| `referee` | string (query param) | Nome do árbitro para ajuste de λ (±8% baseado em 3,715 jogos) |
| `home_goals` / `away_goals` | int | Gols atuais para previsão in-play |
| `minute` | int 1–90 | Minuto atual do jogo para previsão in-play |
| `home_red` / `away_red` | int 0–2 | Cartões vermelhos — reduz λ_ataque × 0.72^N por jogador a menos |
| `brier_score` | float 0–0.25 | Erro quadrático médio (menor = melhor, <0.22 = modelo bom) |
| `lambda_home` (in-play) | float | Gols esperados do mandante **no tempo restante** (não no jogo inteiro) |
| `weather_factor` | float 0.75–1.0 | Multiplicador climático em λ (1.0 = neutro, 0.85 = trovoada) |
| `weather_condition` | string | Label climático: "clear", "cloudy", "drizzle", "rain", "snow", "storm" |
| `half_time` | object | Previsão para o intervalo: `home_win_prob`, `draw_prob`, `away_win_prob`, `over_0_5_prob`, `over_1_5_prob`, `most_likely_score`, `lambda_home`, `lambda_away` |
