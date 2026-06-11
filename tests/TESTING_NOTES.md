# TESTING_NOTES.md — Suite de Testes Fase 0.1

Registro de decisoes, limitacoes e descobertas do processo de criacao da suite.

---

## O que foi coberto

| Arquivo de teste | Modulo alvo | Circuitos testados | Testes |
|---|---|---|---|
| `test_rumination_maturity.py` | `jung_rumination.py` | `_calculate_maturity` (todos os 5 fatores), connection_factor fallback via JSON, time_factor saturacao em 7 dias, forced_temporal_synthesis, integridade dos MATURITY_WEIGHTS | 25 |
| `test_loop_failure_policy.py` | `consciousness_loop.py` | `_get_phase_retry_policy` (defaults, DB valido, DB invalido/null/negativo), PHASES (8 fases, ordem, cobertura 0-24h), `_classify_phase_exception` (fatal vs recuperavel), FATAL_EXCEPTION_TYPES | 28 |
| `test_diary_anchors.py` | `agent_diary.py` | PROFILE_SOURCE_RE (13 validos, 10 invalidos, extracao multipla), `_as_text` (truncamento real, whitespace, None), `_json_loads` (valido/invalido/passthrough), `_safe_float` | 50 |
| `test_development_phases.py` | `agent_development.py` | PHASES (6 fases, campos, imutabilidade), SOURCE_RE, `_as_text`, `_json_loads`, `_extract_json_object` (JSON puro/markdown/embedded), NarrativeDevelopmentEvaluator (schema, coerce, govern_phase, build_evidence, valid_review, evaluate sem LLM) | 61 |

**Total: 164 testes, todos passando (0 xfail, 0 skip).**

---

## O que ficou de fora e por que

### 0. Runner de regressao cognitiva (Fase 0.3)

`tests/regression_runner.py` executa os cenarios canonicos de `tests/scenarios/`
em tres modos:

```bash
python tests/regression_runner.py --mock
python tests/regression_runner.py --live --model deepseek/deepseek-v4-flash
python tests/regression_runner.py --diff tests/regression_runs/run_a.json tests/regression_runs/run_b.json
```

O modo `--mock` e deterministico, offline e roda no CI. Ele cobre propriedades
mecanicas dos cenarios: formula de maturidade, sintese temporal, sinais de sonho,
identidade, will e conversas-tipo.

O modo `--live` exige `OPENROUTER_API_KEY` e e bloqueado em CI. Ele nao foi
executado nesta entrega para evitar custo e chamada externa sem uma decisao
explicita de modelo/custo pelo mantenedor. O primeiro diff live deve ser anexado
a estas notas quando o mantenedor aprovar a execucao local.

Diff mock D2, apos remover o hardcode `7.0` da formula de maturidade:
`python tests/regression_runner.py --diff .tmp_before_d2_mock.json .tmp_after_d2_mock.json`
retornou `changed_count=0`. Isso e esperado porque `MAX_DAYS_FOR_SYNTHESIS`
continua valendo 7; a mudanca preserva o comportamento atual e evita divergencia
futura se a constante mudar.

### 1. Digestao completa de tensoes (`_process_digest_cycle`)

A logica de `forced_temporal_synthesis` esta embutida em `_process_digest_cycle`,
que exige:
- Banco populado com tensoes reais (incluindo `source_kind`, `source_table`, etc.)
- Chamadas LLM para gerar simbolos de sintese (prompt de julgamento)
- `RuminationEngine._find_related_tensions` que usa busca por similaridade textual

Coberto indiretamente: os valores das constantes (`MAX_DAYS_FOR_SYNTHESIS`,
`MIN_MATURITY_FOR_SYNTHESIS`) e a formula booleana de decisao sao testados
em `test_rumination_maturity.py::TestForcedTemporalSynthesis`. O calculo de
maturidade (`_calculate_maturity`) e testado isoladamente com casos sinteticos.

**Para cobrir completamente**: o Runner de Regressao (Tarefa 0.3) devera executar
`_process_digest_cycle` contra estados sinteticos canonicos com LLM mockado.

