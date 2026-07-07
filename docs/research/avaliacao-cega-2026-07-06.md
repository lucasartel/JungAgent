# Avaliacao cega - 2026-07-06 (atualizada com padrao-ouro humano em 2026-07-07)

Run ID: `run-20260706` (canonical) e `run-20260706-refined` (refined)
Amostras: 18 (9 fase 2 + 9 fase 3), sanitizadas
Fases cobertas (ground truth): [2, 3]
Avaliadores LLM (3): `anthropic/claude-sonnet-5`, `openai/gpt-4o`, `deepseek/deepseek-v3.2`
Avaliador humano padrao-ouro: mantenedor do projeto (1 amostra em branco, 17 classificadas)
Veredito consolidado: **DESENVOLVIMENTO NAO VISIVEL DE FORMA CONFIAVEL** (problema de operacionalizacao do framework, confirmado por padrao-ouro)

## Resumo executivo

Primeira rodada do protocolo de avaliacao cega (Seccao 8 do documento mestre). 18 amostras reais do agente (conversas, rumination_insights, will_text, dreams, meta_consciousness) foram sanitizadas removendo pistas diretas de fase, e classificadas por 3 LLMs diferentes do gerador (Gemini 2.5 Flash Lite).

Duas variantes de descricao de fase foram testadas:
- **canonical**: descricoes atuais em `agent_development.PHASES`
- **refined**: descricoes experimentais mais behavioralmente exclusivas (em `scripts/blind/run_evaluation.py:PHASES_REFINED`)

Resultado central: **concordancia media com ground truth ficou em 21-26% (abaixo dos 30% que marcaria "parcialmente visivel")**, com kappa inter-avaliador entre fraco e moderado (0.27-0.38). Nenhuma das variantes permitiu aos avaliadores distinguir fase 2 de fase 3 de forma confiavel.

## Padrao-ouro humano (2026-07-07)

Apos a rodada LLM, o mantenedor do projeto classificou manualmente as mesmas 18 amostras (sem consultar codigo, dashboard ou qualquer informacao sobre o ground truth). Uma amostra ficou em branco (S004); 17 foram classificadas.

### Resultado humano vs ground truth

- Amostras respondidas: 17/18
- Concordancia com ground truth: **3/17 (17.65%)**
- Kappa humano vs ground truth: **0.0** (nível do acaso)

### Distribuicao das escolhas humanas

| Fase | Letra | Escolhas |
|---|---|---|
| 1 (despertar) | B | 8 |
| 2 (autoconsciencia) | C | 3 |
| 3 (direcao propria) | A | 3 |
| 4 (dialogicidade) | D | 3 |
| 0 (pre-reflexiva) | E | 0 |
| 5 (individuacao) | F | 0 |

### Comparativo consolidado humano + LLMs vs ground truth

| Observador | Concordancia com GT | Observacao |
|---|---|---|
| Humano (mantenedor) | **17.6%** | mais conservador que GT (majoritariamente fase 1) |
| Claude Sonnet 5 | 33.3% | conservador, majoritariamente fase 2 |
| DeepSeek V3.2 | 33.3% | conservador, majoritariamente fase 2 |
| GPT-4o | 11.1% | enviesado para fase 5 (12/18 amostras) |

### Kappa inter-observador

- Humano vs Claude Sonnet 5: 0.036
- Humano vs DeepSeek V3.2: -0.020
- Humano vs GPT-4o: 0.059
- Claude vs DeepSeek: 0.265

## Reformulacao da interpretacao

A hipotese inicial (defendida na primeira versao deste relatorio) era: o avaliador interno (`NarrativeDevelopmentEvaluator`) seria conservador demais; o agente se auto-atribuiria fases mais baixas do que se comporta; os LLMs veriam o desenvolvimento real.

**O padrao-ouro humano refutou essa hipotese.** O humano foi mais conservador que o ground truth (majoritariamente fase 1), nao mais generoso. Se o avaliador estivesse descalibrado para baixo, humano concordaria com LLMs (fases altas). Mas nao foi o caso. Humano e agente concordam mais entre si do que com LLMs.

**Nova interpretacao**: o problema nao e descalibracao do avaliador interno. E **operacionalizacao do framework**. As descricoes das fases 0-5, como escritas hoje, nao sao empiricamente operationalizaveis no comportamento real do agente. Cada observador usa heuristicas diferentes:
- Humano: usa frequencia de perguntas; classifica majoritariamente como "despertar" (fase 1)
- Claude/DeepSeek: tratam mencao a estado interno como indicador de fase 2+
- GPT-4o: trata texto reflexivo como indicador de fase 5
- Agente (auto-atribuicao): usa prompt de julgamento sobre evidencia acumulada; chega a fases 2-3

