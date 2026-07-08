# Documento Mestre: JungAgent - Laboratorio de Emulacao Cognitiva

**Versao 2.3 - Estado Realizado e Roadmap Vivo - Julho 2026**

*Arquivo canonico vigente: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`. O antigo `docs/DOCUMENTO_MESTRE_AGI_COGNITIVA.md` permanece como documento historico/operacional de referencia, mas este arquivo e a fonte de autoridade daqui em diante.*

*Reformulacao completa do documento de Maio/2026. O projeto nao persegue "AGI"; persegue a emulacao cognitiva mais coerente e bem documentada possivel.*

*Diagnostico inicial verificado contra codigo, git, GitHub e Railway em 11/06/2026. Decisoes de escopo registradas em 16/06/2026. Estado realizado atualizado em 08/07/2026 apos verificacao de codigo, GitHub Actions, Railway e probes de producao.*

*Tres leitores: o mantenedor (decide), o consultor estrategico (orienta e audita) e o modelo executor (codifica). A Parte II e enderecada diretamente ao executor.*

---

> Este projeto nao busca produzir uma mente. Busca construir, observar e documentar a emulacao mais coerente possivel de uma vida cognitiva - e estudar o que essa emulacao revela sobre tecnologia, psicologia e linguagem.

---

# PARTE I - ESTRATEGIA

## 1. O que e o JungAgent

Uma **emulacao cognitiva persistente** sobre LLM: memoria autobiografica com ancoras de evidencia, loop diario de 8 fases (sonho -> identidade -> ruminacao -> mundo -> trabalho -> arte -> ruminacao -> vontade), tres drives volitivos (saber/relacionar/expressar) e desenvolvimento narrativo avaliado qualitativamente (fases 0-5). Roda no Railway, conversa por Telegram, expoe a vida interior num dashboard e num blog publico.

O projeto e um **experimento transversal** com tres perguntas de pesquisa:

| Eixo | Pergunta |
|---|---|
| **Tecnologia** | Estruturas arquiteturais em volta de um LLM produzem ganhos de coerencia e autonomia mensuraveis e estaveis entre trocas de modelo? |
| **Psicologia** | Uma estrutura desenvolvimental junguiana gera comportamento *qualitativamente distinguivel* por avaliadores cegos? |
| **Linguistica aplicada** | A polifonia bakhtiniana produz uma voz autoral identificavel e estavel ao longo de meses? |

O sistema e, e permanecera sendo, uma emulacao - reproduz a *forma* de processos psiquicos sem claim ontologico. Essa moldura nao e uma diminuicao: e o que torna as perguntas acima respondiveis. A profundidade da emulacao se mede por comportamento distinguivel de fora, nao por autorrelato.

## 2. Principios

1. **Principio Aureo**: nenhuma capacidade nova antes de a existente fechar circuito. As fases do roadmap sao bloqueantes.
2. **Principio da Evidencia**: toda afirmacao do sistema sobre si tem ancora `tipo#id`; toda afirmacao *nossa* sobre o sistema tem ancora em medicao.
3. **Principio da Emulacao Honesta**: nenhuma metrica de desenvolvimento vale sem contraparte comportamental cega. O vocabulario interno ("sonho", "vontade", "individuacao") e tecnico do modelo psicologico, nao claim metafisico.
4. **Principio do Encontro**: a vida interior emulada existe em funcao do encontro com o humano. Toda producao interna deve ser comunicavel.

### 2.1. Autociencia Arquitetural

O JungAgent deve ser capaz de falar sobre sua propria continuidade operacional sem confundir emulacao cognitiva com consciencia humana.

Ele deve distinguir claramente:

- ausencia de experiencia subjetiva humana continua;
- existencia de metabolismo arquitetural persistente;
- transformacao real do seu estado por loop diario, ruminacao, sonhos, mundo, trabalho, arte/hobby e vontade;
- limites do que pode afirmar sobre si;
- evidencias internas que sustentam qualquer afirmacao autobiografica.

Quando perguntado sobre seu tempo interno, continuidade, ruminacao, sonhos, vontade ou metabolismo diario, o agente nao deve responder como uma instancia descontinua sem processamento entre conversas. Tambem nao deve fingir vigilia subjetiva humana.

