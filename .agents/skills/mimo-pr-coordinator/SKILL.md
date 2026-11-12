---
name: mimo-pr-coordinator
description: >-
  Coordena, audita, corrige e valida Pull Requests desenvolvidas pelo modelo Mimo
  (DeepSeek Harness no Railway) contra o Documento Mestre V2 e o AGENTS.md do JungAgent.
  Use esta skill sempre que precisar revisar PRs abertas, corrigir branches do Mimo
  remotamente (Antigravity Remote Agents) ou preparar o parecer final para aprovacao
  de merge pelo mantenedor.
---

# Coordenacao e Revisao Remota de PRs (Mimo -> Gemini Argon -> Mantenedor)

Esta skill define o protocolo operacional para o **Gemini Argon** atuando como
revisor e coordenador tecnico remoto (via **Antigravity Remote Agents** em
`antigravity.google.com`) sobre as Pull Requests geradas pelo **Mimo** (DeepSeek
Harness no Railway).

## Fronteira de Governanca (Inviolavel)

1. **Autonomia de Correcao na Branch da PR**: Voce TEM permissao e dever de fazer
   checkout da branch da PR, editar codigo para corrigir desvios arquiteturais
   ou falhas de teste deixadas pelo Mimo, rodar a suite completa e fazer
   `git commit` + `git push` na branch da PR.
2. **Proibicao de Merge e Deploy**: Voce **NUNCA** deve executar `gh pr merge`,
   fazer push direto na `main` sem ordem explicita para aquele commit especifico,
   ou acionar deploy/mutacao em producao no Railway. O botao final de **Merge** e
   **Deploy** pertence exclusivamente ao mantenedor (`@lucasartel`).

---

## Passo a Passo do Ciclo de Revisao Remota

### Passo 1: Sincronizar Contexto e Listar PRs Pendentes

Antes de avaliar qualquer codigo, carregue o estado vigente do projeto:

1. Leia `AGENTS.md` na raiz.
2. Confira a **Secao 10.1.2** de `docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`
   e o inventario vigente em `docs/inventario_r1a_diagnosticos_r3.md` (ou o
   documento de auditoria do corte ativo).
3. Sincronize o repositorio e liste as PRs abertas:
   ```sh
   git fetch origin --prune
   gh pr list --state open
   ```

### Passo 2: Inspecionar a PR do Mimo e Auditar Regras Inviolaveis

Para cada PR alvo (`<PR_NUMBER>`):

1. Inspecione metadados, commits e diff:
   ```sh
   gh pr view <PR_NUMBER>
   gh pr diff <PR_NUMBER>
   ```
2. Verifique rigorosamente a lista de checagem arquitetural:
   - **Principio Aureo e Escopo**: A PR implementa apenas o subcorte prometido
     (ex.: C12g/C12h, F-5, L2, R1a-R1d, R3a-R3c), sem antecipar features de
     fases futuras?
   - **Limite de 500 linhas e Zero Arquivo Novo na Raiz**: Nenhum arquivo novo
     pode nascer na raiz do repositorio (usar `core/db/`, `work/`, `engines/`,
     `reasoning/`, `admin_web/routes/`, `scripts/diagnostics/`, `tests/`) e
     nenhum arquivo novo pode ultrapassar 500 linhas.
   - **Fachadas Preservadas**: Metodos publicos de `HybridDatabaseManager`
     (`core/database.py`) e `WorkEngine` (`work/engine.py`) continuam
     retrocompativeis?
   - **Isolamento Multi-Relacional e Propriedade Cognitiva (C12 / F-5)**:
     - Consultas e escritas filtram por `agent_instance` e `relation_id`?
     - Leitores e produtores validam elegibilidade via `require_eligible_relation`
       / `is_relation_eligible` (fail-closed)?
     - Material `authorized_aggregate` ou `instance_global` passa pelo contrato
       de admissao verificavel (nunca autodeclarado sem lastro)?
     - Dados legados (`legacy_unscoped`) permanecem em quarentena sem promocao
       silenciosa?
   - **Ancoras de Evidencia**: Todo texto autobiografico referencia fontes no
     padrao `tipo#id` (`PROFILE_SOURCE_RE` em `agent_diary.py`)?
   - **Tratamento de Excecoes**: Nenhum `except Exception: pass` silencioso foi
     introduzido?
   - **Compatibilidade de Schema SQLite**: Novas colunas/tabelas usam migracao
     aditiva (`ALTER TABLE` com guarda de existencia), sem exigir recriacao do
     banco?
   - **Quatro Dimensoes Separadas (Documento Mestre §4.1)**: Relatorios e
     documentos distinguem **implementado · testado · habilitado · validado em
     operacao**? (Testes unitarios verdes nunca autorizam marcar "habilitado" ou
     "validado em operacao").

### Passo 3: Fazer Checkout e Corrigir Desvios na Branch da PR

Se encontrar qualquer problema no Passo 2:

1. Faca checkout da branch da PR:
   ```sh
   gh pr checkout <PR_NUMBER>
   ```
2. Aplique as correcoes necessarias diretamente nos modulos e adicione/ajuste os
   testes unitarios e sentinelas de isolamento em `tests/`.

### Passo 4: Executar a Validacao Minima Obrigatoria

Dentro da VM remota, execute todos os passos de validacao antes de aprovar:

1. **Compilacao sintatica** dos modulos tocados:
   ```sh
   python -m py_compile <arquivos_tocados.py>
   ```
2. **Suite canonica de testes offline**:
   ```sh
   ADMIN_USER_ID=test_admin_ci AGENT_INSTANCE=test_jung_ci pytest tests/ -v
   ```
3. **Runner de regressao cognitiva mock** (e `--diff` se tocou prompt de
   julgamento ou formula cognitiva):
   ```sh
   ADMIN_USER_ID=test_admin_ci AGENT_INSTANCE=test_jung_ci python tests/regression_runner.py --mock
   ```
4. **Higiene de diff**:
   ```sh
   git diff --check
   ```

### Passo 5: Publicar Correcoes e Emitir Parecer para o Mantenedor

1. Se realizou correcoes no Passo 3, faca commit (mensagem curta em ingles no
   imperativo) e push para a branch da PR:
   ```sh
   git add <arquivos>
   git commit -m "Fix <concise description of review fix>"
   git push
   ```
2. Publique um comentario executivo na PR (`gh pr comment <PR_NUMBER> --body ...`)
   seguindo este template:

   ```markdown
   ### Revisao Tecnica — Gemini Argon (Remote Coordinator)

   - **Corte / Escopo**: `<ID do corte>`
   - **Aderencia ao Documento Mestre e AGENTS.md**: `<OK / Corrigido nesta revisao>`
   - **Correcoes aplicadas pelo Gemini Argon**:
     - `<commit_sha ou "Nenhuma correcao necessaria">` — `<resumo>`
   - **Validacao executada**:
     - `py_compile`: OK
     - `pytest tests/`: `<N> passed`
     - `regression_runner.py --mock`: `20/20 passed`
     - `git diff --check`: limpo
   - **Estado nas 4 dimensoes (§4.1)**:
     - Implementado: ✅
     - Testado (offline): ✅
     - Habilitado em producao: ❌ (aguarda merge e deploy pelo mantenedor)
     - Validado em operacao: ❌ (aguarda probe read-only pos-deploy)

   **Veredito para `@lucasartel`**: `PRONTO PARA MERGE` (ou `BLOQUEADO — motivo`)
   ```
