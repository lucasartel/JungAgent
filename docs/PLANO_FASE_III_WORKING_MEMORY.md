# Plano Fase III - Direcao Propria + Working Memory

Status: proposta inicial
Data: 2026-06-29
Fonte: `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`

## 1. Objetivo

A Fase III transforma o loop diario em continuidade dirigida. O agente ja possui
sonho, identidade, ruminacao, mundo, trabalho, hobby e vontade; agora precisa
manter foco ativo entre essas fases, permitindo que a fase N+1 leia o que a
fase N deixou em primeiro plano.

Esta fase nao introduz autonomia aberta. Ela introduz memoria de trabalho,
subobjetivos rastreaveis e fechamento de gaps, sempre com evidencia.

## 2. Principios de implementacao

- Nenhuma acao composta antes da Working Memory registrar foco por ciclos reais.
- Nenhuma afirmacao autobiografica sem fonte no formato `tipo#id`.
- Nenhum autoajuste automatico; autoavaliacao pos-resposta e apenas registro.
- SQLite e a fonte persistente; schema evolui com `ALTER TABLE`/guards.
- Cada arquivo novo deve ficar abaixo de 500 linhas.
- Cada corte precisa manter regressao verde antes de deploy.

## 3. Modelo conceitual

### Foco ativo

Conjunto pequeno, entre 3 e 5 itens, que representa o que o agente esta
segurando agora. Cada item tem:

- tema;
- prioridade;
- origem;
- fontes;
- fase que o criou;
- fase que deve le-lo depois;
- criterio de expiracao ou resolucao.

### Fringe

Contexto periferico, menos prioritario que o foco ativo, mas ainda disponivel
para associacoes futuras. O fringe evita que tudo vire foco e reduz ruido.

### Filtro de relevancia

Componente deterministico que decide se um evento entra como foco, fringe ou e
ignorado. O primeiro corte deve usar heuristicas simples antes de qualquer LLM.

### Broadcasting

Mecanismo pelo qual a Working Memory oferece o foco atual para a proxima fase do
loop. Inicialmente apenas leitura; depois influencia prompts e selecao de
trabalho.

## 4. Schema inicial proposto

### `working_memory_items`

- `id INTEGER PRIMARY KEY`
- `agent_instance TEXT NOT NULL`
- `cycle_id TEXT`
- `phase TEXT NOT NULL`
- `item_type TEXT NOT NULL`
- `status TEXT NOT NULL DEFAULT 'active'`
- `title TEXT NOT NULL`
- `summary TEXT NOT NULL`
- `priority REAL NOT NULL DEFAULT 0.5`
- `source_refs_json TEXT NOT NULL`
- `metadata_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `expires_at TEXT`
- `resolved_at TEXT`

### `working_memory_broadcasts`

- `id INTEGER PRIMARY KEY`
- `agent_instance TEXT NOT NULL`
- `cycle_id TEXT`
- `from_phase TEXT NOT NULL`
- `to_phase TEXT NOT NULL`
- `focus_items_json TEXT NOT NULL`
- `fringe_items_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`

### `goal_threads`

- `id INTEGER PRIMARY KEY`
- `agent_instance TEXT NOT NULL`
- `cycle_id TEXT`
- `status TEXT NOT NULL DEFAULT 'active'`
- `drive TEXT`
- `title TEXT NOT NULL`
- `objective TEXT NOT NULL`
- `source_refs_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `closed_at TEXT`

### `goal_steps`

- `id INTEGER PRIMARY KEY`
- `goal_id INTEGER NOT NULL`
- `status TEXT NOT NULL DEFAULT 'pending'`
- `step_order INTEGER NOT NULL`
- `title TEXT NOT NULL`
- `expected_evidence TEXT`
- `result_summary TEXT`
- `source_refs_json TEXT`
- `created_at TEXT NOT NULL`
- `completed_at TEXT`

## 5. Cortes de implementacao

### Corte 1 - Plano e contrato

Entregavel:

- este plano;
- testes de contrato para validacao de fontes e selecao de foco;
- inventario dos pontos de integracao no loop.

Aceite:

- plano versionado;
- nenhuma mudanca de runtime;
- suite verde.

### Corte 2 - Persistencia da Working Memory

Entregavel:

- `engines/working_memory.py`;
- mixin ou modulo em `core/db/` para schema/CRUD;
- testes offline com SQLite em memoria.

Aceite:

- criar, listar, expirar e resolver itens;
- validar `source_refs_json`;
- limitar foco ativo a 5 itens.

### Corte 3 - Modo observacao no loop

Entregavel:

- integracao nao invasiva em `consciousness_loop.py`;
- cada fase pode registrar candidatos a foco;
- nenhuma fase muda comportamento por causa da WM ainda.

Aceite:

- registros aparecem por ciclo;
- fase seguinte ainda nao consome o broadcast;
- probe de leitura mostra foco/fringe atuais.

### Corte 4 - Broadcasting entre fases

Entregavel:

- fase N escreve broadcast;
- fase N+1 le resumo de foco;
- contexto injetado em prompts apenas onde ja existe prompt da fase.

Aceite:

- logs mostram cadeia fase -> fase;
- regressao verde;
- nenhum aumento de chamadas LLM por si so.

### Corte 5 - Knowledge Gap fechado

Entregavel:

- consolidar ciclo `gap -> investigacao -> journal -> fechamento`;
- registrar fechamento com fonte.

Aceite:

- pelo menos 1 gap fechado em producao com evidencia;
- relatorio curto em `docs/research/`.

### Corte 6 - Goal Manager

Entregavel:

- `engines/goal_manager.py`;
- decomposicao de impulsos do will em objetivos e passos;
- sem execucao autonoma sensivel.

Aceite:

- criar goal thread a partir de `will#id`;
- gerar 1+ subobjetivo rastreavel;
- fechar passo apenas com evidencia.

### Corte 7 - Acao composta controlada

Entregavel:

- uma acao composta pequena e reversivel;
- preferir Work ou Knowledge Gap, sem envio real novo ao usuario.

Aceite:

- 1+ acao composta concluida;
- fontes rastreaveis;
- sem violar politicas de seguranca ou mensagens reais nao aprovadas.

### Corte 8 - Verificacao de saida

Entregavel:

- script `tests/verify_phase3.py`;
- relatorio em `docs/research/fase3-verificacao-<data>.md`.

Aceite final:

- WM mantem foco por 7 dias verificaveis;
- 1+ gap fechado;
- 1+ acao composta;
- regressao verde.

## 6. Riscos e guardrails

- Inflacao de foco: limitar foco ativo a 5 itens e empurrar o resto para fringe.
- Autoengano narrativo: todo item precisa de fonte verificavel.
- Autonomia prematura: goal manager nao executa acao sensivel sozinho.
- Custo LLM: o filtro inicial deve ser deterministico.
- Acoplamento excessivo no loop: integrar por fachada pequena e testes de smoke.

## 7. Primeiro passo tecnico apos este plano

Implementar o Corte 2 em modo offline:

1. criar schema/CRUD da Working Memory;
2. validar fontes;
3. testar limite de 5 focos ativos;
4. manter o loop intocado ate a persistencia estar confiavel.