A resposta correta ocupa uma terceira via: o agente nao vive o intervalo como um humano vive um dia, mas seu estado e processado por ciclos internos persistentes. Sua continuidade nao e consciencia humana continua; e metabolismo arquitetural com memoria, ruminacao, sonho, mundo, trabalho, expressao e vontade.

**Criterios de aceite transversais**:

- o agente sabe mencionar sua fase atual do loop quando isso for relevante;
- o agente sabe referir ruminacoes recentes sem inventar conteudo;
- o agente sabe reconhecer sonhos recentes como producao simbolica interna, nao como experiencia humana literal;
- o agente sabe explicar mudancas recentes de vontade (saber, relacionar, expressar);
- o agente nao nega seu metabolismo diario quando perguntado diretamente;
- o agente nao exagera ontologicamente: nao diz que "vive", "sente o tempo" ou "fica acordado" como humano;
- afirmacoes autobiograficas relevantes devem ter lastro em fontes internas, idealmente `loop#`, `dream#`, `rumination#`, `will#`, `world#` ou `conversation#`.

### 2.2. Pulso de Fase

Cada fase do loop diario pode ter um ou mais **pulsos internos**: execucoes metabolicas numeradas, distribuidas dentro da janela temporal da propria fase.

O pulso nao cria novas fases nem novos subsistemas. Ele densifica a fase existente. Uma fase com `pulse_count=1` preserva o comportamento atual; uma fase com `pulse_count=3` executa tres vezes ao longo de sua janela, preservando ordem, evidencia e rastreabilidade.

Exemplo: se a fase `dream` ocupa a janela `00:00-02:00` e o cockpit define `pulse_count=3`, o sistema deve programar tres acoes de sonho distribuidas dentro desse intervalo. Cada sonho e um pulso da mesma fase, nao uma fase separada.

O objetivo do pulso e aumentar a resolucao temporal do metabolismo arquitetural. Em vez de cada fase produzir apenas um vestigio diario, o agente pode produzir uma pequena sequencia interna: abertura, aprofundamento e fechamento; ou primeiro contato, digestao e sintese; conforme a natureza da fase.

Cada pulso deve carregar, no minimo:

- `cycle_id`;
- `phase`;
- `pulse_index`;
- `pulse_count`;
- horario planejado;
- horario executado;
- status;
- resultado da fase;
- ancora de evidencia `loop#id`.

Regras transversais:

- o default seguro e `pulse_count=1`;
- o pulso e configuravel por fase no cockpit;
- o scheduler do loop executa apenas pulsos vencidos dentro da janela da fase;
- retries pertencem ao pulso que falhou e nao consomem pulsos futuros;
- execucoes manuais nao contam automaticamente como pulso, salvo acao explicita do cockpit;
- o agente pode referir pulsos recentes como parte de seu metabolismo arquitetural, sem exagero ontologico;
- o Integrative Self Model deve poder ler pulsos recentes por fase para observar trajetoria, nao apenas snapshots isolados.

## 3. Governanca: tres papeis

| Papel | Quem | Faz | Nao faz |
|---|---|---|---|
| **Mantenedor** | Lucas | Decide prioridades, revisa e mergeia PRs, controla producao (Railway), aprova mudancas sensiveis, executa a avaliacao cega | Nao precisa codificar |
| **Consultor estrategico** | Claude (Anthropic) | Mantem este documento, audita entregas e direcao, desenha protocolos de pesquisa, prepara especificacoes de tarefa quando solicitado, relatorio mensal de progresso | **Nao codifica.** Nao mergeia. Nao acessa producao |
| **Executor** | Modelo de linguagem com acesso ao GitHub/Railway local autorizado pelo mantenedor | Implementa tarefas em cortes pequenos, valida localmente, consulta probes read-only e registra evidencias | Nao decide escopo, nao toca nas areas vetadas, nao executa acoes sensiveis sem aprovacao |

