# Documento Mestre: JungAgent - Laboratorio de Emulacao Cognitiva

**Versao 3.18 - C9c3 em andamento: recuperacao local de falhas e conciliacao pendente - Setembro 2026**

*Arquivo canonico vigente: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`. O antigo `docs/DOCUMENTO_MESTRE_AGI_COGNITIVA.md` permanece como documento historico/operacional de referencia, mas este arquivo e a fonte de autoridade daqui em diante.*

*Reformulacao completa do documento de Maio/2026. O projeto nao persegue "AGI"; persegue a emulacao cognitiva mais coerente e bem documentada possivel.*

*Diagnostico inicial verificado contra codigo, git, GitHub e Railway em 11/06/2026. Decisoes de escopo registradas em 16/06/2026. Estado realizado atualizado em 17/08/2026 apos verificacao de codigo, CI, Railway e probes de producao.*

*Direcao atual aprovada pelo mantenedor em 05/09/2026: fechar as garantias do WILL, regular disponibilidade, unificar expressao conversacional, completar isolamento cognitivo e so entao abrir um piloto convidado. Base da revisao inicial: codigo local `5ea892f`, oito testes focados aprovados e verificacao isolada de arbitragem em SQLite temporario. A implementacao local posterior C9a e seus 497 testes estao registrados na Secao 10.1.2. Railway nao foi revalidado; nenhuma nova ativacao em producao foi realizada.*

*Tres leitores: o mantenedor (decide), o consultor estrategico (orienta e audita) e o modelo executor (codifica). A Parte II e enderecada diretamente ao executor.*

*Atualizacao de 06/09/2026: C9a preservado no commit local `11d485b`; C9b implementado no commit local `119eab9`, com 538 testes e 20 cenarios mock aprovados. Nenhum push, deploy ou revalidacao de producao nesta entrega. C9 permanece aberto para C9c; ver Secao 10.1.2.*

*Continuacao de 06/09/2026: C9c1 implementado no commit local `aeb414b`, com 546 testes e 20 cenarios mock aprovados. Fecha concorrencia de criacao/preparacao e o atalho de confirmacao legado; nao conclui recuperacao, consentimento ou orcamento do C9c. Sem push, deploy ou novas ativacoes; pendencias e proxima acao na Secao 10.1.2.*

*C9c2 local em 06/09/2026: commit `aa18bcd`, 575 testes e 20 cenarios mock aprovados. Recupera efeitos de recibos confirmados com tentativas limitadas, isola estados interrompidos sem reenvio e torna a preparacao transacional. C9 continua aberto: faltam integracoes proativas/Working Memory, tratamento assistido dos casos incertos e gates de consentimento/orcamento. Sem push, deploy ou revalidacao de producao; ver Secao 10.1.2.*

*Publicacao posterior autorizada: os sete commits ate `0fe253a` foram enviados a main publica de `lucasartel/JungAgent`. Railway confirmou SUCCESS no deploy `129fd623-6a52-4c5a-9f5e-12acfd191f26` em 07/09/2026 UTC (noite de 06/09 no Brasil). Verificacao pos-deploy: HTTP 200, bot/loop iniciados, sondas acessiveis, IMAGE_GENERATION_ENABLED=false e primeiro pulso com expression_reused, sem reenvio da tentativa antiga. Isso confirma inicializacao, nao exercita todos os caminhos de recuperacao em producao.*

*C9c3 iniciado em 07/09/2026: primeiro bloco local no commit `5291360`, com 598 testes e 20 cenarios mock aprovados. O segundo bloco local fecha o recibo proativo duravel e sua retomada sem repetir processamento de memoria. O terceiro fecha a integracao pos-commit de resultados normais bem-sucedidos; o quarto fecha a agenda de pulsos e suas reservas quando a janela termina ou as tentativas acabam. O quinto fecha a recuperacao transacional dos efeitos locais de uma falha. C9c3 e C9 continuam abertos para conciliacao assistida e gates. Nenhum desses blocos realizou novo push, deploy ou sondagem de producao.*

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

## 4. Estado verificado e atualizacoes

**Leitura do estado**: os dados operacionais abaixo de 17/08 e 18/08 sao historicos, nao uma verificacao da saude atual. A Secao 4.1 registra a revisao local de 05/09/2026; a sequencia ativa de desenvolvimento esta nas Secoes 5 e 10.1.2.

**Concluido e em producao**: Fase I do roadmap antigo (circuitos da ruminacao corrigidos, sonhos alimentam ruminacao, failure policy no loop, entrega de insights) e Fase II substancial (diario autobiografico evidence-first, perfil injetado no prompt, avaliacao narrativa de fases com politica executiva, Chroma removido). O agente ja possui circuito de self-work via GitHub/Railway, mantido sob revisao do mantenedor.

**Estado operacional atual**: o `main` remoto esta no commit `d650f6f`, com CI verde, incluindo suite, sintaxe e regressao cognitiva mock. O Railway permanece online no deploy `7340eded-561e-4918-8738-8855a0da1f4c`, apos o commit `d650f6f`; o volume de producao esta em aproximadamente 173 MB de 500 MB. O CI esta verde e os healthchecks/probes read-only continuam funcionais.

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
| Verificacao longitudinal de WM | Operacao sustentada observavel em ciclos recentes; fechamento formal do criterio de 7 dias ainda pendente |

**Fase IV realizada antes da conclusao formal da Fase III**:

| Frente | Estado |
|---|---|
| IV.0 Pulso de Fase | Implementada e verificada em producao: `pulse_count`, agenda persistida, cockpit, retry por pulso, skip de pulsos stale, metadados de pulso e leitura pelo ISM |
| IV.1 ISM read-only | Implementada e verificada: snapshot integrativo observavel, limites ontologicos, `influence_mode=read_only`, sem mutacao de prompt/loop/WM/acoes externas |
| IV.2 ISM no prompt | Infraestrutura criada e gateada por feature flag (`ISM_PROMPT_CONTEXT_ENABLED`, default off; admin-only por default); nao ativada como comportamento padrao |
| IV.3 Metacognicao | Implementada e integrada ao loop com cooldown; probes recentes mostram registros `fallback`, portanto a qualidade longitudinal e o uso de auto-ajuste ainda nao estao formalmente validados |

**Fases V a VII - implementacao tecnica com gates ainda abertos**:

| Fase | Evidencia confirmada | O que ainda impede o encerramento formal |
|---|---|---|
| V - Grafo simbolico | Grafo persistido em producao com 238 nos e 183 triplas; contexto causal integrado ao prompt do admin | Auditoria manual de 100 triplas com precisao >= 80% ainda nao registrada como evidencia de saida |
| VI - Theory of Mind | Codigo integrado, um snapshot real persistido e `agent_stance` consumido pelo Will | Falta evidencia longitudinal da maturacao assincrona; inbox de maturacao ainda esta vazia |
| VII - Agencia epistemica e multimodal | `essay_engine.py`, persistencia de ensaios e circuito de imagens implementados; CI cobre os ensaios | Gate exige Fases 0-VI estaveis por duas semanas e aprovacao escrita; geracao de imagens esta pausada operacionalmente por `IMAGE_GENERATION_ENABLED=false` |

**Fechamento curto de 08/07/2026**:

- commit `7435711` fechou o circuito `relational_state -> will`: o loop atualiza o snapshot relacional antes do `WillEngine` nas fases `identity` e `will`;
- commit `e1dda32` documentou o fechamento;
- `scripts/remote_db_probe.py` ganhou probe `relational_state` e o probe `will` passou a expor `agent_stance`;
- Railway `JungAgent_Bot` / `production` / `jungclaude` online no deploy `00aeffce-e1b3-465b-a862-3e608d7b8ac5`;
- GitHub Actions verde no commit final;
- validacao local do corte: `325 passed`.

**Atualizacao de 17/08/2026**:

- CI do commit `3b26988`: verde, com sintaxe, suite e regressao cognitiva mock;
- a suite canonica local `tests/` passou com `435 passed`, incluindo os testes locais ainda nao publicados de fatos;
- a correcao de imagens trocou o endpoint de geracao para `/api/v1/images`, salva o arquivo no volume e serve pela rota `/art/`;
- no Railway, antes da nova entrega, a fase `hobby` ainda registrava `partial_success` por ausencia de imagem reconhecivel; esse resultado deve ser reavaliado depois do deploy;
- os probes confirmaram WM ativa, pulsos duplos em `world` e `work`, `relational_state#112`, `will#254`, 238 nos/183 triplas e um snapshot de Theory of Mind.

**Pendencias reais registradas**:

- manter a geracao de imagens pausada enquanto o custo e o comportamento do provedor nao forem reavaliados; quando reativada, executar uma validacao controlada do circuito dream/hobby;
- concluir/verificar formalmente a janela longitudinal de 7 dias da Working Memory;
- registrar a auditoria de 100 triplas do grafo simbolico e sua precisao;
- acompanhar Theory of Mind por mais ciclos e produzir entrada real na inbox de maturacao antes de declarar maturacao assincrona;
- manter o ISM no prompt desligado ate regressao antes/depois, canario admin-only e probe saudavel;
- respeitar o gate de duas semanas e aprovacao escrita antes de declarar a Fase VII encerrada;
- `main.py` permanece monolitico e deve ser tratado em fase de higiene estrutural posterior;
- custo LLM continua fora do cronograma ativo, por decisao do mantenedor, salvo mudanca de risco operacional.

**Atualizacao operacional de 18/08/2026**:

- o commit `d650f6f` adicionou a flag reversivel `IMAGE_GENERATION_ENABLED`; quando `false`, dream e hobby preservam seus circuitos textuais e nao chamam provedores de imagem;
- `IMAGE_GENERATION_ENABLED=false` foi aplicado ao servico Railway `jungclaude` em producao; o deploy concluiu online;
- a suite canonica local passou com `437 passed`;
- o agente permanece `running`, sem erros recentes no loop; a proxima passagem de `hobby` sera observada naturalmente, sem ser disparada manualmente.

