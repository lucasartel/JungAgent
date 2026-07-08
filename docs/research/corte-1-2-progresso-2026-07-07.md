# Corte 1.2 - Concluído (2026-07-08)

**Branch**: `fase-iii/corte1-2-will-relational-integration` (mergeada em `1cd0a7c`)
**Deploy**: push para origin/main em 2026-07-08 10:24 UTC, container novo online, `/health` saudável

## O que foi entregue

### Código
- `will_engine.py` (+48 linhas):
  - `_latest_relational_state(user_id)` — busca snapshot relacional via mixin
  - `agent_instance` no `__init__` (lê de `instance_config` com fallback)
  - `relational_state` incluído no payload do `_build_source_payload`
  - `agent_stance` enriquece estado após LLM/fallback (nunca sobrescreve LLM)
  - `_save_state` persiste `agent_stance` em nova coluna
- `core/db/schema.py` (+5 linhas): `ALTER TABLE agent_will_states ADD COLUMN agent_stance TEXT` com guarda
- `tests/test_will_engine_relational.py` (8 testes): DB lookup, payload, enriquecimento LLM+fallback, não-overwrite

### Validação
- 320 testes verdes (+8)
- regression_runner --mock pass sem diff nos 20 cenários (incluindo os 4 will_*)
- py_compile limpo
- git diff --check limpo
- /health saudável pós-deploy
- Schema "criado/verificado" log confirma execução das migrations

## Decisões de design aplicadas

1. **NÃO mexi no schema JSON de saída do LLM** — só enriqueci input. Confirmado por regression_runner --mock sem diff.
2. **`agent_stance` é fallback determinístico** — nunca sobrescreve o que o LLM produz (testado explicitamente).
3. **Coluna `agent_stance` adicionada via `ALTER TABLE` com guarda** — aderente à regra de schema compatível do AGENTS.md.

## Pendências deixadas

- **Validação completa da coluna em produção**: o download do DB via `railway volume files` foi interrompido por timeout. Confirmar amanhã via probe ou download estável que a coluna `agent_stance` existe em `agent_will_states` no SQLite de produção.
- **Smoke check comportamental**: o próximo `sync_loop` na fase `will` (~22:00 SP) deve produzir um `agent_will_states` com `agent_stance` preenchido. Verificar via `railway run python scripts/remote_db_probe.py will --pretty` amanhã.
- **Atualizar `tests/TESTING_NOTES.md`**: 320 testes agora, não documentado desde 164 (Fase 0.1).

## Atualizacao 2026-07-08 - fechamento curto

- `RelationalStateEngine.refresh()` foi acoplado ao `ConsciousnessLoopManager` antes do `WillEngine` nas fases `identity` e `will`, sem criar fase nova nem chamada LLM adicional.
- `remote_db_probe.py will` passou a expor `agent_stance`, e o novo probe `relational_state` permite validar snapshots em producao.
- Deploy Railway `ba6b940e-7eed-41cc-a082-114e7a1e47ef` subiu com sucesso. O probe `relational_state` esta disponivel, mas ainda sem linhas ate a proxima execucao real de `identity` ou `will` apos o deploy.

## Próximo passo recomendado

**Corte 2**: `engines/action_catalog.py` (registro de tipos de ação) + `engines/action_proposer.py` (lê `will_state` + `relational_state` + `working_memory`, propõe ações). Subsistema que permite ao agente propor ações com base nas vontades.

Outras opções: smoke check em produção primeiro, ou pausa.

## Estado do plano de protagonismo

- ✅ Corte 1 (relational_state standalone)
- ✅ Corte 1.2 (integração no will_engine)
- 🔜 Corte 2 (action_catalog + action_proposer)
- 🔜 Corte 3 (controlled_action expandido + 5 ações iniciais)
- 🔜 Corte 4 (engines/research_action — reuso scholar_engine)
- 🔜 Corte 5 (engines/expressive_action)
- 🔜 Corte 6 (integração ao loop)

## Referências rápidas

- Corte 1 commit: `319e6ed`
- Corte 1.2 commit: `1cd0a7c`
- Schema SQLite: `core/db/schema.py` (migrations com guarda)
- Testes: `tests/test_relational_state.py` (19) + `tests/test_will_engine_relational.py` (8)
