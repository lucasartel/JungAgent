# Auditoria C12 — Fechamento C5 (C12-C5): corte aprovado, correções e backlog

**Branch:** `feature/c12c5-closing-audit` · **Base:** `eee70fb` (Merge PR #48 — C12c4) · **Data:** 29/09/2026
**Natureza:** documento de escrita. A suíte não foi reexecutada neste passo; a validação registrada do PR está na Seção 6. Nenhum segredo, credencial ou dado de produção é reproduzido aqui.

---

## 1. Contexto

O **C12** é o ciclo de *fechamento cognitivo multi-relacional* do JungAgent: toda família de tabela que possui coluna de escopo (`agent_instance` + `relation_id`/`origin_relation_id`) precisa ser lida e escrita dentro da Relation correta, com recusa **fail-closed** quando o escopo não é verificável. O contrato canônico vive em `core/db/cognitive_ownership.py` (domínios, `prompt_access`, `status`/`next_cut` por tabela) e as funções de escopo em `core/db/relation_scope.py`, `engines/will_scope.py` e `work/tenancy.py`.

O **C5** é a frente de *auditoria de fechamento* do próprio C12: verificação read-only do estado real do código antes de declarar o ciclo encerrado. Nesta rodada, três trilhas de auditoria (subagentes, somente leitura, sem alteração de repositório) varreram o código e cada uma encerrou com relatório completo (`arquivo:linha`, severidade P1/P2, cobertura de testes):

| Trilha | Origem | Foco |
|---|---|---|
| **T1** | relatório de resalvas da spec C3 (revisões anteriores) | estado FECHADO/PARCIAL/ABERTO por item: `agent_hobby_artifacts`, psychometrics, `analysis_records`, camada IRT/Postgres, `irt_routes` |
| **T2** | varredura de SQL cru sem escopo | leituras brutas em todo o repositório; 16 achados P1 e 14 P2, mais listas de "OK por design" |
| **T3** | pendências restantes do ciclo C12 | fatiamento UNESCO por org, ciclo de vida de anexos Work, apagamento verificável por Relation fora do Work, migração legada |

Fatos do repositório nesta entrega: as correções estão no worktree da branch `feature/c12c5-closing-audit` (commit pendente neste ponto); a superfície pública `/blogdojung` continua em `main.py` (rota `main.py:498`), mas a construção do feed foi extraída para o módulo novo `blog_feed.py`; nenhum deploy ou ativação de produção aconteceu nesta rodada.

---

## 2. Escopo do corte aprovado

**Rubrica de severidade** (adotada pelas trilhas e mantida aqui): **P1** = conteúdo bruto (texto/valores/linhas) de tabela com coluna de Relation cruza Relations/usuários ou chega a superfície pública/sem gate; **P2** = apenas agregados/ids, escopo ausente/implícito em superfícies `require_master`, ou leitura de operador atrás de gate duplo.

**Dentro do corte (corrigido neste PR — Seção 3):** os **P1 críticos**, definidos por onde o dado privado aparece:

1. superfície **pública** `/blogdojung` (sem gate algum) — leituras do *living state* e do feed de entradas;
2. **payload/prompt do Will** — conteúdo cru de ruminação e conversas misturando Relations;
3. **leituras de prompt do domínio Work** — autobiografia, contexto vivo de leituras e conversa do operador;
4. **gatilho de expurgo** `scripts/purge_work_relation.py` — fechava o achado da T3 "`purge_work_for_relation` não tem nenhum chamador de produção";
5. **testes** de fuga/trava correspondentes (Seção 6).

**Fora do corte (→ backlog C6+ — Seção 5):** agregados globais, leituras do laboratório atrás de `require_master`, tabelas sem coluna de escopo (já marcadas `BLOCKED`/`C12g` no contrato) e as pendências de ciclo de vida (TTL de anexos, UNESCO por org, apagamento por Relation fora do Work). Nada é descartado: todos os achados das três trilhas seguem na Tabela da Seção 5.

**Verificados como já fechados nesta rodada (sem ação):**

- `archetype_conflicts` + `full_analyses` (T1, item 2b): colunas de escopo por migração aditiva, carimbo na escrita e `_pattern_scope` fail-closed — **FECHADO**;
- psychometrics EQ/values (T1, item 2a): escopo de Relation presente nas leituras principais — **FECHADO** com a ressalva P2 `agent_development` (registrada na Seção 5 como `T1-2a`);
- migração `scripts/migrate_legacy_user_data.py` (T3, item 4): **executada e verificada** (`--verify` read-only, 24 arquivos, `ok: true`; sem passo de deleção por desenho) — nada a fazer.

---

## 3. P1s corrigidos neste PR

Sete achados P1. As referências de *antes* apontam para `main.py` (pré-extração) e as de *depois* para o código já alterado no worktree.

### 3.1 — Motor Will lia ruminação e conversas cruas de todas as Relations (T2, achado 1)

- **Arquivo:** `will_engine.py` — `_recent_conversations` (`:529-555`), `_recent_rumination` (`:598-620`), `_active_rumination_tensions` (`:622-650`), `_build_source_payload` (`:717`), `refresh_cycle_state` (`:1236`).
- **Natureza do risco:** texto bruto (`user_input`/`ai_response`, `pole_a_content`/`pole_b_content`, `full_message`) de `conversations` e `rumination_*` entrava no payload do Will. O filtro era condicional (`if relation_id and "relation_id" in table_columns(...)`) e `scope_context` era chamado **sem** `resolve_participant_user_id` → `relation_id=None`, ramos `else` rodavam SQL **sem Relation e sem `agent_instance`**. Os 4 chamadores de produção passam só `user_id` (`rumination_scheduler.py:128`, `consciousness_loop.py:2208` e `:2723`, `admin_web/routes/trigger_routes.py:63`).
- **Correção:** `resolve_participant_user_id=user_id` nas duas chamadas de `scope_context` (`will_engine.py:734` em `_build_source_payload` e `:1256` em `refresh_cycle_state`); nos três helpers, **escopo global estrito**: com a coluna `relation_id` presente e sem Relation resolvida, a query recebe `AND relation_id IS NULL` (`:549`, `:616`, `:643`). Os ramos `if scoped_relation_id ... else <helper sem relation>` de `_build_source_payload` foram eliminados — ruminação, tensões e conversas agora sempre recebem `scoped_relation_id`.

### 3.2 — Blog público: último pulso do Will sem filtro (T2, achado 2)

- **Arquivo:** antes `main.py:646-661`; hoje `blog_feed.py` (`_load_blog_living_state`, cláusula em `:190`), rota pública `GET /blogdojung` em `main.py:498`.
- **Natureza do risco:** `SELECT ... FROM agent_will_pulse_events ORDER BY updated_at DESC LIMIT 1` sem **nenhum** filtro (nem `user_id`) numa rota sem autenticação; `action_attempted` (truncado em 84 chars) é renderizado como `living_state.will.last_release.action` no site público.
- **Correção:** `legacy_quarantine_clause(table="agent_will_pulse_events", relation_column="relation_id")` na leitura.

### 3.3 — Blog público: bloco de identidade global (T2, achado 3)

- **Arquivo:** antes `main.py:668-676`, `:686-694`, `:706-713`, `:764-772`; hoje `blog_feed.py:215` (`agent_identity_core`), `:237` (`agent_identity_contradictions`), `:261` (`agent_possible_selves`), `:323` (`agent_relational_identity`).
- **Natureza do risco:** até 110–180 chars de conteúdo identitário privado (traits, pares contraditórios, selves ideal/temido, identidade relacional) publicados com filtro apenas de `is_current`/`status` — sem instância, Relation ou user. O mesmo *living state* já aplicava quarentena aos sonhos logo ao lado (hoje `blog_feed.py:382`), ou seja, havia precedente de cuidado que não foi aplicado aqui.
- **Correção:** `legacy_quarantine_clause` com `origin_relation_id` nas quatro leituras.

### 3.4 — Blog público: feed de artefatos hobby sem qualquer escopo (T1, item 1 / P1)

- **Arquivo:** antes `main.py:847-869` (sem cláusula nenhuma); hoje `blog_feed.py:411-418` (`_load_blogdojung_entries`).
- **Natureza do risco:** `agent_hobby_artifacts` **não possui** colunas `relation_id`/`agent_instance` (contrato canônico marca a tabela `legacy_unscoped`, `status="BLOCKED"`, `next_cut="C12g"`) e aceita escrita participante via `will_pressure.py:940` — o feed público publicava `title`/`summary`/`image_url` de qualquer usuário.
- **Correção:** filtro explícito `user_id = ADMIN_USER_ID` na leitura do feed. O carimbo de escopo da tabela (colunas + mixin) permanece no backlog C12g — ver `T1-1b` na Seção 5.

### 3.5 — Autobiografia Work no prompt sem tenancy (T2, achado 5)

- **Arquivo:** `agent_identity_context_builder.py:1454` `_get_work_autobiography` (render em `:2382-2404`). *Observação de atribuição:* o briefing deste PR citava "(main.py)"; a função está no builder — a correção foi aplicada no local correto.
- **Natureza do risco:** `work_projects`, `work_artifacts` e `work_experience_events` lidos só por `status`, **zero** filtro de org/instância/Relation — nomes de projetos, títulos e eventos privados entram no prompt, violando `OWNERSHIP_CONTRACTS["work_and_actions"]` (`prompt_access = NEVER`, "C12f denies direct prompt access"), com o agravante apontado pela T2 de a procedência ser *declarada* pelo escopo da conversa em `core/engine.py:873-946` ("lavagem" de origem).
- **Correção:** `legacy_quarantine_clause` com `origin_relation_id` prefixado nas três leituras (`p.`, `a.`, `e.`).

### 3.6 — Leituras Work assimiladas no contexto vivo (T2, achado 6)

- **Arquivo:** `engines/work_scheduler.py:327` `get_assimilated_reading_context`.
- **Natureza do risco:** `work_artifacts` com `status='assimilated' AND content_type='reading_note'` sem org/instância/Relation — `provider_payload_json` (notas de leitura privadas) ia para o "contexto vivo" do agente.
- **Correção:** quarentena `origin_relation_id` na query (`work_scheduler.py:330-347`).

### 3.7 — Leituras Work na conversa do operador (T2, achado 15)

- **Arquivo:** `core/rumination_interiority.py:121` `admin_reading_awareness`.
- **Natureza do risco:** a leitura já checava `agent_instance` e porta admin (bom), mas **sem** `org_id`/`origin_relation_id` — material privado de outra org/Relation entrava no prompt do operador como `private_source_recall` declarado com a Relation da conversa (`core/engine.py:920-934`).
- **Correção:** quarentena `origin_relation_id` (`rumination_interiority.py:121-138`).

### 3.8 — Entregas de suporte no mesmo PR

- **`blog_feed.py` (novo):** `_load_blog_living_state` e `_load_blogdojung_entries` extraídos de `main.py` (import em `main.py:37`), isolar a superfície pública e torná-la testável sem subir a aplicação.
- **`scripts/purge_work_relation.py` (novo):** gatilho de produção para `purge_work_for_relation`, que antes só era alcançado por testes (T3). CLI: `--relation-id` (obrigatório), `--apply`, `--verify`, `--no-files`, `--pretty`; **dry-run por padrão**; `verify_work_purge` roda **sempre** no final; `exit 1` quando `--apply` deixa célula sobrevivente (`purge_is_clean` falso), `exit 2` para `relation_id` inválido.
- **Testes novos/atualizados:** `tests/test_c12c5_closing_audit.py` (8 testes) e ajuste dos stubs em `tests/test_will_engine_relational.py:127-138` (fetchers auxiliares agora aceitam os kwargs de escopo) — detalhe na Seção 6.

---

## 4. Decisão registrada: upload stamping (org/agent na importação)

**Decisão: NÃO alterado neste PR.** Registrado como *item de decisão*, não como bug — não entra no backlog como pendência de correção.

**Rationale.** `resolve_work_tenancy` (`work/tenancy.py:83-92`) é o único caminho legítimo de tenancy dos INSERTs do Work, e seu docstring é expresso: sem Relation explícita, a origem fica `NULL` (não classificada) — **"nunca se infere a org do admin que disparou o run"**; o docstring do módulo repete a política C4: *"Nunca se infere a organizacao do admin"* ("Never infer organization ownership from the admin user") — `org_id` nasce **apenas** de uma Relation explícita que carrega `org_id` próprio. Os caminhos de criação/importação no Work são `require_master` (`admin_web/routes/work_routes.py`, todas as rotas com `Depends(require_master)`).

Stampear `org_id`/`agent_instance` no momento da importação inferiria a organização a partir do master que dispara o run — exatamente o que o design C4 fail-closed por origem proíbe: um registro não pertence a uma org só porque um admin participante está vinculado a ela. As consequências práticas seriam (a) linhas carimbadas com a org errada quando o run for disparado por um master de outra org, e (b) um precedente que enfraquece o gate `org_admin` (que hoje trata registro sem origem atribuível como master-only).

**Caminho correto, se desejado:** um master que queira linhas carimbadas faz o carimbo **na origem** (papo/comando), depois da importação, com uma Relation explícita que carrega org própria — aí `resolve_work_tenancy` preenche `org_id` legitimamente. Qualquer revisão desta posição deve vir como proposta explícita de mudança de política C4, não como correção pontual.

**Relação com o backlog:** o achado `T3-2` (anexos de upload sem `origin_relation_id`) é distinto e segue aberto; sua correção deve observar esta mesma decisão — carimbo só com Relation explícita repassada na origem, nunca org inferida do admin.

---

## 5. Achados restantes — backlog C6+ (P2 e P1 fora do corte crítico)

> **Atualização 2026-10-01 (cortes C6–C9).** Linhas marcadas ✅ já foram publicadas e não devem ser tratadas como backlog: C6/PR #50 (T2-04, T2-07..T2-14), C7/PR #51 (T2-16, T2-29), C8/PR #52 (T2-17, T2-18, T2-20, T2-30) e C9/PR #53 (T2-19, B01 — agregados do blog e âncora do feed). **Roteiro acordado do restante:** E (T2-26, T2-27, T2-28 — fallbacks sem filtro, código morto, scripts de sondagem) → C (T2-21, T2-22, T2-25 — contagens e metacognição) → B (T2-23, T1-2a, T1-2c1, T1-2c2 — psicometria e desenvolvimento) → F (T3-1, T3-2 — Work e organizações), mantendo **D (T1-1b)** no roteiro mesmo com a futura retirada de Arte/Hobby do ciclo (registros históricos ainda alimentam leitores do agente) e **T3-3** (apagamento verificável por Relation fora do Work) explicitamente no roteiro.

A especificação deste documento previa a tabela de "achados P2 restantes"; como as trilhas também registraram **P1s que ficaram fora do corte aprovado** (superfícies majoritariamente atrás de `require_master` ou de script de operador, e dois caminhos de prompt/retalho ainda sem gate — `T2-04` e `T2-07`), eles seguem na mesma tabela, marcados como P1, para que **nenhum achado das três trilhas se perca**. O ID preserva a numeração original de cada trilha (T1/T2/T3) para rastreabilidade com os relatórios-fonte. Nenhum achado P1/P2 das trilhas foi omitido: os corrigidos estão na Seção 3, os remanescentes aqui.

| ID | Arquivo / local | Resumo | Severidade | Destino |
|---|---|---|---|---|
| T2-04 | `agent_identity_context_builder.py:221-230` | `agent_will_states` lido no contexto de autoconsciência arquitetural só com `WHERE user_id = ?` (sem instância/scope/Relation) e **vai para o prompt**; a leitura de sonho logo acima já é fail-closed (`_dream_visibility_scope`, `:1247-1252`) e a irmã `_latest_will_signal` (`:1659`) já é escopada — inconsistência explícita | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-07 | `core/db/semantic_memory.py:61-73` | Recall de fatos (`user_facts_v2`/`user_facts`) com `WHERE user_id = ? AND fact_value LIKE ?`, sem Relation/instância; alimenta `_build_enriched_query` (`:84`) da recuperação semântica | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-08 | `admin_web/routes/user_analysis_routes.py:123-154, 197-210` | Painel de dados brutos do usuário: últimas 10 linhas de `conversations` com `user_input`/`ai_response` inteiros, só por `user_id`; `require_master` mas **sem** `verify_user_access` (helper existe em `:29`) | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-09 | `admin_web/routes/user_analysis_routes.py:92` | `archetype_conflicts` só por `user_id` — mistura Relations do mesmo participante no painel (gate de org `require_org_admin` + `verify_user_access` existe) | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-10 | `admin_web/routes/research_lab_memory.py:39-40, 63-64, 107-126` | Fatos e gaps crus (`fact_value`, `the_gap`) só por `user_id`, atrás de `require_master` + flag, sem Relation | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-11 | `admin_web/routes/research_lab_rumination.py:35-61, 230-269, 299-340` | Ruminação do laboratório sem escopo de Relation/instância; `SELECT COUNT(*) FROM conversations` **sem WHERE** (`:230`); loop dinâmico sobre `sqlite_master` montando `SELECT * FROM {table} WHERE user_id = ?` em runtime | **P1** (contagem global: P2) | ✅ **FECHADO** (C6/PR #50) |
| T2-12 | `admin_web/routes/research_lab_mind.py:102, 167, 237` | Grafo da mente com fragmentos/tensões/insights crus nos payloads, só `user_id = ADMIN_USER_ID` | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-13 | `admin_web/routes/research_lab_debug.py:126-137, 143-149, 162-177, 189-201` | Debug devolve texto bruto (previews de `user_input`, `content` de fragmentos); atenuado por gate duplo `require_master` + `UNSAFE_ADMIN_ENDPOINTS_ENABLED` | **P1** (atenuado) | ✅ **FECHADO** (C6/PR #50) |
| T2-14 | `admin_web/routes/diagnostics_routes.py:132-141` | Diagnóstico de `user_facts` com `fact_value` cru por `user_id` (laço de usuários), `require_master` + flag | **P1** | ✅ **FECHADO** (C6/PR #50) |
| T2-16 | `scripts/blind/extract_samples.py:104-112, 132-140, 166-174, 199-207, 234-242` | Exportação de amostras de `conversations`, `rumination_insights`, `agent_will_states`, `agent_dreams`, `agent_meta_consciousness` só por `user_id`, sem gate (script CLI) | **P1** | ✅ **FECHADO** (C7/PR #51) |
| T2-17 | `will_pressure.py:484-491, 567-576` | Tensões de ruminação por `user_id` (busca de `id` + `COUNT` do backlog) **mesmo com `relation_id` já em escopo** na própria `_calculate_accumulation` (`:464-471`) — ganhos de pressão de uma Relation alimentam o estado de outra | P2 | ✅ **FECHADO** (C8/PR #52) |
| T2-18 | `will_pressure.py:423-461` | `_latest_conversation` e `_recent_real_conversation_count` só aplicam `if relation_id` (ids/timestamps); chamadores sem relation ficam sem filtro de instância | P2 | ✅ **FECHADO** (C8/PR #52) |
| T2-19 | `blog_feed.py:144, 299, 304-306` (antes `main.py:740-752`, `:605-611`, `:466-473`, `:791`) | Agregados públicos do blog: `COUNT` global de `rumination_tensions` (`status='maturing'`), `GROUP BY fragment_type` de `rumination_fragments` sem user/instância/Relation, `consciousness_loop_state ORDER BY id DESC` — vazam existência/volumes entre Relations | P2 | ✅ **FECHADO** (C9/PR #53) |
| T2-20 | `admin_web/routes/research_lab_dashboards.py:128-156, 192, 220-232` | Contagens globais de `agent_will_states` (`COUNT(*)`, `COUNT(DISTINCT cycle_id)`), `agent_will_pressure_state` e `agent_will_pulse_events` sem user/instância/Relation | P2 | ✅ **FECHADO** (C8/PR #52) |
| T2-21 | `engines/meta_cognition.py:144-151` | Metacognição conta `rumination_insights`/`rumination_tensions` por `user_id`, sem Relation/instância (colunas existem) | P2 | Backlog C6+ *(backlog conhecido: meta_cognition)* |
| T2-22 | `core/db/users.py:116` | `get_user_stats`: `SELECT COUNT(*) FROM conversations WHERE user_id = ?` sem Relation/instância | P2 | Backlog C6+ *(backlog conhecido: users.py:116)* |
| T2-23 | `quality_detector.py:125-138`; `evidence_extractor.py:404-406` | Psicometria (`user_psychometrics`, coluna `relation_id` existe) por `user_id`; em `evidence_extractor` a versão vinda de outra Relation desvia a leitura de `psychometric_evidence` | P2 | Backlog C6+ *(backlog conhecido: quality_detector)* |
| T2-24 | `admin_web/routes/irt_routes.py:271-476` (também T1, item 2d) | `/user/{user_id}` e `/comparison/{user_id}`: queries nominais só `user_id`, sem org/Relation/instância — **master-only deliberado**, com comentário no código ("superfície master-only até existir origem por registro") e testes que exigem `require_master`; agregados do dashboard já usam `org_user_scope_clause`. Limitação conhecida: origem por registro ainda não existe | P2 / decisão registrada | Backlog C6+ *(backlog conhecido: irt_routes)* |
| T2-25 | `jung_memory_metrics.py:51, 107, 218, 292-308, 347-348` | Métricas de memória com contagens por `user_id` e ranking global `SELECT user_id, user_name, COUNT(*) ... GROUP BY user_id` sem Relation/org — expõe `user_id`+`user_name` cruzando usuários | P2 | Backlog C6+ *(backlog conhecido: jung_memory_metrics)* |
| T2-26 | `engines/integrative_self.py:210, 225, 260-266` | Fallbacks condicionais que caem para caminho **sem** filtro quando as colunas de escopo não existem no DB (caminho principal é fail-closed com `origin_relation_id IS NULL` em `core/db/integrative_self.py:263-280`) | P2 | Backlog C6+ *(backlog conhecido: integrative_self fallbacks)* |
| T2-27 | `jung_core_facts_v2_integration.py:127-135, 252-257, 303-308, 329-335` | Módulo legado lê `user_facts_v2`/`user_facts`/`user_patterns` por `user_id` sem Relation; **sem importadores hoje (código morto)** — viraria P1 se religado | P2 | Backlog C6+ *(backlog conhecido: jung_core_facts_v2 dead code)* |
| T2-28 | `scripts/remote_db_probe.py` (19 ocorrências: `:182`, `:224`, `:595`, `:666`, `:839`, `:990`, `:1032`, `:1079`, `:1109`, `:1217`, `:1237`, `:1254`, `:1260`, `:1269`, `:1288`, `:2006`, `:2024`, `:2029`, `:2093`); `scripts/diagnostics/*`; `test_memory_metrics.py`/`test_llm_extraction.py` na raiz | Sondagem/diagnóstico por `user_id` em `conversations`/`agent_will_*`/`rumination_*`/`user_facts`; parte usa `_will_scope_filter` (correto), parte (`query_dreams`, `search_terms`, `count_rows`) não | P2 | Backlog C6+ *(backlog conhecido: remote_db_probe)* |
| T2-29 | `admin_web/routes/trigger_routes.py:46-55, 137`; `research_lab_rumination.py:230`; `consciousness_loop._phase_input_summary` | Contagens globais no fluxo do loop/gatilhos: `consciousness_loop_state` (sem Relation, filtra só `user_id=ADMIN`) e `SELECT COUNT(*) FROM conversations` **sem WHERE** — a contagem global é a mais grave do grupo | P2 | ✅ **FECHADO** (C7/PR #51) |
| T2-30 | `engines/loop_failure_post_commit_integration.py:65-66` | Idempotência do loop: `rumination_fragments WHERE user_id = ? AND source_kind='loop_failure'` — retorna só `id`, sem `relation_id`/`agent_instance` | P2 | ✅ **FECHADO** (C8/PR #52) |
| T1-1b | `will_engine.py:665-678`; `agent_meta_consciousness.py:111-131`; `agent_diary.py:1072-1086`; contrato `core/db/cognitive_ownership.py:217-223` | Leitores de `agent_hobby_artifacts` isolam por `user_id` mas **misturam Relations** (a tabela não tem `relation_id`/`agent_instance`; `_latest_hobby` sequer usa scope helper; contrato marca `BLOCKED`/`next_cut="C12g"`) | P2 | Backlog C6+/C12g *(carimbo de escopo da tabela)* |
| T1-2a | `core/db/psychometrics.py:130-137`; `core/db/agent_development.py:21-42` | `agent_development` sem colunas de escopo → cláusula vazia e "autoconsciência" lida/escrita por `user_id` puro (contadores derivados, não texto) | P2 | Backlog C6+ |
| T1-2c1 | `psychometric_validator.py:564-629` | `get_system_quality_report` promete partição (parâmetro/docstring `agent_instance`) mas as queries (`:595-602`, `:615-618`, `:622-629`) **não filtram nada**; sem chamadores nem testes — usar ou remover o parâmetro | P2 | Backlog C6+ |
| T1-2c2 | `irt_engine.py:787, 805-811` | `compare_with_legacy` lê `user_psychometrics WHERE user_id = $1` sem filtro de instância/Relation, atrás de rota master | P2 | Backlog C6+ |
| T3-1 | `core/db/legacy_exports.py:235-283` (pendência declarada no próprio docstring `:243-244`); `admin_web/routes/unesco_export_routes.py:35-48` | Fatiamento UNESCO **por org nunca entrou**: 6 subqueries filtram só Relation+instância; `unesco_pilot_data` sem coluna de org; rotas `require_master` sem parâmetro de org (precedente pronto em `dashboard_routes.py:317-330`) | P2 | Backlog C6+ *(backlog conhecido: UNESCO org scope)* |
| T3-2 | `work/attachments.py:80`; `admin_web/routes/work_routes.py:170-191`; `core/db/schema.py:1413-1431`; `work/retention.py` | Ciclo de vida de anexos Work: sem TTL/expiry, sem job de limpeza, sem reconciliação arquivo↔linha; upload não repassa `origin_relation_id` → anexo de produção fica `NULL` e é **inalcançável pelo expurgo** (fail-closed documentado em `retention.py:5-7`); testes carimbam a origem manualmente, não exercitam o caminho real | P2 | Backlog C6+ *(backlog conhecido: attachments TTL; carimbo observa a Seção 4)* |
| T3-3 | `core/db/` (nenhum `purge`/`erase` por Relation); política em `core/db/cognitive_ownership.py:62+` | Apagamento verificável **fora do Work**: só existe expurgo Work (agora com gatilho — Seção 3.8) e deleção por *usuário* (`users.py:70-100`); as colunas de Relation já migraram para ~6 famílias de tabela — falta rotina `purge`/`verify`/auditoria espelhando `work/retention.py` | P2 (esforço médio-alto) | Backlog C6+/C12g *(backlog conhecido: erase-by-Relation fora do Work)* |
| B01 | `blog_feed.py:354-363` (antes `main.py`) | Âncora do feed `/blogdojung`: `MAX(created_at)` sobre `UNION ALL` de `agent_dreams`/`agent_hobby_artifacts`/`consciousness_loop_phase_results WHERE phase='world'`/`external_research` **sem escopo de user/instância/Relation** — a janela de datas do feed é calculada a partir de material de qualquer origem (só metadado, mas mistura origens) | P2 | ✅ **FECHADO** (C9/PR #53) |

**Nota de cobertura de testes:** a T2 anotou ausência de teste de escopo para a maioria dos itens acima (ex.: `_load_blog_living_state` antes deste PR, `user_agent_data_page`, `jung_memory_metrics`, `research_lab_rumination`); os testes novos do PR (Seção 6) cobrem **apenas** os caminhos corrigidos. Cada item do backlog deve entrar com o próprio teste ao ser fechado.

**Item verificado como resolvido (não é backlog):** T3-4 — `scripts/migrate_legacy_user_data.py` executado (24 arquivos copiados com manifesto sha256), `--verify` `ok: true`, sem nada a deletar por desenho; coberto por `tests/test_c12c4_legacy_migration.py` (5 testes).

---

## 6. Testes

| Arquivo | O que cobre |
|---|---|
| **`tests/test_c12c5_closing_audit.py`** (novo, **8 testes**) | Fuga **e** trava de quarentena em cada frente corrigida: `test_blog_living_state_quarantines_classified_content` (`:138`) — conteúdo classificado (`rel-1`) nunca aparece e legado (`NULL`) continua visível em traits/contradição/selves/relacional/último pulso; `test_blog_entries_quarantine_dreams_and_filter_hobby_owner` (`:160`) — sonhos em quarentena + hobby restrito ao dono; `test_will_global_scope_reads_only_quarantined_rows` (`:303`) — escopo global estrito do Will só lê `relation_id IS NULL`; `test_will_source_payload_resolves_participant_relation` (`:329`) — `_build_source_payload` resolve o participante via `resolve_participant_user_id`; `test_work_autobiography_quarantines_classified_projects` (`:466`); `test_scheduler_reading_context_quarantines_classified_readings` (`:482`); `test_operator_reading_awareness_quarantines_classified_readings` (`:492`); `test_run_purge_reports_clean_after_apply_and_fails_when_dirty` (`:539`) — fluxo do gatilho de expurgo: apply limpo reporta `clean`, e sujeira sobrevivente derruba o exit code |
| **`tests/test_will_engine_relational.py`** (ajustado) | Stubs dos fetchers auxiliares (`_recent_rumination`, `_active_rumination_tensions`, `_recent_conversations`, …) agora aceitam os kwargs de escopo (`:127-138`) — acompanham a nova assinatura imposta pela correção do Will |
| **Suíte completa** | **1068 passed** |

Cobertura pré-existente relevante mantida: `tests/test_c12c4_retention.py` e `tests/test_c12c4_real_schema_flow.py` (expurgo Work), `tests/test_c12g_revocation_gate.py` / `test_c12g_revocation_raw_queries.py` (revogação), `tests/test_cognitive_ownership_contract.py` (contrato canônico, que mantém `agent_hobby_artifacts` como `BLOCKED`/`C12g`).

---

## 7. Vocabulário e funções de escopo

- **fail-closed** — na ausência de leitor/resolvedor de Relations, de elegibilidade verificável ou de colunas de escopo, a leitura é **negada** (`1 = 0`), nunca degradada para "sem filtro". Exemplos canônicos: `_dream_visibility_scope` (`agent_identity_context_builder.py:1247-1252`), `jung_rumination._relation_scope` (`:236-254`), `work/tenancy.py` (registro sem origem atribuível é master-only).
- **`NULL` = origem não classificada** — `relation_id`/`origin_relation_id` `NULL` significa *linha legada cuja procedência ainda não foi revisada*. **Não** significa "visível para todos": `NULL` só é lido por caminhos de legado/administração, nunca absorvido por consultas relacionais de outra Relation.
- **Quarentena (`legacy_quarantine_clause`)** — cláusula de leitura que restringe a `origin_relation_id IS NULL` (coluna de Relation da tabela), mantendo visível apenas o material legado não classificado e excluindo tudo que já pertence a uma Relation. Definida em `core/db/relation_scope.py`; usada agora em todo o blog (`blog_feed.py`), nas leituras Work (`agent_identity_context_builder.py`, `work_scheduler.py`, `rumination_interiority.py`) e na consolidação de identidade. É a mesma semântica de `rel_null`/`rel_not_null` em `core/db/legacy_exports.py`.
- **Escopo global estrito vs. pipeline pessoal** — no Will: com `resolve_participant_user_id=user_id`, `scope_context` resolve a Relation do participante e os helpers filtram `relation_id = ?` (**pipeline pessoal**); quando nenhuma Relation é resolvida (pipeline global/legado), os helpers filtram `relation_id IS NULL` (**escopo global estrito**) — nunca "todas as Relations". Antes da correção, o ramo `else` rodava sem filtro algum; a dupla de testes `test_will_global_scope_reads_only_quarantined_rows` + `test_will_source_payload_resolves_participant_relation` trava os dois lados.
- **Escopo de org (`org_user_scope_clause`, `org_scope`)** — recorte por *adesão* (`user_organization_mapping`), não por origem do registro; master tem visão global por política (`admin_web/auth/org_scope.py`). Um participante em duas orgs aparece nas duas — limitação conhecida e testada (`test_c12c3_review_fixes.py:260`).
- **`require_master` / `require_org_admin` / flags unsafe** — gates de *papel*, distintos do escopo de *origem*: `require_master` fecha para outros papéis, mas não recorta org/Relation/instância (por isso várias superfícies do laboratório seguem P1/P2 mesmo com gate). `UNSAFE_ADMIN_ENDPOINTS_ENABLED` é atenuante, não escopo.
- **Origem de tenancy Work** — `resolve_work_tenancy` só preenche `org_id`/`origin_class` a partir de Relation explícita com org própria (ver decisão na Seção 4); sem isso, tudo `NULL` e o registro fica master-only.

---

*Documento produzido no âmbito do C12-C5 (auditoria de fechamento). Fontes: relatórios das três trilhas read-only deste ciclo, verificação pontual no worktree da branch `feature/c12c5-closing-audit` e contratos canônicos do repositório. Nenhuma execução de suíte, commit ou alteração de arquivo além deste documento foi feita para produzi-lo.*