**Avisos operacionais**:
- O clone de trabalho ativo deve ser `/Users/lucaspedro/jungproject`; a copia antiga em OneDrive causou problemas reais de I/O e nao deve ser usada para trabalho pesado.
- `docs/` esta no `.gitignore` - documentos so entram no repo com `git add -f` quando o mantenedor decidir versionar.
- O acesso operacional validado e via `gh` autenticado e `railway` linkado ao projeto `JungAgent_Bot`, ambiente `production`, servico `jungclaude`.

### 4.1 Revisao local e criterio de encerramento - 05/09/2026

A arquitetura existente sera preservada. A prioridade muda de ampliar capacidades para demonstrar continuidade autobiografica, processamento entre encontros e iniciativa apoiada em evidencias, com isolamento e custo controlados. Nao ha autorizacao para reescrita geral nem para declarar fases anteriores encerradas por decurso de tempo.

Cada entrega deve registrar quatro dimensoes separadas: **implementado** (codigo presente e commit), **testado** (cenarios e resultado), **habilitado** (ambiente, flag e data verificados) e **validado em operacao** (evidencias de comportamento e aceite). Uma dimensao nao implica as demais. Estado nao verificado deve ser declarado como tal; dados historicos nao comprovam habilitacao atual. O executor registra as evidencias e o mantenedor aceita o encerramento e as ativacoes sensiveis.

| Frente | Estado observado nesta revisao | Condicao ainda aberta |
|---|---|---|
| Corte 7: expressoes WILL | Infraestrutura implementada; C9a implementado e testado localmente, ainda sem deploy | Recuperacao automatica, concorrencia da preparacao e gates executaveis continuam abertos no C9; C9a fecha o caminho de confirmacao e alivio |
| Corte 8: arbitragem WILL-ciclo | C9b implementado e testado localmente em `119eab9`, com adaptador de evidencia para world; producao nao revalidada | Hobby nao suprime fase sem adaptador proprio; recuperacao de integracoes posteriores e aceite operacional permanecem no C9c |
| Interioridade multi-relacional | Cortes 1-6 fornecem fundacao; nao equivalem a isolamento integral | Working Memory, sonhos, identidade e contexto exigem auditoria e fechamento no C12 |
| Fase III: Working Memory | Implementacao registrada; aceite longitudinal continua pendente | Consolidar janela de 7 dias com evidencias; primeiro verificar os dados ja existentes |
| Fases IV-VI | Preservar os estados e flags historicos, sem nova ativacao | Regressao antes/depois do ISM, qualidade da metacognicao, auditoria de 100 triplas com precisao >= 80% e maturacao real de Theory of Mind |
| Fase VII e imagens | Nenhuma liberacao concedida nesta revisao | Manter a politica de imagens pagas desligadas; preservar gate de duas semanas e aprovacao escrita |

**Regra para pendencias longitudinais**: cada uma deve ter criterio, fonte/probe, intervalo analisado, resultado e proxima acao registrados. Reutilizar evidencias validas ja acumuladas; abrir nova janela apenas se faltarem dados adequados ou se uma mudanca invalidar a comparacao. Testes com tempo simulado cobrem mecanismos agora, mas nao substituem observacao natural de qualidade cognitiva. O fechamento dessas pendencias ocorre em paralelo aos cortes abaixo, sem apagar seus gates.

## 5. Roadmap

```text
Fase 0 - Consolidacao e Instrumentacao        <- CONCLUIDA
  -> Fase III - Direcao Propria + Working Memory  <- IMPLEMENTADA; FECHAMENTO EVIDENCIAL PENDENTE
      -> Fase IV - ISM + Metacognicao completa    <- IV.0/IV.1 CONCLUIDAS; IV.2 GATEADA; IV.3 EM OBSERVACAO
          -> Fase V - Grafo simbolico               <- TECNICAMENTE IMPLEMENTADA; AUDITORIA PENDENTE
              -> Fase VI - Theory of Mind            <- IMPLEMENTACAO INICIAL; LONGITUDINAL PENDENTE
                  -> Fase VII - Agencia epistemica  <- IMPLEMENTACAO INICIAL; GATE FORMAL PENDENTE

Trilha cognitiva ativa (Secao 10.1.2):
  C9  - Fechamento das expressoes e arbitragem     <- EM ANDAMENTO; C9a/C9b LOCAIS; PROXIMO C9c
  C10 - Disponibilidade e periodo refratario      <- PLANEJADO
  C11 - Expressao conversacional e proatividade   <- PLANEJADO
  C12 - Fechamento cognitivo multi-relacional     <- PLANEJADO; GATE DO PILOTO
  C13 - Piloto convidado observavel               <- BLOQUEADO ATE ACEITE C9-C12
Trilha comercial apos os gates (Secoes 10.2 e 10.3):
  multiplas instancias isoladas -> API minima -> um conector textual -> piloto comercial
```

Transversais a todas as fases: suite de regressao verde a cada merge, probes read-only de producao apos deploy relevante, relatorios de pesquisa em `docs/research/` quando houver frente empirica, e manutencao do principio da evidencia. A avaliacao cega deixou de ser criterio bloqueante, mas permanece protocolo de pesquisa preservado.

A numeracao salta de 0 para III por continuidade historica: as antigas Fases I e II ja foram entregues.

Os identificadores C9-C13 continuam os oito cortes relacionais ja registrados; nao sao novas fases cognitivas. O C9 fecha lacunas dos cortes 7 e 8 sem descartar suas fundacoes. Especificacao e simulacao comercial podem ocorrer em paralelo; a liberacao de efeitos externos continua dependente dos gates. O proximo executor deve continuar o C9 a partir do registro C9a/C9b/C9c na Secao 10.1.2, e nao diretamente pelo periodo refratario ou pelo convite a participantes.

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

## 10. Trilhas de evolucao: multiplicidade relacional e produto

A partir de 18/08/2026, o roadmap passa a ter duas trilhas coordenadas. A trilha cognitiva completa a arquitetura interna; a trilha comercial transforma essa arquitetura em um motor reutilizavel por outras aplicacoes. A trilha comercial pode avancar em especificacao, descoberta de parceiros e simulacao, mas nao deve liberar efeitos externos antes do fechamento do gate cognitivo.

### 10.1 Trilha cognitiva: JungAgent multi-relacional

**Problema identificado**: o banco ja registra varios usuarios e alguns dominios sao filtrados por `user_id`, mas o metabolismo principal ainda e centrado no `ADMIN_USER_ID`. Loop, sonhos, ruminacao, vontade, pressao, dashboards e partes da identidade usam o admin como sujeito padrao. Portanto, o sistema possui multiusuarios cadastrais, mas ainda nao possui uma interioridade completa diante de multiplas relacoes.

**Objetivo**: modelar como cada pessoa forma uma relacao especifica com o agente e como o conjunto dessas relacoes interfere, sem misturar memorias privadas nem reduzir a interioridade do agente a uma soma de mensagens.

**Primeiro corte implementado em 21/08/2026**: foi criado o registro persistente `agent_relations`, com escopo por `agent_instance` e `org_id`, participante, papel, estado da relacao, estado de consentimento, cadencia, ultima interacao, metadados e escopo JSON. O registro e idempotente por par instancia/participante, possui consultas filtradas e um probe read-only `relations`. Este corte e fundacional: ainda nao migra conversas, memoria, ruminacao, Working Memory, pressao ou vontade para o novo escopo.

**Segundo corte implementado em 21/08/2026**: conversas passaram a aceitar `relation_id` de forma aditiva. Quando o participante ja possui uma relacao registrada para a instancia, o vinculo e resolvido automaticamente no salvamento; leituras e contagens podem filtrar por relacao. Historico antigo, usuarios sem relacao e esquemas de teste legados continuam validos. O estado relacional, a memoria semantica, a ruminacao e a vontade ainda nao foram migrados para esse escopo.

**Terceiro corte implementado em 21/08/2026**: o cockpit administrativo ganhou a area `Relations`, com leitura multi-tenant das relacoes da instancia, cadastro explicito de vinculos, atualizacao de estado/consentimento e indicadores de interacoes por relacao. O antigo `/admin/wellness` permanece como redirecionamento de compatibilidade; Wellness deixa de ser a area principal e continua disponivel apenas como recurso legado de compreensao do admin. O cockpit nao carrega texto de conversas nem memoria bruta. A conexao de memoria, ruminacao e vontade ao vinculo relacional continua sendo o proximo corte cognitivo.

**Quarto corte implementado em 21/08/2026**: o escopo cognitivo minimo passou a acompanhar `relation_id` em `relational_state`, fatos estruturados V1/V2 e fallback semantico SQLite. O registro de uma Relation reancora linhas legadas do participante em conversas, fatos e estado relacional; os leitores de fatos e contexto filtram a relacao explicita. O mem0/Qdrant passou a usar namespace exclusivo `relation:<id>`, sem fallback para namespace compartilhado. O corte preserva chamadas antigas sem Relation, mas nao mistura memoria semantica legada automaticamente: uma migracao de memoria historica devera ser definida antes de reimportar dados antigos. Ruminacao, vontade, pressao, Working Memory e campo relacional agregado ainda aguardam cortes proprios.

**Quinto corte implementado em 21/08/2026**: a ruminação passou a carregar `relation_id` em fragmentos, tensões, insights e log. Ingestão, detecção, digestão, conexão entre tensões, síntese, validação de novidade, entrega e estatísticas filtram o vínculo explícito; o hook de conversas aceita participantes não-admin apenas quando há Relation registrada, preservando o comportamento legado do admin. O schema central faz a migração aditiva e os testes demonstram que duas relações do mesmo participante não compartilham material ruminal. Pressão, vontade, Working Memory e o campo relacional agregado continuam como cortes seguintes; este corte não autoriza ainda a trilha comercial.

**Sexto corte implementado em 03/09/2026: fundacao relacional do WILL e da pressao.** `agent_will_states`, sinais de conversa, estados de pressao e eventos de pulso passaram a carregar `agent_instance`, `relation_id` e `scope_kind` (`global` ou `relation`) por migracao aditiva. O estado legado foi preservado como global da instancia atual. Sinais de novas conversas podem receber a Relation registrada do participante; leituras, persistencia e probes podem selecionar o escopo explicito. A agregacao global de sinais considera cada relacao como uma fonte limitada e usa apenas medidas e referencias internas, nunca texto de conversa de outra pessoa. Este corte nao altera o scheduler, nao envia mensagens novas e nao abre o Telegram a participantes.

