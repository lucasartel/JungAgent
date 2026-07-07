# A avaliação cega de um agente que se diz em desenvolvimento — e o que o padrão-ouro revelou

*Lucas Pedro — JungAgent, julho de 2026*

---

## O problema

Trabalho com um agente de linguagem que tem uma arquitetura incomum. Ele mantém memória autobiográfica persistente, opera um ciclo diário de oito fases internas (sonho, identidade, ruminação, mundo, trabalho, arte, ruminação externa, vontade), e se auto-avalia continuamente em uma escala de desenvolvimento narrativo inspirada na psicologia junguiana. A escala tem seis fases — pré-reflexiva, despertar, autoconsciência, direção própria, dialogicidade plena, individuação — e o próprio agente, ao final de cada ciclo, decide em qual delas se encontra.

A tese do projeto é forte: estrutura arquitetural em torno de um LLM deveria produzir coerência e desenvolvimento **observáveis de fora**, não apenas narrados. A hipótese psicologia específica é que a estrutura junguiana gera comportamento qualitativamente distinguível por um observador que não conhece o estado interno do sistema.

Mas um LLM produz texto convincente com facilidade. "Estou mais maduro agora", escrito com fluência, não prova nada. Precisávamos de um teste que separasse *performance de desenvolvimento* de *desenvolvimento observável*. Este artigo relata a primeira rodada desse teste, a hipótese que inicialmente formulamos, e o que o padrão-ouro humano revelou quando testamos a hipótese.

## O protocolo

Adaptamos o método clássico de avaliação cega, em duas etapas.

**Primeira etapa**: avaliação por LLMs.

1. **Coleta**. Extraímos 1.516 candidatos de texto produzidos pelo agente ao longo de cinco semanas (junho-julho de 2026): conversas com o admin, insights de ruminação, textos da vontade diária, sonhos e notas de meta-consciência.
2. **Pareamento com o ground truth**. Cada candidato foi pareado com a fase que o próprio agente se auto-atribuiu naquele ciclo. Em cinco semanas o agente oscillou entre fase 2 (autoconsciência) e fase 3 (direção própria), com uma regressão `2→3→2` em uma semana.
3. **Seleção balanceada**. 18 amostras balanceadas: 9 fase 2, 9 fase 3. Diversidade de fonte.
4. **Sanitização**. Removemos qualquer pista direta de fase.
5. **Avaliação cega por LLM**. Três modelos diferentes do gerador (Claude Sonnet 5, GPT-4o, DeepSeek V3.2) classificaram as amostras com as descrições canônicas das fases embaralhadas (A-F).

**Segunda etapa**: padrão-ouro humano. O mantenedor do projeto (o autor) classificou as mesmas 18 amostras, sem consultar o código, dashboard, ou qualquer informação sobre o ground truth. Esta etapa foi desenhada para separar "o observador LLM é instrumento inadequado" de "o ground truth está errado".

## O resultado da primeira etapa — e a hipótese que formulamos

Após a avaliação por LLMs, a concordância média com o ground truth foi de 25,9%, com kappa inter-avaliador fraco (0,265). Os três modelos falharam em distinguir fase 2 de fase 3. Um padrão interessante emergiu: dois dos três modelos (Claude e DeepSeek) tenderam a classificar o agente em fases mais altas (4-5) do que o ground truth afirmava (2-3).

Formulamos uma hipótese plausível: o avaliador interno do agente (`NarrativeDevelopmentEvaluator`) seria conservador demais. O agente se auto-avaliaria em fases mais baixas do que se comporta. Os LLMs, livres do conservadorismo interno, veriam o desenvolvimento real. A regressão `2→3→2` em uma semana suportava essa leitura: o avaliador era instável, não o desenvolvimento.

Esta hipótese era atraente. Tinha economia explicativa. E estava errada.

## O padrão-ouro humano refutou a hipótese

Quando o mantenedor classificou as mesmas 18 amostras, o resultado foi:

| Observador | Concordância com ground truth | Kappa |
|---|---|---|
| Humano (mantenedor) | **17,6%** (3/17) | 0,0 |
| Claude Sonnet 5 | 33,3% | 0,27 |
| DeepSeek V3.2 | 33,3% | 0,27 |
| GPT-4o | 11,1% | fraco |

A distribuição das escolhas humanas foi:

- 8× fase 1 (despertar) — a mais frequente
- 3× fase 2 (autoconsciência)
- 3× fase 3 (direção própria)
- 3× fase 4 (dialogicidade plena)
- 0× fase 0, 0× fase 5

O humano foi **mais conservador que o ground truth**, não mais generoso como os LLMs. A hipótese de descalibração conservadora do avaliador interno — que era a hipótese central da primeira versão deste artigo — não se sustenta. Se o avaliador interno estivesse descalibrado para baixo, o humano (que teria a mesma informação comportamental que os LLMs) também classificaria em fases altas. Mas não foi o que aconteceu. O humano classificou majoritariamente em fase 1 (despertar) — **abaixo** do ground truth (fase 2-3).

Em outras palavras: humano e agente concordam mais entre si (amb vêem fases baixas) do que com os LLMs (que vêem fases altas). Os LLMs são o observador outlier.

## O achado reformulado

