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
| **F-5** ⚠️ | Loop material em ruminação | **Proveniência corrigida no R1a-1**: o material de loop ganhou `source_kind='loop_material'` e deixou de se passar por `conversation` (antes mascarado pelo fallback do `ingest`, com `conversation_id` sintético negativo e sem `source_table`/`source_id`). **A via de "material global autorizado" segue PENDENTE — o R1a-4 (PR #65) NÃO a fechou**: ele dá proveniência e dedup às lacunas, mas dar origem própria não torna material global nem autorizado; `NULL` não é autorização e origem não é propriedade. |

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
| **R1a-4** ✅ *(implementado · testado; habilitado não verificado; em revisão)* | **Consolidação conceitual (R1b pontual).** **Entregue**: `knowledge_gaps` ganha `source_kind` (conjunto fechado `conversation` · `world` · `goal_manager`, derivado dos produtores reais), `dedup_key` (normalização conservadora: NFKC + casefold + pontuação, **sem semântica**; `the_gap` original nunca reescrito) e contagem de observações. Chave de dedup única por escopo `(agent_instance, origin_class, relation_id, fingerprint)` — fecha o bug em que `status='investigating'` saía da chave e a mesma pergunta virava linha nova. Comportamento por status definido: `open`/`investigating`/encerradas são reutilizadas, nunca reabertas nem duplicadas; recorrência ≠ retry (ancora nova vs. repetida) e **nenhuma delas altera `importance_score` nem apresenta confiança**. Proveniência indeterminada vai à quarentena explícita (`provenance_json.origin_indeterminate` + motivo + pendência), **sem promoção a material global autorizado** e sem interromper a conversa. **Sem backfill**: linhas históricas não são tocadas. | Fila + origem por entrada + dedup por escopo ✅ |

### F-5 — Via de material global autorizado: inventário e decomposição (04/11/2026)

**Status**: inventário feito (leitura estática), decomposição **proposta para aprovação**;
implementação, testes, habilitação e aceite **pendentes**. Ficou em aberto de
propósito na R1a-4 — o Princípio Áureo procura *criar* a via antes de alimentá-la.

É o fechamento da lacuna `F-5` da Seção 5 e do trecho da spec de R1a que manda
*"Implementar uma via explícita para material global autorizado, distinta da via
privada e do legado sem classificação"*. Não abre capacidade nova.

#### Limite de escopo (registrado a pedido do mantenedor)

F-5 fecha a **admissão segura** do material global — *quem pode nascer global*.
**Não comprova** que leituras do Work foram **assimiladas nem utilizadas nas
conversas**. Essa é uma afirmação diferente, com aceite próprio (**R1d**):
continuidade de investigação, transferência e uso conversacional. Nada em F-5
pode ser lido como esse aceite, e os dois status continuam registrados à parte.

#### Condições do mantenedor (04/11/2026) — incorporadas ao contrato

Aprovação da decomposição **com cinco ajustes**, que passam a valer como
requisitos de cada subcorte:

1. **Recusa não vira legado.** `legacy_unscoped` é destino **apenas** de
   histórico não classificado, nunca de falha nova. Material novo recusado
   **permanece privado** quando houver vínculo válido, ou entra em
   **quarentena explícita com motivo persistido**. `legacy_unscoped` não pode
   funcionar como destino conveniente para admissão negada.
2. **Duas rotas de admissão distintas.** (A) **livros e fontes públicas** podem
   nascer global mediante **critérios próprios** — sem exigir uma Relation
   artificial para um livro; (B) **contribuições derivadas de relações** exigem
   consentimento e proveniência.
3. **Consentimento não significa divulgação.** O opt-in de interioridade
   autoriza **contribuir com a interioridade agregada**; **não** autoriza expor
   conteúdo pessoal a outros interlocutores. Admissão global não abre leitura
   interpessoal.
4. **Leituras também protegidas.** Controlar writers novos **não** corrige
   registros antigos autodeclarados. O plano **define** o tratamento das
   leituras para material **sem autorização verificável**, **sem reclassificar
   o histórico automaticamente**.
5. **Testes de isolamento em cada entrega.** F-5c é a verificação integrada,
   mas **F-5a e F-5b já entregam** testes de recusa, instâncias distintas e
   consentimento revogado.

#### Achado central

**Não existe predicado de elegibilidade para material global autorizado.** O
rótulo `authorized_aggregate` é **auto-declarado por quem escreve**, e 13 sítios
de leitura confiam nele.

| Mecanismo | O que valida | Alcance |
|---|---|---|
| `resolve_cognitive_origin` (`core/db/cognitive_provenance.py`) | **só consistência**: Relation ⇒ `relation_private`; `relation_private` ⇒ exige Relation; classe desconhecida ⇒ `ValueError`. **Não pergunta** se o material é elegível a ser global. Sem Relation, cai em `requested_class ou legacy_unscoped`. | **5 chamadores**: `knowledge_gaps` (2), `dreams`, `scholar_engine` (2) — e **nenhum deles pede** `authorized_aggregate` hoje |
| Auto-declaração do writer | nada — literal na instrução | `world_consciousness._persist_epistemic_knowledge_gap` grava `'instance_global','authorized_aggregate'` **direto no `INSERT`**, sem passar pelo resolver |
| `agent_relations.scope_json.global_interiority = 1` | **a única autorização real**: opt-in explícito por Relation | consultado por **1 leitor** (`core/rumination_interiority.global_rumination_influence`, agregado *text-free*) e por **nenhum writer** |
| `cognitive_ownership.py` | vocabulário + contrato dos 23 domínios + `validate_ownership_contracts` (valida a própria tabela de contratos) | **nenhuma função de elegibilidade** — `grep` por *authoriz/elegib/admit/promot* = 0 |

Os 13 sítios que confiam no rótulo (`origin_class IN ('instance_global','authorized_aggregate')`
ou equivalente): `core/cognitive_context.py` (montador C12f), `agent_identity_context_builder`
(2), `core/db/essays`, `core/db/meta_cognition`, `core/db/symbolic_graph` (2),
`core/db/working_memory`, `engines/integrative_self` (3), `engines/symbolic_graph`,
`scholar_engine`.

Legítimo e distinto: `core/engine.py:1018` grava `authorized_aggregate` vindo de
`global_rumination_influence`, que **sim** consulta o opt-in — mas é o único
caminho cuja declaração tem lastro.

#### Onde resta `NULL` / legado

- Schema: quase todas as tabelas usam `ownership_class TEXT NOT NULL DEFAULT
  'legacy_unscoped'` (e `origin_class` idem) — **`NULL` não é o problema**; o
  padrão já é honesto e `resolve_cognitive_origin` nunca devolve `NULL`.
- Exceção real: **`processing_queue.origin_class` é nullable sem `default`** —
  item enfileirado pode nascer sem origem. Já é contabilizado por
  `rows_missing_origin_class` na sonda da PR #67.
- O `NULL` que importa é o histórico (`dedup_key`, `source_kind`) da R1a-4:
  sem backfill, **por desenho** — e por isso nunca é alcançado pelo dedup.

#### ⚠️ Medições de produção pendentes

Contagens reais de `ownership_class`/`origin_class` por tabela e o nº de
Relations com `global_interiority=true` **não foram coletadas**.

**Diagnóstico de acesso (corrigido em 04/11/2026 — a leitura anterior estava
forte demais).** `railway status` funciona (config cacheado), mas `whoami` e
`ssh` falham com `failed to refresh OAuth token: Operation not permitted
(os error 1)`; `~/.railway/config.json` existe e é legível. **EPERM no refresh
não comprova token expirado** — indica que a CLI não conseguiu renovar
(permissão/keychain do SO). A verificação de acesso é ação do mantenedor
(`railway login`), **sem contornar a proteção de credenciais**.

**Não bloqueia o desenvolvimento**: F-5a–F-5b se desenvolvem e testam offline.
As contagens são **necessárias para o aceite operacional** e devem ser
registradas antes dele. Script pronto e somente-leitura (só contagens, `mode=ro`).

#### Decomposição revisada (aprovada em 04/11/2026 com as 5 condições)

| Subcorte | Escopo | Testes já nesta entrega |
|---|---|---|
| **F-5a** | **Contrato de elegibilidade.** Predicado no contrato canônico com **duas rotas** (condição 2): **(A)** fonte pública/livro → `authorized_aggregate` por **critérios próprios**, **sem Relation artificial**; **(B)** contribuição derivada de Relation → exige consentimento vigente **e** proveniência. **Recusa** (condição 1): com vínculo válido → mantém `relation_private`; sem vínculo → **quarentena explícita com motivo persistido** — **nunca `legacy_unscoped`**. Define ainda a **regra de leitura** (condição 4) para material sem autorização verificável. Sem escrita de dados. | ✅ recusa · instâncias distintas · consentimento revogado |
| **F-5b** | **Admissão nos writers.** Passar os caminhos auto-declarados pelo predicado (começando por `world_consciousness`). Quem não passar → `relation_private` ou quarentena com motivo (**condição 1: `legacy_unscoped` proibido como destino de recusa nova**). | ✅ recusa · instâncias distintas · consentimento revogado |
| **F-5c** | **Verificação integrada.** Duas Relations × duas instâncias; revogação/apagamento retiram a contribuição. | **Entregue: 27 testes sem `xfail` — P1 do reader E do produtor corrigidos com o gate canônico; ambos os casos pegos pelas sentinelas** |
| **F-5d** | **Observabilidade.** Sonda do que foi declarado global-authorized, por origem e escopo — espelha a PR #67 (metadados, sem conteúdo). | — |
| **F-5e** (requisito de encerramento) | **Tratamento das leituras** (condição 4). Aplicar a regra definida em F-5a aos **13 sítios de leitura**: material sem autorização verificável tem tratamento explícito, **sem reclassificar o histórico automaticamente**. | **Entregue: 7 testes — 14 pontos protegidos (13 + leitor principal), nenhum `xfail`** |

> **F-5e é requisito de encerramento do F-5** — decisão do mantenedor em
> 05/11/2026, que aceitou o rótulo e rejeitou o enquadramento anterior como
> "complemento opcional". Controlar só os writers novos deixa os registros
> antigos autodeclarados expostos nos 13 sítios de leitura; **sem F-5e, o F-5
> não pode ser declarado encerrado.** As condições 1–5 continuam valendo para
> F-5a..d desde a entrega deles.

**Revisão de 05/11/2026 — 4 achados, todos corrigidos:**

| PR | Sev. | Achado | Correção |
|---|---|---|---|
| #69 | **P1** | Consentimento geral era tratado como autorização global: o predicado exigia `consent_status="granted"` e não pedia `scope_json.global_interiority` | rota relação passa a exigir o opt-in; sem ele → `keep_private` (`global_interiority_not_granted`) |
| #69 | **P1** | Origem ausente ou desconhecida passava pela rota pública (`source_kind="unknown"` + `conversation#123` → `admit_global`); âncora sintaticamente válida não comprova fonte pública | lista explícita `PUBLIC_SOURCE_KINDS` = `{world, work_reading}`; ausente → `public_source_kind_missing`, desconhecida → `public_source_kind_not_allowed` |
| #69 | P2 | `relation_id` sem `relation_agent_instance` era aceito — ausência presumia pertencimento à instância | instância comprovada obrigatória em qualquer rota que declare vínculo; ausência → `quarantine` (`relation_instance_unverified`) |
| #67 | P2 | A regex de `last_error` aceitava frases curtas em ASCII: `"User Maria has depression"` saía integral (reproduzida) | lista explícita `ALLOWED_QUEUE_ERROR_CODES`; todo o resto → `has_last_error=True` sem texto |

**Status em 05/11/2026**: F-5a foi **aceito e mergeado** — #67 `9805798`,
#68 `f91c0dc`, #69 `92b8f7a`; deploy Railway `SUCCESS`, endpoint `healthy`,
bot e agendadores iniciados. **F-5b está em andamento.**

#### F-5b — Fluxo do writer `world_consciousness` (registrado antes de codar)

**Branch** `feat/f5b-world-admission` · **Escopo** apenas
`_persist_epistemic_knowledge_gap` · registrado em 05/11/2026 **antes de
qualquer alteração de código**.

**Pontos de escrita atuais**

| Ponto | O que faz hoje |
|---|---|
| `world_consciousness.py:423` (`INSERT INTO knowledge_gaps`) | único INSERT do arquivo; caminho de conexão próprio; **sempre** `ownership_class='instance_global'`, `origin_class='authorized_aggregate'` (literal), `source_kind='world'`, `origin_quarantined=0` |
| `world_consciousness.py:566` (`UPDATE`) | fechamento de lacuna — **fora** do F-5b |
| chamador `:2429` | fase `world`; repassa `resolved_will_state` de `_resolve_will_state` |
| recorrência (R1a-4) | `observe_gap_occurrence` antes do INSERT; `IntegrityError` → observa na linha vencedora |

Hoje **não há consulta de elegibilidade**: o rótulo é autodeclarado — exatamente a lacuna F-5.

**Fluxo alvo**

1. `source_refs = ["will#<id>"]` quando `will_state.id` é numérico; **sem id → lista vazia**;
2. `assess_global_admission(route=public_source, source_kind="world", source_refs, private_derived=False)`;
3. `ValueError` → `logger.warning` com contexto (instância, usuário, motivo) → `return None`: **sem escrita, sem fallback de promoção, sem propagar erro à conversa**;
4. `admits_global` → `origin_class='authorized_aggregate'`, `origin_quarantined=0`;
5. recusa → `origin_class='instance_global'`, `origin_quarantined=1`, motivo persistido em `provenance_json`;
6. dedup, âncoras e contadores: **inalterados** (R1a-4).

**Resultados esperados**

| Situação | `source_refs` | Decisão | Gravação |
|---|---|---|---|
| `will_state.id` numérico | `["will#N"]` | `admit_global` | `authorized_aggregate` · `origin_quarantined=0` |
| sem id ou id não numérico | `[]` | `quarantine` (`public_source_not_identified`) | `instance_global` · `origin_quarantined=1` + motivo |
| entrada inválida (flag ambígua, instância vazia, rota desconhecida) | — | `ValueError` | **nenhuma escrita** — log rastreável, conversa segue |
| mesma pergunta chegando de novo | — | recorrência R1a-4 | mesma linha: âncoras somadas, contador +1 |

**Decisão de design explícita — ponto de revisão**

> **Aceita pelo mantenedor em 05/11/2026**: preservar a chave temporariamente
> **por compatibilidade**, **sem atribuir a ela significado de autorização**.
> A chave é token de unicidade; a autorização vive exclusivamente em
> `origin_class` + `origin_quarantined`. A remoção da dependência de
> compatibilidade fica registrada como dívida, não como decisão silenciosa.

- **`dedup_key` mantém o token `origin_class='authorized_aggregate'`**, inalterado, enquanto a **coluna** passa a refletir o resultado da admissão. Motivo: a chave embute `origin_class`, e trocá-la por `instance_global` **quebraria garantia do R1a-4** — `test_world_and_mixin_concurrent_recurrence_counts_once` escreve via mixin com `authorized_aggregate`; duas chaves distintas dariam **duas linhas** para a mesma pergunta. Verificado que **nada faz parse de `dedup_key`** (token de unicidade opaco). O estado de autorização vive em `origin_class` + `origin_quarantined`.
- Recusa grava `instance_global`, **não** `authorized_aggregate`: o predicado devolve `origin_class=None` (nenhuma classe concedida) e `legacy_unscoped` é proibido pela condição 1. `instance_global` é a única classe restante que **não afirma autorização** nem pertence a Relation. Confirmado que `get_active_knowledge_gaps` aceita as duas classes igualmente e bloqueia pelo flag `origin_quarantined = 0` — ou seja, o flag é o mecanismo; a classe é honestidade do registro.

**Proteção do caminho completo (exigência do mantenedor, 05/11/2026)**

> *"Material em quarentena não pode ser consumido pelo ISM, **mesmo após
> fechamento**. Fechamento epistemológico não equivale a autorização."*

- **Corrigido em F-5b**: `engines/integrative_self.py` — as **duas** branches
  da leitura de `knowledge_gaps` (principal e fallback legado) agora filtram
  `origin_quarantined = 0`. O fallback só aplica o filtro quando a coluna
  existe (schema pré-R1a-4 não a tem).
- **Teste real**: criação recusada → fechamento (`status='resolved'`) →
  leitura ISM **bloqueada**; com controle (`test_admitted_creation_stays_readable_after_closure`)
  provando que o filtro não bloqueia material legitimamente admitido.
  Verificado que **os dois testes falham sem a correção** e passam com ela.
- **Duas ordens cobertas**: admitida→recusada (`test_admission_then_refusal_keeps_the_first_decision`)
  e recusada→admitida (`test_refusal_then_admission_keeps_the_first_decision`).
  Em ambas a classe é decidida **na criação**; a recorrência soma âncora e
  contador e **nunca** libera quarentena automaticamente nem reclassifica.

**Achado restante para F-5e**: `admin_web/routes/research_lab_memory.py:179`
lê `knowledge_gaps` sem filtrar `origin_quarantined` (atrás de
`require_master`) — **fora** do caminho do ISM e mantido na auditoria
completa de leitores do F-5e, conforme orientação do mantenedor.

**Fora do escopo**: nenhuma reclassificação ou reprocessamento de histórico; nenhum envio de mensagem; nenhuma alteração de produção. **F-5e continua obrigatório para encerrar F-5**, e este corte **não comprova assimilação nem uso das leituras nas conversas**.

#### F-5c — Verificação integrada (05/11/2026)

**Status (revisão do mantenedor, 05/11/2026)**: **ambas as P1 foram
corrigidas nesta entrega** com o gate canônico — o **reader** (revogação) e
os **produtores** (`add_knowledge_gap`, `upsert_epistemic_knowledge_gap`).
As sentinelas passam **normalmente, sem `xfail`** — condição de aprovação do
F-5c. A falha do produtor foi **apresentada antes** do reparo e reparada
**depois da autorização**, no mesmo limite: produtores de `knowledge_gaps`,
sem reforma geral. F-5d e F-5e continuam **pendentes e obrigatórios** para o
encerramento do F-5.
Nenhuma alteração de ambiente de produção nesta entrega: Railway não foi
tocado; o deploy segue sendo responsabilidade do mantenedor.

##### Cobertura da admissão — quem já usa e quem continua pendente

**Não se declara todos concluídos pela alteração no World.** Inventário dos
writers que podem nascer `authorized_aggregate`:

| Writer | Alvo | Usa `assess_global_admission`? | Estado |
|---|---|---|---|
| `world_consciousness._persist_epistemic_knowledge_gap` | `knowledge_gaps` | **sim** (F-5b) | **Gated** |
| `core/engine.py:1018` (`rumination_influence`) | contexto de prompt (não grava classe) | não — mas tem **lastro próprio**: só inclui Relations com `scope_json.global_interiority = 1` | Legítimo, **fora** do predicado |
| mixin `add_knowledge_gap` / `upsert_epistemic_knowledge_gap` (chamadores `fact_extraction`, `memory_flush`, `controlled_action`) | `knowledge_gaps` | **sim** (esta entrega) | **Gated**: a origem declarada passa pelo contrato; recusa → `instance_global` + quarentena, nunca legado |
| `core/db/dreams`, `scholar_engine` | próprias tabelas | **não** (mesmo resolver) | **Pendente** |
| `world_state` (`world_consciousness.py:2634`) | cache JSON do mundo | **não** | **Pendente** |

**Estado desta entrega**: 2 de 4 caminhos de `knowledge_gaps` gated (world writer
na F-5b, mixin agora) — o resto segue pendente e declarado como tal.

##### Dimensões separadas (mestre §4.1)

| Dimensão | Estado |
|---|---|
| **Implementado** | ✅ F-5a `92b8f7a` · F-5b `8573418` · F-5c `60c8ee9` · admissão nos produtores + 2 P1 · **F-5e tratamento das leituras** (esta entrega) |
| **Testado** | ✅ F-5a 41 · F-5b 17 · F-5c 27 · admissão 16 · **F-5e 7** · regressão mock 20/20 · suíte completa **1442 passed** |
| **Publicado** | ✅ F-5a/b mergeadas em `main` com deploy Railway `SUCCESS` |
| **Validado em operação** | ❌ **não verificado** — o deploy confirma *execução*, não aceite operacional; sondas de produção seguem pendentes de `railway login` |

##### [P1] Revogação não bloqueava `get_active_knowledge_gaps` — CORRIGIDO nesta entrega

**Como foi encontrada** (reprodução executada, não deduzida):

```python
rel = db.register_agent_relation(..., consent_status="granted")
db.add_knowledge_gap(..., relation_id=rel, origin_class="relation_private", ...)
db.get_active_knowledge_gaps("alice", relation_id=rel)   # → 1 item
db.conn.execute("UPDATE agent_relations SET status='revoked',
                 consent_status='revoked' WHERE relation_id=?", (rel,))
db.get_active_knowledge_gaps("alice", relation_id=rel)   # → AINDA 1 item ✗
```

**Causa**: `get_active_knowledge_gaps` resolvia o escopo com `resolve_relation_id`,
que **não** checa `status`/`consent_status` (valida apenas instância e
participante), em vez do gate canônico `require_eligible_relation` — plugado
em **13** pontos e **ausente** em `core/db/knowledge_gaps.py`.

**Reparo (aprovado pelo mantenedor em 05/11/2026)**: o reader agora chama o
**gate canônico** `require_eligible_relation` depois de resolver o escopo e,
na recusa (`ValueError`), registra o motivo com contexto e devolve `[]` —
**sem fallback para o escopo global, sem cair no legado, sem silenciar a
exceção**. Sem reforma geral: um único ponto de chamada, nenhum contrato de
chamador alterado.

**Sentinela passando normalmente**: `test_revoked_relation_material_is_not_readable`
(s/ `xfail`) + pausa, consentimento pendente, resolução automática e vínculo
de outra instância. **F-5c só é aprovado com essa sentinela verde** — condição
do mantenedor.

**Preservação verificada**: `test_refused_relation_does_not_fall_back_to_global_scope`
garante que fonte pública continua legivel para quem nao tem vinculo
inelegivel, e que a recusa de Relation **nao** cai no escopo global.

**Valor bruto de opt-in**: os testes de opt-in passam o valor direto do
`scope_json` (**sem `bool()`**) — `bool("false")` seria `True` e abriria
admissao sem consentimento; `test_raw_scope_value_is_never_coerced_by_truthiness`
trava isso com `scope={"global_interiority": "false"}` → `ValueError`.

##### [P1] O produtor escrevia após revogação — CORRIGIDO nesta entrega

**Como foi encontrada** (reprodução executada):

```python
rel = db.register_agent_relation(..., consent_status="granted")
db.conn.execute("UPDATE agent_relations SET status='revoked',
                 consent_status='revoked' WHERE relation_id=?", (rel,))
n0 = COUNT(knowledge_gaps)                                    # → 0
db.add_knowledge_gap("alice", ..., relation_id=rel,
                     origin_class="relation_private", ...)    # gravou=True
n1 = COUNT(knowledge_gaps)                                    # → 1  ✗
```

**Causa**: `add_knowledge_gap` → `resolve_cognitive_origin` → `resolve_relation_id`,
que devolve o id **sem checar elegibilidade**. A política C12g diz
"uma Relation só **lê/produz** conteúdo quando ativa e com consentimento
concedido" — a leitura já obedecia (reparo acima); a **produção** ainda não.

**Reparo (aprovado pelo mantenedor em 05/11/2026)**: `add_knowledge_gap` e
`upsert_epistemic_knowledge_gap` agora chamam o **gate canônico**
`require_eligible_relation` **após** resolver/validar o vínculo e **antes** de
qualquer `INSERT` ou atualização por deduplicação (`observe_gap_occurrence`).
O import é o protegido no topo do módulo, seguindo o padrão *direct-file*
já usado por `cognitive_provenance` e `source_kinds` — sem quebrar os testes
que carregam `knowledge_gaps.py` isolado.

**Na recusa**: `logger.warning` com usuário, instância, relação e motivo —
**sem conteúdo pessoal** — e retorno `None` (contrato `Optional[int]`): nunca
um ID que pareça sucesso. Vínculo de outra instância continua `ValueError`
(`relation_agent_instance_mismatch`): argumento inválido, não política de
consentimento.

**Sentinelas — `tests/test_f5c_producer_gate.py`, 9 testes, SEM `xfail`:**

| Cenário | O que prova |
|---|---|
| revogação · pausa · consentimento pendente | **nenhuma linha nova** **e** nenhuma mutação de âncoras, contadores e timestamps na linha existente (`observation_count`, `source_refs_json`, `last_observed_at`) |
| vínculo explícito | gate nos dois produtores |
| resolução automática (sem `relation_id`) | gate vale igualmente |
| outra instância | `ValueError`, sem nenhuma linha |
| sem fallback | a recusa não produz linha em `authorized_aggregate`, `instance_global` **nem** `legacy_unscoped` |
| **controles** | fonte pública independente e Relation elegível seguem gravando e deduplicando |

**Prova de que as sentinelas pegam o bug**: com os dois gates removidos
temporariamente, **6 dos 9 testes falham**; restaurados, 9/9 verdes.

##### Correção de expectativa no teste de apagamento

`erase_relation` **redige conteúdo e preserva a linha** como registro auditado
(docstring: *"Redige o conteudo… Retorna contagem de celulas redigidas"*), não
exclui. Meu primeiro teste esperava exclusão e falhou; a correção garante o que
o apagamento assegura de fato: **o material original não sobrevive em nenhuma
leitura** (`the_gap`, `topic`, `public_question` sem o texto original).

#### Admissão global nos produtores de `knowledge_gaps` (06/11/2026)

**Tarefa do mantenedor**: fechar a admissão global nos dois métodos — já
respeitavam revogação, mas **não usavam o contrato** que decide se o material
pode ser global.

**O que mudou** (`core/db/knowledge_gaps.py`): `add_knowledge_gap` e
`upsert_epistemic_knowledge_gap` chamam `assess_global_admission`
(`route=public_source`, com o `source_kind` **declarado** e as âncoras) toda
vez que a escrita nasceria `authorized_aggregate`.

| Declaração | Resultado |
|---|---|
| fonte pública comprovada (`world` + âncora) | `authorized_aggregate`, `origin_quarantined=0`, legível |
| sem origem | recusa → `instance_global` + quarentena (`public_source_kind_missing`) |
| `conversation` | recusa (`route_mismatch_use_relation_route`) — condição 3 |
| origem desconhecida (`telegram`) | recusa (`public_source_kind_not_allowed`) |
| sem âncora | recusa (`public_source_not_identified`) |

Em **toda** recusa: `provenance_json.admission_refused` com o motivo,
classe `instance_global` (nunca `authorized_aggregate`, **nunca** `legacy_unscoped`),
`origin_quarantined=1`, e o registro **não** aparece em nenhuma leitura.

**Decisão de design — chave de dedup com a classe DECLARADA** (mesma
aceitação da F-5b): `gap_identity_key` embute `origin_class` e **nada faz
parse dela**. Usar a classe **gravada** na chave partiria a mesma pergunta em
duas linhas quando uma escrita é admitida e outra recusada — **reproduzido
como falha real** (`assert 2 == 1` em
`test_quarantined_write_never_pollutes_existing_valid_gap`). A chave fica com
a classe **declarada** (token); a **coluna** reflete a admissão decidida.

**Dois testes R1a-4 atualizados** — não afrouxados: ambos passavam por
declarar global **sem** cumprir o contrato, exatamente o buraco que o F-5a
existia para fechar.

| Teste | Antes | Depois |
|---|---|---|
| `test_world_and_conversation_never_merge` | global sem proveniência | ganha `source_kind="world"` + âncora; **intenção (isolamento) preservada** |
| `test_quarantined_gap_is_not_consumed_even_if_global` | exigia `origin_class='authorized_aggregate'` **na recusa** | classe nova (`instance_global` + motivo na proveniência) **e** linha histórica global em quarentena inserida direto — o invariante *"o flag bloqueia, nunca a classe"* segue coberto |

**Sentinelas — `tests/test_knowledge_gap_global_admission.py`, 10 testes, sem
`xfail`**: admissão por fonte pública comprovada; recusa nas 4 origens; nunca
legado; dedup entre admitida→recusada **e** recusada→admitida (quarentena
sticky); paridade `add`/`upsert`; Relation elegível segue privada.

**Prova**: com os dois blocos de admissão desabilitados, **6 dos 10 testes
falham**; restaurados, 10/10 verdes. Suíte completa: **1429 passed**.

**Cobertura**: 2 dos 4 caminhos de `knowledge_gaps` gated (tabela acima);
`dreams`, `scholar_engine` e `world_state` seguem **pendentes**.

##### Revisão do mantenedor (06/11/2026) — 2 P1, ambos corrigidos

Não recomendou merge até corrigir. Ambos afetavam `add_knowledge_gap`
**e** `upsert_epistemic_knowledge_gap`.

**[P1-1] `instance_global` contornava a admissão.** O contrato só era
chamado para `authorized_aggregate`, mas **os leitores aceitam as duas
classes**. Reprodução: `source_kind="conversation"` recusada como
`authorized_aggregate`, porém **aceita e legível por outro usuário** quando
declarada `instance_global`.

> **Correção**: a condição passou a ser
> `resolved_origin in {AUTHORIZED_AGGREGATE, INSTANCE_GLOBAL}` — toda
> declaração que torna o material globalmente legível passa pelo contrato,
> não só a que afirma autorização.

**[P1-2] Evidência de origem privada ignorada.** Os dois métodos passavam
`private_derived=False` **fixo**. Reprodução: `provenance={"private_derived": True}`
admitida como global e devolvida a outro usuário — o writer substituía
evidência por afirmação de fonte pública.

> **Correção**: `private_derived=(provenance or {}).get("private_derived", False)`
> — a **evidência do chamador manda**. Chave ausente mantém o lado da própria
> declaração; tipo ambíguo (`"false"`) recusa com `ValueError` de
> `explicit_flag`, **nunca** vira `True`.

**Sentinelas novas (6; total 16 no arquivo)** — todas comprovando o
**bloqueio na leitura por outro participante**, não só a coluna:

| Teste | Cobre |
|---|---|
| `test_instance_global_declaration_is_gated_too` | P1-1: `instance_global` + `conversation` → quarentena, ilegível por carol **e** por dave |
| `test_instance_global_with_public_source_stays_readable` | **controle**: `instance_global` legítimo segue legível por dave |
| `test_private_derived_evidence_forces_refusal` | P1-2: evidência `True` → recusa **e** evidência preservada no `provenance_json` |
| `test_private_derived_evidence_also_gates_instance_global` | P1-2 sobre `instance_global` |
| `test_absent_or_false_private_derived_keeps_public_source` | **controle**: chave ausente/`False` → fonte pública segue admitida e lida por dave |
| `test_upsert_honors_both_p1_guards` | paridade do `upsert` nos dois P1 |

**Prova**: com os dois P1 reabertos nos dois produtores, **4 dos 16 testes
falham**; restaurados, 16/16 verdes. Suíte completa: **1435 passed**.

**Observação verificada (fora do escopo destes P1)**: a classe
`LEGACY_UNSCOPED` **não é legível por ninguém** — escrita sem declaração
(`fact_extraction`/`memory_flush` sem `origin_class`, sem Relation) nasce
`legacy_unscoped` com `origin_quarantined=0` e devolve **0 itens** para
carol, dave, admin e system (medido, não deduzido). Não é uma brecha de
leitura; é o efeito opaco do legado, e decidir fechar ou consumi-lo é corte
próprio.

**Condição 5 em vigor desde F-5a**: recusa, instâncias distintas e consentimento
revogado não esperam F-5c — entram nas entregas de F-5a e F-5b.

**Fora do escopo de F-5**: projeção de conteúdo privado → global com revisão e
política de retratação. Isso é o R1 completo (R1b/R1c) e exige decisão própria;
F-5 só fecha *quem pode declarar* global, não converte privado em global.
Além disso — e por decisão do mantenedor — F-5 **não** comprova assimilação nem
uso das leituras nas conversas (ver *Limite de escopo* acima).

#### F-5e — Tratamento das leituras (09/11/2026)

**Tarefa**: aplicar a regra do F-5a aos 13 sítios de leitura — material sem
autorização verificável tem tratamento explícito, **sem reclassificar o
histórico**. É o requisito de encerramento do F-5.

**Regra escolhida pelo mantenedor** (pergunta registrada antes de codar):
**evidência por tabela** — rejeitadas as alternativas "predicado completo em
tudo" (recusaria quase tudo nas tabelas sem `source_kind` → regressão em
essays/working_memory/symbolic_graph) e "marcar sem bloquear" (o material
continuaria chegando ao prompt).

