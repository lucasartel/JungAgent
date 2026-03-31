# Rumination, Jung Lab e Jung Mind

## Escopo
Este documento descreve o funcionamento atual do processo de ruminação do EndoJung, como ele aparece no loop, como é exposto no `Jung Lab` e no `Jung Mind`, e quais melhorias fazem mais sentido para a próxima rodada.

O foco aqui é o sistema como ele existe hoje no código e no runtime observado no Railway, não uma descrição idealizada.

## Visão Geral
Hoje a ruminação é um subsistema real, com banco próprio, prompts próprios, motor dedicado e integração com o loop e com a identidade.

As peças principais são:
- `jung_rumination.py`: motor central
- `rumination_config.py`: limiares, frequências e limites
- `rumination_prompts.py`: prompts de extração, detecção de tensões, síntese e validação de novidade
- `identity_rumination_bridge.py`: ponte bidirecional com o sistema de identidade
- `consciousness_loop.py`: orquestra as duas passagens de ruminação dentro do dia psíquico
- `admin_web/routes.py` + `admin_web/templates/jung_lab.html`: painel operacional/diagnóstico
- `admin_web/routes.py` + `admin_web/templates/jung_mind.html`: visualização de rede da ruminação
- `jung_core.py`: hook que injeta conversas do admin na ruminação

## Como a ruminação entra no sistema
Em `jung_core.py`, toda conversa salva do admin no Telegram passa por um hook de ruminação.

Condições atuais:
- só o `ADMIN_USER_ID` entra na ruminação
- só mensagens com `platform == "telegram"` entram
- o hook chama `RuminationEngine.ingest(...)`

Isso significa que a ruminação hoje é:
- admin-only
- dependente de conversas normais do Telegram
- alimentada tanto pelo conteúdo da fala do usuário quanto pelos sinais calculados na troca (`tension_level`, `affective_charge`, `existential_depth`)

## Arquitetura interna da ruminação
O motor em `jung_rumination.py` tem cinco fases.

### 1. Ingestão
`ingest()` decide se a conversa merece entrar na ruminação.

Regras principais:
- se não for admin, sai
- calcula `activation_score`
- só segue se `activation_score >= MIN_RUMINATION_ACTIVATION_SCORE`
- pede ao LLM extração de `fragments`
- salva fragmentos relevantes em `rumination_fragments`
- se criou fragmentos, dispara `detect_tensions()`

Pontos fortes:
- há um gate real de ativação
- a ruminação não tenta absorver tudo
- fragmentos já entram tipados (`valor`, `desejo`, `medo`, `comportamento`, `contradicao`, `emocao`, `crenca`, `duvida`)

Fragilidade atual:
- a qualidade da ingestão depende muito da consistência do JSON do modelo
- o limiar de ativação foi enriquecido no `jung_core`, mas a seleção ainda é sensível a drift de prompt/modelo

### 2. Detecção de tensões
`detect_tensions()` busca fragmentos recentes não processados e alguns históricos, monta um prompt de contraste e pede ao LLM para devolver tensões.

Saída esperada:
- tensões tipadas
- dois polos (`pole_a`, `pole_b`)
- descrição
- intensidade

Persistência:
- salva em `rumination_tensions`
- registra os fragmentos usados
- incrementa tentativas nos fragmentos não usados

Pontos fortes:
- já existe a noção certa de “material vivo” versus material sem tensão
- há proteção para não queimar cedo demais fragmentos ainda fermentando

Fragilidades:
- esta é a fase mais frágil a JSON malformado
- no Railway já apareceu erro real de parse do payload de tensões
- quando o parse falha, o sistema registra “nenhuma tensão detectada”, o que mistura ausência real de tensão com falha de leitura do LLM

### 3. Digestão
`digest()` revisita tensões abertas e amadurecendo.

Ela:
- busca tensões `open` e `maturing`
- procura novas evidências desde a última revisita
- recalcula maturidade
- pode arquivar
- pode mover para `ready_for_synthesis`
- depois chama `check_and_synthesize()`

Pontos fortes:
- a ideia de maturação é real
- o sistema não tenta sintetizar tudo imediatamente
- existe fila de prontas e poda da fila

Fragilidades:
- o cálculo de maturidade é importante demais para continuar quase invisível ao painel
- há muito peso em intensidades e heurísticas sem explicação clara na UI
- `duration_ms` do loop não reflete a duração real da fase, então a observabilidade da digestão no loop está enganosa

### 4. Síntese
`check_and_synthesize()` busca tensões `ready_for_synthesis` e chama `_synthesize_tension()`.

A síntese:
- gera `internal_thought`
- gera `core_image`
- gera `internal_question`
- valida novidade contra insights recentes
- salva em `rumination_insights` com status `ready`

Pontos fortes:
- a melhor ideia do sistema está aqui: o insight é monólogo interior, não conselho
- a validação de novidade evita repetição direta