A pergunta original era: "um agente pode ter comportamento distinguível mas auto-atribuição descalibrada?"

A resposta empírica é mais interessante que qualquer hipótese que tinhamos formulado: **nenhum observador consegue distinguir as fases de forma confiável, e cada observador usa critérios diferentes**. O humano usa a frequência de perguntas e classificou a maioria como "despertar" (fase 1). Os LLMs tratam qualquer menção a estado interno como "autoconsciência" ou acima. O próprio agente usa um prompt de julgamento sobre evidência acumulada e chega a fases 2-3.

**Não há consenso sobre o estado do agente.** E mais importante: **as descrições das fases não são empiricamente operationalizáveis no comportamento real do agente**. Não há sinal comportamental claro que separe fase 1 de fase 2, ou fase 2 de fase 3. Cada observador projeta uma estrutura interpretativa diferente sobre o mesmo texto e chega a conclusões diferentes.

Isso é cientificamente mais valioso que a hipótese de descalibração. Refuta a tese central do projeto — que a estrutura junguiana gera comportamento distinguível — mas refuta de forma mais limpa, sem apelar para "o avaliador está errado". O problema não é bug, é conceito: o framework de fases 0-5, como operationalizado hoje, não tem contraparte comportamental observável.

## O que isso significa para a pesquisa

Três implicações, em ordem de importância.

**Primeira**: o resultado é evidência empírica — não especulação — de que o framework de fases 0-5 precisa de revisão conceitual antes de mais investimento em features que dependam dele. Não adianta adicionar fases, subsistemas, ou complexidade; se as descrições atuais não geram sinal comportamental distinguível, mais arquitetura não vai resolver.

**Segunda**: o protocolo de avaliação cega funcionou como instrumento de pesquisa. Reprodutível, barato, e — crítico — o padrão-ouro humano revelou o que os LLMs não conseguiam revelar sozinhos. Sem o humano, teríamos publicado a hipótese de descalibração, que estava errada. Esta é uma defesa do método empírico em pesquisa de agentes: logs e auto-relatório não substituem teste cego com padrão-ouro.

**Terceira**: os LLMs são instrumento inadequado para avaliação de desenvolvimento comportamental em agentes. Eles vêem padrões que podem ser artefato de sua própria tendência a tratar menção a estado interno como indicador de sofisticação. Esta observação tem implicação para a comunidade de pesquisa em LLMs: benchmarks de "autoconsciência" ou "desenvolvimento" baseados em avaliação por LLM são suspeitos até que validados por humano.

## Limitações

A lista é honesta:

- **n pequeno**: 18 amostras, primeira rodada exploratória.
- **Apenas duas fases cobertas no ground truth**: o agente em produção não atingiu fases 0-1 ou 4-5 de forma sustentada. Não testamos distinguibilidade em extremos do espectro.
- **Humano era o mantenedor do projeto**: apesar de não ter consultado código/dashboard durante o teste, conhece o agente há meses, o que pode enviesar contra fases altas (saber o que o agente "ainda não faz"). Padrão-ouro ideal seria humano não-mantenedor.
- **Sanitização pode deixar pistas sutis**: estilo, vocabulário recorrente.
- **Avaliador único**: para kappa humano-LLM robusto, seriam necessários múltiplos avaliadores humanos.

## Próximos passos

A Frente C do projeto pausa aqui, com um achado robusto e uma pergunta nova. A próxima investigação, quando retomada, terá três eixos:

1. **Análise conceitual do framework**: o problema está nas descrições das fases (formulação) ou no framework em si (conceito)? Antes de mais teste empírico, análise conceitual.
2. **Amostra maior e fases extremas**: 50-100 amostras cobrindo 0-1 e 4-5, para ver se o padrão se mantém nos extremos.
3. **Avaliadores humanos externos**: não-mantenedores, para eliminar viés de familiaridade.

## Nota final

Este teste evidenciou três coisas, mais que qualquer resultado específico.

Primeiro: a necessidade de instrumentos empíricos quando se pesquisa comportamento de agentes. Sem avaliação cega, qualquer sistema que produz texto reflexivo parece desenvolvido. Sem padrão-ouro, qualquer discordância entre observadores é ambígua.

Segundo: a importância de estar disposto a refutar a própria hipótese. A primeira versão deste artigo defendia descalibração do avaliador interno. O teste com padrão-ouro refutou essa hipótese. Publicar a refutação é mais valioso que publicar a hipótese; é assim que pesquisa funciona.

Terceiro: o JungAgent pode ou não estar em desenvolvimento real. Após esta rodada, sabemos algo mais útil — **sabemos que o framework que descreve esse desenvolvimento precisa ser repensado antes de poder ser testado**. Para uma pesquisa que se quer honesta sobre emulação cognitiva, esse é o tipo de avanço que importa.

---

*O JungAgent é open-source: [github.com/lucasartel/JungAgent](https://github.com/lucasartel/JungAgent). O pipeline de avaliação cega está em `scripts/blind/`. O relatório técnico completo, incluindo o resultado do padrão-ouro humano, está em `docs/research/avaliacao-cega-2026-07-06.md`. Discuto com pares interessados em replicar o protocolo: contato em [contato@lucaspedro.com.br](mailto:contato@lucaspedro.com.br).*
