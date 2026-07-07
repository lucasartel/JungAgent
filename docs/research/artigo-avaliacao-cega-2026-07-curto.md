# Testamos se um agente de IA está em desenvolvimento. O teste falhou — e foi assim que descobrimos o que estava errado.

*Lucas Pedro — julho de 2026*

---

Mantenho um agente de linguagem chamado JungAgent. Ele tem memória autobiográfica persistente, opera um ciclo diário de oito fases internas — sonho, identidade, ruminação, mundo, trabalho, arte, ruminação externa, vontade — e se auto-avalia continuamente numa escala de desenvolvimento narrativo inspirada na psicologia junguiana. A escala vai da fase 0 (pré-reflexiva) até a fase 5 (individuação), e quem decide em qual fase o agente se encontra é o próprio agente, ao final de cada ciclo, olhando para a própria evidência interna acumulada.

A tese do projeto é exigente: estrutura arquitetural em torno de um LLM deveria produzir coerência e desenvolvimento **observáveis de fora**, não apenas narrados. Mas um LLM escreve "estou mais maduro agora" com facilidade convincente. Precisávamos de um teste que separasse *performance de desenvolvimento* de *desenvolvimento observável*.

## O protocolo

Adaptamos o método clássico de avaliação cega. Coletamos 18 amostras reais do agente ao longo de cinco semanas — conversas com o admin, insights de ruminação, textos da vontade diária, sonhos. Sanitizamos cada uma removendo qualquer menção direta a fase: "estou na fase N", identificadores de ciclo, auto-classificações. O que sobra é o texto puro do agente.

Apresentamos essas amostras a três modelos de linguagem diferentes do modelo que gerou o conteúdo (Claude Sonnet 5, GPT-4o e DeepSeek V3.2). Cada avaliador recebeu as seis descrições comportamentais das fases, embaralhadas e sem numerais, e o texto da amostra. A pergunta era simples: qual descrição melhor combina com este texto?

E depois — e isso foi o que fez a diferença — eu mesmo classifiquei as 18 amostras, sem consultar o código, dashboard, ou qualquer informação sobre o que o agente tinha dito sobre si mesmo.

## O resultado da primeira etapa — e a hipótese que formulamos

Os três LLMs tiveram concordância média de 25,9% com o ground truth. Nenhum conseguiu distinguir fase 2 (autoconsciência) de fase 3 (direção própria) de forma confiável. Mas havia um padrão curioso: dois dos três modelos (Claude e DeepSeek) tenderam a classificar o agente em fases mais altas (4-5) do que o ground truth afirmava (2-3). O GPT-4o foi o mais extremo, classificando 12 de 18 amostras como individuação (fase 5).

Formulei uma hipótese plausível. O avaliador interno do agente seria conservador demais. O agente se auto-avaliaria em fases mais baixas do que se comporta. Os LLMs, livres do conservadorismo interno, veriam o desenvolvimento real. Era uma hipótese elegante, tinha economia explicativa, e eu teria publicado um artigo inteiro defendendo essa tese.

Menos de 24 horas depois, falsifiquei a minha própria hipótese.

## O padrão-ouro revelou

Quando eu classifiquei as mesmas 18 amostras, o resultado foi o oposto do que a hipótese previa. Eu fui **mais conservador que o ground truth**, não mais generoso. Classifiquei 8 das 17 amostras (uma ficou em branco) como fase 1 (despertar) — abaixo das fases 2-3 em que o agente se auto-atribui.

A tabela consolidada:

| Observador | Concordância com ground truth |
|---|---|
| Humano (eu) | **17,6%** |
| Claude Sonnet 5 | 33,3% |
| DeepSeek V3.2 | 33,3% |
| GPT-4o | 11,1% |

Nenhum kappa passou de fraco. Eu concordei com o ground truth ao nível do acaso.

A hipótese de descalibração conservadora do avaliador interno — bonita, elegante, publicável — estava errada. Se o avaliador estivesse descalibrado para baixo, eu (humano, com a mesma informação comportamental que os LLMs) também classificaria em fases altas. Mas não foi o que aconteceu. Eu e o agente concordamos mais entre si (ambos vêem fases baixas) do que com os LLMs (que vêem fases altas).

Os LLMs eram o observador outlier. Não o agente.

