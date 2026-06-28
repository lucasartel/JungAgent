# CLAUDE.md — Convenções operacionais do JungAgent

Leia antes de qualquer alteração. Em caso de conflito, este arquivo perde apenas para `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md` (o documento mestre vigente).

## O que é este projeto

Emulação cognitiva persistente sobre LLM (memória autobiográfica, ruminação, sonhos, vontade, desenvolvimento narrativo). Experimento transversal entre tecnologia, psicologia junguiana e linguística aplicada. **Não é um produto de chat; é um organismo de pesquisa.** O vocabulário interno ("sonho", "vontade", "individuação") é técnico do modelo psicológico — preserve-o, não o "corrija".

## Regras invioláveis

1. **Princípio Áureo**: nenhuma capacidade nova antes de a existente fechar circuito. Não adicione features fora da fase atual do roadmap.
2. **Fase atual**: Fase 0 (consolidação e instrumentação). Consulte a Seção 4 do documento mestre antes de começar qualquer tarefa.
3. **Máximo 500 linhas por arquivo novo.** Nenhum arquivo novo nasce na raiz — use os pacotes (`core/db/`, `work/`, `engines/`, `reasoning/`, `admin_web/routes/`).
4. **Âncoras de evidência**: toda afirmação autobiográfica do agente referencia uma fonte no padrão `tipo#id` (ex.: `conversation#1423`, `dream#87`, `loop#205`). Regex canônica em `agent_diary.py` (`PROFILE_SOURCE_RE`). Nunca gere texto autobiográfico sem âncora.
5. **Não silencie exceções.** O loop usa retry/cooldown/failure policy por fase (`consciousness_loop.py`). Novos `except Exception: pass` são proibidos; falhas devem ser logadas com contexto e, quando relevante, re-agendáveis.
6. **Testes antes do merge**: rode a suíte em `tests/` antes de concluir qualquer tarefa. Se a suíte ainda não cobre o que você alterou, adicione o teste na mesma entrega.
7. **Não toque sem aprovação explícita do mantenedor**: execução autônoma de código (Fase VII), políticas de segurança, e qualquer coisa que envie mensagens reais ao admin/usuários em produção.

## Mapa rápido da arquitetura

| Área | Onde está |
|---|---|
| Loop diário (8 fases: dream → identity → rumination intro → world → work → hobby → rumination extro → will) | `consciousness_loop.py` |
| Ruminação (fragmentos → tensões → insights; maturidade com `connection_count` e síntese forçada temporal) | `jung_rumination.py` |
| Vontade (3 drives: saber/relacionar/expressar) | `will_engine.py`, `will_pressure.py` |
| Sonhos | `dream_engine.py` |
| Identidade e contexto de prompt | `agent_identity_context_builder.py`, `core/engine.py` |
| Diário/perfil autobiográfico (evidence-first) | `agent_diary.py` |
| Desenvolvimento narrativo (fases 0–5, avaliação qualitativa) | `agent_development.py`, `agent_development_policy.py` |
| Mundo / knowledge gaps | `world_consciousness.py`, `core/db/knowledge_gaps.py` |
| Trabalho autônomo | `work/` |
| Persistência | SQLite + Qdrant; `core/database.py` (em decomposição para `core/db/`) |
| Admin web | `main.py`, `admin_web/` (FastAPI + Jinja2 + HTMX) |
| Interface | `telegram_bot.py` |

Deploy: Railway (volume em `RAILWAY_VOLUME_MOUNT_PATH`). LLM via OpenRouter.

## Convenções de trabalho

- **Refatoração dos monólitos** (`core/database.py`, `core/engine.py`, `admin_web/routes.py`, `main.py`): extraia em cortes pequenos, mantendo o arquivo original como fachada compatível até a extração completa. Nunca quebre os métodos públicos de `HybridDatabaseManager`.
- **Compatibilidade de schema**: alterações em tabelas SQLite usam `ALTER TABLE` com guarda de existência (padrão já usado em `jung_rumination.py`). Nunca exija recriação de banco.
- **Prompts de LLM são código crítico**: mudanças em prompts de julgamento (maturidade, avaliação narrativa, síntese) exigem rodar o runner de regressão e registrar o diff de comportamento.
- **Custo**: não adicione chamadas LLM a fases do loop sem registrar no contador de custo (Fase 0.5) e mencionar no PR.
- **Idioma**: código e identificadores em inglês; strings voltadas ao agente/admin em português (sem acentos nos módulos que já seguem esse padrão — observe o arquivo vizinho).
- **Branches**: `jungagent/self-work/*` são PRs gerados pelo próprio agente — revise, não delete. Trabalho humano/assistido usa branches descritivas normais.
- **Commits**: mensagem curta em inglês no imperativo (padrão do histórico: "Inject narrative autobiography into prompt").

## Validação mínima por entrega

1. `python -m py_compile` nos módulos tocados
2. Suíte `tests/` verde
3. Se tocou prompt de julgamento ou fórmula cognitiva: runner de regressão + diff documentado
4. `git diff --check` limpo

## Onde buscar contexto

- Roadmap e critérios de saída da fase: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`
- Histórico e decisões anteriores: `docs/` (ROADMAPs e PLANOs por subsistema)
- Resultados de pesquisa (avaliação cega, relatórios mensais): `docs/research/`