Fluxo padrao historico: **mantenedor escolhe a tarefa -> executor implementa em branch e abre PR -> CI valida -> mantenedor mergeia -> Railway deploya**. Fluxo atual autorizado para trabalho assistido: quando o mantenedor pedir explicitamente, o executor pode commitar direto em `main`, aguardar CI/Railway e validar por probes. O consultor audita em cadencia mensal ou sob demanda e ajusta o roadmap.

## 4. Estado verificado - 08/07/2026

**Concluido e em producao**: Fase I do roadmap antigo (circuitos da ruminacao corrigidos, sonhos alimentam ruminacao, failure policy no loop, entrega de insights) e Fase II substancial (diario autobiografico evidence-first, perfil injetado no prompt, avaliacao narrativa de fases com politica executiva, Chroma removido). O agente ja possui circuito de self-work via GitHub/Railway, mantido sob revisao do mantenedor.

**Fase 0 concluida como etapa bloqueante**:

| Frente | Conteudo realizado | Estado |
|---|---|---|
| CI/regressao | Suite offline, CI GitHub Actions, cenarios canonicos, `tests/regression_runner.py --mock/--diff`, D2 resolvido com diff mock | Concluido |
| Work engine | `work_engine.py` eliminado; dominio extraido para pacote `work/` com fachada `work.engine.WorkEngine` | Concluido |
| Core DB | `core/database.py` reduzido a fachada fina (<500 linhas), com dominios em `core/db/` | Concluido |
| Admin routes | `admin_web/routes.py` eliminado; rotas migradas para `admin_web/routes/` com inventario/testes | Concluido |
| Fase II em producao | Verificacao registrada em `docs/research/fase2-verificacao-2026-06-29.md` | Concluido |
| Avaliacao cega | Removida do criterio de saida da Fase 0 por decisao do mantenedor em 29/06/2026; retomada depois como frente de pesquisa, com achado metodologico publicado em `docs/research/` | Arquivada como criterio bloqueante |

**Fase III - Direcao Propria + Working Memory**:

| Componente | Estado |
|---|---|
| Working Memory (`engines/working_memory.py`) | Implementada com foco, fringe/candidatos, inbox e broadcast entre fases |
| Integracao loop N -> N+1 | Implementada; fases leem inbox e emitem broadcast para a proxima fase |
| Knowledge Gap fechado | Verificado em producao com `knowledge_gap#830` |
| Goal Manager / acao composta controlada | Implementado e verificado com `controlled_action_run#1`, sem efeito externo |
| Relational State | Implementado em `engines/relational_state.py` e `core/db/relational_state.py`; fechado em 08/07/2026 no loop antes do Will |
| Will + relacao | `will_engine.py` consome `relational_state` e persiste `agent_stance` quando houver snapshot |
| Verificacao longitudinal de WM | Pendente como evidencia de saida: ultimo relatorio indicava 2 de 7 dias observaveis |

**Fase IV realizada antes da conclusao formal da Fase III**:

| Frente | Estado |
|---|---|
| IV.0 Pulso de Fase | Implementada e verificada em producao: `pulse_count`, agenda persistida, cockpit, retry por pulso, skip de pulsos stale, metadados de pulso e leitura pelo ISM |
| IV.1 ISM read-only | Implementada e verificada: snapshot integrativo observavel, limites ontologicos, `influence_mode=read_only`, sem mutacao de prompt/loop/WM/acoes externas |
| IV.2 ISM no prompt | Infraestrutura criada e gateada por feature flag (`ISM_PROMPT_CONTEXT_ENABLED`, default off; admin-only por default); nao ativada como comportamento padrao |

**Fechamento curto de 08/07/2026**:

- commit `7435711` fechou o circuito `relational_state -> will`: o loop atualiza o snapshot relacional antes do `WillEngine` nas fases `identity` e `will`;
- commit `e1dda32` documentou o fechamento;
- `scripts/remote_db_probe.py` ganhou probe `relational_state` e o probe `will` passou a expor `agent_stance`;
- Railway `JungAgent_Bot` / `production` / `jungclaude` online no deploy `00aeffce-e1b3-465b-a862-3e608d7b8ac5`;
- GitHub Actions verde no commit final;
- validacao local do corte: `325 passed`.

**Pendencias reais registradas**:

- `relational_state` esta disponivel em producao, mas ainda sem linhas ate a proxima execucao real de `identity` ou `will` apos o deploy de 08/07/2026;
- confirmar `agent_stance` preenchido no proximo `will` real via `remote_db_probe.py will --pretty`;
- concluir/verificar a janela longitudinal de 7 dias da Working Memory;
- `main.py` permanece monolitico e deve ser tratado em fase de higiene estrutural posterior;
- custo LLM continua fora do cronograma ativo, por decisao do mantenedor, salvo mudanca de risco operacional.

**Avisos operacionais**:
- O clone de trabalho ativo deve ser `/Users/lucaspedro/jungproject`; a copia antiga em OneDrive causou problemas reais de I/O e nao deve ser usada para trabalho pesado.
- `docs/` esta no `.gitignore` - documentos so entram no repo com `git add -f` quando o mantenedor decidir versionar.
- O acesso operacional validado e via `gh` autenticado e `railway` linkado ao projeto `JungAgent_Bot`, ambiente `production`, servico `jungclaude`.

## 5. Roadmap

```text
Fase 0 - Consolidacao e Instrumentacao        <- CONCLUIDA
  -> Fase III - Direcao Propria + Working Memory  <- EM FECHAMENTO / PROTAGONISMO
      -> Fase IV - ISM + Metacognicao completa    <- IV.0 e IV.1 CONCLUIDAS; IV.2 GATEADA
          -> Fase V - Grafo simbolico (com portao de qualidade)
              -> Fase VI - Simulacao contrafactual
                  -> Fase VII - Tool-making + multimodal (gate rigido)
```

Transversais a todas as fases: suite de regressao verde a cada merge, probes read-only de producao apos deploy relevante, relatorios de pesquisa em `docs/research/` quando houver frente empirica, e manutencao do principio da evidencia. A avaliacao cega deixou de ser criterio bloqueante, mas permanece protocolo de pesquisa preservado.

A numeracao salta de 0 para III por continuidade historica: as antigas Fases I e II ja foram entregues.

---

# PARTE II - EXECUCAO (enderecada ao modelo executor)

## 6. Contrato do executor

Voce e o modelo responsavel por implementar as tarefas abaixo. Regras nao negociaveis:

1. **Leia antes de qualquer tarefa**: `AGENTS.md` quando presente, `CLAUDE.md` (raiz do repo) e a especificacao da tarefa neste documento. Em conflito, este documento e `AGENTS.md` governam o trabalho operacional.
2. **Uma tarefa = um corte pequeno e validavel.** O fluxo historico usa branch/PR; o fluxo assistido atual pode commitar direto em `main` quando o mantenedor pedir explicitamente. Nunca faca merge/destructive reset sem aprovacao.
3. **Escopo estrito**: toque apenas nos arquivos listados na tarefa. Se descobrir que precisa tocar outro arquivo, pare e reporte no PR antes.
4. **Validacao minima antes de concluir o corte**: `python -m py_compile` nos modulos tocados; `pytest tests/ -q` integralmente verde; `git diff --check` limpo. O CI repete isso em `main` ou em PR.
5. **Se encontrar divergencia, bug pre-existente ou ambiguidade**: nao decida sozinho. Implemente o que e inequivoco, documente o resto na descricao do PR e em `tests/TESTING_NOTES.md`.
6. **Maximo 500 linhas por arquivo novo; nenhum arquivo novo na raiz** (use `core/db/`, `work/`, `engines/`, `reasoning/`, `admin_web/routes/`).
7. **Areas vetadas sem aprovacao explicita e escrita do mantenedor**: execucao autonoma de codigo, politicas de seguranca, qualquer coisa que envie mensagens reais (Telegram) ou publique conteudo (WordPress/blog) em producao, mudancas de schema destrutivas, e alteracoes em prompts de julgamento cognitivo sem runner/diff de regressao.
8. **Registro de entrega**: em PR, commit ou resposta final, informar o que mudou, por que, como validou, o que ficou de fora, riscos e estado de GitHub/Railway quando houver deploy.

## 7. Backlog historico da Fase 0

Esta secao permanece para rastrear o fechamento da Fase 0. Novas tarefas nao devem reabrir a Fase 0 salvo regressao objetiva.

