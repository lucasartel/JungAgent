# Plano de Decomposicao de core/database.py

Data: 2026-06-17
Fase: 0.7 do Documento Mestre de Emulacao Cognitiva V2
Escopo deste PR: documentacao apenas, sem mudanca de runtime.

## Objetivo

Decompor `core/database.py` sem quebrar a fachada publica `HybridDatabaseManager`.
O arquivo tem 3.130 linhas e ainda concentra schema, bootstrap de clientes,
conversas, busca semantica, fatos, padroes, analises e metodos agregadores.

O objetivo final da Fase 0.7 e deixar `core/database.py` com menos de 500 linhas
ou elimina-lo como implementacao pesada, preservando imports e chamadas atuais.

## Regra de preservacao

`HybridDatabaseManager` continua sendo a fachada publica durante toda a fase.
Consumidores nao devem migrar para os mixins diretamente no primeiro momento.
Cada corte deve:

- mover um dominio para `core/db/<dominio>.py`;
- adicionar o mixin correspondente na heranca da fachada;
- preservar nomes e assinaturas publicas;
- incluir teste offline ou smoke de import/instanciacao quando possivel;
- passar CI antes do proximo corte.

## Consumidores principais

Consumidores diretos da fachada:

- `core/engine.py`: `save_conversation`, `build_rich_context`, `semantic_search`;
- `telegram_bot.py`: instanciacao, conversas, estado do agente;
- `jung_proactive_advanced.py`: `save_conversation`, `get_user_conversations`;
- `jung_rumination.py`: conversas recentes e salvamento de conversa;
- `admin_web/routes.py`: usuarios, conversas, introspeccao legada;
- `admin_web/routes/*`: instanciacao da fachada em dashboards e modulos;
- `main.py`: usuarios e instanciacao;
- scripts/testes legados: fact extraction, memory consolidation, retrieval.

Contratos que nao podem quebrar:

- `from core.database import HybridDatabaseManager`;
- `from core import HybridDatabaseManager`;
- `from jung_core import HybridDatabaseManager`;
- metodos publicos usados por consumidores existentes.

## Mapa atual de metodos

Bootstrap e schema:

- `__init__` linhas 40-127;
- `transaction` linhas 133-148;
- `_init_sqlite_schema` linhas 154-1204.

Conversas e metadata:

- `_calculate_recency_tier` linhas 1212-1229;
- `_get_dominant_archetype` linhas 1231-1252;
- `_extract_people_from_conversation` linhas 1254-1295;
- `_extract_topics_from_keywords` linhas 1297-1327;
- `calculate_temporal_boost` linhas 1329-1376;
- `save_conversation` linhas 1382-1526;
- `get_user_conversations` linhas 1528-1582;
- `count_conversations` linhas 1584-1588;
- `conversations_to_chat_history` linhas 1590-1625;
- `save_proactive_approach` linhas 1631-1652;
- `_extract_names_from_text` linhas 1657-1680;
- `_detect_topics_in_text` linhas 1682-1708.

Busca, contexto e memoria semantica:

- `_is_factual_memory_query` linhas 1710-1756;
- `_get_current_facts_any` linhas 1758-1812;
- `_get_priority_facts_for_query` linhas 1814-1886;
- `build_priority_fact_context` linhas 1888-1905;
- `_build_enriched_query` linhas 1907-1988;
- `_calculate_adaptive_k` linhas 1994-2034;
- `_rerank_memories` linhas 2036-2169;
- `semantic_search` linhas 2175-2200;
- `_mem0_context_to_memory_rows` linhas 2202-2219;
- `_fallback_keyword_search` linhas 2221-2246;
- `_search_relevant_facts` linhas 2252-2325;
- `_format_facts_hierarchically` linhas 2327-2358;
- `_get_relevant_patterns` linhas 2360-2382;
- `_compress_context_if_needed` linhas 2384-2403;
- `build_rich_context` linhas 2405-2518.

Fatos e correcoes:

- `extract_and_save_facts` linhas 2524-2597;
- `_save_or_update_fact` linhas 2599-2652;
- `extract_and_save_facts_v2` linhas 2658-2740;
- `_get_current_facts` linhas 2742-2762;
- `_apply_correction` linhas 2764-2805;
- `_find_current_fact` linhas 2807-2826;
- `_annotate_chromadb_correction` linhas 2828-2830;
- `_update_chroma_document` linhas 2832-2834;
- `_save_fact_v2` linhas 2836-2929.

Padroes, desenvolvimento e analises:

- `detect_and_save_patterns` linhas 2935-3009;
- `_ensure_agent_state` linhas 3015-3018;
- `_update_agent_development` linhas 3020-3023;
- `_check_phase_progression` linhas 3025-3028;
- `get_agent_state` linhas 3030-3033;
- `get_milestones` linhas 3035-3038;
- `get_user_conflicts` linhas 3044-3053;
- `save_full_analysis` linhas 3059-3079;
- `get_user_analyses` linhas 3081-3089;
- `get_all_users` linhas 3098-3120;
- `count_memories` linhas 3122-3124;
- `close` linhas 3126-3129.

## Cortes propostos

### Corte 0.7.1 - Schema SQLite

Criar `core/db/schema.py`.

Mover:

