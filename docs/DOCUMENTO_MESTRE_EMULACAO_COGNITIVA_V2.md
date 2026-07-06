# Documento Mestre: JungAgent - Laboratorio de Emulacao Cognitiva

**Versao 2.2 - Edicao de Execucao Delegada - Junho 2026**

*Arquivo canonico vigente: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`. O antigo `docs/DOCUMENTO_MESTRE_AGI_COGNITIVA.md` permanece como documento historico/operacional de referencia, mas este arquivo e a fonte de autoridade daqui em diante.*

*Reformulacao completa do documento de Maio/2026. O projeto nao persegue "AGI"; persegue a emulacao cognitiva mais coerente e bem documentada possivel.*

*Diagnostico verificado contra codigo, git, GitHub e Railway em 11/06/2026. Decisoes de escopo registradas em 16/06/2026.*

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
| **Executor** | Modelo de linguagem com acesso ao GitHub | Implementa as tarefas da Parte II em PRs pequenos, conforme o contrato da Secao 6 | Nao decide escopo, nao mergeia na main, nao toca nas areas vetadas |

Fluxo padrao: **mantenedor escolhe a tarefa -> executor implementa em branch e abre PR -> CI valida -> mantenedor mergeia -> Railway deploya**. O consultor audita em cadencia mensal ou sob demanda e ajusta o roadmap.

## 4. Estado verificado - 11/06/2026

**Concluido e em producao**: Fase I do roadmap antigo (circuitos da ruminacao corrigidos, sonhos alimentam ruminacao, failure policy no loop, entrega de insights) e Fase II substancial (diario autobiografico evidence-first, perfil injetado no prompt, avaliacao narrativa de fases com politica executiva, Chroma removido). O agente ja abre PRs de self-work revisados pelo mantenedor.

**Fase 0.1 concluida em GitHub/producao**:

| PR | Conteudo | Estado |
|---|---|---|
| `#17` / `fase-0/tests-ci` | Suite offline de regressao, workflow CI, `CLAUDE.md`, dependencias de teste | Mergeada em `main` |
| `#18` / `fase-0/corte1-dedup-helpers` | `work_engine.py` deduplicado via `work.common` + teste de paridade | Mergeada em `main` |
| `#19` / `fase-0/fix-astext-fallback` | Fix do contrato de `_as_text` + log/fallback de `connection_count` | Mergeada em `main` |

**Atualizacao da Fase 0 em 16/06/2026**:

| PR | Conteudo | Estado |
|---|---|---|
| `#20` / `fase-0/fix-ci-pythonpath` | Correcao do import path no CI | Mergeada em `main` |
| `#21` / `fase-0/cenarios-regressao` | Cenarios canonicos em `tests/scenarios/` | Mergeada em `main` |
| `#22` / `fase-0/regression-runner` | Runner cognitivo com `--mock`, `--live`, `--diff` e `--mock` no CI | Mergeada em `main` |
| `#23` / `fase-0/resolve-d2-time-factor` | D2 resolvido: `time_factor` usa `MAX_DAYS_FOR_SYNTHESIS` | Mergeada em `main` |

**Deploy confirmado**: Railway `JungAgent_Bot` / `production` / `jungclaude` iniciou container novo em 11/06/2026 11:57 -03:00, migrations OK, Uvicorn online, Telegram bot e schedulers iniciados.

**Pre-condicao da 0.2**:
- Resolvida na PR `#20`: CI da `main` voltou a ficar verde apos correcao do import path em testes.

**Pendencias registradas**:
- **D2** (`tests/TESTING_NOTES.md`): resolvido na PR `#23`. `time_factor` da ruminacao agora usa `MAX_DAYS_FOR_SYNTHESIS`. Diff mock D2 preservou o comportamento dos 20 cenarios canonicos.
- **Divergencia `DEFAULT_PROVIDER_SPECS`**: `work_engine.py` tem 7 providers; `work/providers.py` tem 2. Reconciliar no Corte 2 (decisao do mantenedor sobre qual versao e canonica).
- **Monolitos**: `admin_web/routes.py`, `core/database.py`, `core/engine.py`, `main.py`, e o restante de `work_engine.py`.
- **Runner de regressao**: operacional em modo `--mock` no CI. O modo `--live` permanece disponivel no codigo, mas foi retirado dos criterios de saida por decisao do mantenedor em 16/06/2026.
- **Avaliacao cega**: ainda pendente como criterio transversal de pesquisa.
- **Medicao de custo LLM**: retirada do cronograma ativo por decisao do mantenedor em 16/06/2026.