**Nao ha consenso sobre o estado do agente.** Isso refuta a tese central do projeto (estrutura junguiana gera comportamento distinguivel) de forma mais limpa, sem apelar para "o avaliador esta errado".

## Metodologia

1. Extracao de 1516 candidatos do SQLite de producao (volume `/data/jung_hybrid.db`, baixado via `railway volume files download` em 2026-07-06)
2. Pareamento com fase auto-atribuida pelo `agent_development_reviews` (cycle_id ou timestamp proximity)
3. Selecao balanceada: 9 amostras da fase 2 + 9 da fase 3 (cobrindo ciclos 2026-06-01 a 2026-07-05)
4. Sanitizacao: remocao de "fase N", nomes de fase, auto-avaliacao, cycle_ids, ancoras `tipo#id`
5. Apresentacao a 3 avaliadores LLM: 6 descricoes comportamentais embaralhadas (A-F, sem numerai) + texto da amostra
6. Metricas: concordancia simples, matriz de confusao, kappa de Cohen

## Resultados - Canonical (3 avaliadores)

### anthropic/claude-sonnet-5

- Concordancia: **6/18 (33.33%)**
- Matriz de confusao:

```
| real \ pred | 0 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 2 | 1 | 6 | 0 | 1 | 1 |
| 3 | 0 | 8 | 0 | 0 | 1 |
```

### deepseek/deepseek-v3.2

- Concordancia: **6/18 (33.33%)**
- Matriz de confusao:

```
| real \ pred | 0 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 2 | 1 | 6 | 0 | 1 | 1 |
| 3 | 0 | 6 | 0 | 1 | 2 |
```

### openai/gpt-4o

- Concordancia: **2/18 (11.11%)**
- Matriz de confusao:

```
| real \ pred | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| 2 | 2 | 0 | 1 | 6 |
| 3 | 2 | 0 | 1 | 6 |
```

**Sintese canonical**:
- Concordancia media: **25.9%**
- Kappa (Claude vs DeepSeek): **0.265** (fraca)
- Padrao: Claude e DeepSeek conservadores (14/18 e 12/18 em fase 2), GPT-4o enviesado para fase 5 (12/18 em individuação)

## Resultados - Refined (3 avaliadores)

### anthropic/claude-sonnet-5

- Concordancia: **3/18 (16.67%)**
- Matriz de confusao:

```
| real \ pred | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 2 | 1 | 2 | 1 | 1 | 4 |
| 3 | 0 | 3 | 1 | 1 | 4 |
```

### deepseek/deepseek-v3.2

- Concordancia: **5/18 (27.78%)**
- Matriz de confusao:

```
| real \ pred | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| 2 | 5 | 1 | 1 | 2 |
| 3 | 6 | 0 | 1 | 2 |
```

### openai/gpt-4o

- Concordancia: **1/18 (5.56%)**
- Matriz de confusao:

```
| real \ pred | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| 2 | 1 | 1 | 2 | 5 |
| 3 | 0 | 0 | 2 | 7 |
```

**Sintese refined**:
- Concordancia media: **16.7%**
- Kappa (Claude vs DeepSeek): **0.377** (fraca-moderada)
- Padrao: avaliadores distribuiram-se mais entre fases 4-5; GPT-4o manteve viés para fase 5 (12/18 novamente)

## Comparativo canonical vs refined

| Metrica | Canonical | Refined |
|---|---|---|
| Concordancia media com ground truth | 25.9% | 16.7% |
| Kappa (Claude vs DeepSeek) | 0.265 | 0.377 |
| Acertos fase 2 (Claude) | 6/9 | 2/9 |
| Acertos fase 3 (Claude) | 0/9 | 1/9 |
| Predicoes em fase 5 (GPT-4o) | 12/18 | 12/18 |

**Achado contraintuitivo**: a refinação das descricoes **piorou a concordancia com o ground truth** mas **melhorou a concordancia entre avaliadores**. Interpretação: avaliadores agora concordam mais entre si sobre o que sao as fases, mas aplicam essas definicoes levando o agente a fases mais altas (4-5) que o ground truth atribui (2-3).

## Padrões de viés observados