### 2. `_assess_phase_retry` (ConsciousnessLoopManager)

Este metodo e testavel offline, mas depende de `_get_phase_retry_policy` (ja testado)
e `_get_latest_phase_attempt` que exige linhas na tabela
`consciousness_loop_phase_results`. Optou-se por testar os componentes individuais
ao inves de montar fixtures de BD mais pesadas nesta entrega.

**Para cobrir**: adicionar fixture que insere uma linha de resultado com `status=failed`
e testar o path `should_retry=True` vs `should_retry=False`.

### 3. `AgentDiaryWriter` (agent_diary.py)

O construtor e os metodos de escrita do `AgentDiaryWriter` dependem de um esquema
de BD completo (~20 tabelas) que nao e criado pelos modulos testados.
O schema completo esta em `core/database.py` (HybridDatabaseManager, 124KB).
Criar o schema completo em memoria para testes unitarios seria acoplamento excessivo
ao estado atual do monolito.

**Para cobrir**: apos a decomposicao de `core/database.py` (Tarefa 0.7),
criar fixture baseada no schema extraido para `core/db/`.

### 4. `NarrativeDevelopmentEvaluator.evaluate` com LLM

O caminho `use_llm=True` chama `get_llm_response` de `llm_providers.py`.
Testado apenas o caminho `use_llm=False` (fallback deterministico) e o caso
`no_narrative_evidence`. O caminho LLM requer mock de `llm_providers.get_llm_response`.

**Para cobrir**: no Runner de Regressao (Tarefa 0.3), mockar `get_llm_response`
e testar o path de avaliacao com diferentes respostas sinteticas do LLM.

### 5. Scripts de migracao e utilitarios one-shot

Arquivos como `migrate_*.py`, `apply_agent_identity_migration.py`,
`force_apply_identity_migration.py`, `setup_instance.py`, `endojung_snapshot_export.py`
importam dependencias externas (qdrant, telegram) no nivel de modulo e sao scripts
de execucao unica. Excluidos da compilacao no CI via lista explicita.
Nao tem testes — sao scripts operacionais, nao logica de dominio.

---

## Descobertas sobre o codigo de producao

### D1. `_as_text` — comportamento de truncamento (agent_diary.py e agent_development.py)

O truncamento usa `text[:limit-1].rstrip() + "..."`, produzindo saida de
`limit + 2` caracteres no pior caso (string sem espacos no final).
Isso e diferente do que o nome `limit` sugere. O comportamento e consistente
entre os dois modulos e foi documentado nos testes.

**Nao e um bug** — a saida e previsivel e o codigo e intencional. Mas qualquer
consumidor que assuma `len(result) <= limit` estara errado. Considerar renomear
o parametro para `target_length` ou ajustar a formula para `text[:limit-3] + "..."`.

### D2. `_calculate_maturity` usa `max(connection_count, len(json_ids))`

O fallback de `connection_count` via `connected_tension_ids` JSON e silencioso:
se `connection_count=3` e `len(json_ids)=1`, o valor 3 prevalece (via `max`).
Se `connection_count=0` e o JSON e invalido, silenciosamente retorna 0.
Comportamento verificado e testado; nao e um bug, mas e um contrato implicito
que nao estava documentado.

### D3. Constante `MAX_DAYS_FOR_SYNTHESIS` agora governa o time_factor

Resolvido na Fase 0.3/D2: `_calculate_maturity` e o runner mock usam
`time_factor = min(1.0, days / MAX_DAYS_FOR_SYNTHESIS)`. O diff mock antes/depois
nao alterou os 20 cenarios canonicos porque a constante permanece em 7.

Historico da pendencia original:

`time_factor = min(1.0, days / 7.0)` usa 7 hardcoded, enquanto
`MAX_DAYS_FOR_SYNTHESIS = 7` esta em `rumination_config.py`. Se alguem alterar
`MAX_DAYS_FOR_SYNTHESIS` no config, o `time_factor` NAO mudara junto — a formula
usa a constante 7.0 diretamente. Considerar usar
`min(1.0, days / MAX_DAYS_FOR_SYNTHESIS)` para consistencia.