##### Os dois helpers (`core/db/cognitive_ownership.py`)

| Helper | Onde vale | Regra |
|---|---|---|
| `globally_readable_sql(columns, *, origin_column, prefix)` | 11 sítios SQL | `authorized_aggregate` só com âncora (`source_refs_json`; `sources_cited_json` em essays); **sem coluna de âncoras → não serve**; `origin_quarantined = 0` quando existir; `instance_global` segue servindo (escopo da instância, não afirmação de autorização) |
| `is_globally_readable(row, *, agent_instance)` | `knowledge_gaps` (única tabela com `source_kind`) | rota pública do **próprio** `assess_global_admission` — sem duplicar regra; usa `provenance_json.private_derived` (mesma regra do P1-2) |

**14 pontos de aplicação** (os 13 do inventário + o leitor principal):

`core/cognitive_context.sql_visibility` · `agent_identity_context_builder._identity_visibility`
· `_dream_visibility_scope` · `core/db/essays.list_philosophical_essays` ·
`core/db/meta_cognition.get_latest_meta_cognition_evaluation` ·
`core/db/symbolic_graph.list_symbolic_triples` · `query_causal_neighborhood` ·
`core/db/working_memory.find_goal_thread_by_source_ref` ·
`engines/integrative_self._latest_components` (sonhos, working memory e
lacunas) · `engines/symbolic_graph.extract_from_identity_contradictions` ·
`scholar_engine.get_recent_loop_inspirations` (ramo com e sem Relation) ·
**`knowledge_gaps.get_active_knowledge_gaps`** (leitor principal — incluído
pelo mandato "predicado real onde existe `source_kind`").

