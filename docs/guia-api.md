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

```bash
curl "$BASE/predictions/?home=Arsenal&away=Chelsea"
```

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
| Narrativa completa | `GET /predictions/{id}/full-analysis` | Sob demanda (botão) |

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