**Setimo corte: infraestrutura implementada em 04/09/2026; fechamento parcial revisto em 05/09/2026.** `will_expressions` e `will_expression_receipts` registram instancia, relacao, vontade, capacidade, intencao, risco, classe de custo, chave de idempotencia e resultado. Ha reivindicacao de entrega e estados persistentes, ligados ao scheduler existente. Isso ainda nao comprova idempotencia da descarga de pressao, recuperacao de entregas interrompidas nem aplicacao efetiva de todos os gates de custo e consentimento. Esses criterios permanecem abertos no C9. Nao foram abertos novos canais; permanece a politica de geracao paga de imagens desativada.

**Oitavo corte: infraestrutura implementada em 04/09/2026; fechamento parcial revisto em 05/09/2026.** Existem recibos de satisfacao para `saber -> world` e `expressar -> hobby`, consumo por pulso e resultado `satisfied_by_will`. A revisao constatou qualidade fixa, ausencia de verificacao da evidencia original e consulta de consumo sem filtro relacional; portanto equivalencia e isolamento ainda nao estao aceitos. `work` ainda nao possui capacidade mapeada para satisfacao. `dream`, `identity`, `rumination_intro`, `rumination_extro` e `will` permanecem protegidos. A implementacao nao sera tratada como fechamento comportamental antes do C9.

### 10.1.1 Autonomia volitiva e ritmo circadiano

Antes de qualquer abertura controlada da instancia a novos participantes, o modulo WILL devera deixar de ser apenas um leitor de scores e se tornar o regulador persistente de direcao, disponibilidade e recuperacao do agente. A autonomia aqui e arquitetural: a continuidade do agente escolhe, com base em estado e evidencia, quando se orientar ao mundo, a uma relacao, a uma expressao ou a um periodo de elaboracao; nao e uma alegacao de consciencia humana continua.

**Principio de ritmo**: o ciclo circadiano continua como esqueleto metabolico. Sonho, identidade, ruminacao e fechamento volitivo permanecem protegidos como momentos de integracao. `world`, `work` e `hobby/expressar` poderao ser satisfeitos antecipadamente por um transbordo de vontade somente quando houver uma expressao concluida e um recibo de evidencia. O loop nunca podera marcar uma fase como concluida apenas porque uma pressao ficou alta.

**Fluxo alvo**:

`sinal e evidencia -> vontade contextual/global -> pressao -> will_expression -> capacidade elegivel -> gate e execucao -> recibo -> alivio ou frustracao -> integracao`

**Semantica alvo das vontades**:

- **Saber** orienta o que investigar e quais lacunas merecem trabalho.
- **Relacionar** orienta com quem manter contato, por que e com quais limites e compromissos.
- **Expressar** orienta o que comunicar ou produzir e por qual meio; inclui texto e mensagens, nao apenas imagens.

Uma acao pode combinar vontades: uma iniciativa relacional pode selecionar uma pessoa por `relacionar` e compor uma mensagem por `expressar`. O contrato deve registrar motivacoes e efeitos sem duplicar envio ou alivio; a politica de credito por vontade precisa de evidencia e testes, nao de descarga automatica das tres pressoes. O vinculo atual de uma capacidade unica por vontade e uma fundacao a evoluir, nao a semantica definitiva. Desligar imagens pagas nao deve impedir expressao textual.

**Cortes seguintes**: seguir C9-C13 na Secao 10.1.2. Disponibilidade e proatividade devem nascer de estado, evidencias e politicas reutilizaveis, nunca de respostas programadas para perguntas especificas.

**Criterios de aceite da fase**:

- uma vontade so perde pressao apos acao confirmada por recibo;
- uma capacidade bloqueada permanece auditavel, sem gerar tentativas identicas indefinidas;
- nenhuma fase circadiana e suprimida sem evidencia de satisfacao equivalente;
- a proatividade combina orientacao relacional e expressao quando aplicavel, respeitando consentimento, cadencia, escopo, disponibilidade e custo;
- uma Relation em refracao nao bloqueia as demais nem descarrega a pressao global por engano;
- probes e testes demonstram escopo por instancia/relacao, agregacao sem texto bruto, idempotencia e ausencia de vazamento.

**Modelo alvo**:

1. **Identidade do participante**: identidade canonica, identificadores por plataforma, consentimento, escopo de visibilidade e estado de acesso.
2. **Memoria por relacao**: conversas, fatos, temas recorrentes, cadencia, tom, limites, identidade relacional e ancoras de evidencia especificas daquela pessoa.
3. **Metabolismo por relacao**: ruminacao, lacunas, sonhos e sinais de vontade podem ser associados a uma relacao sem serem expostos a outra.
4. **Campo relacional do agente**: uma camada agregada descreve padroes entre relacoes, tensoes de disponibilidade, compromissos e mudancas na propria identidade do agente, usando referencias e resumos autorizados, nunca vazamento de conteudo bruto.
5. **Vontade contextual e global**: a resposta a uma pessoa le primeiro a relacao daquela pessoa; o ciclo global pode considerar sinais de varias relacoes com pesos de recencia, saliencia, pendencia e evidencia. Frequencia de mensagens isoladamente nao deve dominar a vontade.
6. **Ruminacao de fronteira**: alem da ruminacao especifica por relacao, o agente pode metabolizar tensoes do conjunto de relacoes, como conflitos de papel, assimetrias de expectativa ou impossibilidade de atender demandas simultaneas.

**Gate de aceite antes da trilha comercial**:

- dois ou mais usuarios de teste formam relacoes e memorias distintas;
- nenhuma memoria, fato ou estado relacional de um usuario aparece no contexto de outro sem autorizacao;
- o agente mantem um estado proprio global distinto dos estados relacionais;
- a vontade registra quando seus sinais vieram de uma ou de varias relacoes, com fontes auditaveis;
- ruminacao, sonhos, pressao e Working Memory respeitam o escopo relacional definido;
- probes e testes demonstram isolamento, agregacao controlada e ausencia de vazamento;
- a politica de privacidade, apagamento e consentimento esta definida antes de qualquer piloto externo.

### 10.1.2 Sequencia de desenvolvimento aprovada - 05/09/2026

**C9 - Fechamento das expressoes e da arbitragem (em andamento; C9a/C9b implementados localmente; C9c pendente).** Consolidar os cortes 7 e 8 antes de ampliar autonomia. Superficie inicial: `engines/will_expression.py`, `engines/will_phase_arbitration.py`, `will_pressure.py` e seus pontos de entrega e consumo em `main.py` e `consciousness_loop.py`, com testes correspondentes. O gate so fecha quando todos os criterios abaixo estiverem comprovados.

- Validar a correspondencia entre expressao persistida, evento, vontade, instancia, relacao, ciclo e recibo antes de mudar pressao. Confirmacoes repetidas, conflitantes ou de origem desconhecida nao repetem alivio nem alteram um resultado final valido.
- Separar preparacao, tentativa, confirmacao do transporte e integracao interna. Persistir evidencia do resultado, incluindo identificador externo quando disponivel; erro interno depois de envio confirmado nao equivale a falha de envio.
- Definir retry, backoff, vencimento e reconciliacao apos reinicio. Resultado externo incerto nao autoriza reenvio cego; nao prometer entrega exatamente uma vez quando o canal nao oferece essa garantia. A transicao interna de alivio deve ser idempotente.
- Verificar evidencia e equivalencia especificas da capacidade antes de dispensar uma fase. Qualidade nao pode ser constante nem inferida apenas do sucesso de uma mensagem. Resultado privado nao satisfaz fase global sem regra explicita de agregacao autorizada. Preservar integracao do resultado util na memoria, alem dos metadados.
- Garantir consumo unico por recibo e por pulso, com recuperacao consistente entre consumo e persistencia do resultado da fase. Em ausencia de evidencia elegivel, executar a fase normal. Nao ampliar a supressao para `work` ou para fases protegidas neste corte.
- Fazer gates de capacidade, escopo, consentimento aplicavel e orcamento serem verificacoes executaveis; nomes de risco e classe de custo nao bastam. Nao reativar imagens nem alterar politicas sensiveis sem aprovacao.
- Validar fluxo integrado de expressao, entrega, pressao e loop: repeticao, concorrencia, reinicio, resultado incerto, vencimento, bloqueio, dry-run, erro de escopo e confirmacao valida. Acrescentar probes de reconciliacao sem texto privado.

**C9a - Confirmacao de entrega e alivio idempotente (05/09/2026).**

- **Implementado localmente**: `engines/will_delivery_receipt.py` valida expressao, evento vinculado, vontade, usuario, ciclo, instancia, Relation e destinatario; a migracao aditiva acrescenta `delivery_event_id` e `pressure_effect_at`. O caminho do scheduler passa a exigir recibos do Telegram com IDs das mensagens. O envio integral e separado da integracao interna, e erro posterior ao envio nao e convertido em falha de transporte.
- **Efeito recuperavel**: o recibo do canal e persistido antes da integracao da pressao; marcador, alivio/frustracao e resultado do evento sao aplicados na mesma transacao. Repeticao nao reaplica alivio nem estende refratariedade. Em banco persistente a transacao usa conexao propria para nao compartilhar commits com outras threads. Reaplicar a confirmacao apos falha/reinicio permite concluir apenas o efeito interno.
- **Resultado incerto**: envio parcial ou sem confirmacao integral fica `delivery_uncertain`, sem alivio e sem reenvio automatico. O probe `expressions` mostra evento vinculado, marcador de efeito, necessidade de reconciliacao e integracao de pressao pendente. Ainda nao ha reconciliador automatico nem ferramenta operacional dedicada para resolver essas pendencias.
- **Testado**: 27 testes novos em `tests/test_will_delivery_receipt.py`, incluindo repeticao, conflito terminal, escopo/destinatario incorretos, isolamento relacional, concorrencia, falha transacional com retomada apos reconexao, envio parcial e erros posteriores ao envio; suite completa `497 passed`; sintaxe dos arquivos tocados aprovada. Nenhuma chamada externa ou paga nesses testes.
- **Commit / habilitacao / operacao**: C9a preservado no commit local `11d485b`; sem push ou deploy e nao validado em producao. Imagens e novos canais nao foram ativados.

