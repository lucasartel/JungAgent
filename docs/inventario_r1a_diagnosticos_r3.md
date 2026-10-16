# Inventário R1a + Diagnósticos R3 — Primeira Entrega

**Data:** 2026-10-12 · **Base:** `886441d` (main, PRs #59/#60 integradas)
**Natureza:** documento de escrita. Leitura estática do código. Suíte não reexecutada neste passo. Nenhum segredo, credencial ou dado de produção reproduzido.
**Fonte:** dois relatórios de varredura (produtores/filas e leitores/escopos) cruzados com `core/db/cognitive_ownership.py`, `relation_scope.py`, `engines/will_scope.py`, `work/tenancy.py`.

---

## 1. Contexto

**R1** é *assimilação cumulativa multiorigem*: o circuito entre receber, elaborar, consolidar, recuperar e usar conhecimento ao longo do tempo. A lacuna não é ausência total de persistência — é o **circuito incompleto** entre esses cinco momentos.

**R3** é *confiabilidade*: impedir promoção indevida de insights incompletos, diagnosticar causas reais dos fallbacks WILL/metaconsciência, fechar a operação da proatividade textual.

Esta entrega apresenta: (A) inventário de produtores e filas, (B) inventário de leitores e escopos, (C) lacunas demonstradas com severidade, (D) diagnósticos R3, (E) decomposição proposta em subcortes.

---

## 2. Inventário A — Produtores de conhecimento

### 2.1 Ingestão de PDFs

| Produtor | Arquivo·Função | Tabela | Escopo |
|---|---|---|---|
| Extração de texto | `engines/work_task_manager.extract_pdf_text` | nenhum (retorna dict) | — |
| Upload de anexo | `work/attachments.save_project_attachment` | `work_project_attachments` | Work tenancy (Relation herdada) |
| Persistência de extração | `work/attachments._extract_and_store_project_pdf` | `work_project_attachments` (UPDATE `extracted_text`) | idem |
| **Legado R6** | `core/db/work_tasks.create_work_task_attachment` | `work_task_attachments` | `agent_instance` |

**Nota:** o texto extraído fica embutido no campo `extracted_text`, não vira linha própria de "conhecimento". Consumidores: `work/package_builder` (inclui no prompt), `get_project_attachment_text`, `read_project_pages`.

### 2.2 Extração de fatos

| Produtor | Arquivo·Função | Tabela | Escopo |
|---|---|---|---|
| Regex (legado) | `core/db/fact_extraction.extract_and_save_facts` | `user_facts` | Relation |
| LLM (v2) | `core/db/fact_extraction.extract_and_save_facts_v2` | `user_facts_v2` + `knowledge_gaps` | Relation |
| Flush pré-compaction | `memory_flush._extract_and_persist` | `user_facts_v2` | ⚠ sem Relation (legado) |
| Proatividade da vontade | `engines/will_proactive_record._invoke` | `user_facts_v2` | Relation |

**⚠ Bug: `memory_flush.py` L113 desempacota 2 valores de `extract_facts()` que retorna 3 — `ValueError` engolido pelo `except` → fatos do flush silenciosamente descartados.**

### 2.3 Fragmentos de ruminação

| Origem | Arquivo·Função | `source_kind` |
|---|---|---|
| Conversa | `core/db/conversations.save_conversation` hook | `conversation` |
| Loop (fases) | `consciousness_loop._feed_loop_failure_to_rumination` | sintético |
| Sonho | `dream_engine._insert_dream_rumination_fragment` | `dream` |
| Trabalho/leitura | `work/engine.record_work_experience` + `work/persistence` | `work`, `work_reading` |

Cascata adiante: `rumination_fragments` → `detect_tensions` → `rumination_tensions` (status `open`) → `digest` (maturidade) → `check_and_synthesize` → `rumination_insights` (status `ready`).

### 2.4 Extração de identidade

| Produtor | Tabelas |
|---|---|
| `agent_identity_extractor.store_extracted_identity` | `agent_identity_core`, `agent_identity_contradictions`, `agent_possible_selves`, `agent_relational_identity`, `agent_self_knowledge_meta`, `agent_agency_memory`, `agent_narrative_chapters` |
| `identity_rumination_bridge` | `agent_identity_contradictions` (`unresolved`), `agent_possible_selves` (`active`), `agent_identity_core` |

Escopo: `agent_instance` + `origin_relation_id` via `_identity_origin`.

### 2.5 Trabalho (Work)

| Produtor | Tabela | Escopo |
|---|---|---|
| `work/persistence.create_artifact_for_brief` | `work_artifacts` (`composed`) | Work tenancy |
| `work/persistence._persist_reading_package` | `work_artifacts` (`assimilated`/`blocked`) | Work tenancy |
| `engines/expressive_action.handle_compose_essay_draft` | `work_artifacts` (`essay_draft`) | ⚠ **sem colunas de tenancy** |
| `engines/synthesis_action.handle_synthesize_cross_source` | `work_artifacts` (`synthesis_note`) | ⚠ **sem colunas de tenancy** |
| `hobby_art_engine._save_artifact` | `agent_hobby_artifacts` | `agent_instance` |

### 2.6 Conhecimento de mundo

| Produtor | Tabela | Escopo |
|---|---|---|
| `world_consciousness._persist_epistemic_knowledge_gap` | `knowledge_gaps` (`open`) | `instance_global` + `authorized_aggregate` |
| `core/db/fact_extraction` (gaps LLM) | `knowledge_gaps` (`open`) | Relation |
| `scholar_engine.conduct_research` | `external_research` (`active`) | `agent_instance` + `finding_scope` |

**Dois caminhos de `knowledge_gaps`** com `origin_class` distinto escrevem na mesma tabela — relevante para qualquer leitura agregada R1.

### 2.7 Fatos, padrões e marcos

| Tabela | Produtor | Escopo |
|---|---|---|
| `user_facts` / `user_facts_v2` | ver 2.2 | Relation |
| `user_patterns` | `analysis_records.detect_and_save_patterns` + `jung_memory_consolidation._create_consolidated_memory` | Relation |
| **`user_milestones`** | ⚠ **ZERO produtores em todo o código** | — |

---

## 3. Inventário B — Filas e estados pendentes

| Fila | Estado inicial | Consumidor | Transições |
|---|---|---|---|
| `work_briefs` | `queued` | `_select_next_brief` (`WHERE status='queued'`) | → `awaiting_approval` → `draft_created`/`published` |
| `work_approval_tickets` | `pending` | `approve_ticket` / `reject_ticket` | → `executed`/`rejected`/`delivery_failed` |
| `knowledge_gaps` | `open` | `get_active_knowledge_gaps`, `close_knowledge_gap_with_evidence` | → `resolved` |
| `agent_will_pulse_events` | `no_action`/`delivery_in_progress` | `will_delivery_receipt`, `will_recovery` | → `delivery_confirmed`/`delivery_uncertain` |
| `agent_dreams` | `pending` | `get_latest_dream_insight`, `mark_dream_delivered` | → `faded`/`delivered` |
| `consciousness_phase_pulses` | `pending` | `_get_due_phase_pulse` | → `completed`/`failed`/`exhausted`/`skipped` |
| `rumination_fragments` | `processed=0` | `drain_detection_backlog` | → `detection_attempts`+1 |
| `rumination_tensions` | `open` | `digest` | → `maturing` → `ready_for_synthesis` → `synthesized` |
| `rumination_insights` | `ready` | `check_and_deliver` | → `delivered` |
| `goal_steps` | `pending` | `complete_goal_step` | → `completed` |
| `controlled_action_runs` | `pending` | `complete_controlled_action_run` | → `completed`/`failed` |
| `action_proposals` | `proposed` | `dispatch_proposal` | → `executed`/`skipped`/`failed` |
| `will_proactive_effects` | `pending` | `run_pending_effects` | → `running` → final |
| `async_maturation_inbox` | `maturing` | ⚠ **nenhum chamador de produção** | — |
| **`work_task_attachments`** | `extraction_status='pending'` | ⚠ **sem job de retry** | síncrono no upload |
| **`work_project_attachments`** | `extraction_status='pending'` | ⚠ **sem job de retry** | síncrono no upload |
| `work_tasks` (LEGADO) | `open` | `list_open_work_tasks` | → `in_progress`/`overdue`/`completed` |

---

## 4. Inventário C — Leitores e escopos

### 4.1 Principais leitores

| Leitor | Tabelas | Escopo aplicado |
|---|---|---|
| `agent_identity_context_builder` (2.663 linhas) | 14+ tabelas de identidade, vontade, ruminação, sonhos, trabalho | `agent_instance` + `origin_relation_id` (Relation ou global) — via `_identity_visibility` |
| `will_engine` | `conversations`, `agent_dreams`, `rumination_*`, `agent_will_*`, `relational_state` | `scope_context` (instance + `scope_kind` + `relation_id`) |
| `core/engine.JungianEngine` | monta prompt: fatos, memória semântica, ruminação, vontade, identidade | `require_eligible_relation` + `CognitiveContextAssembler` |
| `engines/integrative_self` (ISM) | 7 componentes do loop + dreams + will + rumination | **fail-closed**: sem colunas de escopo → componente não anexado |
| `dream_engine` | `rumination_fragments` (24h recentes), identidade, `agent_will_states` | Relation estrita; will só global |
| `jung_rumination` | `rumination_fragments/tensions/insights` | `_relation_scope` + `_relation_allowed` (fail-closed) |
| `work/engine` | `world_state_cache` (seeds), `build_context_summary_for_llm_v2` | legacy quarantine + `work_scope_clause` |
| `scripts/remote_db_probe` (23 probes) | ~30 tabelas | `_relation_scope` + `_will_scope_filter` (global exclui Relation; relation exige `--relation-id`) |

### 4.2 Os quatro sistemas de escopo

| Sistema | Arquivo | O que define |
|---|---|---|
| Contrato canônico (C12a) | `core/db/cognitive_ownership.py` | 23 domínios, 4 classes (`relation_private`, `instance_global`, `authorized_aggregate`, `legacy_unscoped`), políticas de prompt (`same_relation_only`, `instance_global_only`, `authorized_aggregate_only`, `never_in_prompt`) |
| SQL fail-closed (C12g) | `core/db/relation_scope.py` | `RelationQueryScope` → `AND relation_id = ?` / `IS NULL` / **`1 = 0`**; `legacy_quarantine_clause`; `personal_read_clause` |
| Escopo do WILL (C12b) | `engines/will_scope.py` | 5 tabelas com `scope_kind` (`global`/`relation`); `scope_context` resolve e propaga |
| Tenancy do Work (C12c4) | `work/tenancy.py` | 10 tabelas com `org_id/agent_instance/origin_class/origin_relation_id`; `work_scope_clause` por papel |

### 4.3 Cobertura de escopo (resumo)

| Coluna de escopo | Nº de tabelas |
|---|---|
| `agent_instance` | ~78 |
| `relation_id` | ~33 |
| `origin_relation_id` | ~26 |
| `scope_kind` (WILL) | 5+ |
| `scope_key` (availability) | 3 |
| Work tenancy (`org_id`) | 10 |
| **Sem escopo nenhum** | 23 (inclui `goal_steps`, `will_expression_receipts`, `will_proactive_effects`, `users`, config) |

---

## 5. Lacunas demonstradas (severidade)

### P1 — conteúdo cru com escopo ausente em leitura de prompt

| # | Onde | Problema |
|---|---|---|
| **P1-1** | `agent_identity_context_builder._get_latest_work_identity_signal` L1458 | `rumination_fragments` lido **só por `user_id`** — sem `relation_id` nem `agent_instance`. Entra no prompt via `build_current_mind_state` (default `include_unscoped_operational_context=True`, usado em `core/engine.py:2151` e nas fases do loop). |
| **P1-2** | `agent_identity_context_builder.build_architectural_self_awareness_context` L292 | `rumination_insights` lido **só por `user_id`** — bloco que vai ao prompt do admin. (A irmã `_fetch_recent_rumination_insights` é escopada; esta não.) |
| **P1-3** | `engines/work_scheduler.get_reading_context` | `work_briefs`/`work_projects` consultados **sem filtro de escopo** (apenas `origin='scheduled_reading'`); entra no prompt com flag ligada. |
| **P1-4** | `engines/expressive_action`, `engines/synthesis_action` | `work_artifacts` gravados **sem colunas de tenancy** (`org_id`/`agent_instance`/`origin_class`/`origin_relation_id`) — divergem de `work/persistence.py`. |

### P2 — escopo ausente ou divergente fora do prompt direto

| # | Onde | Problema |
|---|---|---|
| **P2-1** | `will_engine._latest_meta_consciousness` L665 | `agent_meta_consciousness` só por `user_id`, sem `agent_instance` — diverge do builder escopado. |
| **P2-2** | `build_architectural_self_awareness_context` L314 | `working_memory_items` filtra `agent_instance` mas **não** `ownership_class`/`relation_id` — pode trazer item `relation_private` de outra Relation. |
| **P2-3** | `agent_hobby_artifacts` | duas semânticas: por `user_id` em `_get_recent_hobby_art_memory`, escopada em `will_engine._latest_hobby`. |
| **P2-4** | `agent_development_policy._load_development_state` L168 | `agent_development` lido só por `user_id` (tabela tem `relation_id`/`agent_instance`). |
| **P2-5** | `_identity_visibility` L87-88 / `_dream_visibility_scope` L1272 | degradação silenciosa: sem colunas de escopo → `1 = 1` (PRAGMA-aware). |

### Produtos órfãos / filas sem consumer

| # | Onde | Problema |
|---|---|---|
| **F-1** ✅ | `user_milestones` | **RESOLVIDO — decisão: aposentar** (branch `fase-iv/r1a-2-processing-queue`). Sem produtor e sem consumidor em todo o código (o INSERT de marcos do agente é em `milestones`, usada por `agent_development`). Criar produtor seria capacidade nova (viola o Princípio Áureo); DROP não existe no projeto. Entregue: nota de aposentadoria no `CREATE` (`core/db/schema.py`), nota no contrato `structured_facts` (`core/db/cognitive_ownership.py`) mantendo o apagamento correto de linhas legadas, e guarda de regressão `TestOrphanDecisions::test_user_milestones_retired_has_no_producer` que falha se alguém adicionar produtor/leitor sem nova decisão do mantenedor. |
| **F-2** ✅ | `async_maturation_inbox` | **RESOLVIDO — decisão: conectar até o limite da regra 7** (mesma branch). Upstream ligado em `JungianEngine.process_message` (`core/engine.py:_defer_message_to_maturation`): a política `evaluate_message_maturation_policy` — que nunca era chamada — agora decide, e `add_maturation_inbox_item` — que nunca teve produtor — ganhou um. Enfileirar falhando **não suprime a resposta** (disponibilidade do turno > supressão). Downstream ligado em `engines/maturation_drain.py`: item maduro (limiar ≤ vontade de relacionar) vira trabalho durável em `processing_queue` (`maturation_inbox`) com a Relation carimbada e `ownership_class='relation_private'` — a fila não promove material privado a global. **O envio real continua atrás do gate existente**: `mark_maturation_item_delivered` só pode ser chamado por quem entregou de fato (`mark_delivered_for_queue_item`), porque marcar sem entregar seria mentir no estado da máquina. |
| **F-3** ✅ | `memory_flush.py` L113 | **RESOLVIDO** (branch `fase-iv/r1a-1-source-vias-flush-fix`, commit `65e3a42`): unpack corrigido, resolvedor defensivo `_resolve_db_manager`, correções logadas mas não aplicadas, gaps persistidos. 10 testes com SQLite real cobrindo persistência, isolamento por Relation, repetição sem duplicação e falha sem sucesso fictício. **Subcorte do R1a-1** — mapeamento de produtores e contrato de vias de origem ainda pendentes. |
| **F-4** ✅ | `work_task_attachments`/`work_project_attachments` `extraction_status='pending'` | **RESOLVIDO** (mesma branch): extração deixa de ser single-shot. `core/db/processing_queue.py` cria a primeira fila explícita do projeto (`processing_queue`) com claim atômico, teto de tentativas e backoff exponencial — molde de `engines/loop_post_commit_integration.py`. `work/extraction_retry.py` faz backfill idempotente de PDFs `pending`/`failed`, reexecuta o extrator no worker e é agendado em `main.py` (a cada 30 min). A falha no upload também enfileira na hora, copiando a origem da linha do anexo. |
| **F-5** ⚠️ | Loop material em ruminação | **Proveniência corrigida no R1a-1**: o material de loop ganhou `source_kind='loop_material'` e deixou de se passar por `conversation` (antes mascarado pelo fallback do `ingest`, com `conversation_id` sintético negativo e sem `source_table`/`source_id`). **A via de "material global autorizado" segue pendente** (R1a-4): dar origem própria não torna o material global — `NULL` não é autorização e origem não é propriedade. |

---

## 6. Diagnósticos R3

### R3a — promoção identitária de insights incompletos

- **Estado:** `_govern_phase_transition` (`agent_development.py:471`) bloqueia promoção com `confidence < 0.55` — **limiar fixo**, não política revisável.
- **Confiança fixa no prompt:** o `_evaluate_with_llm` pede `"confidence": 0.72` como exemplo e usa `0.62 if recommended > previous else 0.48` como fallback — **valores hardcoded**, não derivados da evidência.
- **`cognitive_ownership.py`** já tem a infraestrutura: domínios com `prompt_policy = never_in_prompt` e `status`/`next_cut` por tabela. A política de confiança pode ser expressa ali de forma revisável.
- **Lacuna:** não há mecanismo que impeça a promoção identitária (`sync_mature_insights_to_core`) de insights que falharam na validação de novidade ou têm `depth_score` baixo. O `_validate_novelty` existe na síntese mas o resultado não é propagado como gate de promoção.

### R3b — diagnóstico de fallbacks WILL/metaconsciência

- **Estado:** `_fallback_state` (`will_engine.py:960`) é chamado em 2 pontos (L1189, L1317) quando `_generate_with_llm` falha.
- **Lacuna:** não há instrumentação que registre **por que** o fallback ocorreu (timeout? JSON malformado? sem endpoint?). O `logger.warning("WillEngine caiu em fallback heuristico")` diz que caiu, mas não diz a causa.
- **Metaconsciência:** `generate_cycle_reading` tem fallback silencioso — se o LLM falha, a leitura é gravada com conteúdo heurístico sem marca de origem.
- **Lacuna:** não há dashboard que mostre taxa de fallback por fase, nem alerta quando a taxa ultrapassa um limiar.

### R3c — proatividade textual

- **Estado:** `engines/will_proactive_record` já persiste com `delivery_confirmed` como critério (L29). `engines/will_expression.py` tem estados `planned` → `pending` → `running` → final.
- **Lacuna parcial:** `jung_proactive_advanced.py` (sistema proativo legado) opera paralelamente com o sistema novo `will_expression`. Dois sistemas de proatividade, sem verificação cruzada de idempotência entre eles.
- **Estado parcialmente fechado:** consentimento (`agent_relations.consent_status`), disponibilidade (`agent_availability_*`) e confirmação de entrega (`delivery_confirmed`) existem como primitivas, mas não há teste de integração que verifique os 3 juntos num cenário de falha.

---

## 7. Decomposição proposta

### R1a — Assimilação cumulativa (4 subcortes)

| Subcorte | Escopo | Entrega |
|---|---|---|
| **R1a-1** | **Inventário + vias de origem.** ~~Criar enum explícito de `source_kind`~~ *(ajustado: não substituir fonte por propriedade — `source_kind` diz de onde veio; `ownership_class` já representa propriedade)*. **Entregue**: bug do flush (subcorte, ver F-3) **e** a taxonomia de `source_kind` como dimensão de **proveniência** — explícito que ela não combina nem substitui propriedade (`ownership_class`/`origin_class`/`relation_id` seguem os contratos C12). Mapeamento dos produtores das Tabelas 2.1–2.7 registrado no módulo. **Sem migração de dados históricos**: linhas já gravadas mantêm o valor recebido; a validação vale para escritas novas. | Bug flush ✅ (subcorte) · **Taxonomia entregue**: `core/db/source_kinds.py` com conjunto fechado de proveniência de conteúdo, contrato explícito de que origem ≠ propriedade, validação fail-closed nos produtores (`jung_rumination.ingest`, `work/engine.record_work_experience`) e separação da proveniência do material de loop (F-5). **Mapeamento documental das Tabelas 2.1–2.7 registrado no próprio módulo.** Sem migração de dados históricos. |
| **R1a-2** ✅ *(implementado · testado · publicado; habilitado não verificado)* | **Fila de processamento para material global.** ~~Criar fila explícita (`processing_queue` ou equivalente) para material global autorizado, separada de material privado.~~ **Entregue**: `processing_queue` (`core/db/processing_queue.py`, também no schema canônico) com claim atômico, teto de tentativas e backoff. Cada item carrega `ownership_class`/`origin_class`/`origin_relation_id` próprios — a fila **não promove origem**; quem enfileira decide a classe de propriedade e o material privado (`relation_private`) continua privado. F-1 aposentado, F-2 conectado até o gate de envio, F-4 com retry real. `processing_queue` é *delete-table* no expurgo de Relation (item aponta material que o expurgo remove). | Fila + retry + órfãos decididos ✅ (PR #62, merge `8245d63`) |
| **R1a-3** ✅ *(implementado · testado · publicado; habilitado não verificado)* | **Corrigir leitores sem escopo P1-1..P1-4 + P2-1..P2-4.** Aplicar `_relation_scope`/`scope_context`/`legacy_quarantine_clause` nas 8 leituras identificadas. | 8 correções escopadas + testes de isolamento ✅ (PR #61, merge `fa56e05`) |
| **R1a-4** | **Consolidação conceitual (R1b pontual).** Conectar `source_kind` → `knowledge_gaps` com `origin_class` distinto (os 2 caminhos da Tabela 2.6). Indexação idempotente. | Fonte, qualidade e incerteza por entrada |

### R3 — Corretivos (3 subcortes)

| Subcorte | Escopo | Entrega |
|---|---|---|
| **R3a** | **Política de confiança revisável.** Substituir limiar fixo `0.55` e confianças hardcoded por política declarativa em `cognitive_ownership.py`. Gate: `sync_mature_insights_to_core` exige `depth_score >= X` E `novelty_validated = true` E `evidence_count >= Y`. | Política + gate + teste |
| **R3b** | **Diagnóstico de fallbacks.** Instrumentar `_fallback_state` com `fallback_reason` persistido; adicionar métrica `fallback_rate` por fase; alerta quando taxa > limiar. Dashboard de observabilidade. | Motivo persistido + métrica + dashboard |
| **R3c** | **Proatividade unificada.** Verificar integração entre `jung_proactive_advanced` (legado) e `will_expression` (novo): idempotência cruzada, consentimento, disponibilidade, entrega confirmada. Teste de integração com os 4 critérios num cenário de falha. | Teste de integração + quarentena de sistema legado |

### Ordem sugerida

```
R1a-1 (vias de origem + bug flush) → R1a-3 (correções P1/P2)
→ R1a-2 (fila + órfãos) → R1a-4 (consolidação)
→ R3a → R3b → R3c
```

R1a-3 pode correr em paralelo com R1a-2 (não se sobrepõem). R3 pode iniciar após R1a-1, pois depende da taxonomia de origem para a política de confiança.

### Progresso — dimensões separadas (mestre §4.1)

O mestre §4.1 exige quatro dimensões registradas à parte — **implementado ·
testado · habilitado · validado em operação** — e afirma que "uma dimensao nao
implica as demais". **Publicação** é informação adicional, não uma das quatro:
código publicado pode continuar deliberadamente desligado.

"Habilitado" exige **ambiente, flag e data verificados**. Nenhum pós-deploy
foi verificado aqui, então essa coluna é ❌ em todos os cortes — não por
código ausente, mas por ausência de verificação. CI verde não encerra circuito
cognitivo.

| Corte | Implementado | Testado | Habilitado | Publicado | Validado em operação |
|---|---|---|---|---|---|
| **R1a-1** | ✅ reparo do `memory_flush` **+** taxonomia de `source_kind` (PR #64, em revisão) | ✅ 10 testes do flush + 20 da taxonomia | ❌ não verificado | Parcial — flush em `6ece048`; taxonomia ainda em revisão | ❌ |
| **R1a-2** | ✅ `processing_queue`, retry F-4, órfãos F-1/F-2 | ✅ 45 testes + regressão 20/20 | ❌ não verificado | ✅ `8245d63` (PR #62) | ❌ |
| **R1a-3** | ✅ correções de isolamento P1 | ✅ testes de isolamento | ❌ não verificado | ✅ `fa56e05` (PR #61) | ❌ |
| **R1a-4** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **R3a–R3c** | ❌ | ❌ | ❌ | ❌ | ❌ |

**O que é incondicional e o que é gateado** (para quando a verificação de
habilitação for feita):

- R1a-2: o scheduler de retry e o dreno de maturação rodam sem flag. A
  **interceptação conversacional de maturação é deliberadamente desligada**
  por `THEORY_OF_MIND_MATURATION_DEFER_ENABLED=false` — publicado, mas
  intencionalmente não habilitado, porque o consumidor de entrega ainda não
  existe (regra 7).
- R1a-3: os filtros de escopo são caminhos de código incondicionais.
- R1a-1 (taxonomia, PR #64, ainda em revisão): a validação de `source_kind`
  é incondicional; sem flag.

**Validação em operação pendente para R1a-2/R1a-3**: após o deploy destas
PRs, um probe read-only deve confirmar (a) que nenhum item de `processing_queue`
carrega instância/Relation trocados, (b) que o retry de anexo `failed` de fato
recupera material e (c) que nenhum leitor P1 corrigido voltou a vazar entre
Relations. Isso é evidência separada e não foi executado aqui.

**F-5 permanece aberto** de propósito: é o alvo da taxonomia de origem (R1a-1 pendente / R1a-4), e o Princípio Áureo procura criar a via de "material global autorizado" antes de alimentá-la.

---

## 8. Limitações desta entrega

- Leitura estática. Suíte não reexecutada; nenhum teste de integração rodado.
- Os achados de P1/P2 são de auditoria de código, não de observação de produção. Confirmação em produção requer probe (não executado aqui).
- Severidade P1/P2 segue a rubrica da auditoria C12: P1 = conteúdo bruto cruza Relations ou chega a superfície pública/sem gate; P2 = apenas agregados/ids ou escopo ausente em `require_master`.
- `user_milestones` e `async_maturation_inbox` marcados como órfãos com base em varredura de INSERT/UPDATE em produção (fora de `tests/`). Pode haver produtores via scripts manuais não detectados.