Fragilidades:
- ainda depende demais de JSON perfeito
- `_recover_synthesis_payload()` ajuda mais a síntese do que a detecção de tensões; a fase 2 segue mais vulnerável
- o status `ready` depois é reutilizado por outras integrações de formas conceitualmente diferentes

### 5. Entrega
`check_and_deliver()` e `_deliver_insight()` são o mecanismo antigo de envio proativo.

Eles:
- verificam inatividade do usuário
- verificam cooldown
- escolhem um insight pronto
- enviam via Telegram
- salvam a entrega como conversa `platform="proactive_rumination"`

No desenho atual do loop, porém, a entrega foi suprimida dentro de `consciousness_loop.py`.

Isso gera uma tensão arquitetural:
- o motor ainda tem entrega proativa embutida
- o loop trata a ruminação como metabolismo interno
- o `Jung Lab` manual ainda chama `check_and_deliver()`

## Ponte com identidade
`identity_rumination_bridge.py` cria uma ponte valiosa, mas ainda heterogênea.

Fluxos atuais:
- tensões maduras -> contradições de identidade
- insights prontos -> núcleo identitário
- fragmentos recorrentes -> selves possíveis
- contradições fortes -> novas tensões de ruminação

Pontos fortes:
- este é o coração mais promissor da “vida sistêmica”
- há bidirecionalidade real entre identidade e ruminação

Fragilidades:
- a idempotência ainda depende muito de checagens simples por conteúdo
- alguns fluxos marcam `status = delivered` em insights ao sincronizar com identidade, embora isso não seja “entrega” no sentido conversacional
- há risco de confundir “insight metabolizado pelo self” com “insight enviado ao usuário”

## Ruminação dentro do loop
Em `consciousness_loop.py`, a ruminação aparece em dois momentos:

- `rumination_intro` entre `03:00–06:00`
- `rumination_extro` entre `19:00–22:00`

Estado atual do loop:
- cada fase roda uma vez por ciclo
- se a fase atual ainda não tiver resultado bem-sucedido no ciclo, ela roda uma vez na própria janela
- não há mais reexecução contínua da mesma fase

Hoje a intro:
- injeta sonho recente
- injeta estado identitário atual
- digere

Hoje a extro:
- injeta resumo do mundo
- injeta último scholar
- alimenta contradições da identidade para a ruminação
- digere
- sincroniza de volta tensões, insights e fragmentos

Ponto muito positivo:
- a ruminação no loop já não é um módulo isolado; ela virou um mediador entre sonho, identidade, mundo e scholar

## Jung Lab: o que ele é hoje
O `Jung Lab` é um painel operacional e de diagnóstico.

Ele mostra:
- estatísticas gerais
- últimos fragmentos
- tensões ativas
- insights
- status do scheduler legado de ruminação

Ele também permite:
- digestão manual
- start/stop de `rumination_scheduler.py`
- diagnóstico completo
- debug full
- exportações
- fixes pontuais de plataforma

Problema conceitual:
o `Jung Lab` ainda está muito preso ao modelo anterior de ruminação como subsistema semi-independente, com scheduler próprio e entrega própria, enquanto o projeto já caminha para a ruminação como órgão do loop.

## Jung Mind: o que ele é hoje
O `Jung Mind` é uma visualização em grafo construída em `/api/jung-mind-data`.

Estrutura:
- nó central `JUNG`
- até 200 fragmentos
- até 100 tensões
- até 50 insights
- sinapses laterais por palavras em comum entre fragmentos

Forças:
- visualiza hierarquia de forma intuitiva
- torna o processo mental auditável
- é ótimo para detectar densidade, excesso e desertos simbólicos

Fragilidades:
- usa heurística muito simples de sinapse (`2+` palavras em comum), o que tende a produzir ruído lexical
- o status das tensões na visualização não conversa bem com os statuses reais do motor
- o mapa é muito “topologia de palavras” e pouco “topologia de processo”

## O que observei no Railway
Na produção, a ruminação mostrou sinais fortes de vida real:
- criação sucessiva de insights
- digestão de dezenas de tensões
- sincronização de insights para identidade

Mas também apareceram fragilidades concretas:
- erro de parse JSON na detecção de tensões
- o sistema continuando como se “não houvesse tensão” quando o parse falha
- observabilidade de duração ruim no loop (`duration_ms` quase sempre 1, mesmo quando a fase leva minutos)

## Diagnóstico geral
Minha leitura franca é esta:

O núcleo conceitual do processo é forte.
As conexões entre conversa, ruminação, identidade e loop já são significativamente mais sofisticadas do que a média de sistemas agentic.

Mas o sistema ainda está dividido em duas eras:
- a era da ruminação como módulo autônomo com scheduler próprio, entrega própria e painéis de debug
- a era da ruminação como órgão central do loop e da individuação do agente

