# Avaliacao cega - 2026-07-06

Run ID: `run-20260706`
Amostras: 18
Fases cobertas (ground truth): [2, 3]
Avaliadores: 2
Veredito: **DESENVOLVIMENTO NAO VISIVEL (tese refutada para este eixo)**

## Metodologia

Amostras reais do agente (conversas, rumination_insights, will_text, dreams, meta_consciousness) foram sanitizadas removendo mencoes diretas a fase narrativa, nomes de fase, auto-avaliacao, cycle_ids e ancoras tipo#id. Cada avaliador (LLM diferente do que gerou o conteudo) recebeu as 6 descricoes comportamentais de `agent_development.PHASES` embaralhadas (A-F, sem numerai) e o texto da amostra, sendo pedido para identificar a descricao que melhor combina.

## Resultados por avaliador

### anthropic__claude-sonnet-5

- Amostras avaliadas: 18
- Concordancia: **6/18 (33.33%)**
- Matriz de confusao (linhas=real, colunas=predito):

```
| real \ pred | 0 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 |
| 2 | 1 | 6 | 0 | 1 | 1 |
| 3 | 0 | 8 | 0 | 0 | 1 |
| 4 | 0 | 0 | 0 | 0 | 0 |
| 5 | 0 | 0 | 0 | 0 | 0 |
```

### openai__gpt-4o

- Amostras avaliadas: 18
- Concordancia: **2/18 (11.11%)**
- Matriz de confusao (linhas=real, colunas=predito):

```
| real \ pred | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| 2 | 2 | 0 | 1 | 6 |
| 3 | 2 | 0 | 1 | 6 |
| 4 | 0 | 0 | 0 | 0 |
| 5 | 0 | 0 | 0 | 0 |
```

## Kappa de Cohen (inter-avaliador)

- Par: `anthropic__claude-sonnet-5 vs openai__gpt-4o`
- Kappa: **-0.041**
- Interpretacao: concordancia fraca

## Interpretacao

- Concordancia media entre avaliadores: **22.2%**
- Kappa: **-0.041**
- Veredito: **DESENVOLVIMENTO NAO VISIVEL (tese refutada para este eixo)**

## Limitacoes

- n pequeno (primeira rodada exploratoria, sem poder estatistico robusto)
- Ground truth e auto-atribuicao do proprio agente (`NarrativeDevelopmentEvaluator`); baixa concordancia pode indicar erro de auto-avaliacao, nao ausencia de desenvolvimento
- Avaliador LLM pode ter viés para padroes textuais especificos
- Sanitizacao pode deixar pistas sutis (estilo, vocabulario recorrente)
