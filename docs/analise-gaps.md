# Análise de Gaps e Oportunidades — Goat Tips Backend

> Análise realizada em 2026-03-22 sobre o estado atual do projeto.

---

## 1. Avaliação do Modelo Poisson

### O que está bem feito
- A fórmula base (`λ = attack × defense × league_avg`) está correta e segue o framework Dixon-Coles 1997
- A matriz 7×7 de probabilidades de placar é gerada corretamente via produto externo (`np.outer`)
- O cálculo de Over 2.5, BTTS e top 5 placares a partir da matriz está correto
- O fallback em cadeia (pkl local → Azure Blob → treino inline) é robusto
- A remoção de margem da casa de apostas para calcular probabilidades reais está bem implementada

### Gaps identificados no Poisson

| # | Gap | Impacto | Severidade |
|---|-----|---------|------------|
| **P1** | **Correção de Dixon-Coles para placares baixos ausente** — O modelo subestima 0-0, 1-0, 0-1 e 1-1 (representam ~35% dos resultados da PL). O paper original inclui um fator ρ que corrige isso. | Alto | Alto |
| **P2** | **Sem time-decay (ponderação temporal)** — Um jogo de 2014 tem o mesmo peso de um de 2026. Times que subiram recentemente ou mudaram de treinador ficam mal representados. | Alto | Alto |
| **P3** | **Força de ataque/defesa não diferencia mando** — O `_fit_inline()` agrega gols em casa e fora na mesma métrica. Times com forte vantagem em casa perdem essa informação. | Médio | Médio |
| **P4** | **Scripts de treinamento não existem** — O README documenta `scripts/train_model.py` e `retrain/retrain.py` mas nenhum dos dois existe no repositório. O retreinamento semanal não está implementado. | Crítico | Crítico |
| **P5** | **Confiança baseada apenas em "time encontrado no dataset"** — Um time com 200 jogos e outro com 5 recebem a mesma confiança "Alta". | Baixo | Baixo |
| **P6** | **MAX_GOALS = 7 sem normalização** — A soma da matriz não chega a 1.0 (trunca em 6 gols). Deveria normalizar: `mat /= mat.sum()`. | Baixo | Baixo |

---

## 2. Extração de Valor dos Dados — Gaps

### Dados disponíveis vs. utilizados

| Dado | Disponível | Usado no Poisson? | Usado no Analytics? | Usado na Narrativa? |
|------|------------|-------------------|---------------------|---------------------|
| Placar final | Sim | Sim | Sim | Sim |
| Shots on target | Sim (86K rows) | Não | Não | Parcial (display) |
| Possession | Sim | Não | Não | Parcial |
| Dangerous attacks | Sim | Não | Sim (risk score) | Sim |
| Corners | Sim | Não | Não | Parcial |
| Fouls | Sim | Não | Não | Não |
| Offsides | Sim | Não | Não | Não |
| Referee | Sim (nome+id) | Não | Não | Não |
| Stadium | Sim | Não | Não | Não |
| League position | Sim | Não | Não | Não |
| Round/rodada | Sim | Não | Não | Não |
| Odds de fechamento | Sim (20K rows) | Não | Não | Parcial |
| Timeline (subs) | Sim | Não | Não | Não |
| 1st half vs 2nd half stats | Sim (separado por período) | Não | Não | Não |

### Insights de alto valor que poderiam ser extraídos