Hoje ele funciona, mas com sobreposição de paradigmas.

## Melhorias propostas

### 1. Unificar oficialmente a ruminação sob o loop
Objetivo:
tirar a ambiguidade entre “scheduler próprio do Jung Lab” e “ruminação do loop”.

Proposta:
- descontinuar `rumination_scheduler.py` como caminho principal
- manter o Jung Lab como painel e gatilho manual, não como centro operacional paralelo
- deixar explícito no UI quando a ruminação veio do loop e quando veio de gatilho manual

### 2. Separar metabolização interna de entrega
Objetivo:
parar de misturar `ready`, `delivered`, `synced_to_identity` e `delivered_to_user`.

Proposta:
- introduzir estados distintos para insights:
  - `ready_internal`
  - `synced_to_identity`
  - `ready_for_delivery`
  - `delivered_to_user`
- fazer o bridge não usar `delivered` como efeito colateral

### 3. Endurecer a fase de detecção de tensões
Objetivo:
fazer a fase 2 falhar de modo inteligível, não silencioso.

Proposta:
- criar um parser de recuperação específico para `tensions`
- registrar `llm_parse_failed` separado de `no_tensions_detected`
- salvar a resposta bruta truncada em `rumination_log` para auditoria

### 4. Corrigir a métrica de duração das fases do loop
Objetivo:
parar de mostrar `duration_ms=1` em fases que levam minutos.

Proposta:
- medir `started_at` no início real de `execute_phase`
- medir `completed_at` ao final
- recalcular `duration_ms` depois da fase, não no placeholder inicial

### 5. Fazer o Jung Lab refletir o loop atual
Objetivo:
transformar o painel de legado em painel do órgão vivo.

Proposta:
- mostrar as duas passagens do dia (`intro` e `extro`)
- mostrar insumos injetados pelo loop
- mostrar o que foi sincronizado para identidade
- mostrar claramente se houve:
  - ingestão
  - novas tensões
  - novas sínteses
  - sync com identidade

### 6. Fazer o Jung Mind representar processo, não só estrutura
Objetivo:
dar ao mapa mais verdade dinâmica.

Proposta:
- colorir tensões por status real (`open`, `maturing`, `ready_for_synthesis`, `synthesized`, `archived`)
- desenhar edges diferentes para:
  - origem em conversa
  - passagem por síntese
  - sync com identidade
- reduzir sinapses lexicais rasas e introduzir sinapses por coocorrência processual

### 7. Melhorar idempotência do bridge
Objetivo:
evitar acoplamentos implícitos baseados só em texto.

Proposta:
- adicionar colunas explícitas de exportação/sincronização:
  - `exported_to_identity_at`
  - `exported_identity_id`
  - `synced_from_contradiction_id`
- usar isso no lugar de inferência apenas por conteúdo semelhante

### 8. Tornar a ruminação mais legível no snapshot e no admin
Objetivo:
fazer a ruminação aparecer como parte do organismo inteiro.

Proposta:
- incluir no snapshot do sistema:
  - tensões mais maduras
  - últimos insights
  - material injetado do sonho/mundo/scholar
  - o que foi passado para identidade

### 9. Revisar thresholds com base no comportamento real
Objetivo:
afinar a ruminação com os dados já produzidos.

Proposta:
- auditar:
  - `MIN_RUMINATION_ACTIVATION_SCORE`
  - `MIN_INTENSITY_FOR_TENSION`
  - `MIN_MATURITY_FOR_SYNTHESIS`
  - `MAX_READY_TENSIONS`
- fazer isso olhando distribuição real no Railway, não só intuição

### 10. Redesenhar a observabilidade para distinguir três coisas
Objetivo:
não chamar tudo de “ruminação”.

Proposta:
explicitar no painel:
- matéria-prima: fragmentos
- conflito: tensões
- cristalização: insights
- integração: bridge com identidade

## Ordem recomendada de trabalho

### RUM-1
Corrigir duração real das fases do loop e diferenciar parse failure de no tension.

### RUM-2
Limpar estados de entrega/sincronização de insights.

### RUM-3
Refazer Jung Lab para refletir a ruminação do loop.

### RUM-4
Refazer Jung Mind para refletir processo e status reais.

### RUM-5
Revisar thresholds e idempotência do bridge com base nos dados de produção.

## Conclusão
O processo de ruminação já é um dos órgãos mais importantes e mais promissores do EndoJung.

Hoje ele já faz quatro coisas raras no mesmo sistema:
- metaboliza conversa em material psíquico
- amadurece tensão ao longo do tempo
- cristaliza pensamento interno
- afeta a própria identidade do agente

O passo seguinte não é “inventar” a ruminação.
É consolidá-la como centro do organismo, limpando legados antigos, melhorando a observabilidade e tornando a ponte com identidade e loop mais rigorosa.