##### Testes — `tests/test_f5e_reader_authorization.py`, 7 testes

Condição SQL (variantes, alias `t.`, `ownership_class`, quarentena) ·
predicado real (admitida/recusada/quarentena/Relation/`instance_global`) ·
**exclusão na leitura sem tocar a linha** · fonte legítima servida ·
`instance_global` segue servindo · ramo de Relation intacto ·
sonho do ISM sem âncora bloqueado e com âncora servido.

**Prova de que pegam a ausência do tratamento**:

| Neutralização | Resultado |
|---|---|
| `globally_readable_sql` → `1 = 1` + predicado desligado | **3 testes falham** |
| chamada do filtro removida de `get_active_knowledge_gaps` | `test_unverifiable_global_is_not_served_and_row_is_untouched` **falha** |
| restaurado | 7/7 verdes |

**Suíte completa: 1442 passed** (1435 + 7) · mock 20/20 · `diff --check` limpo.

##### Correção de ambiente de teste (registrada)

Os módulos `working_memory` e `integrative_self` são carregados **isolados**
por 3 arquivos de teste; meus imports `core` em tempo de execução passaram a
exigir `OpenAI` no stub. Comparação com baseline isolada:

| Arquivo (isolado) | Baseline | Antes da correção |
|---|---|---|
| `test_goal_manager` | 3 passed | 3 failed |
| `test_integrative_self` | 7 passed | 2 failed |
| `test_c12e_scope_fallbacks` | 12 passed | 3 failed |