| # | Insight | Dados necessários | Complexidade |
|---|---------|-------------------|--------------|
| **D1** | **Perfil de árbitro** — média de cartões/jogo, tendência punitiva | `referee_name` + `match_timeline` + `match_stats` | Baixa |
| **D2** | **Vantagem real de mando por estádio** | `stadium_name` + `events` | Baixa |
| **D3** | **Padrão de gols por half** — times que marcam mais no 1T vs 2T | `match_stats` (period = 1st/2nd) | Baixa |
| **D4** | **Corner conversion rate** | `match_stats` (corners + goals por período) | Média |
| **D5** | **Shot efficiency** — proxy de xG sem ter xG | `match_stats` (shots_on_target + goals) | Baixa |
| **D6** | **Forma ponderada por qualidade do adversário** | `events` (home_position/away_position) | Média |
| **D7** | **Odds vs resultado real** — calibração via Brier score | `odds_snapshots` + `events` | Média |
| **D8** | **Padrão de comeback** — % de viradas por time | `match_timeline` + `events` | Média |
| **D9** | **Fatigue/congestion effect** — performance com <4 dias de intervalo | `events` (time_utc por time) | Média |
| **D10** | **Fouls-to-cards ratio** | `match_stats` (fouls + yellow_cards) | Baixa |

---

## 3. Gaps de Infraestrutura

| # | Gap | Descrição |
|---|-----|-----------|
| **I1** | **Pipeline de retreinamento inexistente** | `scripts/train_model.py` e `retrain/` não existem. O modelo nunca é retreinado. |
| **I2** | **Sem validação do modelo** | Não há backtesting, Brier score, ou comparação modelo vs odds. |
| **I3** | **Sem cache no full-analysis** | Cada chamada faz 4+ chamadas BetsAPI + 1 LLM. Sem cache. |
| **I4** | **Risk scores são heurísticas simples** | Thresholds arbitrários sem validação estatística. |

---

## 4. Plano de Ação (priorizado por impacto/esforço)

### Sprint 1 — Fundação

**1.1 Criar `scripts/train_model.py` e `retrain/retrain.py`**
- Implementar o pipeline de treino documentado no README
- Serializar com joblib, upload para Azure Blob
- Configurar Container Apps Job de retreinamento semanal

**1.2 Adicionar time-decay ao Poisson**
- Peso exponencial: `w = ξ^(days_since_match / 365)`, com `ξ = 0.5` (half-life ~1 temporada)
- Alterar `_fit_inline()` para usar weighted average

**1.3 Implementar correção Dixon-Coles (ρ)**
- Fator de correção para placares 0-0, 1-0, 0-1, 1-1
- Estimar ρ por MLE (~0.03–0.05 na PL)

### Sprint 2 — Explorar dados subutilizados

**2.1 Endpoint `/analytics/teams/{name}/profile`**
- Shot efficiency, padrão 1T vs 2T, clean sheet home/away, corner stats

**2.2 Endpoint `/analytics/referees/{name}/stats`**
- Média de cartões, faltas, times beneficiados/prejudicados

**2.3 Endpoint `/analytics/stadiums/{name}/stats`**
- Home win rate, média de gols

**2.4 Enriquecer prompt do LLM**
- Passar perfil de árbitro, stats de estádio e shot efficiency no contexto do GPT-4.1

### Sprint 3 — Validação e calibração

**3.1 Backtesting do modelo**
- Hold-out dos últimos 200 jogos
- Brier score, log-loss, calibration plot
- Comparar com odds de mercado

**3.2 Calibração dos risk scores**
- Validar thresholds nos dados históricos

### Sprint 4 — Refinamentos

**4.1 Separar ataque/defesa por mando**
- `attack_home`, `attack_away`, `defense_home`, `defense_away`

**4.2 Cache no full-analysis**
- TTL 5 min (ao vivo), 1 hora (upcoming)

**4.3 Normalizar a matriz de probabilidades**
- `mat /= mat.sum()`

---

## Resumo

| Área | Status | Potencial desperdiçado |
|------|--------|----------------------|
| Modelo Poisson | Funcional mas básico | ~40% de melhoria possível |
| Dados de stats (86K rows) | Quase não usados | ~70% |
| Dados de timeline | Só gols e cartões | ~50% |
| Dados de odds (20K rows) | Só display | ~80% |
| Referee/stadium | Nunca analisados | 100% |
| Pipeline ML | Não existe | Crítico |