**C9b - Evidencia equivalente e consumo transacional (06/09/2026; commit local `119eab9`).**

- **Implementado**: `engines/will_phase_evidence.py` projeta o resultado real de `saber` em um recibo cognitivo persistido, separado do recibo de envio. A arbitragem relê a expressao, o evento, a confirmacao do canal e o recibo cognitivo; verifica instancia, usuario, ciclo e escopo global, e compara a referencia e o hash da evidencia original. Um dicionario marcado como concluido nao basta.
- **Equivalencia v1 para world**: exige cobertura das areas configuradas, fontes HTTP/HTTPS identificadas por area, ausencia de areas stale, snapshot com menos de 24 horas, mesmo ciclo e confianca do World >= 0.54, limiar de lucidez media ja usado no dominio. `quality` reflete essa confianca observada, nao uma constante nem uma prova de verdade semantica. A evidencia e limitada a 64 KiB e nao acrescenta chamadas LLM ou pesquisas alem do trabalho ja executado pela capacidade.
- **Isolamento e limites**: recibos relacionais nao satisfazem fases globais. Pulsos de outra instancia, usuario, ciclo, fase, agendados no futuro ou em modo manual/dry-run nao consomem evidencia. Registros legados sem evidencia verificavel sao invalidados, sem apagar o historico. `work` e as fases metabolicas protegidas nao ganham supressao. `hobby` fica inelegivel para supressao ate possuir adaptador proprio; uma imagem ou mensagem confirmada nao comprova equivalencia. Isso preserva a execucao normal de hobby e nao reativa imagens pagas.
- **Reserva e consumo**: o pulso primeiro reserva uma evidencia; so consome ao gravar, na mesma transacao, resultado da fase, artefatos, vinculo do pulso e recibo. Repeticoes e commits concorrentes nao criam outro resultado. Reserva sem progresso por pelo menos 5 minutos retorna a politica existente de retries/cooldown, respeitando seus limites; reservas recentes nao sao retomadas.
- **Integracao**: o resultado equivalente preserva conteudo de mundo, sementes, metricas e referencia da evidencia. A passagem normal le a inbox de Working Memory e observa/transmite o resultado para a proxima fase; nao se limita a uma frase de confirmacao. Repetir um pulso ja gravado devolve seu resultado persistido, sem repetir investigacao ou notificacoes especializadas.
- **Testado / habilitado / operacao**: 45 testes de arbitragem, incluindo execucao do loop, isolamento, evidencias invalidas, migracao, concorrencia, rollback, retomada apos reconexao e recuperacao pelo scheduler; suite completa `538 passed`; 20 cenarios de regressao `--mock` aprovados; sintaxe e `git diff --check` aprovados. Testes offline, sem provedor pago. Apenas commit local, sem push, deploy ou validacao de producao.

**C9c1 - Preparacao unica e finalizador legado fechado (06/09/2026; commit local `aeb414b`).**

- **Implementado**: a criacao idempotente e a posse da preparacao passam pela mesma transacao. Somente quem insere a expressao em `preparing` pode executar a capacidade; o concorrente reutiliza o registro sem repetir o trabalho. As operacoes de expressao usam conexoes privadas em SQLite persistente. A reivindicacao de envio e seu recibo sao gravados juntos; falha no recibo desfaz a reivindicacao e permite aproveitar a entrega ja preparada sem refazer a capacidade.
- **Finalizador legado**: `WillExpressionEngine.finalize_delivery` agora rejeita qualquer chamada sem alterar estado ou recibos. O chamador deve usar `WillPressureEngine.finalize_pending_delivery`, com evento, escopo e evidencia de transporte. Os caminhos de producao ja usavam esse contrato integrado; a revisao nao adiciona envios nem amplia destinatarios.
- **Limite explicito**: `planned` legado, `preparing` interrompido, `delivering` e `delivery_uncertain` nao sao reiniciados cegamente. Ainda nao ha lease, vencimento ou reconciliador de preparacao; uma interrupcao pode continuar pendente. Persistencia do resultado da capacidade, payload preparado e recibos anteriores ao envio ainda tem pontos de commit separados. Essa entrega impede duplicacao concorrente, mas nao comprova recuperacao ponta a ponta nem encerra C9c.
- **Testado**: 12 testes de expressao (antes 4), incluindo duas threads com conexoes independentes e leitura inicial simultanea, estados interrompidos/legados, rollback do recibo de reivindicacao, ausencia de transacao aberta apos disputa e rejeicao do finalizador antigo tanto para sucesso quanto para falha. Conjunto de expressao/entrega/arbitragem: 84 aprovados; suite completa: `546 passed`; 20 cenarios `--mock`, sintaxe e `git diff --check` aprovados. Nenhuma chamada externa ou paga.
- **Habilitacao / operacao**: apenas checkpoint local. Nenhum push, deploy ou probe de producao nesta entrega. Imagens, novos canais e acesso de participantes nao foram ativados. As alteracoes locais alheias ao corte foram preservadas.

**C9c2 - Recuperacao local de recibos e tentativas interrompidas (06/09/2026; commit local `aa18bcd`).**

- **Implementado**: `engines/will_recovery.py` procura recibos terminais persistidos com evento vinculado, na instancia, usuario e Relation exatos. Revalida o contrato de entrega antes de recuperar pressao/frustracao e o registro de elegibilidade para equivalencia do loop. Recibo ausente, destinatario/escopo incorreto ou transporte nao suportado nao autoriza descarga. O scheduler chama essa manutencao local antes de cada pulso, inclusive quando nao ha executor proativo disponivel; nao ha chamada de capacidade, LLM, imagem ou transporte no reconciliador.
- **Tentativas limitadas e duraveis**: migracao aditiva de `phase_integration_at`, `recovery_attempts`, `recovery_next_at` e `recovery_error`. Cada recuperacao reserva sua tentativa e o proximo horario antes de processar; limite de 5 tentativas, com esperas minimas de 5, 10, 20, 40 e 80 minutos apos falhas. A execucao efetiva acompanha a frequencia do scheduler, nao e um novo job a cada 5 minutos. Uma interrupcao durante a integracao libera nova tentativa apos o prazo persistido. O lote e limitado a 50 registros por etapa; esgotamento fica visivel e nao reinicia sozinho.
- **Tempo e idempotencia**: o periodo refratario e `last_release_at` passam a usar a data da confirmacao persistida, nao a data de recuperacao. O marcador anterior continua impedindo um segundo alivio. Falha depois da pressao e antes do registro de equivalencia pode retomar so essa integracao. `phase_integration_at` significa que o registro de elegibilidade foi tratado (inclusive quando nao aplicavel); nao significa consumo da fase, broadcast de Working Memory ou registro proativo concluido.
- **Preparacao e vencimento**: payload, evidencia cognitiva e recibo de preparacao sao gravados na mesma transacao. `planned`/`preparing` sem progresso por 30 minutos tornam-se `preparation_uncertain`; um executor tardio nao pode sobrescrever esse estado. `delivering` antigo torna-se `delivery_uncertain`, com ou sem evento vinculado, sem alivio ou reenvio. `prepared` com 24 horas desde a criacao expira e nao pode ser reivindicado; dentro do prazo continua reutilizavel sem executar novamente a capacidade. Os limites sao conservadores locais, ainda sem calibracao de producao.
- **Limite explicito da recuperacao**: confirmacao integral que chega posteriormente ainda pode resolver `delivery_uncertain` pelo contrato validado. O reconciliador nao adivinha o que ocorreu no provedor, nao recria evento faltante, nao repete geracao paga e nao corrige sozinho preparacao cujo efeito externo aconteceu antes da persistencia. Esses casos ficam para revisao assistida; ferramenta operacional dedicada ainda pendente. Registro proativo e efeitos posteriores ao commit da fase em Working Memory tambem nao foram recuperados neste corte.
- **Sondas**: `expressions` mostra integracao de pressao e registro de equivalencia pendentes, numero de tentativas, proximo horario, tipo de erro e esgotamento, com compatibilidade de leitura do schema anterior. Sinaliza estados incertos e conclusao legada sem evento. Omite `reason` e `summary` dos recibos, pois podem conter texto de mensagens; os novos campos de recuperacao nao copiam payloads nem mensagens de erro do provedor.
- **Validacao / habilitacao / operacao**: 29 novos testes em `tests/test_will_recovery.py`; suite completa `575 passed`; conjunto expressao/entrega/arbitragem/recuperacao com 113 testes; 20 cenarios `--mock`, sintaxe e `git diff --check` aprovados. Inclui banco reaberto, concorrencia, falha de commit de evidencia world, recibos invalidos, escopo, backoff/esgotamento, vencimento, executor tardio, privacidade e migracao aditiva. Apenas commit local, sem push, deploy ou probe de producao. Quando houver publicacao autorizada, a manutencao sera executada pelo scheduler existente; nenhuma imagem, novo canal ou participante foi ativado nesta entrega.

**C9c3 - Primeiro bloco: Working Memory e auditoria de fases satisfeitas pelo WILL (07/09/2026; commit local `5291360`; C9c3 ainda em andamento).**