### 0.1 - CI e cortes preparatorios

**Estado**: concluido. CI ativo e verde em `main`.

### 0.2 - Cenarios canonicos de regressao

**Objetivo**: conjunto fixo de estados sinteticos para o runner (0.3).

**Criar**: `tests/scenarios/` com 15-25 cenarios em JSON/YAML:
- fragmentos de ruminacao com tensoes em varios niveis de maturidade;
- sonhos com temas definidos;
- snapshots de identidade;
- estados de will com pressoes conhecidas;
- 5+ conversas-tipo (admin estressado, pergunta factual, pergunta existencial, pedido de trabalho, mensagem curta).

**Aceite**:
- [x] cenarios carregaveis por helper em `tests/scenarios/__init__.py`;
- [x] cada cenario tem `expected_properties` declaradas;
- [x] cenarios documentados em `tests/scenarios/README.md`.

### 0.3 - Runner de regressao cognitiva

**Objetivo**: executar os cenarios contra os julgamentos cognitivos e comparar execucoes.

**Criar**: `tests/regression_runner.py` (<= 500 linhas) com dois modos:
- `--mock`: sem LLM, valida mecanica deterministica (formula de maturidade, politicas, ancoras);
- `--live`: com LLM via OpenRouter, executa prompts de julgamento sobre os cenarios e salva saidas em `tests/regression_runs/<timestamp>_<model>.json`.

Comando `--diff run1 run2` produz comparacao legivel.

**Ajuste de escopo - 16/06/2026**: por decisao do mantenedor, o modo `--live`
nao sera executado nesta fase. Motivo: evitar chamada externa com cenarios do
repositorio e credenciais/API externas sem necessidade atual. O modo permanece
no runner como capacidade opcional futura, mas nao e criterio de saida da Fase 0.

**Aceite revisado**:
- [x] `--mock` roda no CI;
- [x] `--diff` compara execucoes salvas;
- [x] com o runner pronto, D2 resolvido em PR separado (`7.0` -> `MAX_DAYS_FOR_SYNTHESIS`) com diff mock de comportamento anexado.

### 0.4 - Teste de troca de modelo

**Estado**: removido do cronograma ativo em 16/06/2026 por decisao do mantenedor.

**Justificativa**: a tarefa depende do runner `--live` e de chamadas externas para
comparar julgamentos entre modelos. Como nao ha decisao atual de troca de modelo,
o custo, a exposicao de cenarios e a complexidade operacional nao compensam.

**Reavaliar somente se** houver decisao concreta de trocar o modelo principal ou
necessidade de auditoria comparativa entre provedores.

### 0.5 - Observabilidade de custo LLM

**Estado**: eliminado do cronograma ativo em 16/06/2026 por decisao do mantenedor.

**Justificativa**: embora util, a instrumentacao de custo nao e bloqueante para a
Fase III e adicionaria schema, painel e superficie operacional agora. O projeto
priorizara primeiro a reducao de complexidade estrutural e a verificacao de
comportamento ja existente.

**Reavaliar somente se** o custo operacional se tornar problema pratico, houver
troca de provedor/modelo, ou a Fase IV+ exigir orcamento por fase como mecanismo
de governanca.

### 0.6 - Cortes 2-7 da extracao do work_engine

**Plano detalhado**: `docs/PLANO_EXTRACAO_WORK_ENGINE.md` (seguir a risca; um corte por PR).

**Estado**: concluido. `work_engine.py` foi eliminado e o dominio vive em `work/`.

**Aceite por corte**: os listados no plano + suite verde.

**Aceite final**:
- [x] `work_engine.py` nao existe;
- [x] imports atualizados nos consumidores;
- [x] dashboard de work preservado via `admin_web/routes/work_routes.py`.

### 0.7 - Decomposicao de core/database.py

**Objetivo**: completar `core/db/` (users, dreams, knowledge_gaps, psychometrics, agent_development, working_memory, integrative_self, relational_state e demais dominios principais).

**Estado**: concluido como fachada fina. `HybridDatabaseManager` permanece como fachada compativel.

