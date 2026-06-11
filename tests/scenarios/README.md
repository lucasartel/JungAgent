# Cenarios canonicos de regressao

Esta pasta e a entrega da Fase 0.2. Ela define estados sinteticos fixos para o futuro runner de regressao cognitiva da Fase 0.3.

Os cenarios sao deliberadamente offline: nao chamam LLM, Qdrant, Telegram, Railway ou rede. Cada arquivo JSON descreve um estado de entrada e propriedades esperadas que o runner devera verificar em modo `--mock` ou comparar em modo `--live`.

## Estrutura

Cada arquivo `*.json` tem:

- `id`: identificador unico e estavel.
- `version`: versao do conjunto de cenarios, atualmente `0.2`.
- `domain`: um de `rumination`, `dream`, `identity`, `will`, `conversation`.
- `description`: resumo humano do caso.
- `inputs`: dados sinteticos que simulam o estado de entrada.
- `expected_properties`: lista de propriedades que devem permanecer verdadeiras.

## Distribuicao

O conjunto atual contem 20 cenarios:

- 5 de ruminacao e maturidade de tensoes.
- 3 de sonhos com temas definidos.
- 3 de identidade e desenvolvimento narrativo.
- 4 de estados do will.
- 5 conversas-tipo: admin estressado, pergunta factual, pergunta existencial, pedido de trabalho e mensagem curta.

## Uso

```python
from tests.scenarios import load_scenarios

scenarios = load_scenarios()
rumination = load_scenarios(domain="rumination")
```

O helper valida chaves obrigatorias, versao, dominio, `inputs` e `expected_properties`. A suite `test_scenarios_loader.py` garante que todos os cenarios sao carregaveis e que os IDs permanecem unicos.

## Papel no roadmap

Estes cenarios nao executam julgamentos cognitivos. Eles sao a base fixa para:

- Fase 0.3: `tests/regression_runner.py`.
- Comparacao entre execucoes `--mock` e `--live`.
- Resolucao segura da pendencia D2 (`time_factor` fixo em `7.0`) somente depois que o runner existir.