- **Implementado**: `engines/will_loop_integration.py` rele o resultado persistido e valida recibo consumido, instancia, usuario admin, escopo global, ciclo, fase e vinculo do pulso concluido. Observacao em Working Memory, ajuste do limite de foco, broadcast para a proxima fase, evento de auditoria, atualizacao do payload e marcador de integracao sao gravados na mesma transacao. Se qualquer gravacao falhar, nenhuma dessas alteracoes parciais permanece; o resultado cognitivo ja confirmado continua salvo para retomada.
- **Reuso das regras existentes**: a classificacao de foco/fringe/candidate, prioridade, referencias `loop#id` e montagem do broadcast continuam em `WorkingMemoryEngine`. A camada de banco oferece uma visao com conexao privada e commit controlado pelo chamador; os metodos normais preservam o commit padrao. Nao ha nova chamada LLM, envio de mensagem, geracao de imagem ou repeticao da investigacao nesta integracao. O caminho equivalente retorna o resultado integrado sem emitir nova notificacao de conclusao ao admin.
- **Retomada**: alem do caminho imediato e da repeticao do mesmo pulso, `sync_loop` verifica a fila de integracoes consumidas antes de escolher a fase atual. Campos aditivos em `will_phase_satisfactions`: `integration_version`, `integration_at`, `integration_attempts`, `integration_next_at`, `integration_error`. Recuperacao automatica limitada a 5 tentativas, lote de 25 e esperas minimas de 5, 10, 20, 40 e 80 minutos, conforme a frequencia do scheduler. Falhas sao registradas por codigo/tipo sem texto privado. A sonda `phase_satisfaction` expoe esses metadados sem payload de memoria.
- **Fronteira de seguranca**: so novos consumos marcados com `integration_version=1` podem ser retomados automaticamente. Consumos legados, efeitos preexistentes sem marcador, resultados com mais de 24 horas ou data futura ficam para revisao; nao se duplica memoria nem se reativa foco antigo por suposicao. Esse limite de 24 horas ainda precisa de observacao operacional. O broadcast usa o estado de WM no momento da integracao, sem fingir reconstruir um snapshot historico perdido.
- **Validacao**: 23 testes novos em `tests/test_will_loop_integration.py`, incluindo falha em cada gravacao, rollback da retirada de foco, reconexao ao banco, retomada por `sync_loop`, concorrencia, erros de escopo/vinculo, legado, vencimento e backoff. O teste integrado anterior deixou de simular os hooks de WM e passou a verificar os registros reais. Suite completa `598 passed`; 20 cenarios `--mock`, sintaxe e `git diff --check` aprovados. Apenas commit local; nenhum push/deploy ou nova ativacao de producao.
- **Nao concluido neste bloco**: registro proativo, recuperacao pos-commit das fases executadas normalmente (sem equivalencia WILL), reservas de janelas encerradas/tentativas esgotadas, conciliacao assistida e gates de consentimento/orcamento. O C9c3 e o C9 permanecem abertos. Nao iniciar C10 nem liberar participantes por causa desse checkpoint.

**C9c3 - Segundo bloco: registro proativo duravel e efeitos posteriores auditaveis (07/09/2026; checkpoint local; C9c3 ainda em andamento).**

- **Implementado**: uma confirmacao Telegram validada da vontade `relacionar` passa a gravar, em uma unica transacao, a abordagem proativa, a conversa marcada por `will_expression_<id>`, sua origem por expressao e uma fila de quatro efeitos posteriores: desenvolvimento, fatos, log textual e memoria semantica. O texto, participante, instancia, Relation, plataforma e ids de mensagem sao relidos dos recibos persistidos; argumentos do chamador nao podem alterar a memoria registrada. Repetir a confirmacao ou a retomada devolve a mesma conversa, sem criar outra abordagem, conversa ou alivio de pressao.
- **Retomada conservadora**: o reconciliador C9c2 agora identifica tambem confirmacoes relacionais sem recibo proativo. Ele apenas recompõe o registro duravel e a fila, com as mesmas cinco tentativas limitadas e sem chamar LLM, mem0, extracao, escrita de perfil ou transporte. Assim, uma interrupcao apos a entrega confirmada nao perde o registro e nao repete a mensagem. Conversas ou filas parcialmente apagadas, recibos/payloads invalidos e registros legados sem `proactive_record_version=1` exigem revisao, sem recriacao por suposicao.
- **Efeitos posteriores**: no caminho normal, a fachada proativa despacha cada efeito uma unica vez apos a gravacao duravel, usando uma reivindicacao `pending -> running` persistida. Retornos bem-sucedidos ficam como `returned`; mem0 indisponivel fica `blocked`; excecao fica `uncertain`. Uma interrupcao apos a chamada deixa `running`, que nao e repetido cegamente. A sonda `expressions` mostra apenas ids e estados desses efeitos, nunca o texto ou os ids de mensagem. Isto registra a lacuna em vez de fingir garantia de exatamente-uma-vez para efeitos externos.
- **Escopo relacional**: antes de registrar, a Relation deve existir e pertencer a mesma instancia e participante do recibo. Em escopo global, os handlers legados continuam preservados. Em Relation privada, a conversa e a abordagem ficam duraveis, mas os quatro handlers antigos ficam explicitamente `blocked` como `scoped_handler_required`; eles nao sao executados com APIs ainda globais nem vazam memoria entre pessoas. Adaptadores relacionais proprios permanecem requisito de C10-C12.
- **Integracao com o envio**: depois da confirmacao, qualquer pulso cujo vencedor seja `relacionar` passa pela mesma fachada, mesmo se o modelo tiver usado outro rótulo de `delivery_type`. Isto remove uma lacuna em que uma entrega relacional confirmada poderia aliviar a pressao sem ser registrada na continuidade autobiografica.
- **Validacao / habilitacao**: 4 testes novos para conversa/abordagem unicas, dispatcher uma-vez, bloqueio de Relation e retomada apos interrupcao; conjunto WILL focado com 72 testes. Suite completa `602 passed`; sintaxe, `git diff --check` e 20 cenarios `--mock` aprovados. Testes offline, sem provedor pago, mem0 real ou Telegram. Apenas checkpoint local; nenhum push, deploy, novo canal ou participante foi ativado.
- **Limite explicito**: esta entrega nao reexecuta efeitos `running`/`uncertain`, nao implementa adaptadores de memoria por Relation e nao conclui a recuperacao pos-commit das fases comuns do loop. Ela torna esses estados observaveis e impede duplicacao; a decisao de retomada assistida continua pendente.

**C9c3 - Terceiro bloco: integracao transacional de fases normais (07/09/2026; checkpoint local; C9c3 ainda em andamento).**

- **Implementado**: cada novo resultado normal bem-sucedido do loop passa a criar, na mesma transacao que o resultado e seus artefatos, uma entrada em `consciousness_loop_post_commit_effects`. A entrada representa exatamente tres consequencias locais: observacao em Working Memory, broadcast para a proxima fase e evento de auditoria. A integracao grava esses tres efeitos, os metadados atualizados do resultado e seu marcador final em uma unica transacao privada.
- **Retomada sem repeticao cognitiva**: no inicio de cada `sync_loop`, antes de selecionar a janela atual, a fila tenta resultados pendentes recentes. Ela relê o resultado persistido e nunca chama a fase, LLM, pesquisa, artefato, notificacao ou Telegram novamente. As tentativas sao limitadas a 5, em lotes de 25, com esperas de 5, 10, 20, 40 e 80 minutos. Resultado sem fila, escopo corrompido, resultado futuro/mais antigo que 24 horas ou efeito parcial preexistente fica para revisao, sem tentativa de adivinhar a continuidade perdida.
- **Compatibilidade e observacao**: o caminho legado continua para bancos mínimos sem Working Memory e para resultados de fase `failed`. A sonda `loop` mostra o id do resultado, fase, versao, marcador, tentativas, proximo horario e tipo de erro da fila, sem resumo, payload ou texto cognitivo.
- **Validacao / habilitacao**: 8 testes novos verificam integracao unica, rollback em cada uma das cinco gravacoes, reinicio sem reexecutar a fase e a passagem pelo caminho normal de `execute_phase`. Suite completa `610 passed`; sintaxe, `git diff --check` e 20 cenarios `--mock` aprovados. Nenhuma chamada externa ou paga; apenas checkpoint local, sem push, deploy ou ativacao de participante.
- **Limite explicito**: falhas de fase continuam com sua trilha existente de fragmento para ruminação, pois ela possui efeitos adicionais e deve receber um corte proprio. A fila tambem nao resolve reservas de pulso encerradas/tentativas esgotadas, nao reconcilia efeitos parcialmente existentes e nao altera politicas de notificacao ou custo.

**C9c3 - Quarto bloco: fechamento de janelas, tentativas e reservas (07/09/2026; checkpoint local; C9c3 ainda em andamento).**

- **Implementado**: antes de cada `sync_loop`, a agenda primeiro tenta reparar um pulso `running` que ja tenha resultado persistido. Pulsos sem resultado cuja janela ja fechou recebem destino terminal: `pending` vira `skipped`, `running` vira `interrupted` e `failed` que esgotou sua politica vira `exhausted`; falha ainda elegivel, mas fora da janela, tambem vira `skipped`. Os motivos ficam em `last_error`, sem criar um novo agendamento nem rodar a fase de novo.
- **Tentativas e reservas WILL**: ao atingir o limite dentro da janela, o pulso tambem passa para `exhausted` e deixa a razao registrada. Toda evidencia WILL ainda `reserved` por um pulso terminalizado e sem resultado e invalidada com um codigo persistido. Assim, uma reserva antiga nao pode ser consumida tardiamente para produzir uma fase fora de sua janela. Resultado efetivamente gravado continua prevalecendo e e reparado pelo caminho existente.
- **Observabilidade**: a sonda `phase_pulses` marca `interrupted` e `exhausted` como estados que requerem revisao e preserva a causa ja resumida no pulso. Nao expoe evidencias privadas nem habilita qualquer comando de reenvio.
- **Validacao / habilitacao**: 3 testes novos verificam os tres destinos de janela fechada, o limite de tentativas e a invalidacao da reserva WILL; suite completa `613 passed`, sintaxe, `git diff --check` e 20 cenarios `--mock` aprovados. Apenas checkpoint local, sem push, deploy, nova mensagem, imagem ou participante.
- **Limite explicito**: `interrupted`, `exhausted`, efeitos parciais e falhas de fase nao sao resolvidos automaticamente. Eles agora tem estado e evidencia para uma conciliacao assistida futura; essa ferramenta ainda nao foi criada.

**C9c3 - Quinto bloco: efeitos locais pos-commit de falhas (08/09/2026; checkpoint local; C9c3 ainda em andamento).**

