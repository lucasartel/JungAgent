# Corte 1.2 - Estado de progresso (pausa em 2026-07-07)

**Branch**: main (Corte 1 commitado e deployado em 319e6ed)
**Próxima retomada**: Corte 1.2 — integração do `relational_state` no `will_engine`

## Contexto operacional

- **Corte 1 deployado em produção**: schema `relational_state` criado, mixin ativo em `HybridDatabaseManager`, engine funcional standalone
- **312 testes verdes**, regression_runner --mock pass sobre 20 cenários
- `/health` saudável em produção

## Plano do Corte 1.2 (decidido, não iniciado)

**Objetivo**: fechar o circuito entre o subsistema relacional e a vontade. Hoje o `relational_state` é passivo (produz snapshots que ninguém lê). Após o Corte 1.2, o `WillEngine.refresh_cycle_state` lerá o snapshot como insumo, e o `agent_stance` vira campo do `will_state`.

### Decisões de design já tomadas

1. **NÃO mexer no prompt de julgamento do LLM** (`will_engine._generate_with_llm`). É prompt crítico (regra do AGENTS.md exige regression runner + diff para mudar). Em vez disso, adicionar `agent_stance` ao estado via fallback determinístico após a chamada LLM, sem mudar o schema JSON de saída que o LLM produz.
2. **Adicionar `relational_state` ao payload do prompt** (input adicional, não output). O LLM recebe mais contexto mas continua produzindo o mesmo schema.
3. **Schema do `agent_will_states`**: o estado é salvo como JSON em `source_summary_json` ou similar — não precisa de `ALTER TABLE`. Validar onde exatamente o estado é serializado antes de implementar.

### Passos a executar (em ordem)

1. **Adicionar método `_latest_relational_state(self, user_id)` ao `WillEngine`** (linha ~485, ao lado de `_latest_dream`, `_latest_meta_consciousness`, etc.). Padrão:
   ```python
   def _latest_relational_state(self, user_id: str) -> Optional[Dict[str, Any]]:
       try:
           return self.db.get_latest_relational_state(
               agent_instance=self.agent_instance, user_id=user_id,
           )
       except Exception as exc:
           logger.debug("WillEngine: sem estado relacional: %s", exc)
           return None
   ```
   **Atenção**: confirmar se `WillEngine.__init__` tem `self.agent_instance`. Caso contrário, ler de `instance_config.AGENT_INSTANCE`.

2. **Em `_build_source_payload`** (linha 493-), adicionar chamada `relational = self._latest_relational_state(user_id)` e incluir no return:
   ```python
   "relational_state": {
       "agent_stance": relational.get("agent_stance"),
       "silence_delta_hours": relational.get("silence_delta_hours"),
       "cadence_baseline_hours": relational.get("cadence_baseline_hours"),
       "recurring_themes": [t.get("theme") for t in relational.get("recurring_themes", [])][:5],
       "affective_tone": relational.get("affective_tone_recent", {}),
   } if relational else None,
   ```

3. **Em `refresh_cycle_state`** (linha 919), após obter `state` (LLM ou fallback), enriquecer com `agent_stance`:
   ```python
   if not state.get("agent_stance"):
       relational = self._latest_relational_state(user_id)
       if relational:
           state["agent_stance"] = relational.get("agent_stance")
   ```
   Isso garante que `agent_stance` está sempre presente no estado final sem mudar o schema que o LLM produz.

4. **Em `_fallback_state`** (linha 635), também incluir `agent_stance` no retorno se disponível. Verificar se `_fallback_state` tem acesso ao payload (sim, recebe `payload`) — extrair de `payload.get("relational_state", {}).get("agent_stance")`.

5. **Atualizar prompt do LLM** (`_generate_with_llm` linha 813): incluir no prompt uma instrução como "considere o estado relacional abaixo ao formular a vontade" + o bloco `relational_state`. **NÃO mudar o schema JSON de saída** que o LLM produz — só enriquecer input.

6. **Criar `tests/test_will_engine_relational.py`** — testes cobrindo:
   - `_latest_relational_state` retorna None quando mixin ausente (DB sem schema)
   - `_latest_relational_state` retorna dict quando há snapshot
   - `refresh_cycle_state` enriquece estado com `agent_stance` mesmo em fallback
   - Payload do prompt inclui `relational_state` quando disponível, `None` quando não
   - Monkeypatch do LLM para validar que o schema de saída não mudou

7. **Validação final**:
   - `pytest tests/ -q` verde
   - `regression_runner.py --mock` pass (sem diff nos 20 cenários, porque o mock não usa LLM)
   - `git diff --check` limpo
   - py_compile em will_engine.py

8. **Branch + commit + push**: `fase-iii/corte1-2-will-relational-integration`. Deploy single-shot com confirmação.

### Pontos de atenção para amanhã

- **AGENTS.md regra de prompts críticos**: mudar input do prompt é mais brando que mudar schema de saída, mas ainda é sensível. Documentar bem no PR se houver mudança visível de comportamento.
- **Custo LLM**: o novo input pode aumentar o tempo de geração (mais tokens no prompt). Verificar tamanho do `recurring_themes` (limite a 5) e `affective_tone_recent` (já é pequeno).
- **Testar com regression_runner --live** (decisão do mantenedor): para ver se a adição de contexto relacional muda os outputs do LLM nos 4 cenários `will_*` do canonical. Se mudar, é diferença a documentar.
- **Smoke check em produção após deploy**: rodar um sync_loop manual via admin endpoint e ver se `agent_stance` aparece no `agent_will_states` final.

### Referências rápidas

- Corte 1 mixin: `core/db/relational_state.py:RelationalStateDatabaseMixin`
- Corte 1 engine: `engines/relational_state.py:RelationalStateEngine`
- Corte 1 tests: `tests/test_relational_state.py` (19 testes, todos verdes)
- WillEngine alvo: `will_engine.py:493` (`_build_source_payload`), `:919` (`refresh_cycle_state`)
- Schema SQLite: já em produção via `core/db/schema.py:1115`
- Plano de protagonismo original: este Corte 1.2 fecha o substrato relacional; depois segue para Corte 2 (`action_catalog` + `action_proposer`)

## Pendências menores

- Atualizar `tests/TESTING_NOTES.md` com a cobertura de relational_state (19 testes novos).
- Decidir se `relational_state` deve ser exposto no admin web (dashboard de relação) — pode ser Corte 1.3 separado.