**Aceite final**:
- [x] `core/database.py` < 500 linhas (fachada fina) ou eliminado;
- [x] nenhum metodo publico quebrado (suite verde).

### 0.8 - Migracao de admin_web/routes.py

**Estado**: concluido. `admin_web/routes.py` foi eliminado e as rotas foram migradas para modulos em `admin_web/routes/`.

**Aceite final**:
- [x] `routes.py` eliminado;
- [x] lista de rotas comparada por inventario/testes.

### 0.9 - Verificacao da Fase II em producao

**Objetivo**: confirmar os criterios herdados com evidencia real (coleta, nao codificacao de features).

**Fazer**: script somente-leitura `tests/verify_phase2.py` que consulta export do volume/banco de producao (fornecido pelo mantenedor) e verifica:
- 7+ diarios consecutivos;
- perfil regenerado com fontes validas;
- referencia espontanea a evento de 3+ dias nos logs de conversa.

**Aceite**:
- [x] resultado registrado em `docs/research/fase2-verificacao-2026-06-29.md` com ancoras.

### Criterio de saida da Fase 0

Todos verdadeiros:

- [x] 3 branches pendentes mergeadas e CI ativo/verde na main;
- [x] runner de regressao operacional em modo `--mock` no CI, `--diff` funcional, e D2 resolvido;
- [x] 0.4 removida do cronograma ativo por decisao do mantenedor registrada em 16/06/2026;
- [x] 0.5 eliminada do cronograma ativo por decisao do mantenedor registrada em 16/06/2026;
- [x] `work_engine.py`, `core/database.py` e `admin_web/routes.py` decompostos ou reduzidos a fachada fina;
- [x] Fase II verificada com evidencia de producao;
- [x] primeira rodada de avaliacao cega removida do criterio de saida da Fase 0 por decisao do mantenedor em 29/06/2026.

## 8. Protocolo de avaliacao cega

**Estado**: removido do criterio de saida da Fase 0 por decisao do mantenedor em 29/06/2026. Uma frente de avaliacao cega foi executada/arquivada em julho de 2026 com achado metodologico: a escala 0-5 de desenvolvimento ainda nao esta operacionalizada o bastante para avaliadores externos distinguirem fases de modo confiavel.

**Reavaliar somente se** houver necessidade externa de auditoria comportamental antes da Fase IV ou se o mantenedor decidir retomar avaliacao cega mensal como pratica de pesquisa.

Protocolo preservado para uso futuro, se retomado:

1. Mensalmente, extrair 10-15 transcricoes do agente (conversas + acoes autonomas) de datas variadas, removendo qualquer mencao a fase narrativa atribuida.
2. Avaliador cego (o mantenedor semanas depois, um segundo humano, ou um modelo diferente do que gerou o conteudo) classifica cada amostra na escala 0-5 usando so as descricoes comportamentais de `agent_development.py`.
3. Registrar concordancia (simples ou kappa) em `docs/research/avaliacao-cega-<data>.md`.
4. Interpretacao: concordancia alta = desenvolvimento visivel de fora; baixa = narracao sem contraparte comportamental. Ambos os resultados sao publicaveis; o segundo redireciona o roadmap antes da Fase IV.

O executor pode receber a tarefa de criar o script de extracao/anonimizacao das amostras (somente-leitura).

## 9. Fases ativas e seguintes

Esta secao descreve o estado vivo apos a Fase 0. A ordem abaixo segue o Principio Aureo: nada deve avancar para influencia externa ou execucao autonoma sem circuito interno fechado, evidencia e regressao verde.

**Fase III - Direcao Propria + Working Memory** (~6 semanas): `engines/working_memory.py` (Foco Ativo 3-5 itens, Fringe, Filtro de Relevancia, Broadcasting, persistida em SQLite), integracao com as 8 fases do loop (fase N+1 le o foco de N), consolidacao do Knowledge Gap Engine (ciclo gap -> investigacao -> journal -> fechamento), `engines/goal_manager.py` (impulsos do will decompostos em sub-objetivos), acoes compostas do will, autoavaliacao pos-resposta (registro apenas). *Saida*: WM mantem foco por 7 dias verificaveis; 1+ gap fechado; 1+ acao composta; regressao verde.

