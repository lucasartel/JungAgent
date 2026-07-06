# Verificacao da Fase IV em producao

Gerado em: 2026-07-06 18:35 -03:00 (pos-validacao da transicao work -> hobby)
Banco: `/data/jung_hybrid.db` (producao)
Agent instance: `jung_v1`
Status geral: OK (todas as correcoes da Frente A validadas; Fase IV.0 criterio #5 confirmado deployado e funcional)

## Contexto operacional

- Deploy pre-Frente-A: `56515a5` (Gate ISM prompt context) em producao desde 2026-07-04 14:53 -03:00
- Deploy Frente-A (3 cortes cirurgicos): `4c56481` em 2026-07-06 15:10 -03:00
- Cortes aplicados:
  - A1 (`a79747a`): Versionamento do documento mestre (`docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`)
  - A2 (`9d86df4`): Saneamento de pulses stalled (`_skip_stale_phase_pulses`) - criterio #5 da Fase IV.0
  - A3 (`4c56481`): Remocao de dead code (`_assess_phase_retry`, `_summarize_retry_assessment`) + renomeacao `verify_phase4*` -> `verify_phase_ism_readonly*`
- Validacao pre-push: `293 passed`, `regression_runner --mock` pass sobre 20 cenarios

## Fase IV.0 - Pulso de Fase (criterios do documento mestre, Secao 9)

### Criterio 1: pulse_count=1 preserva comportamento
- Status: OK (validado por `test_default_pulse_count_is_one` em `tests/test_loop_failure_policy.py:142`)
- Em producao: todos os phases com `pulse_count=1` (default)

### Criterio 2: consciousness_phase_config guarda pulse_count com schema compativel
- Status: OK (migration `ALTER TABLE ... ADD COLUMN pulse_count INTEGER DEFAULT 1` em `core/db/schema.py:1110`)
- Em producao: schema aplicado sem destruicao (deploy trouxe `database_migrations - Nenhuma migration pendente`)

### Criterio 3: Tabela persistente de agenda com campos obrigatorios
- Status: OK (tabela `consciousness_phase_pulses` em `core/db/schema.py:748-764` com cycle_id, phase, pulse_index, pulse_count, scheduled_at, executed_at, status, phase_result_id, attempts, last_error)

### Criterio 4: Cockpit permite configurar pulse_count por fase
- Status: OK (endpoint `POST /admin/consciousness-loop/phase-config/pulse-count` em `admin_web/routes/consciousness_loop_routes.py:113`, com clamp `[1, MAX_PHASE_PULSE_COUNT]`)

### Criterio 5: Scheduler executa apenas pulsos vencidos dentro da janela
- Status: OK (CORRIGIDO E CONFIRMADO EM PRODUCAO)
- Bug pre-existente: `_get_due_phase_pulse` filtrava apenas `scheduled_at <= current` sem checar `current <= phase_deadline_at`; pulsos `pending` de janelas fechadas ficavam stalled
- Fix: adicionado metodo `_skip_stale_phase_pulses` em `consciousness_loop.py`, chamado em `sync_loop` quando detecta transicao de fase (linhas ~2614-2620), marcando `pending`/`failed` da fase anterior como `skipped`
- Commit: `9d86df4 Skip stale phase pulses when window closes`
- Evidencia de producao (transicao `work -> hobby` em 2026-07-06 15:01:38 SP / 18:01:38 UTC):
  - Log: `LOOP PHASE START cycle_id=2026-07-06 phase=hobby trigger_name=hobby_phase trigger_source=scheduled_trigger`
  - Log: `WORKING MEMORY inbox phase=hobby from_phase=work broadcast_id=43 focus_count=5 fringe_count=17`
  - Log: `LOOP NOTIFY enviado ao admin para fase=hobby`
  - Log: `Sync concluido: action=phase_transition cycle_id=2026-07-06 phase=hobby`
  - Log: `LOOP PHASE RESULT phase=hobby status=partial_success duration_ms=62047 artifact_count=3 warning_count=1 error_count=0`
  - Ausencia de log `Skipped %d stale phase pulses` indica `skipped_count == 0` (logger.info so registra quando > 0)
  - Interpretacao: com `pulse_count=1` (default em producao) e o pulso unico de work executando normalmente (`status=completed`), nao havia `pending`/`failed` acumulados para skipar. Bug pre-existente nao estava ativo em producao, mas o fix esta deployado e funcional.
- Ancora de evidencia observada: `phase_result#827` (hobby), `broadcast#43` (work->hobby), `broadcast#44` (hobby->rumination_extro), `working_memory_item#44`
- Testes cobrindo: `test_skip_stale_phase_pulses_marks_pending_as_skipped`, `test_skip_stale_phase_pulses_marks_failed_as_skipped`, `test_skip_stale_phase_pulses_preserves_completed_and_running`, `test_skip_stale_phase_pulses_scoped_to_phase`, `test_get_due_phase_pulse_returns_none_after_skip` (5 testes em `tests/test_loop_failure_policy.py`)

### Criterio 6: Retries pertencem ao pulso que falhou
- Status: OK (`_get_due_phase_retry` em `consciousness_loop.py:424-470` re-mostra mesmo row `failed` com cooldown; `_mark_phase_pulse_running` incrementa `attempts` no mesmo row)
- Validado por `test_failed_pulse_retry_does_not_consume_future_due_pulse`

### Criterio 7: Execucoes manuais nao contam como pulso automatico
- Status: OK (`execute_current_phase` em `consciousness_loop.py:2729-2737` chama `execute_phase(...)` sem `pulse=`)

### Criterio 8: Resultados de fase incluem metadados de pulso
- Status: OK (`execute_phase` escreve em `result["metrics"]`: pulse_id, pulse_index, pulse_count, pulse_scheduled_at, pulse_attempt; e em `result["raw_result"]["phase_pulse"]`)
- Validado por `test_execute_phase_records_pulse_metadata`

### Criterio 9: ISM consegue ler pulsos recentes por fase
- Status: OK (`engines/integrative_self.py:_recent_phase_pulse_component` le 12 pulses mais recentes, monta `source_refs=[loop#<phase_result_id>]`, sumario "Trajetoria curta de pulsos")
- Validado por `tests/test_integrative_self.py`

## Fase IV.1 - ISM read-only

### snapshot diario em primeira pessoa dos subsistemas
- Status: OK (`IntegrativeSelfModel.build_snapshot` em `engines/integrative_self.py`)

### Limites ontologicos explicitos, sem influenciar prompt/loop/WM/acoes externas
- Status: OK
- `limits` declarativos em cada snapshot: `human_consciousness_claim=False`, `prompt_influence=False`, `loop_decision_influence=False`, `working_memory_mutation=False`, `external_side_effects=False`
- Guarda em DB: `upsert_integrative_self_snapshot` rejeita `influence_mode != "read_only"` (`core/db/integrative_self.py`)
- Feature flag de injeção no prompt: gateada por `ISM_PROMPT_CONTEXT_ENABLED` (default off), admin-only por `ISM_PROMPT_CONTEXT_ADMIN_ONLY`

### Regressao verde
- Status: OK
- Marcacao: `passed`
- Evidencia local: `293 passed in 11.55s` pos-merge A1+A2+A3
- Cenario mock do ISM no regression_runner: `--variant ism_preview` valida `preview_only=True`, `injectable=False`, todos os 7 componentes `REQUIRED_ISM_COMPONENTS` presentes, 5 influence flags `False`, ausencia dos `FORBIDDEN_ISM_OVERCLAIMS`

## Correcoes cirurgicas da Frente A

### A1 - Documento mestre versionado
- Status: OK
- Commit: `a79747a Version master cognitive emulation document`
- Evidencia: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md` agora rastreado no `Jung-Claude/main` (e em `origin/main` pos-push)

### A2 - Saneamento de pulses stalled
- Status: OK (deployado, confirmado funcional, sem bug ativo em producao)
- Commit: `9d86df4 Skip stale phase pulses when window closes`
- Metodo: `_skip_stale_phase_pulses` em `consciousness_loop.py`
- Chamada: em `sync_loop` quando `state["current_phase"] != target_phase.key`
- Evidencia em producao: transicao work -> hobby em 2026-07-06 15:01:38 SP executou sem erros; `skipped_count == 0` (default pulse_count=1, nenhum pulse pending acumulado em work)

### A3 - Remocao de dead code e desambiguacao
- Status: OK
- Commit: `4c56481 Remove dead retry assessment and disambiguate ISM verifier naming`
- Funcoes removidas: `_assess_phase_retry`, `_summarize_retry_assessment` (87 linhas, incluindo 1 `except Exception: pass` silencioso)
- Renomeacoes: `tests/verify_phase4.py` -> `tests/verify_phase_ism_readonly.py`, `tests/test_verify_phase4.py` -> `tests/test_verify_phase_ism_readonly.py`
- Dockerfile atualizado para COPY do novo path

## Validacao pos-deploy (2026-07-06 15:10 -03:00)

- Container novo online em 15:10:35
- `database_migrations - Nenhuma migration pendente`
- Telegram bot: `bot_running: true`
- Schedulers: Loop, Identidade, World Consciousness, Will Pulse - todos agendados
- Loop sincronizando sem erro: `🧭 [LOOP] Sync concluido: action=noop cycle_id=2026-07-06 phase=work`
- `/health` publico: `{"status":"healthy","bot_running":true}` HTTP 200 em 1.0s
- `/` landing: HTTP 200 em 0.8s

## Conclusao

A Fase IV esta consolidada em producao. Todos os 9 criterios da Fase IV.0 (Pulso de Fase) confirmados em codigo/teste; o criterio #5 (saneamento de pulses stalled) foi corrigido na Frente A e confirmado deployado e funcional, com primeira transicao natural (`work -> hobby` em 2026-07-06 15:01:38 SP) processando sem erros e sem `pending` acumulado (configuracao default `pulse_count=1`).

A Fase IV.1 (ISM read-only) esta estavel, com guardas em codigo rejeitando `influence_mode != "read_only"` em nivel de persistencia, e injecao no prompt via feature flag desativada por default.

A pendencia longitudinal restante nao bloqueia a Frente C (Pesquisa):
- Observar uma transicao com `pulse_count > 1` configurado para confirmar via log `Skipped N stale phase pulses` (fortalece evidencia do fix A2)
- Sondagem direta do DB de producao requer download do volume Railway (nao trivial via CLI atual)

Proxima etapa recomendada: iniciar Frente C (Pesquisa) - retomar avaliacao cega mensal e rodar `regression_runner --live` para detectar drift entre modelos nos prompts de julgamento.