Corrigido com o **padrão já existente em ~10 arquivos de teste** (bloco do
stub `OpenAI` no topo) — os três voltaram ao baseline exato (3/7/12 passed).
Falhas pré-existentes em `test_working_memory`, `test_loop_failure_policy`,
`test_action_catalog` e `test_loop_action_proposal` **não foram tocadas**.

##### Fora do escopo (confirmado)

Nenhuma linha reclassificada (testado: a recusa é exclusão na leitura e a
`origin_class` permanece intacta) · nenhum histórico reprocessado · nenhuma
quarentena liberada · nenhuma alteração de produção · Railway intocado.

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

> **Atualização 2026-11-04 — primeira verificação de pós-deploy.**
> Ambiente: deploy `fdf8e817` (2026-11-04 20:21 -03:00), 3 s após o merge de
> #65; healthcheck `ok` (loop `running / rumination_extro`, tabelas
> obrigatórias presentes). **`railway run` não alcança o volume** (`/data`
> não existe localmente), então tudo rodou via `railway ssh` em modo `mode=ro`
> e só metadados: o probe local abriria um SQLite vazio e registraria
> "produção" falsamente.
>
> - **R1a-4 → habilitado ✅ (schema)**: `source_kind`, `dedup_key`,
>   `observation_count`, `last_observed_at`, `origin_quarantined` — todos
>   `PRESENT` em `knowledge_gaps` de produção. **Validado em operação segue
>   ❌**: 0 linhas com `dedup_key` (1763 históricas) — a via nova ainda não
>   rodou, e o mestre manda *"aguardar uma ocorrência natural, sem mensagem ou
>   ação artificial para fabricar evidência"*.
> - **R1a-2 → habilitado parcial**: `processing_queue` existe com tenancy
>   completo; o worker de retry agendado em `main.py` **não** foi verificado.
> - **R1a-1 e R1a-3 → ❌** não verificados nesta rodada.

