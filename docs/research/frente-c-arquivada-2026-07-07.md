# Frente C - Arquivamento

**Data de arquivamento**: 2026-07-07
**Período ativo**: 2026-07-06 a 2026-07-07
**Estado**: concluida com achado publicavel, pausa estrategica

## O que foi entregue

- Pipeline completo de avaliacao cega (`scripts/blind/`)
- Primeira rodada com 3 LLMs avaliadores (Claude Sonnet 5, GPT-4o, DeepSeek V3.2)
- Padrao-ouro humano (mantenedor)
- 2 variantes testadas (canonical, refined)
- 3 documentos publicaveis:
  - Relatorio tecnico: `docs/research/avaliacao-cega-2026-07-06.md`
  - Artigo longo: `docs/research/artigo-avaliacao-cega-2026-07.md`
  - Artigo curto: `docs/research/artigo-avaliacao-cega-2026-07-curto.md`

## Achado central

O framework de fases 0-5 do `agent_development`, como operationalizado hoje, **nao tem contraparte comportamental observavel de forma confiavel**. Testado por:
- 3 LLMs (concordancia media 25.9% com ground truth, kappa 0.27)
- 1 humano padrao-ouro (concordancia 17.6%, kappa 0.0)
- 2 variantes de descricao de fase (canonical e refined)

Nenhum observador conseguiu distinguir fase 2 de fase 3 de forma consistente. Cada observador usa heuristicas diferentes e chega a classificacoes diferentes.

## Hipotese inicial refutada

A primeira versao dos artigos defendia: avaliador interno conservador demais (agente se subestima). O padrao-ouro humano refutou essa hipotese — o humano tambem classificou em fases mais baixas que o ground truth, nao mais altas como os LLMs. A questao nao e descalibracao, e **operacionalizacao**.

## Pendencias deixadas para o futuro

- Analise conceitual do framework: problema de formulacao (descricoes) vs conceito (fases em si)
- Amostra maior cobrindo fases extremas (0-1 e 4-5)
- Avaliadores humanos externos (nao-mantenedores)
- Segmentacao por source_type (conversa, rumination, will, dream, meta)

## Implicacao tecnica imediata

`agent_development.phase` nao e variavel confiavel para depender em features novas ate o framework ser revisado. Features que ja usam (perfil autobiografico, ISM snapshots) podem herdar o problema mas nao devem ser bloqueadas.

## Decisao

Frente C arquivada em estado publicavel. Retomada somente quando houver motivacao concreta (novos dados longitudinais, revisao conceitual feita, ou demanda externa de pares).