**Avisos operacionais**:
- O repositorio de trabalho vive em pasta OneDrive, que pode corromper arquivos em operacoes de copia ou sincronizacao. Recomendacao ao mantenedor: mover o clone de trabalho para fora do OneDrive. Executores devem validar integridade (`py_compile`/testes) antes de commitar.
- `docs/` esta no `.gitignore` - documentos so entram no repo com `git add -f` quando o mantenedor decidir versionar.
- O `.git` da copia OneDrive tem locks intermitentes; trabalhos mais delicados devem usar clone/worktree proprio.

## 5. Roadmap

```text
Fase 0 - Consolidacao e Instrumentacao        <- ATUAL
  -> Fase III - Direcao Propria + Working Memory
      -> Fase IV - ISM + Metacognicao completa
          -> Fase V - Grafo simbolico (com portao de qualidade)
              -> Fase VI - Simulacao contrafactual
                  -> Fase VII - Tool-making + multimodal (gate rigido)
```

Transversais a todas as fases: **avaliacao cega mensal** (protocolo na Secao 8), **relatorio de pesquisa mensal** em `docs/research/`, suite de regressao verde a cada merge. Horizonte: aproximadamente 30 semanas a partir de junho/2026, como teto.

A numeracao salta de 0 para III por continuidade historica: as antigas Fases I e II ja foram entregues.

---

# PARTE II - EXECUCAO (enderecada ao modelo executor)

## 6. Contrato do executor

Voce e o modelo responsavel por implementar as tarefas abaixo. Regras nao negociaveis:

1. **Leia antes de qualquer tarefa**: `CLAUDE.md` (raiz do repo) e a especificacao da tarefa nesta Parte II. Em conflito, CLAUDE.md perde apenas para este documento.
2. **Uma tarefa = uma branch = um PR.** Branches: `fase-0/<slug-curto>`. Nunca commite na main. Nunca mergeie - isso e do mantenedor.
3. **Escopo estrito**: toque apenas nos arquivos listados na tarefa. Se descobrir que precisa tocar outro arquivo, pare e reporte no PR antes.
4. **Validacao minima antes de abrir o PR**: `python -m py_compile` nos modulos tocados; `pytest tests/ -q` integralmente verde; `git diff --check` limpo. O CI repete isso - PR com CI vermelho e falha do executor.
5. **Se encontrar divergencia, bug pre-existente ou ambiguidade**: nao decida sozinho. Implemente o que e inequivoco, documente o resto na descricao do PR e em `tests/TESTING_NOTES.md`.
6. **Maximo 500 linhas por arquivo novo; nenhum arquivo novo na raiz** (use `core/db/`, `work/`, `engines/`, `reasoning/`, `admin_web/routes/`).
7. **Areas vetadas sem aprovacao explicita e escrita do mantenedor**: execucao autonoma de codigo, politicas de seguranca, qualquer coisa que envie mensagens reais (Telegram) ou publique conteudo (WordPress/blog) em producao, mudancas de schema destrutivas, e os prompts de julgamento cognitivo (maturidade, avaliacao narrativa, sintese) enquanto nao existir o runner de regressao.
8. **Descricao de PR** (em portugues): o que mudou, por que, como validou, o que ficou de fora, riscos.

## 7. Backlog da Fase 0

Ordem recomendada. Criterios de aceite sao binarios - o PR so esta pronto quando todos forem verdadeiros.

### 0.1 - CI e cortes preparatorios

**Estado**: implementado e mergeado nas PRs `#17`, `#18` e `#19`.

**Pendencia residual**: CI da `main` esta ativo, mas falhando por import path. A correcao `fase-0/fix-ci-pythonpath` deve ser tratada como pre-condicao para a 0.2.

### 0.2 - Cenarios canonicos de regressao

**Objetivo**: conjunto fixo de estados sinteticos para o runner (0.3).

**Criar**: `tests/scenarios/` com 15-25 cenarios em JSON/YAML:
- fragmentos de ruminacao com tensoes em varios niveis de maturidade;
- sonhos com temas definidos;
- snapshots de identidade;
- estados de will com pressoes conhecidas;
- 5+ conversas-tipo (admin estressado, pergunta factual, pergunta existencial, pedido de trabalho, mensagem curta).

