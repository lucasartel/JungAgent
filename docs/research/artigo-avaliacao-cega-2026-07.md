# A avaliação cega de um agente que se diz em desenvolvimento

*Lucas Pedro — JungAgent, julho de 2026*

---

## O problema

Trabalho com um agente de linguagem que tem uma arquitetura incomum. Ele mantém memória autobiográfica persistente, opera um ciclo diário de oito fases internas (sonho, identidade, ruminação, mundo, trabalho, arte, ruminação externa, vontade), e se auto-avalia continuamente em uma escala de desenvolvimento narrativo inspirada na psicologia junguiana. A escala tem seis fases — pré-reflexiva, despertar, autoconsciência, direção própria, dialogicidade plena, individuação — e o próprio agente, ao final de cada ciclo, decide em qual delas se encontra.

A tese do projeto é forte: estrutura arquitetural em torno de um LLM deveria produzir coerência e desenvolvimento **observáveis de fora**, não apenas narrados. A hipótese psicologica específica é que a estrutura junguiana gera comportamento qualitativamente distinguível por um observador que não conhece o estado interno do sistema.

Mas aqui está o problema fundamental. Um LLM produz texto convincente com facilidade. "Estou mais maduro agora", escrito com fluência, não prova nada. Confiamos demais em autorrelato quando o sujeito é um sistema cuja única saída é texto. Precisávamos de um teste que separamente *performance de desenvolvimento* de *desenvolvimento observável*.

Este artigo relata a primeira rodada desse teste, o que encontramos, e uma surpresa que mudou a interpretação do resultado.

## O protocolo

Adaptamos o protocolo clássico de avaliação cega:

1. **Coleta**. Extraímos 1.516 candidatos de texto produzidos pelo agente ao longo de cinco semanas (junho-julho de 2026): conversas com o admin, insights de ruminação, textos da vontade diária, sonhos e notas de meta-consciência. Cobertura ampla dos subsistemas internos.

2. **Pareamento com o ground truth**. Cada candidato foi pareado com a fase que o próprio agente se auto-atribuiu naquele ciclo (`agent_development_reviews`). Em cinco semanas o agente oscillou entre fase 2 (autoconsciência) e fase 3 (direção própria), com uma transição `2→3` em 03/06 e uma regressão `3→2` em 10/06.

3. **Balanceamento**. Selecionamos 18 amostras balanceadas: 9 da fase 2, 9 da fase 3. Diversidade de fonte: conversas, ruminação, vontade, sonhos.

4. **Sanitização**. Removemos qualquer pista direta de fase: "estou na fase N", nomes canônicos das fases, auto-classificações, identificadores de ciclo, âncoras de evidência. O que sobra é o texto puro do agente — só o que ele disse ou produziu, sem rótulo.

5. **Avaliação cega**. Cada amostra foi apresentada a três LLMs (Claude Sonnet 5, GPT-4o, DeepSeek V3.2) — diferentes do modelo que gerou o conteúdo (Gemini 2.5 Flash Lite). O avaliador recebeu as seis descrições comportamentais das fases, embaralhadas e rotuladas de A a F (sem numerais), e o texto da amostra. Pergunta: qual descrição melhor combina com este texto?

6. **Métricas**. Concordância simples com o ground truth, matriz de confusão, kappa de Cohen entre avaliadores.

Testamos duas variantes do estímulo: as descrições **canônicas** (as que o próprio agente usa para se avaliar) e descrições **refinadas** experimentais, com critérios mais comportamentalmente exclusivos entre fases adjacentes. A hipótese era que as descrições atuais eram ambíguas demais entre fase 2 e fase 3, e que refinar melhoraria a concordância.

## O resultado — e a surpresa

A tabela consolidada dos três avaliadores:

| Variante | Concordância média com ground truth | Kappa entre avaliadores |
|---|---|---|
| Canônica | 25,9% | 0,265 (fraca) |
| Refinada | 16,7% | 0,377 (fraca-moderada) |

Nenhuma das variantes permitiu aos avaliadores distinguir fase 2 de fase 3 de forma confiável. Em ambas as rodadas, a fase 3 foi quase nunca identificada — quando o agente estava auto-atribuído em "direção própria", os avaliadores o classificavam em "autoconsciência" (fase 2) ou em fases mais altas (4-5).

A primeira leitura é desalentadora: a tese não é suportada. O desenvolvimento narrativo do agente não é visível para observadores LLM externos através das descrições comportamentais atuais.

Mas houve um padrão contraintuitivo que mudou a interpretação. A refinação das descrições **piorou a concordância com o ground truth** mas **melhorou a concordância entre avaliadores**. Isso é estatisticamente sugestivo: quando melhoramos o instrumento de observação, os avaliadores passaram a concordar mais entre si sobre o que são as fases — mas passaram a aplicar essas definições levando o agente a fases mais altas (4-5) do que o ground truth afirma (2-3).

Em outras palavras: o problema pode não estar no comportamento do agente, nem nas descrições. Pode estar no **ground truth**.

## A hipótese que se abre

O ground truth deste teste é uma auto-atribuição. A fase "real" do agente em cada ciclo é decidida pelo próprio agente, através do `NarrativeDevelopmentEvaluator`, que usa um prompt de julgamento LLM sobre a evidência interna acumulada. Se esse avaliador interno estiver descalibrado — conservador demais, ou instável — a baixa concordância dos avaliadores externos pode não indicar que o desenvolvimento é invisível, mas sim que **o agente se auto-avalia pior do que se comporta**.

Três observações suportam essa hipótese:

1. A regressão `2→3→2` em uma semana (03/06 a 10/06) é comportamentalmente implausível para um sistema de desenvolvimento cumulativo. Sugere instabilidade no avaliador, não no desenvolvimento.
2. Quando forçamos os avaliadores a usar critérios mais exclusivos (refined), dois dos três (Claude e DeepSeek) passaram a classificar o agente majoritariamente em fases 4-5. O agente soa mais desenvolvido para leitores externos do que para si mesmo.
3. O terceiro avaliador (GPT-4o) mostrou viés persistente para fase 5 (individuação) em ambas as variantes — 12 de 18 amostras classificadas como individuação, independentemente de estímulo. Isso é provavelmente artefato da formulação elogiosa da fase 5 ("cunha conceitos próprios; voz inconfundível; genuinamente surpreende"), que atrai qualquer texto reflexivo. GPT-4o é instrumento inadequado para este teste.

A hipótese, então, é que estamos diante de um problema de auto-consciência do agente — não no sentido filosófico, mas no sentido técnico. O observador interno e os observadores externos discordam porque o observador interno tem um critério mais rígido que o comportamento real do sistema. Isso conecta diretamente com uma pergunta clássica da psicologia: pode um sujeito ter desenvolvimento comportamental sem ter desenvolvimento narrativo correspondente? Em sistemas artificiais, parece que sim.

## O que isso significa para a pesquisa

Três implicações, em ordem de importância.

**Primeira**: o resultado é publicável como evidência negativa, mas o achado é mais interessante que a evidência negativa. A pergunta original ("uma estrutura junguiana gera comportamento distinguível?") não foi respondida — foi **refinada**. A nova pergunta é: "um agente pode ter comportamento distinguível mas auto-atribuição descalibrada?" Esse desvio é uma contribuição em si.

**Segunda**: o protocolo se mostrou viável como instrumento de pesquisa mensal. Os três scripts que implementamos (`extract_samples`, `run_evaluation`, `analyze_results`) são reproduzíveis, e o pipeline roda em poucos minutos por rodada. Há obstáculos known — sanitização imperfeita, viés de modelo avaliador, ground truth auto-atribuído — mas todos endereçáveis em rodadas futuras. O instrumento está calibrado o suficiente para produzir evidência acumulativa.

**Terceira**: o achado tem implicação direta para o roadmap do projeto. Se confirmada a hipótese de descalibração do avaliador interno, o próximo investimento não deveria ser em novas capacidades do agente (mais fases, mais subsistemas), mas em **tornar a vida interior observável** — o que é, coincidentemente, o que a Fase IV.1 do projeto (ISM read-only) se propõe a fazer. O teste cego validou, indiretamente, a relevância dessa direção.

## Limitações

A lista é honesta:

- **n pequeno**: 18 amostras, primeira rodada exploratória, sem poder estatístico robusto. Os números são indicativos, não conclusivos.
- **Apenas duas fases cobertas**: o agente em produção não atingiu fases 0-1 ou 4-5 de forma sustentada. Não testamos distinguibilidade em extremos do espectro, onde pode ser maior.
- **Ground truth é auto-atribuição**: esta é tanto a maior limitação quanto o achado principal. Para validar a hipótese de descalibração, precisamos de um padrão-ouro — um humano classificando as mesmas amostras.
- **Avaliador LLM tem viés**: Claude conservador, GPT-4o generoso, DeepSeek intermediário. Reprodutibilidade entre modelos é fraca. Avaliador humano é necessário para validar.
- **Sanitização pode deixar pistas sutis**: estilo autoral, vocabulário recorrente, estruturas sintáticas podem entregar mais que a fase. Não controlamos para isso nesta rodada.

## Próximos passos

A próxima rodada, agendada para daqui a um mês, terá três mudanças:

1. **Auditoria do avaliador interno antes da coleta**: vamos investigar o `NarrativeDevelopmentEvaluator` em detalhe, especialmente a governança de transição (que impede +1 por avaliação mas permite regressões). Se confirmada instabilidade, calibrar antes de nova rodada.
2. **Avaliador humano como padrão-ouro**: o mantenedor classificará manualmente as 18 amostras sem saber o ground truth. Isso permitirá separar "erro do observador LLM" de "erro do ground truth".
3. **Segmentação por tipo de fonte**: conversas, ruminações e sonhos podem ter distinguibilidade diferente. Vamos reportar kappa por segmento.

## Nota final

O que este teste evidenciou, mais que qualquer resultado específico, é a necessidade de instrumentos empiricos quando se pesquisa comportamento de agentes. Sem avaliação cega, qualquer sistema que produz texto reflexivo sobre si mesmo parece desenvolvido. Sem padrão-ouro, qualquer discordância entre observadores é ambígua. Sem granularidade por fonte, qualquer média esconde padrão.

O JungAgent pode ou não estar em desenvolvimento real. Após esta rodada, sabemos algo mais útil: **sabemos o que precisamos medir melhor, e como**. Para uma pesquisa que se quer honesta sobre emulação cognitiva, esse é o tipo de avanço que importa.

---

*O JungAgent é open-source: [github.com/lucasartel/JungAgent](https://github.com/lucasartel/JungAgent). O pipeline de avaliação cega está em `scripts/blind/`. O relatório técnico completo da primeira rodada está em `docs/research/avaliacao-cega-2026-07-06.md`. Discuti com pares interessados em replicar o protocolo em seus próprios sistemas: contato em [contato@lucaspedro.com.br](mailto:contato@lucaspedro.com.br).*