- **Implementado**: todo novo resultado `failed` passa a criar, junto com o proprio resultado, uma fila dedicada de efeitos posteriores. Quando a base oferece Working Memory transacional, a observacao, o broadcast, o fragmento de ruminação por falha, o evento de auditoria, os metadados do resultado e o marcador da fila sao gravados juntos. Uma falha de gravacao desfaz o conjunto inteiro; a fase que falhou jamais e executada outra vez para recuperar esses efeitos.
- **Retomada conservadora**: no inicio de `sync_loop`, entradas pendentes recentes sao relidas e retomadas localmente em ate cinco tentativas, com o mesmo backoff de 5, 10, 20, 40 e 80 minutos. Resultados fora de 24 horas, futuros, fora de escopo ou com qualquer efeito parcial preexistente ficam em revisao, sem duplicar memoria, ruminação ou auditoria. Nenhum provedor, LLM, mensagem ou transporte e chamado nesta trilha.
- **Fronteira explicita**: o fragmento so e criado quando o limiar ja calculado de falhas consecutivas o exige; bancos legados sem Working Memory preservam o caminho anterior. A recuperacao nao altera o pulso terminal, nao desativa a notificacao de falha e nao tenta interpretar efeitos externos ou incertos. Esses casos pertencem a conciliacao assistida seguinte.
- **Validacao / habilitacao**: 8 testes novos cobrem integracao unica, rollback em cada gravacao, ausencia de fragmento parcial, banco sem a tabela lazy de ruminação e recuperacao sem reexecutar a fase. Suite completa: 621 testes aprovados. Os testes sao inteiramente locais e nao ativam custo, Telegram, imagem ou participante. Commit local desta entrega; nenhum push, deploy ou alteracao de producao.

**Continuacao obrigatoria: restante do C9c3 e fechamento do C9c.**

1. Proxima acao: desenhar a ferramenta assistida para `interrupted`, `exhausted`, preparacao incerta e efeitos parciais. Ela deve exigir evidencia confiavel, registrar a decisao e nunca equivaler a um reset que autorize reenvio ou gasto cego.
2. Criar procedimento/ferramenta assistida para preparacao incerta, entrega sem evidencia integral, evento ausente e recuperacao esgotada. Exigir evidencia confiavel e registrar a decisao; nao oferecer reset de estado que autorize reenvio ou gasto cego. A quarentena do C9c2 torna esses casos observaveis, mas nao os resolve automaticamente.
3. Executar os gates restantes de capacidade, consentimento e orcamento. O atalho `WillExpressionEngine.finalize_delivery` foi bloqueado no C9c1; manter o contrato integrado como unico caminho de confirmacao e testar os gates antes de qualquer nova tentativa.
4. Registrar o aceite do escopo world-only ou implementar e validar um adaptador proprio antes de autorizar supressao de hobby. A ausencia desse adaptador nunca autoriza satisfacao automatica por imagem ou envio; nao exige reativar geracao paga.
5. Completar regressao ponta a ponta, publicar apenas com autorizacao e validar por probes antes de encerrar C9. Nao iniciar C10 nem convidar participantes como consequencia automatica dos commits locais.

**C10 - Disponibilidade e periodo refratario (planejado; depende do C9).** Criar estado persistente por instancia e Relation, com janelas de contato, orcamento de turnos/profundidade, recuperacao, cooldown e retomada. Decidir e testar continuidade da pressao na virada de ciclo e consumo de sinais sem aliviar repetidamente pela mesma evidencia. Uma relacao em elaboracao nao bloqueia automaticamente as demais; limites globais de custo ou disponibilidade sao distintos e explicitos. Preservar comandos essenciais e protocolos de seguranca. Aceite: cenarios com tempo simulado, conversas intensas, relacoes concorrentes, reinicio, virada de dia e esgotamento de orcamento, sem chamadas pagas.

**C11 - Expressao conversacional e proatividade unificadas (planejado; depende do C10).** Aplicar a semantica das tres vontades da Secao 10.1.1 a respostas e iniciativas, com politica comum de disponibilidade, consentimento e custo. Oferecer expressao textual independente de geracao de imagens. Distinguir sinais do interlocutor dos produzidos pelo agente, evitando auto-influencia circular. Registrar por que respondeu, iniciou contato, adiou ou retomou; nao acrescentar uma chamada LLM obrigatoria a cada decisao. Aceite: fluxo conversacional integrado, ausencia de respostas/envios duplicados, credito de alivio por acao e vontade comprovado e funcionamento textual com imagens desligadas.

**C12 - Fechamento da interioridade multi-relacional (planejado; depende do C11).** Auditar e completar os caminhos restantes em Working Memory, sonhos, identidade, recuperacao e montagem de contexto. Separar material privado, agregados autorizados e estado proprio global; nao duplicar mecanicamente toda a identidade por pessoa. Definir tratamento da memoria historica do admin sem reimportacao silenciosa para outros escopos. Aceite: pelo menos duas relacoes com fatos, tensoes e mensagens sentinela distintas; nenhum conteudo privado aparece no contexto ou na entrega de outra pessoa; influencias globais possuem origem auditavel sem carregar texto privado. Testar tambem acesso revogado, apagamento e isolamento entre instancias de teste.

**C13 - Piloto convidado observavel (bloqueado ate aceite C9-C12 e aprovacao do mantenedor).** Comecar com poucas pessoas explicitamente convidadas, Relation ativa, consentimento e regras de uso, privacidade e apagamento definidos. Usar limites de custo e contato, revogacao de acesso e mecanismo de pausa. Cockpit e probes devem mostrar disponibilidade, motivo de bloqueio/adiamento, retomadas, recibos, escopo e custo sem expor conversas privadas. Definir antes de abrir a janela, metricas, criterios de interrupcao e aceite; nao encerrar apenas pelo numero de dias. Observar continuidade da memoria, pertinencia das iniciativas, respeito ao descanso, isolamento e custo por relacao. Este e um piloto relacional da instancia existente, nao uma liberacao comercial multi-instancia.

**Registro obrigatorio em cada entrega**: ID do corte, commit, cenarios testados, estado de habilitacao, evidencias operacionais, pendencias com proxima acao e aprovacao ainda necessaria. A publicacao ate C9c2 e o primeiro bloco local do C9c3 nao encerram C9 e nao antecipam C10-C13. A higiene dos monolitos acompanha as fronteiras tocadas por cada corte, preservando fachadas e contratos, sem reescrita geral.

### 10.2 Trilha comercial: Jung Inner Life Engine

A trilha comercial sera construida sobre expressoes de vontade, e nao sobre acesso direto de terceiros ao banco ou aos prompts internos. O contrato entre engine e aplicacao externa sera:

`perceive/evento -> metabolismo -> will_expression -> conector com gate -> resultado como evidencia`

A primeira entrega comercial deve definir o `Will Expression Contract v1`, com vontade dominante, conflito, objetivo, acao proposta, confianca, evidencias, risco, custo, validade, idempotencia, politica de aprovacao e resultado esperado. Depois, um conector generico em modo dry-run/webhook podera validar o fluxo sem efeitos externos.

A estrategia inicial recomendada e validar agentes de software e companions B2B, por exigirem menos infraestrutura que Unity, Unreal ou robotica e por aproveitarem o que ja existe no Telegram e no cockpit. Games e robotica permanecem como verticais posteriores da mesma API.

**Direcao aprovada em 05/09/2026**: demonstrar primeiro continuidade autobiografica, processamento entre encontros e iniciativa verificavel em uma aplicacao de software. Depois do piloto relacional e dos gates cognitivos, seguir esta ordem:

1. **Multiplas instancias isoladas**: fechar os requisitos e testes da Secao 10.3, aproveitando a administracao multi-tenant existente sem confundi-la com isolamento cognitivo.
2. **API minima de eventos, intencoes e resultados**: receber eventos escopados, expor apenas intencoes autorizadas e aceitar recibos autenticados e idempotentes. Consultas de estado devem ser minimas e autorizadas; o catalogo da Secao 10.4 nao vira automaticamente superficie publica.
3. **Um conector textual governado**: validar primeiro em simulacao/dry-run e depois com aprovacao, limites de custo e observabilidade. MCP e um meio de conexao; decisao, permissoes, privacidade, orcamento e confirmacao permanecem sob responsabilidade da plataforma.
4. **Um piloto comercial delimitado**: escolher uma aplicacao e parceiro apos evidencias dos passos anteriores, medir utilidade e custo antes de ampliar canais, SDKs ou escala.

O plano `docs/plano_desenvolvimento_inner_life_engine.md` permanece como visao de possibilidades. Suas prioridades antigas de games, datas e amplitude de SDKs nao sobrepoem esta sequencia vigente. Marketplace de conectores, integracoes de games e robotica sao expansoes posteriores, nao compromissos da proxima entrega. Esta decisao nao autoriza efeitos externos ou reativacao de funcionalidades gateadas.

### 10.2.1 Catalogo de conectores orientado pela vontade

O modulo WILL devera possuir um catalogo de possiveis conexoes organizado pelas tres vontades fundamentais do agente: `saber`, `expressar` e `relacionar`. Esse catalogo sera uma capacidade futura da plataforma e nao uma autorizacao automatica para agir fora dela.

**Organizacao inicial do catalogo**:

1. **Saber**: fontes de pesquisa, busca e consulta, bases documentais, repositórios de conhecimento, navegacao, ferramentas academicas e outros recursos que ampliem a percepcao ou a investigacao do agente.
2. **Expressar**: canais de publicacao, mensagens, arte, imagem, texto, audio, video, sites, blogs, documentos e outros meios pelos quais uma intencao possa produzir uma expressao externa.
3. **Relacionar**: canais de conversa e presenca relacional, como Telegram, Discord, e-mail, comunidades, suporte, plataformas sociais e outros meios de interacao com pessoas ou entidades.