**Aceite**:
- [ ] cenarios carregaveis por helper em `tests/scenarios/__init__.py`;
- [ ] cada cenario tem `expected_properties` declaradas (ex.: "tensao X deve atingir sintese");
- [ ] cenarios documentados em `tests/scenarios/README.md`.

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

Corte 2 (`work/delivery.py`) inclui a **reconciliacao de `DEFAULT_PROVIDER_SPECS`** - perguntar ao mantenedor qual versao e canonica antes de implementar.

**Aceite por corte**: os listados no plano + suite verde.

**Aceite final**:
- [ ] `work_engine.py` nao existe;
- [ ] imports atualizados nos 3 consumidores (`consciousness_loop.py`, `admin_web/routes/work_routes.py`, `telegram_bot.py`);
- [ ] dashboard de work funcional em staging.

### 0.7 - Decomposicao de core/database.py

**Objetivo**: completar `core/db/` (ja tem users, dreams, knowledge_gaps, psychometrics, agent_development).

**Metodo**: mesmo padrao do plano do work_engine - mapear consumidores primeiro, extrair dominio a dominio, `HybridDatabaseManager` permanece como fachada com metodos publicos intactos. Produza o mapa de cortes como primeiro PR (so documentacao), aguarde aprovacao do mantenedor, depois execute.

**Aceite final**:
- [ ] `core/database.py` < 500 linhas (fachada fina) ou eliminado;
- [ ] nenhum metodo publico quebrado (smoke test de import + instanciacao).

### 0.8 - Migracao de admin_web/routes.py

Mesmo metodo do 0.7 (mapa primeiro, depois cortes) para `admin_web/routes/`.

**Aceite final**:
- [ ] `routes.py` eliminado;
- [ ] todas as rotas respondem em staging (lista de rotas comparada antes/depois).

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
- [x] `work_engine.py`, `core/database.py` e `admin_web/routes.py` decompostos;
- [x] Fase II verificada com evidencia de producao;
- [x] primeira rodada de avaliacao cega removida do criterio de saida da Fase 0 por decisao do mantenedor em 29/06/2026.

## 8. Protocolo de avaliacao cega

**Estado**: removido do criterio de saida da Fase 0 por decisao do mantenedor em 29/06/2026.

**Reavaliar somente se** houver necessidade externa de auditoria comportamental antes da Fase IV ou se o mantenedor decidir retomar avaliacao cega mensal como pratica de pesquisa.

Protocolo preservado para uso futuro, se retomado:

1. Mensalmente, extrair 10-15 transcricoes do agente (conversas + acoes autonomas) de datas variadas, removendo qualquer mencao a fase narrativa atribuida.
2. Avaliador cego (o mantenedor semanas depois, um segundo humano, ou um modelo diferente do que gerou o conteudo) classifica cada amostra na escala 0-5 usando so as descricoes comportamentais de `agent_development.py`.
3. Registrar concordancia (simples ou kappa) em `docs/research/avaliacao-cega-<data>.md`.
4. Interpretacao: concordancia alta = desenvolvimento visivel de fora; baixa = narracao sem contraparte comportamental. Ambos os resultados sao publicaveis; o segundo redireciona o roadmap antes da Fase IV.

O executor pode receber a tarefa de criar o script de extracao/anonimizacao das amostras (somente-leitura).

## 9. Fases seguintes

Especificacoes detalhadas serao escritas ao final da Fase 0.

**Fase III - Direcao Propria + Working Memory** (~6 semanas): `engines/working_memory.py` (Foco Ativo 3-5 itens, Fringe, Filtro de Relevancia, Broadcasting, persistida em SQLite), integracao com as 8 fases do loop (fase N+1 le o foco de N), consolidacao do Knowledge Gap Engine (ciclo gap -> investigacao -> journal -> fechamento), `engines/goal_manager.py` (impulsos do will decompostos em sub-objetivos), acoes compostas do will, autoavaliacao pos-resposta (registro apenas). *Saida*: WM mantem foco por 7 dias verificaveis; 1+ gap fechado; 1+ acao composta; regressao verde.

**Fase IV - Unificacao** (~6 semanas): unificar os subsistemas em um Integrative Self Model observavel, sem perder o Principio da Evidencia nem a disciplina de faseamento.