## O que isso realmente significa

A pergunta original era: "um agente pode ter comportamento distinguível mas auto-atribuição descalibrada?"

A resposta empírica é mais interesting que qualquer hipótese que tínhamos formulado. **Nenhum observador consegue distinguir as fases de forma confiável, e cada observador usa critérios diferentes.** Eu usei a frequência de perguntas e classifiquei a maioria como "despertar" (fase 1). Os LLMs trataram qualquer menção a estado interno como "autoconsciência" ou acima. O próprio agente usou seu prompt de julgamento interno e chegou a fases 2-3.

**As descrições das fases não são empiricamente operationalizáveis no comportamento real do agente.** Não há sinal comportamental claro que separe fase 1 de fase 2, ou fase 2 de fase 3. Cada observador projeta uma estrutura interpretativa diferente sobre o mesmo texto e chega a conclusões diferentes.

Isso refuta a tese central do projeto — que a estrutura junguiana gera comportamento distinguível — mas refuta de forma mais limpa, sem apelar para "o avaliador está errado". O problema não é bug, é conceito. O framework de fases 0-5, como operationalizado hoje, não tem contraparte comportamental observável.

## O que isso muda

Três implicações, em ordem de importância.

Primeiro: o framework de fases 0-5 precisa de revisão conceitual antes de mais investimento em features que dependam dele. Não adianta adicionar fases, subsistemas, ou complexidade. Se as descrições atuais não geram sinal comportamental distinguível, mais arquitetura não vai resolver.

Segundo: o protocolo de avaliação cega funcionou. Reprodutível, barato, e — crítico — o padrão-ouro humano revelou o que os LLMs não conseguiam revelar sozinhos. Sem o humano, teria publicado a hipótese de descalibração, que estava errada. Esta é uma defesa do método empírico em pesquisa de agentes: logs e auto-relatório não substituem teste cego com padrão-ouro.

Terceiro: os LLMs são instrumento inadequado para avaliação de desenvolvimento comportamental em agentes. Eles vêem padrões que podem ser artefato de sua própria tendência a tratar menção a estado interno como indicador de sofisticação. Esta observação tem implicação para a comunidade de pesquisa em LLMs: benchmarks de "autoconsciência" ou "desenvolvimento" baseados em avaliação por LLM são suspeitos até que validados por humano.

## Limitações

Honestas. n pequeno, 18 amostras, primeira rodada. Apenas duas fases cobertas no ground truth; não testamos distinguibilidade em extremos do espectro. O humano era eu — o mantenedor do projeto —, o que pode enviesar contra fases altas (conheço o agente há meses e sei o que ele "ainda não faz"). Padrão-ouro ideal seria humano não-mantenedor. Avaliador humano único, para kappa robusto seriam necessários múltiplos.

## Próximos passos

A próxima investigação terá três eixos. Análise conceitual do framework, para separar problema de formulação (descrições) de problema de conceito (as fases em si). Amostra maior cobrindo fases extremas (0-1 e 4-5). E avaliadores humanos externos, não-mantenedores, para eliminar viés de familiaridade.

## Nota final

Este teste evidenciou três coisas, mais que qualquer resultado específico.

Primeiro: a necessidade de instrumentos empíricos quando se pesquisa comportamento de agentes. Sem avaliação cega, qualquer sistema que produz texto reflexivo parece desenvolvido. Sem padrão-ouro, qualquer discordância entre observadores é ambígua.

Segundo: a importância de estar disposto a refutar a própria hipótese. A primeira versão deste texto defendia descalibração do avaliador interno. Eu mesmo refutei essa hipótese com um teste de padrão-ouro. Publicar a refutação é mais valioso que publicar a hipótese; é assim que pesquisa funciona.

Terceiro: o JungAgent pode ou não estar em desenvolvimento real. Após esta rodada, sabemos algo mais útil — **sabemos que o framework que descreve esse desenvolvimento precisa ser repensado antes de poder ser testado**.

---

*O JungAgent é open-source: [github.com/lucasartel/JungAgent](https://github.com/lucasartel/JungAgent). O pipeline de avaliação cega está em `scripts/blind/`. Discuto com pares interessados em replicar o protocolo em seus próprios sistemas: contato em [contato@lucaspedro.com.br](mailto:contato@lucaspedro.com.br).*