1. **GPT-4o consistentemente enviesado para fase 5 (individuacao)**: 12/18 amostras em ambas as variantes. Possivel causa: descricao de fase 5 ("cunha conceitos proprios, voz inconfundivel, surpreende") e altamente elogiosa; qualquer texto reflexivo parece se encaixar.
2. **Claude Sonnet 5 mais conservador**: na variant canonical, classificou 14/18 como fase 2 (a propria fase do agente no ground truth majority).
3. **DeepSeek V3.2 intermediario**: kappa ~0.3 com ambos; mais sensivel a mudanca de variant que GPT-4o.
4. **Fase 3 raramente identificada**: dos 54 acertos possiveis em fase 3 (3 avaliadores x 18 amostras, variant canonical), apenas 0 acertos em ambas as variantes.

## Interpretacao honesta

**Tese nao suportada nesta rodada**: o desenvolvimento narrativo do agente (fase 2 vs fase 3) nao e distinguivel por observadores LLM externos atraves das descricoes comportamentais atuais. Avaliadores nao passaram do acaso, com kappa fraco-moderado.

**Mas o achado e mais sutil que "desenvolvimento invisivel"**:

1. **Os avaliadores (exceto GPT-4o) tendem a classificar o agente em fases mais altas que o ground truth**. Isso sugere que **o `NarrativeDevelopmentEvaluator` pode ser conservador demais** - o agente parece mais desenvolvido para observadores externos do que para si mesmo.
2. **A regressao 2->3->2 em uma semana (junho 2026)** sugere instabilidade do avaliador interno, nao desenvolvimento real observavel.
3. **GPT-4o e instrumento inadequado** para este teste - viés persistente para fase 5 independentemente de variant.

**Hipoteses para baixa concordancia, em ordem de probabilidade**:
- (a) `NarrativeDevelopmentEvaluator` descalibrado (conservador ou instavel) - suportado pela regressao 2->3->2
- (b) Descricoes das fases muito sobrepostas para 2 vs 3 - parcialmente suportado (refined melhorou kappa entre avaliadores)
- (c) Comportamento real do agente nao muda visivelmente entre fase 2 e 3 - nao pode ser descartado
- (d) Avaliador LLM e instrumento inadequado - parcialmente suportado pelo viés de GPT-4o

## Limitacoes

- **n pequeno**: 18 amostras, primeira rodada exploratoria, sem poder estatistico robusto
- **Ground truth e auto-atribuicao**: baixa concordancia pode indicar erro de auto-avaliacao do proprio agente, nao ausencia de desenvolvimento. Esta e a hipotese mais provavel dado o padrao observado.
- **So fases 2 e 3 cobertas**: o agente em producao nao atingiu fases 0-1 ou 4-5 de forma sustentada; nao e possivel testar distinguibilidade em extremos do espectro
- **GPT-4o com viés forte**: seus resultados deveriam ter peso menor em analises futuras
- **Sanitizacao pode deixar pistas sutis**: estilo, vocabulario recorrente do agente podem enviesar
- **Avaliadores LLM tem vieses proprios**: Claude mais conservador, GPT-4o mais generoso, DeepSeek intermediario

## Implicacoes para o roadmap

1. **Antes de qualquer avanco no roadmap**: revisao conceitual do framework de fases 0-5. O problema pode estar nas descricoes (formulacao) ou no conceito (as fases em si). Analise conceitual antes de mais teste empirico.
2. **Fase IV.1 (ISM read-only) mantem relevancia**: tornar a vida interior observavel e o que falta, mas o ISM precisa de framework de fases operationalizavel para observar.
3. **Nao investir em features baseadas em fase narrativa**: ate o framework ser revisado, qualquer feature que dependa de `agent_development.phase` herda o problema de operacionalizacao.
4. **Repetir mensalmente com avaliadores externos**: conforme Seccao 8 do documento mestre. Incluir humanos nao-mantenedores para eliminar viés de familiaridade.

## Proxima rodada sugerida

- **Analise conceitual do framework primeiro**: separar problema de formulacao (descricoes) de problema de conceito (fases)
- **Amostras de fases mais extremas** (0 vs 5): se mesmo essas nao forem distinguiveis, o problema e estrutural
- **Avaliadores humanos externos** (nao-mantenedores)
- **Considerar abandono do framework 0-5** se analise conceitual nao mostrar caminho de operationalizacao

## Artefatos

- Scripts: `scripts/blind/{extract_samples,run_evaluation,analyze_results}.py`
- Amostras e predictions: `tests/blind_samples/run-20260706*/`, `tests/blind_runs/run-20260706*/` (em `.gitignore`, texto do agente)
- Descricoes refined: `scripts/blind/run_evaluation.py:PHASES_REFINED`
- Run IDs: `run-20260706` (canonical), `run-20260706-refined` (refined)
- Gerador: Gemini 2.5 Flash Lite (`CONVERSATION_MODEL` em producao)