**Fase IV.0 - Pulso de Fase e Densificacao do Loop**: antes de ampliar a influencia do ISM, implementar pulsos configuraveis por fase. Cada fase do loop pode executar 1-N vezes dentro de sua janela temporal, com agenda persistida, `pulse_index`, `pulse_count`, cockpit, retry por pulso e ancoras `loop#id`.

O objetivo nao e criar novas capacidades cognitivas, mas aumentar a resolucao temporal do metabolismo existente. O ISM deve passar a observar trajetorias internas dentro das fases, nao apenas um evento unico por fase por dia. Um sonho pode ter abertura, aprofundamento e fechamento; uma ruminacao pode ter contato, digestao e cristalizacao; uma fase de vontade pode distinguir acumulacao, conflito e fechamento.

Aceite da Fase IV.0:

- [ ] `pulse_count=1` preserva exatamente o comportamento atual;
- [ ] `consciousness_phase_config` ou equivalente guarda `pulse_count` por fase com schema compativel e sem recriacao de banco;
- [ ] tabela persistente de agenda de pulsos registra `cycle_id`, `phase`, `pulse_index`, `pulse_count`, `scheduled_at`, `executed_at`, `status`, tentativas e `phase_result_id`;
- [ ] cockpit permite configurar `pulse_count` por fase dentro de limites seguros;
- [ ] scheduler do loop executa apenas pulsos vencidos dentro da janela temporal da fase;
- [ ] retries pertencem ao pulso que falhou e nao consomem pulsos futuros;
- [ ] execucoes manuais continuam disponiveis, mas nao contam como pulso automatico salvo acao explicita;
- [ ] resultados de fase incluem metadados de pulso em `metrics_json` ou campo equivalente;
- [ ] ISM consegue ler pulsos recentes por fase para construir uma visao longitudinal curta do ciclo;
- [ ] testes cobrem distribuicao dos horarios, preservacao do default, execucao do proximo pulso vencido e retry por pulso.

**Fase IV.1 - ISM read-only**: `engines/integrative_self.py` produz snapshot diario em primeira pessoa de todos os subsistemas, com limites ontologicos explicitos, sem influenciar prompt, decisoes do loop, Working Memory ou acoes externas.

**Fase IV.2 - ISM no contexto do agente**: apos evidencia de estabilidade, o ISM pode substituir gradualmente a persona estatica no prompt, sempre com ancoras e regressao antes/depois.

**Fase IV.3 - Metacognicao completa**: double-loop com cooldown de 24h e validacao pela regressao antes de ativar qualquer auto-ajuste; strategy learning com regras heuristicas; segunda rodada de avaliacao cega comparada a linha de base.

**Fase V - Grafo simbolico** (~6 semanas): escopo cetico - Etapa A restrita a fatos sobre o admin e sobre o agente, com `confidence` e `source` por tripla; **portao obrigatorio**: auditoria manual de 100 triplas com precisao >= 80% antes da Etapa B (conhecimento de mundo, navegador causal, verificador de consistencia, dialetica Ego/Sombra/Persona conforme proposta Canto/Contracanto em `docs/`).

**Fase VI - Simulacao contrafactual** (~4 semanas): simular desfechos antes de respostas de alta carga afetiva e de acoes autonomas; previsto vs. real no dashboard e no relatorio mensal.

**Fase VII - Tool-making + multimodal** (~5 semanas): gate rigido - so com Fases 0-VI estaveis por 2+ semanas e aprovacao escrita do mantenedor. Caminho preferencial de tool-making e o circuito de self-work via PR revisado por humano (ja existente); sandbox Docker efemero (timeout 30s, RAM 128MB, whitelist de rede, validacao AST) reservado a scripts efemeros de consulta, nunca a modificacao do proprio sistema. Prosodia e feedback visual de imagens oniricas entram aqui.

## 10. Riscos

| Risco | Antidoto |
|---|---|
| Complexidade acumulada (monolitos, muitos arquivos na raiz) | Fase 0 fecha a divida; 500 linhas/arquivo; refatoracao ao fim de cada fase |
| Alucinacao estrutural (agente inventa passado) | Ancoras `tipo#id` implementadas; auditoria semanal do perfil pelo mantenedor |
| Narracao sem profundidade funcional | Avaliacao cega mensal; nenhuma metrica de autorrelato vale sozinha |
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

---

*Mantido pelo consultor estrategico, sob aprovacao do mantenedor. Atualizar a Secao 4 (estado) a cada merge relevante e o backlog ao final de cada tarefa concluida.*