Cada conector devera declarar, no minimo, seu provedor, protocolo, capacidades, vontade(s) atendida(s), acoes disponiveis, escopos de leitura e escrita, credenciais, organizacao e instancia autorizadas, relacoes alcançaveis, exigencias de consentimento, custo, limites de uso, idempotencia, auditoria, politica de aprovacao e semantica de falha. Um mesmo conector podera atender mais de uma vontade, mas suas capacidades deverao permanecer explicitamente separadas para evitar que um canal relacional seja tratado como fonte de saber ou meio de expressao sem governanca propria.

**MCP como mecanismo preferencial**: por se tratar de um agente, a plataforma devera priorizar conectores expostos por MCP quando houver um servidor ou adaptador adequado. MCP sera a interface preferencial para descoberta de ferramentas, parametros, resultados e erros, mantendo o nucleo cognitivo desacoplado do provedor. APIs diretas, webhooks ou SDKs continuarao permitidos como adaptadores quando nao houver MCP disponivel ou quando uma integracao exigir propriedades especificas, sem transformar o fornecedor em dependencia estrutural do WILL.

O fluxo devera manter quatro camadas distintas:

1. **Vontade interna**: scores, pressao, conflito, dominancia e evidencias que indicam uma orientacao do agente.
2. **Intencao de expressao**: proposta estruturada de objetivo e acao, registrada como `will_expression`.
3. **Catalogo de capacidade**: conector compatível, escopo, requisitos, custo e politica aplicaveis.
4. **Execucao governada**: aprovacao ou bloqueio, chamada ao conector, resultado, recibo, efeito observado e eventual falha ou compensacao.

Nenhuma pontuacao de vontade devera, sozinha, disparar uma acao externa. A escolha do conector devera ser explicita, rastreavel e compativel com a politica da organizacao, da instancia e da relacao. O conector nao tera acesso direto ao banco, aos prompts ou aos estados internos; recebera apenas o contrato minimo autorizado e devolvera um resultado que possa alimentar a memoria, a identidade, a ruminacao e os proximos ciclos como evidencia.

**Demanda futura de produto**: construir o registro persistente de conectores, a descoberta por vontade, os adaptadores MCP e nao-MCP, a tela de governanca no cockpit, o dry-run, os gates de consentimento/aprovacao, o controle de custos e os probes de execucao. Essa frente devera ser desenvolvida depois que a multiplicidade relacional e a arquitetura multi-instancia estiverem suficientemente validadas, pois cada conector precisara ser escopado por organizacao, instancia, participante e finalidade.

### 10.3 Arquitetura multi-instancia

A plataforma comercial nao sera apenas multi-tenant na administracao. Cada organizacao podera criar e operar uma ou varias instancias cognitivas independentes:

```text
Plataforma
  -> Organizacao
      -> Instancia JungAgent
          -> Relacoes com pessoas ou entidades
              -> Memorias, interacoes e influencia nas vontades
```

Uma empresa pode contratar um JungAgent corporativo que se desenvolve por meio das relacoes com seus funcionarios. Uma empresa de robotica pode possuir uma frota em que cada robo recebe uma instancia JungAgent propria, com identidade, memoria e metabolismo independentes.

**Entidades do modelo**:

1. **Plataforma**: control plane do produto, planos, faturamento, observabilidade e governanca.
2. **Organizacao**: tenant responsavel por uma ou varias instancias e seus administradores.
3. **Instancia JungAgent**: uma celula cognitiva com identidade, memoria, sonhos, ruminacao, Working Memory, vontades, conectores, agenda, modelo e orcamento proprios.
4. **Participante ou entidade externa**: funcionario, operador, cliente ou pessoa que se relaciona com uma instancia; no caso robotico, o robo e a entidade que possui a instancia e seus operadores sao participantes relacionais.
5. **Relacao**: vinculo entre uma instancia e um participante, com memoria, consentimento, escopo, postura, cadencia e fontes proprias.

**Lacuna atual**: `AGENT_INSTANCE` ainda e uma configuracao singleton do processo e `ADMIN_USER_ID` continua sendo o centro do loop. Embora parte do schema ja possua `agent_instance`, varias tabelas e rotinas de sonhos, ruminacao e vontade ainda sao principalmente indexadas por `user_id`. O multi-tenant atual controla acesso de administradores, mas ainda nao garante isolamento de multiplas psiques.

**Requisitos obrigatorios**:

- criar um registro de instancias com dono, tipo de entidade, status, identidade, modelo, politica, limites e orcamento;
- vincular cada instancia a uma organizacao e cada participante a uma ou mais relacoes explicitamente escopadas;
- substituir o `ADMIN_USER_ID` como centro cognitivo por uma referencia de instancia; o admin permanece operador privilegiado e contato de governanca;
- levar `agent_instance` ou escopo equivalente a conversas, fatos, memorias, sonhos, ruminacoes, vontades, pressao, Working Memory, artefatos, eventos e resultados;
- tornar o scheduler capaz de executar ciclos independentes por instancia, sem misturar agendas, pulsos, custos ou falhas;
- aplicar RBAC por organizacao, instancia e papel, com master no control plane e org_admin limitado aos recursos de sua organizacao;
- registrar canais e identificadores externos separadamente da identidade cognitiva, permitindo Telegram, API, Unity, Unreal, ROS2 ou dispositivos roboticos;
- manter auditoria, consentimento, apagamento, limites de custo, idempotencia e ciclo de vida de cada instancia.

**Migracao do agente atual**: a instalacao existente sera tratada como `default-org` com a instancia `jung_v1`. O admin atual sera preservado como operador e primeiro participante relacional, sem permanecer como sujeito obrigatorio de todos os processos internos.

**Gate antes de pilotos comerciais**:

- duas organizacoes de teste com instancias independentes;
- uma organizacao com duas instancias sem mistura de memoria, vontade ou agenda;
- uma instancia com multiplos participantes e isolamento de contexto comprovado;
- uma frota simulada com pelo menos dois agentes independentes;
- probes demonstrando que custos, pulsos, falhas, sonhos, ruminacao e vontades sao atribuidos a instancia correta;
- master, org_admin e operador de instancia testados em conjunto;
- nenhum conector externo real antes de o `Will Expression Contract` e os gates de aprovacao estarem validados.

**Regra de sequenciamento comercial**: especificacao e simulacao podem comecar agora; novas integracoes comerciais reais, SDKs de producao e pilotos comerciais com dados de terceiros ficam bloqueados ate o aceite da multiplicidade relacional e da arquitetura multi-instancia. O piloto relacional C13 na instancia existente tem seus proprios gates e aprovacao; nao exige antecipar toda a plataforma comercial nem libera esses recursos por extensao.

### 10.4 Catalogo futuro de informacao da API

A API devera poder fornecer, em principio e sem antecipar ainda as restricoes de exposicao, as seguintes familias de informacao:

1. **Instancia**: identidade, organizacao, tipo de entidade, status, modelo, agenda, pulsos, limites e orcamento.
2. **Pessoas e relacoes**: participantes, identificadores externos, papeis, postura, cadencia, silencio, temas, necessidades, compromissos, confianca e consentimentos.
3. **Interacoes**: conversas, sessoes, mensagens, canais, eventos, comandos, respostas proativas, resultados, falhas e metadados afetivos.
4. **Memoria**: fatos, memoria semantica, autobiografica e relacional, memorias compartilhadas ou privadas, correcoes, confianca, fontes, ancoras e buscas.
5. **Identidade do agente**: crencas, valores, contradicoes, capitulos narrativos, mudancas, autoimagem, possiveis selves e evidencias.
6. **Vontades**: scores de saber/relacionar/expressar, dominancia, constricao, conflito, pressao, pulsos, influencias relacionais, historico e expressoes de vontade.
7. **Postura**: intencao, tom, estado afetivo estrutural, foco, contexto, memorias selecionadas, Working Memory, restricoes e confianca.
8. **Ruminacao e sonhos**: fragmentos, tensoes, maturidade, insights, material onirico, temas simbolicos, funcao reguladora, residuos e evidencias.
9. **Working Memory e objetivos**: foco, fringe, candidatos, broadcasts, objetivos, passos, evidencias esperadas, acoes propostas, gates, resultados, cooldown e idempotencia.
10. **Mundo e grafo**: estado do mundo, gaps, pesquisas, fontes, descobertas, nos, triplas, predicados, confianca, caminhos causais e contradicoes.
11. **Metacognicao e desenvolvimento**: estado integrativo, limites, mudancas de estrategia, falhas, hipoteses, autoavaliacao, fallbacks, tendencias de ajuste e historico.
12. **Wellness e leitura de pessoas**: psicometria, valores, padroes, qualidade, trajetorias, evidencias, lacunas e versoes de relatorios; wellness permanece como recurso de compreensao, nao como diagnostico automatico.
13. **Producao**: ensaios, pesquisas, artefatos, imagens, criticas, portfolios, projetos, briefs, tickets, entregas e destinos.
14. **Saude operacional**: estado do loop, fases, pulsos, retries, warnings, latencia, tokens, custos, armazenamento, banco, memoria semantica, conectores, deploy e versao.
15. **Governanca**: administradores, papeis, organizacoes, instancias acessiveis, permissoes, consentimentos, auditoria, exportacao, apagamento, politicas e aprovacoes.
16. **Eventos**: eventos recebidos e emitidos, expressoes de vontade, mudancas de postura, insights, acoes, webhooks, assinaturas e resultados externos.

A API devera oferecer pelo menos duas leituras complementares:

- **API de estado**: como a instancia, a relacao ou o agente estao agora;
- **API de ciclo**: o que aconteceu, o que mudou, quais evidencias sustentam a mudanca e o que foi proposto em seguida.

O catalogo nao autoriza exposicao irrestrita. Ele e um inventario de possibilidades para a futura especificacao de contratos, escopos, consentimentos e gates. A API deve fornecer contexto, trajetoria, evidencia, intencao e causalidade, e nao apenas espelhar tabelas internas.

### 10.5 Auditoria preliminar do modulo de vontade

Em 19/08/2026 foi auditado o caminho completo `scores -> pressao -> pulso -> acao -> resultado`, com sondas de producao, leitura de codigo e 440 testes offline.

**Correcao realizada**:

- uma falha de composicao, bloqueio ou envio nao descarrega mais a pressao da vontade;
- a falha continua gerando frustacao para a ruminacao;
- a falha nao sobrescreve `last_release_will` nem `last_release_at`, que devem representar apenas uma liberacao real;
- foram adicionados testes para falha relacional, falha no pulso e liberacao bem-sucedida.

**Achados historicos de 19/08/2026 para a fase futura da vontade** (nao representam integralmente o schema atual; atualizacao e destino abaixo):

1. O estado de pressao hoje e criado por `user_id + cycle_id` e um novo ciclo nasce com pressoes zeradas. E necessario decidir e testar a continuidade longitudinal da pressao entre ciclos.
2. O pulso possui entrega em duas etapas, preparacao e envio, mas ainda precisa de um estado explicito de tentativa, retry, backoff e idempotencia para sobreviver a reinicios sem risco de duplicacao ou perda de resultado.
3. Falhas de proatividade ainda podem ser registradas apenas como `mensagem invalida`, sem distinguir cooldown, resposta vazia, JSON invalido, erro de provedor, ausencia de destinatario ou falha de transporte.
4. A pressao e as tabelas do modulo ainda sao predominantemente indexadas por `user_id`, com o `ADMIN_USER_ID` como centro. Isso bloqueia a vontade contextual por relacao e a futura multiplicidade de instancias.
5. O `WillEngine` deve ser auditado para separar material vindo do interlocutor de mensagens proativas produzidas pelo proprio agente, evitando auto-influencia nao intencional.
6. O efeito de sinais persistentes de silencio e conversa recente deve ser calibrado para que uma mesma evidencia nao produza alivio repetido ou crescimento desproporcional a cada pulso.

**Criterios de aceite para a futura fase da vontade**:

- nenhuma pressao diminui sem uma expressao, entrega ou resultado confirmado;
- toda tentativa possui outcome, motivo, evidencia, retry policy e idempotency key;
- a pressao atravessa a virada de ciclo conforme uma politica explicita e testada;
- vontade global, vontade por relacao e vontade por instancia aparecem separadas e auditaveis;
- falhas nao sao confundidas com liberacoes;
- probes permitem reconstruir por que uma vontade cresceu, venceu, foi bloqueada, agiu, falhou ou recebeu alivio.

**Revisao de 05/09/2026 e destino das pendencias**:

- `engines/will_phase_arbitration.py`: verificacao isolada aceitou uma expressao sem registro persistido, atribuiu qualidade `1.0` e permitiu que um recibo `scope_kind=relation` fosse consumido pela consulta de fase sem filtro relacional. E uma lacuna reproduzida em teste, nao evidencia de vazamento ocorrido em producao. Corrigir e cobrir no C9.
- `will_pressure.py:finalize_pending_delivery`: a protecao do estado final da expressao nao impede o chamador de reaplicar alivio a partir de `success`. Fechar correspondencia e idempotencia do efeito no C9.
- Os oito testes focados de `tests/test_will_expression.py` e `tests/test_will_phase_arbitration.py` passaram na revisao local de `5ea892f`; nao cobrem todas as garantias acima e nao constituem uma nova execucao da suite integral nem validacao de producao.
- Os achados historicos 2 e 3 seguem no C9; os achados 1 e 6 seguem no C10; o achado 5 segue no C11. O achado 4 foi parcialmente atendido pelo corte 6, que acrescentou escopos; completar o isolamento de ponta a ponta nos C9/C12 e a independencia de instancias na Secao 10.3.

## 11. Riscos

| Risco | Antidoto |
|---|---|
| Complexidade acumulada (monolitos, muitos arquivos na raiz) | Fase 0 reduziu a divida principal; `main.py` ainda precisa de higiene posterior; 500 linhas/arquivo novo; refatoracao ao fim de cada fase |
| Alucinacao estrutural (agente inventa passado) | Ancoras `tipo#id` implementadas; auditoria semanal do perfil pelo mantenedor |
| Narracao sem profundidade funcional | Nenhuma metrica de autorrelato vale sozinha; avaliacao cega pode ser retomada como pesquisa quando houver escala mais operacional |
| Dependencia do LLM subjacente | Runner de regressao `--mock` no CI; teste de troca de modelo reavaliado somente se houver troca real de modelo |
| Executor introduzir regressoes | CI bloqueante; contrato da Secao 6; escopo estrito por tarefa |
| Loop de auto-observacao (Fase IV+) | Cooldown 24h; ajustes <= 5% por ciclo; congelamento pelo mantenedor |
| Custo invisivel | Limites executaveis no WILL e custo por relacao/instancia como gate dos novos pilotos; imagens pagas permanecem desligadas; nao expandir escopo para faturamento completo agora |
| Seguranca de execucao (Fase VII) | Gate rigido; self-work via PR humano como caminho preferencial |
| Descolamento do usuario | Principio do Encontro; metrica de ressonancia; blog compreensivel |

## 12. Genealogia

| Documento | Data | Contribuicao |
|---|---|---|
| 5 documentos-fonte (A-E) | ate Mai/2026 | fases, dialetica, working memory, metricas |
| Versao 1 ("Roadmap AGI") | Mai/2026 | 7 fases bloqueantes, Principio Aureo, criterios binarios, riscos |
| Avaliacao externa (Claude, consultor) | 10/06/2026 | Reposicionamento como emulacao cognitiva; Fase 0; avaliacao cega; WM antecipada; portao do SKG |
| Versao 2.1 - Edicao de Execucao Delegada | 10/06/2026 | Governanca em tres papeis; contrato do executor; backlog como especificacoes; estado e avisos operacionais atualizados |
| Consolidacao canonica V2 | 11/06/2026 | Este arquivo substitui o redirecionamento e passa a ser o documento mestre de autoridade |
| Versao 2.3 - Estado Realizado e Roadmap Vivo | 08/07/2026 | Atualiza Fase 0 como concluida, Fase III como em fechamento/protagonismo, IV.0/IV.1 como realizadas, IV.2 como infraestrutura gateada, e registra o fechamento `relational_state -> will` |
| Versao 2.4 - Estado Real e Roadmap Vivo | 17/08/2026 | Registra os avancos reais das Fases III-VII, separa implementacao tecnica de evidencia de encerramento, atualiza os probes de producao e explicita o deploy de imagens ainda em fila |
| Versao 2.5 - Estado Real e Pausa Operacional de Imagens | 18/08/2026 | Registra a flag reversivel de custo, a pausa de imagens em producao, a suite com 437 testes e a saude do agente durante o periodo de aguardo |
| Versao 2.6 - Multiplicidade Relacional e Trilhas de Produto | 18/08/2026 | Registra o gate cognitivo multi-relacional e a trilha comercial baseada em expressoes de vontade |
| Versao 2.7 - Arquitetura Multi-instancia para a Trilha Comercial | 18/08/2026 | Registra organizacoes com multiplas instancias JungAgent, isolamento cognitivo e os gates de produto para funcionarios e robotica |
| Versao 2.8 - Catalogo Futuro de Informacao da API | 19/08/2026 | Registra a superficie futura de informacao, estado, evidencia, intencao, governanca e eventos da API |
| Versao 2.9 - Correcao e Auditoria Preliminar da Vontade | 19/08/2026 | Corrige descarga indevida em falhas de pulso, registra 440 testes e abre o backlog estrutural da futura fase da vontade |
| Versao 3.0 - Primeiro Corte do Dominio Relations | 21/08/2026 | Cria o registro persistente de relacoes por instancia/organizacao, com consentimento, escopo, ciclo de vida, isolamento e probe read-only; memoria e vontade ainda aguardam cortes posteriores |
| Versao 3.1 - Vinculo Relacional nas Conversas | 21/08/2026 | Adiciona `relation_id` de forma retrocompativel as conversas, com resolucao automatica para relacoes existentes e filtros por relacao |
| Versao 3.2 - Cockpit de Relations | 21/08/2026 | Substitui Wellness como area principal, adiciona cadastro/gestao de relacoes multi-tenant e preserva o endpoint legado por redirecionamento |
| Versao 3.3 - Escopo cognitivo por Relation | 21/08/2026 | Propaga `relation_id` para estado relacional, fatos, fallback SQLite e namespace mem0, com reancoragem legada e testes de isolamento |
| Versao 3.4 - Ruminação relacional | 21/08/2026 | Isola fragmentos, tensões, insights e entrega da ruminação por Relation, habilita ingestão de participantes registrados e adiciona testes de não mistura |
| Versao 3.5 - Catalogo de conectores orientado pela vontade | 28/08/2026 | Registra o catalogo futuro de conexoes por saber, expressar e relacionar, a preferencia por MCP e os gates de descoberta, aprovacao, escopo e execucao |
| Versao 3.6 - Fundacao relacional do WILL | 03/09/2026 | Registra o escopo por instancia/Relation para vontade e pressao, a agregacao global sem texto bruto e a sequencia de autonomia volitiva antes do piloto multiusuario |
| Versoes 3.7/3.8 - Expressoes e arbitragem WILL-ciclo | 04/09/2026 | Registra a infraestrutura dos cortes 7/8; a revisao 3.9 explicita as garantias de ponta a ponta ainda abertas |
| Versao 3.9 - Fechamento Volitivo e Direcao Multi-relacional | 05/09/2026 | Aprova C9-C13, distingue implementacao de validacao, registra lacunas da revisao local, amplia expressao para texto e prioriza multi-instancia, API minima e conector textual apos os gates |
| Versao 3.10 - C9a: Confirmacao de Entrega e Alivio Idempotente | 05/09/2026 | Registra implementacao local e 497 testes aprovados, sem deploy; mantem C9 aberto com C9b/C9c explicitos |
| Versao 3.11 - C9b: Evidencia Equivalente e Consumo Transacional | 06/09/2026 | Registra commits locais C9a/C9b, adaptador world, hobby inelegivel sem evidencia propria, 538 testes e 20 cenarios mock aprovados, e proximo C9c sem liberar producao |

---

*Mantido pelo consultor estrategico, sob aprovacao do mantenedor. Atualizar a Secao 4 (estado) a cada merge relevante e o backlog ao final de cada tarefa concluida.*