- `_init_sqlite_schema`;
- constantes auxiliares de schema, se surgirem durante a extracao.

Risco:

- medio, porque o schema e grande, mas pouco acoplado a chamadas externas.

Validacao:

- smoke de instanciacao com `Config.SQLITE_PATH` temporario;
- verificacao de tabelas principais criadas;
- CI completo.

### Corte 0.7.2 - Conversas

Criar `core/db/conversations.py`.

Mover:

- `_calculate_recency_tier`;
- `_get_dominant_archetype`;
- `_extract_people_from_conversation`;
- `_extract_topics_from_keywords`;
- `calculate_temporal_boost`;
- `save_conversation`;
- `get_user_conversations`;
- `count_conversations`;
- `conversations_to_chat_history`;
- `save_proactive_approach`;
- `_extract_names_from_text`;
- `_detect_topics_in_text`.

Risco:

- alto, porque `save_conversation` aciona mem0, extraçao de fatos e agent state.

Validacao:

- teste offline com SQLite em memoria para salvar e listar conversas;
- smoke de `save_conversation` com `mem0=None` e fact extractor desativado;
- teste de ordenacao/limite de `get_user_conversations`.

### Corte 0.7.3 - Busca Semantica e Contexto

Criar `core/db/semantic_memory.py`.

Mover:

- `_is_factual_memory_query`;
- `_get_current_facts_any`;
- `_get_priority_facts_for_query`;
- `build_priority_fact_context`;
- `_build_enriched_query`;
- `_calculate_adaptive_k`;
- `_rerank_memories`;
- `semantic_search`;
- `_mem0_context_to_memory_rows`;
- `_fallback_keyword_search`;
- `_search_relevant_facts`;
- `_format_facts_hierarchically`;
- `_get_relevant_patterns`;
- `_compress_context_if_needed`;
- `build_rich_context`.

Risco:

- alto, porque e o caminho principal de contexto do agente.

Validacao:

- testes existentes de retrieval;
- smoke deterministico com `mem0=None`;
- comparacao de saida textual para fatos prioritarios e fallback keyword.

### Corte 0.7.4 - Fatos e Correcoes

Criar `core/db/facts.py`.

Mover:

- `extract_and_save_facts`;
- `_save_or_update_fact`;
- `extract_and_save_facts_v2`;
- `_get_current_facts`;
- `_apply_correction`;
- `_find_current_fact`;
- `_annotate_chromadb_correction`;
- `_update_chroma_document`;
- `_save_fact_v2`.

Risco:

- medio/alto, porque ha fallback v1/v2 e integracao com correction detector.

Validacao:

- testes existentes de fact extraction;
- smoke sem LLM real para `_save_or_update_fact` e `_save_fact_v2`;
- teste de correcao preservando historico.

### Corte 0.7.5 - Padroes, Analises e Agregadores

Criar `core/db/patterns.py` e `core/db/analysis.py`, ou um unico
`core/db/analysis.py` se o corte precisar ser menor.

Mover:

- `detect_and_save_patterns`;
- `get_user_conflicts`;
- `save_full_analysis`;
- `get_user_analyses`;
- `get_all_users`;
- `count_memories`.

Manter wrappers de `agent_development` como estao, ou converter para mixin fino
apenas se isso reduzir imports locais repetidos.

Risco:

- medio, pois admin e dashboards dependem desses metodos.

Validacao:

- smoke de listagem de usuarios;
- teste de salvar e listar analise;
- smoke de conflitos.

### Corte 0.7.6 - Bootstrap/Fachada

Criar `core/db/bootstrap.py` somente se ainda houver ganho claro.

Possiveis movimentos:

- inicializacao de conexao SQLite;
- inicializacao mem0;
- inicializacao cliente LLM interno;
- `transaction`;
- `close`.

Risco:

- medio, porque mexe no ciclo de vida da instancia.

Validacao:

- smoke de import/instanciacao;
- fechamento idempotente;
- Railway deploy com migrations OK.

## Ordem recomendada

1. `schema.py`: maior reducao de tamanho com baixo acoplamento de runtime.
2. `conversations.py`: estabiliza o caminho mais usado antes da busca.
3. `semantic_memory.py`: caminho critico do prompt e recuperacao.
4. `facts.py`: separa extracao/correcao depois da busca estar isolada.
5. `analysis.py`/`patterns.py`: limpa a cauda administrativa.
6. `bootstrap.py`: somente se a fachada ainda ficar acima do limite.

## Criterios de saida da Fase 0.7

- `core/database.py` com menos de 500 linhas ou convertido em fachada fina;
- `HybridDatabaseManager` importavel pelos caminhos atuais;
- nenhum consumidor alterado sem necessidade;
- CI verde a cada corte;
- Railway sobe com migrations OK;
- smoke de instanciacao documentado no PR final.

## Primeiro corte de codigo sugerido apos aprovacao

Executar o Corte 0.7.1: extrair `_init_sqlite_schema` para
`core/db/schema.py` como `SchemaDatabaseMixin`.

Justificativa:

- remove cerca de 1.050 linhas do arquivo central;
- reduz risco de colisao com metodos de runtime;
- fornece ganho arquitetural claro sem alterar consumidores externos;
- cria precedente de teste de schema antes dos cortes mais vivos.