| Corte | Implementado | Testado | Habilitado | Publicado | Validado em operação |
|---|---|---|---|---|---|
| **R1a-1** | ✅ reparo do `memory_flush` **+** taxonomia de `source_kind` | ✅ 10 testes do flush + 20 da taxonomia | ❌ não verificado | ✅ `480d94f` (PR #64) | ❌ |
| **R1a-2** | ✅ `processing_queue`, retry F-4, órfãos F-1/F-2 | ✅ 45 testes + regressão 20/20 | ⚠️ tabela no ar (04/11); worker ❌ | ✅ `8245d63` (PR #62) | ❌ (fila vazia — vacuamente) |
| **R1a-3** | ✅ correções de isolamento P1 | ✅ testes de isolamento | ❌ não verificado | ✅ `fa56e05` (PR #61) | ❌ (não sondável) |
| **R1a-4** | ✅ proveniência + dedup por escopo + observação | ✅ 26 testes novos | ✅ schema (04/11) | ✅ `0637788` (PR #65) | ❌ (0 escritas novas) |
| **Observabilidade da fila** (aceite R1a / validação R1a-2) | ✅ sonda `processing_queue` (`scope_integrity`/`age`/`retry`) | ✅ 6 testes | ❌ não verificado | ❌ em revisão (PR #67) | ❌ (fila vazia) |
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

> **Atualização 2026-11-04 — a (a) ganhou ferramenta, mas segue ❌.**
> A validação era **inexecutável**: `processing_queue` não tinha probe algum
> (nem superfície no cockpit). A **PR #67** adiciona a sonda
> `processing_queue` (`scope_integrity`, `age`, `retry`), atendendo ao aceite
> literal do R1a — *"fila e idade dos pendentes ficam observáveis"*. Primeira
> leitura de produção no mesmo dia (dentro do container, `mode=ro`, só
> metadados; nenhum `payload_json`, `the_gap` ou `content` lido):
>
> - **(a)** `rows_missing_instance=0`, `by_agent_instance` só `jung_v1`,
>   `rows_in_scope = rows_total` — **porém a fila está vazia (0 linhas)**:
>   é *vacuamente* verdadeira, não *demonstrada* — nunca houve item para
>   inspecionar. Continua ❌ até a fila ter conteúdo real.
> - **(b)** não exercitada: anexos 5 + 1 todos `extracted`, nenhum `failed`
>   para o retry recuperar. Não se fabrica falha para provar. Continua ❌.
> - **(c)** não é sondável — propriedade de caminho de código, coberta pelos
>   testes de isolamento do R1a-3; sonda não a altera.

**F-5 permanece aberto** de propósito: é o alvo da taxonomia de origem (R1a-1 pendente / R1a-4), e o Princípio Áureo procura criar a via de "material global autorizado" antes de alimentá-la.

---

## 8. Limitações desta entrega

- Leitura estática. Suíte não reexecutada; nenhum teste de integração rodado.
- Os achados de P1/P2 são de auditoria de código, não de observação de produção. Confirmação em produção requer probe (não executado aqui).
- Severidade P1/P2 segue a rubrica da auditoria C12: P1 = conteúdo bruto cruza Relations ou chega a superfície pública/sem gate; P2 = apenas agregados/ids ou escopo ausente em `require_master`.
- `user_milestones` e `async_maturation_inbox` marcados como órfãos com base em varredura de INSERT/UPDATE em produção (fora de `tests/`). Pode haver produtores via scripts manuais não detectados.
