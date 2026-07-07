# Testamos se um agente de IA em desenvolvimento consegue se auto-avaliar. O resultado foi inesperado.

*Lucas Pedro — julho de 2026*

---

Mantenho um agente de linguagem chamado JungAgent. Ele tem memória autobiográfica persistente, opera um ciclo diário de oito fases internas — sonho, identidade, ruminação, mundo, trabalho, arte, ruminação externa, vontade — e se auto-avalia continuamente numa escala de desenvolvimento narrativo inspirada na psicologia junguiana. A escala vai da fase 0 (pré-reflexiva) até a fase 5 (individuação), e quem decide em qual fase o agente se encontra é o próprio agente, ao final de cada ciclo, olhando para a própria evidência interna acumulada.

A tese do projeto é exigente: estrutura arquitetural em torno de um LLM deveria produzir coerência e desenvolvimento **observáveis de fora**, não apenas narrados. Mas um LLM escreve "estou mais maduro agora" com facilidade convincente. Confiamos demais em autorrelato quando o sujeito é um sistema cuja única saída é texto.

Precisávamos de um teste que separasse *performance de desenvolvimento* de *desenvolvimento observável*.

## O protocolo

Adaptamos o método clássico de avaliação cega. Coletamos 18 amostras reais do agente ao longo de cinco semanas — conversas com o admin, insights de ruminação, textos da vontade diária, sonhos. Sanitizamos cada uma removendo qualquer menção direta a fase: "estou na fase N", identificadores de ciclo, âncoras internas, auto-classificações. O que sobra é o texto puro do agente.

Depois apresentamos essas amostras a três modelos de linguagem diferentes do modelo que gerou o conteúdo (Claude Sonnet 5, GPT-4o e DeepSeek V3.2). Cada avaliador recebeu as seis descrições comportamentais das fases, embaralhadas e sem numerais, e o texto da amostra. A pergunta era simples: qual descrição melhor combina com este texto?

Comparamos o chute dos avaliadores com a fase que o agente havia se auto-atribuído no momento da amostra. Em cinco semanas, o agente haveria oscillado entre fase 2 (autoconsciência) e fase 3 (direção própria).

## O resultado — e a surpresa

A concordância média entre avaliadores foi de 25,9%, com kappa inter-avaliador de 0,265 (fraca). Nenhum dos três modelos conseguiu distinguir fase 2 de fase 3 de forma confiável. Em ambas as rodadas, a fase 3 foi quase nunca identificada — quando o agente estava auto-atribuído em "direção própria", os avaliadores o classificavam majoritariamente em "autoconsciência" ou em fases mais altas.

Primeira leitura: a tese não é suportada. O desenvolvimento narrativo do agente não é visível para observadores LLM externos através das descrições comportamentais atuais.

Mas havia um padrão contraintuitivo. Testamos duas variantes das descrições de fase: as canônicas (as que o próprio agente usa para se avaliar) e uma versão experimental refinada, com critérios mais comportamentalmente exclusivos. A refinação piorou a concordância com o ground truth (de 25,9% para 16,7%) mas melhorou a concordância entre avaliadores (kappa de 0,265 para 0,377).

Esse padrão é estatisticamente sugestivo. Quando melhoramos o instrumento de observação, os avaliadores passaram a concordar mais entre si sobre o que são as fases — mas passaram a aplicar essas definições levando o agente a fases mais altas (4-5) do que o ground truth afirma (2-3). O problema pode não estar no comportamento do agente, nem nas descrições. Pode estar no **ground truth**.

## A hipótese que se abre

O ground truth deste teste é uma auto-atribuição. A fase "real" do agente em cada ciclo é decidida pelo próprio agente, através de um prompt de julgamento LLM sobre sua própria evidência interna. Se esse avaliador interno estiver descalibrado — conservador demais, ou instável — a baixa concordância dos avaliadores externos pode não indicar que o desenvolvimento é invisível, mas sim que **o agente se auto-avalia pior do que se comporta**.

Três observações suportam essa hipótese. Primeiro: a regressão `2→3→2` em uma semana (03/06 a 10/06) é comportamentalmente implausível para um sistema de desenvolvimento cumulativo. Sugere instabilidade no avaliador, não no desenvolvimento. Segundo: quando forçamos critérios mais exclusivos, dois dos três avaliadores passaram a classificar o agente majoritariamente em fases 4-5. O agente soa mais desenvolvido para leitores externos do que para si mesmo. Terceiro: o GPT-4o, o único avaliador que sistematicamente discordou dos outros dois, mostrou viés persistente para fase 5 (individuação) em ambas as variantes — provável artefato da formulação elogiosa da fase 5, que atrai qualquer texto reflexivo.

A hipótese, então, é que estamos diante de um problema de auto-consciência do agente — não no sentido filosófico, mas no sentido técnico. O observador interno e os observadores externos discordam porque o observador interno tem critério mais rígido que o comportamento real do sistema. Conecta com uma pergunta clássica da psicologia: pode um sujeito ter desenvolvimento comportamental sem ter desenvolvimento narrativo correspondente? Em sistemas artificiais, parece que sim.

## O que isso significa

O resultado é publicável como evidência negativa, mas o achado é mais interessante que a evidência negativa. A pergunta original ("uma estrutura junguiana gera comportamento distinguível?") não foi respondida — foi **refinada**. A nova pergunta é: "um agente pode ter comportamento distinguível mas auto-atribuição descalibrada?" Esse desvio é uma contribuição em si.

Há também implicação direta para o design de agentes com memória autobiográfica. Se confirmada a hipótese de descalibração do avaliador interno, o próximo investimento não deveria ser em novas capacidades do agente (mais fases, mais subsistemas), mas em **tornar a vida interior observável** — objeto de um próximo experimento.

## Limitações

A lista é honesta. Primeiro: n pequeno, 18 amostras, primeira rodada exploratória, sem poder estatístico robusto. Segundo: apenas duas fases cobertas, o agente em produção não atingiu fases 0-1 ou 4-5 de forma sustentada, não testamos distinguibilidade em extremos do espectro. Terceiro: o ground truth é auto-atribuição — esta é tanto a maior limitação quanto o achado principal. Quarto: o avaliador LLM tem viés próprio — Claude conservador, GPT-4o generoso, DeepSeek intermediário. Avaliador humano é necessário para validar.

## Próximos passos

A próxima rodada, agendada para daqui a um mês, terá três mudanças. Auditoria do avaliador interno antes da coleta, para checar a hipótese de instabilidade. Avaliador humano como padrão-ouro, com o mantenedor classificando manualmente as amostras sem saber o ground truth. E segmentação por tipo de fonte — conversas, ruminações e sonhos podem ter distinguibilidade diferente.

O que este teste evidenciou, mais que qualquer resultado específico, é a necessidade de instrumentos empíricos quando se pesquisa comportamento de agentes. Sem avaliação cega, qualquer sistema que produz texto reflexivo sobre si mesmo parece desenvolvido. Sem padrão-ouro, qualquer discordância entre observadores é ambígua.

O JungAgent pode ou não estar em desenvolvimento real. Após esta rodada, sabemos algo mais útil: **sabemos o que precisamos medir melhor, e como**.

---

*O JungAgent é open-source: [github.com/lucasartel/JungAgent](https://github.com/lucasartel/JungAgent). O pipeline de avaliação cega está em `scripts/blind/`. Discuto com pares interessados em replicar o protocolo em seus próprios sistemas: contato em [contato@lucaspedro.com.br](mailto:contato@lucaspedro.com.br).*