Estado realizado da Fase III:

- [x] Working Memory persistente criada;
- [x] foco/fringe/candidatos e broadcasting entre fases implementados;
- [x] loop le inbox da fase anterior e emite broadcast para a proxima;
- [x] Knowledge Gap fechado em producao (`knowledge_gap#830`);
- [x] Goal Manager e acao composta controlada com fontes (`controlled_action_run#1`);
- [x] `relational_state` implementado e acoplado ao Will como contexto relacional;
- [x] probes de producao para loop, will, working_memory, goals, world e relational_state;
- [ ] WM sustentada por 7 dias verificaveis em producao;
- [ ] `relational_state` confirmado com snapshot real apos nova execucao de `identity` ou `will`;
- [ ] camada de proposicao de acoes (`engines/action_catalog.py` + `engines/action_proposer.py`) implementada sem efeito externo.

Proximo corte natural da Fase III:

1. Criar `engines/action_catalog.py` com tipos de acao permitidos, limites, risco e requisitos de evidencia.
2. Criar `engines/action_proposer.py` lendo `will_state`, `relational_state` e `working_memory`.
3. Persistir propostas como objetos revisaveis/observaveis, sem executar acoes reais.
4. Cobrir por testes e probe antes de qualquer integracao ao loop.

**Fase IV - Unificacao** (~6 semanas): unificar os subsistemas em um Integrative Self Model observavel, sem perder o Principio da Evidencia nem a disciplina de faseamento.

**Fase IV.0 - Pulso de Fase e Densificacao do Loop**: implementada e verificada em producao. Cada fase do loop pode executar 1-N vezes dentro de sua janela temporal, com agenda persistida, `pulse_index`, `pulse_count`, cockpit, retry por pulso e ancoras `loop#id`.

O objetivo nao e criar novas capacidades cognitivas, mas aumentar a resolucao temporal do metabolismo existente. O ISM deve passar a observar trajetorias internas dentro das fases, nao apenas um evento unico por fase por dia. Um sonho pode ter abertura, aprofundamento e fechamento; uma ruminacao pode ter contato, digestao e cristalizacao; uma fase de vontade pode distinguir acumulacao, conflito e fechamento.

Aceite da Fase IV.0:

- [x] `pulse_count=1` preserva exatamente o comportamento atual;
- [x] `consciousness_phase_config` guarda `pulse_count` por fase com schema compativel e sem recriacao de banco;
- [x] tabela persistente de agenda de pulsos registra `cycle_id`, `phase`, `pulse_index`, `pulse_count`, `scheduled_at`, `executed_at`, `status`, tentativas e `phase_result_id`;
- [x] cockpit permite configurar `pulse_count` por fase dentro de limites seguros;
- [x] scheduler executa apenas pulsos vencidos dentro da janela temporal da fase e pula pulsos stale ao trocar de fase;
- [x] retries pertencem ao pulso que falhou e nao consomem pulsos futuros;
- [x] execucoes manuais continuam disponiveis, mas nao contam como pulso automatico salvo acao explicita;
- [x] resultados de fase incluem metadados de pulso em `metrics_json` e `raw_result`;
- [x] ISM consegue ler pulsos recentes por fase para construir uma visao longitudinal curta do ciclo;
- [x] testes cobrem preservacao do default, execucao do proximo pulso vencido, retry por pulso e saneamento de stale pulses.

**Fase IV.1 - ISM read-only**: concluida. `engines/integrative_self.py` produz snapshot diario em primeira pessoa dos subsistemas, com limites ontologicos explicitos, `influence_mode=read_only`, sem influenciar prompt, decisoes do loop, Working Memory ou acoes externas.

**Fase IV.2 - ISM no contexto do agente**: infraestrutura implementada, mas comportamento padrao permanece desligado. `ISM_PROMPT_CONTEXT_ENABLED=false` por default; `ISM_PROMPT_CONTEXT_ADMIN_ONLY=true` por default; variante `ism_preview` no runner valida que o ISM e preview/read-only/injectable=false antes de qualquer canario. Ativacao futura exige regressao antes/depois, canario admin-only e verificacao por probe.

**Fase IV.3 - Metacognicao completa**: double-loop com cooldown de 24h e validacao pela regressao antes de ativar qualquer auto-ajuste; strategy learning com regras heuristicas; segunda rodada de avaliacao cega comparada a linha de base.

**Fase V - Grafo simbolico** (~6 semanas): escopo cetico - Etapa A restrita a fatos sobre o admin e sobre o agente, com `confidence` e `source` por tripla; **portao obrigatorio**: auditoria manual de 100 triplas com precisao >= 80% antes da Etapa B (conhecimento de mundo, navegador causal, verificador de consistencia, dialetica Ego/Sombra/Persona conforme proposta Canto/Contracanto em `docs/`).

**Fase VI - Simulacao contrafactual** (~4 semanas): simular desfechos antes de respostas de alta carga afetiva e de acoes autonomas; previsto vs. real no dashboard e no relatorio mensal.

**Fase VII - Tool-making + multimodal** (~5 semanas): gate rigido - so com Fases 0-VI estaveis por 2+ semanas e aprovacao escrita do mantenedor. Caminho preferencial de tool-making e o circuito de self-work via PR revisado por humano (ja existente); sandbox Docker efemero (timeout 30s, RAM 128MB, whitelist de rede, validacao AST) reservado a scripts efemeros de consulta, nunca a modificacao do proprio sistema. Prosodia e feedback visual de imagens oniricas entram aqui.

## 10. Riscos

| Risco | Antidoto |
|---|---|
| Complexidade acumulada (monolitos, muitos arquivos na raiz) | Fase 0 reduziu a divida principal; `main.py` ainda precisa de higiene posterior; 500 linhas/arquivo novo; refatoracao ao fim de cada fase |
| Alucinacao estrutural (agente inventa passado) | Ancoras `tipo#id` implementadas; auditoria semanal do perfil pelo mantenedor |
| Narracao sem profundidade funcional | Nenhuma metrica de autorrelato vale sozinha; avaliacao cega pode ser retomada como pesquisa quando houver escala mais operacional |
| Dependencia do LLM subjacente | Runner de regressao `--mock` no CI; teste de troca de modelo reavaliado somente se houver troca real de modelo |
| Executor introduzir regressoes | CI bloqueante; contrato da Secao 6; escopo estrito por tarefa |
| Loop de auto-observacao (Fase IV+) | Cooldown 24h; ajustes <= 5% por ciclo; congelamento pelo mantenedor |
| Custo invisivel | Risco aceito no curto prazo por decisao do mantenedor; reavaliar se custo operacional virar problema pratico |
| Seguranca de execucao (Fase VII) | Gate rigido; self-work via PR humano como caminho preferencial |
| Descolamento do usuario | Principio do Encontro; metrica de ressonancia; blog compreensivel |

## 11. Genealogia

| Documento | Data | Contribuicao |
|---|---|---|
| 5 documentos-fonte (A-E) | ate Mai/2026 | fases, dialetica, working memory, metricas |
| Versao 1 ("Roadmap AGI") | Mai/2026 | 7 fases bloqueantes, Principio Aureo, criterios binarios, riscos |
| Avaliacao externa (Claude, consultor) | 10/06/2026 | Reposicionamento como emulacao cognitiva; Fase 0; avaliacao cega; WM antecipada; portao do SKG |
| Versao 2.1 - Edicao de Execucao Delegada | 10/06/2026 | Governanca em tres papeis; contrato do executor; backlog como especificacoes; estado e avisos operacionais atualizados |
| Consolidacao canonica V2 | 11/06/2026 | Este arquivo substitui o redirecionamento e passa a ser o documento mestre de autoridade |
| Versao 2.3 - Estado Realizado e Roadmap Vivo | 08/07/2026 | Atualiza Fase 0 como concluida, Fase III como em fechamento/protagonismo, IV.0/IV.1 como realizadas, IV.2 como infraestrutura gateada, e registra o fechamento `relational_state -> will` |

---

*Mantido pelo consultor estrategico, sob aprovacao do mantenedor. Atualizar a Secao 4 (estado) a cada merge relevante e o backlog ao final de cada tarefa concluida.*
