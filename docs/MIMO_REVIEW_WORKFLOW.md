# Workflow de revisao assistida MiMo -> Revisor GPT -> Mantenedor

**Status (16/11/2026):** configuracao local e documentacao apenas. Workflow
sob demanda (on-demand) no OpenCode. Nenhuma tarefa de feature foi executada
neste entregavel; nenhum runtime, nenhuma workflow de CI e nenhuma acao de
producao foram alteradas. As alteracoes sao: `.opencode/agents/mimo-developer.md`
(novo), este documento (novo), `AGENTS.md` (secao operacional), Documento Mestre
Secao 3 (autorizacao estreita), `.agents/skills/mimo-pr-coordinator/SKILL.md`
(clareza de protocolos) e comentarios de `.github/CODEOWNERS` (entradas
`@lucasartel` inalteradas).

Este fluxo foi autorizado explicitamente pelo mantenedor para trabalho
assistido local. Ele **nao** substitui a governanca de tres papeis do Documento
Mestre Secao 3; ele a complementa com um caminho estreito e revogavel.

## 1. Papeis

| Papel | Quem | Faz | Nao faz |
|---|---|---|---|
| **Mantenedor** | Lucas (`@lucasartel`) | Define o escopo da tarefa, decide prioridades, pode auditar qualquer entrega, controla producao (Railway) | Nao precisa codificar |
| **Revisor tecnico (agente pai)** | Sessao GPT pai no OpenCode | Inicia o subagente, audita a submissao de forma independente (re-executa os gates), reprova/corrige, commita e publica a branch, abre a PR, acompanha a CI e executa o merge **somente** apos sua aprovacao tecnica explicita + CI verde na SHA revisada | Nao altera escopo sozinho, nao altera producao manualmente, nao mergeia sem aprovacao + CI |
| **Desenvolvedor** | MiMo Flash 2.6 via `.opencode/agents/mimo-developer.md` (`mode: subagent`) | Implementa na arvore de trabalho local, roda os gates, entrega relatorio + diff | Nao commita, nao pusha, nao abre/comenta/mergeia PR, nao tem credencial do GitHub, nao toca em governanca ou producao |
| **Consultor estrategico** | Claude (Anthropic) | Inalterado (Documento Mestre Secao 3) | Nao codifica; nao mergeia |

O subagente e iniciado **sob demanda** pelo pai (ferramenta `subagent`), nunca
como agente primario.

## 2. Fluxo passo a passo

1. **Escopo**: o mantenedor define a tarefa (corte, arquivos, criterios de
   aceite). Escopo nao definido = nao iniciar.
2. **Implementacao local**: o pai invoca `mimo-developer`, que implementa na
   branch local atual e roda os gates (Secao 4).
3. **Submissao**: o desenvolvedor entrega relatorio (arquivos, comandos,
   resultados, pendencias) + `git diff`. Sem commit.
4. **Revisao independente do pai**: o pai le o diff completo, re-executa os
   gates por conta propria e decide `PRONTO PARA PUBLICAR` ou `REPROVADO`.
   Submissao reprovada volta ao passo 2; esta decisao sobre o diff local e
   preliminar, pois ainda nao existe SHA da entrega.
5. **Publicacao**: somente o pai commita (mensagem curta em ingles no
   imperativo, padrao do historico), publica a branch descritiva e abre a PR
   usando a identidade do GitHub App publicador (Secao 7.1).
   O revisor confirma que o commit corresponde ao diff revisado e registra sua
   aprovacao com a **SHA** final no parecer da PR (Secao 3).
   O desenvolvedor nunca recebe credencial do GitHub.
6. **CI verde**: a PR so avanca com a checks da CI **concluida e verde** na
   SHA final (Secao 5).
7. **Merge explicito**: o pai so executa `gh pr merge` (ou botao manual) depois
   de aprovacao vigente + CI verde. **Auto-merge proibido.**
8. **Deploy automatico pos-merge**: se configurado, o Railway segue sua
   integracao existente apos o merge, efeito autorizado pelo mantenedor.
   Nao presumir que o deploy espera a CI de `main`: verificar essa configuracao
   separadamente. **Nenhuma acao manual de producao** e permitida por este
   fluxo; validacao pos-deploy por probes read-only, conforme AGENTS.md.

## 3. Aprovacao vinculada a SHA (SHA-bound)

- A aprovacao vale **apenas** para o commit SHA exato revisado e para o diff
  exato desse SHA contra `main`. Registrar a SHA no parecer
  (`aprovado para <full_sha>`).
- **Qualquer novo push na branch invalida imediatamente a aprovacao**, mesmo
  que o push pareca pequeno ("so um typo"). Nao existe aprovacao por branch.
- Apos qualquer novo commit: nova revisao completa do diff, novos gates
  executados, nova aprovacao com a SHA nova. Aprovacao velha = **stale** e deve
  ser rejeitada explicitamente (comentar `STALE: aprovacao anterior cobria
  <sha_anterior>`).
- Nao aprove "por cima" de um push que voce nao leu. Nao aprove baseado em
  descricoes de PR ou mensagens de commit; aprove o diff.

## 4. Gates de validacao (obrigatorios nos dois lados)

O desenvolvedor roda; o pai **re-executa de forma independente** antes de
publicar e antes de mergeiar:

1. Sintaxe: `python -m py_compile <modulos_tocados.py>` (`python3` onde nao
   houver `python` no PATH).
2. **Suite completa** (nunca subconjunto):
   `python -m pytest tests/ -v` — precisa passar inteira; qualquer skip novo
   precisa justificativa. Nesta instalacao local as dependencias ficam fora do
   sistema (`PYTHONPATH=/tmp/opencode/jungagent-test-deps python3 -m pytest
   tests/ -v`), com Python 3.12.3 — a CI oficial roda Python 3.11.
3. **Regressao mock**: `python tests/regression_runner.py --mock` — 20
   cenarios. Se a tarefa tocou prompt de julgamento ou formula cognitiva, o
   revisor roda ainda `--diff` entre execucoes e documenta o diff de
   comportamento (o `--diff` nao esta no allowlist do desenvolvedor).
4. Higiene de diff: `git diff --check` limpo.
5. Regras de entrega do AGENTS.md: maximo 500 linhas por arquivo novo, zero
   arquivo novo na raiz, fachadas preservadas, sem
   `except Exception: pass`, migracao SQLite aditiva, ancoras de evidencia
   `tipo#id`, custo de LLM registrado.

Nenhuma entrega avanca com gate vermelho ou com teste omitido. Ambiente sem
`pytest` instalado = gate **nao executado**, o que e reprovacao, nao aprovacao
implicita (declarar no relatorio).

## 5. CI: nao pode faltar, estar pendente ou falhar

- Merge somente com checks da GitHub Actions **concluidas com sucesso** na SHA
   final da PR. Estados `missing`, `pending`, `failing`, `cancelled`, `skipped`,
   `neutral` ou checks
  de SHA antiga **bloqueiam** o merge.
- Se a PR mudou de SHA depois da ultima revisao, os checks precisam rodar de
  novo na SHA nova; a CI de uma SHA anterior nao vale.
- Falha de CI exige correcao nova, que reinicia o ciclo do Secao 3.

**Estado remoto verificado em 16/11/2026:** a protecao de `main` exige o check
`test` do GitHub Actions, uma aprovacao e review de CODEOWNER; aprovacoes antigas
sao descartadas quando o diff muda. O CODEOWNER atual e `@lucasartel`.
O ruleset `Default` tambem impede exclusao e non-fast-forward na branch padrao.
Nenhuma regra foi alterada. Os novos gates de revisao GPT deste documento sao
**procedurais** e nao substituem o review exigido pelo GitHub. Nao usar bypass,
mesmo que a conta administradora tenha essa possibilidade. O CI existente em
`.github/workflows/ci.yml` nao foi alterado; nenhum workflow novo foi criado.

## 6. Merge: somente explicito, nunca auto-merge

- O merge e sempre uma acao explicita e deliberada do revisor (pai) apos:
  aprovacao vigente na SHA + CI verde na mesma SHA.
- **Auto-merge (merge automático quando a CI verde) fica desativado/proibido**
  neste fluxo.
- Sem push direto em `main` no fluxo assistido (a excecao historica de commit
  direto em `main` do Documento Mestre Secao 3 nao e usada aqui).
- Nenhuma acao de producao manual acompanha o merge: deploy e o automatico do
  Railway apos o push em `main`.

## 7. Limitacoes reconhecidas (ler antes de tentar "exigir" review no GitHub)

1. **Mesma conta GitHub nao aprova a propria PR**: a conta que abriu a PR
    (`lucasartel`, caso usada para publicar) nao pode aprovar a propria PR.
    Se autor e revisor usarem essa mesma conta, a aprovacao tecnica do GPT
    deve ser registrada como parecer com SHA, nao como review independente
    do GitHub. Se uma regra exigir review independente, usar outra identidade
    autorizada ou aguardar o mantenedor, sem bypass.
2. **Para enforced review no GitHub** seria preciso **identidades separadas**:
   autor (conta/App do desenvolvedor) e revisor (outra conta ou App) com
   credenciais distintas, mais branch protection exigindo 1 review aprovado e
   CI verde. Enquanto isso nao existir, a garantia e procedimental.
3. **Autenticacao verificada a cada instalacao**: confirmar `gh auth status`
    e `gh repo view` antes de publicar. Login no browser nao autentica a CLI.
    Nunca pedir tokens no chat nem grava-los no repositorio. Sem credencial
    confirmada, push e abertura de PR permanecem bloqueados.
4. **Ambiente de testes local**: provisionar `requirements-test.txt` em
    ambiente isolado e registrar a versao do Python. Os gates locais sao
    necessarios, mas nao substituem a CI oficial (Python 3.11) na mesma SHA.
5. **Permissoes do subagente sao defesa em profundidade, nao sandbox de SO**:
   o `shell` roda com autoridade do usuario hospedeiro e os testes executam
   codigo do repositorio. Separacao real exige processo isolado sem
   credenciais, sem rede e sem chave git util.
6. **PRs com credenciais**: o desenvolvedor nunca recebe token GitHub/Railway;
   credenciais ficam com o revisor/mantenedor, fora do repo e fora do contexto
   do subagente.
7. Escopo: este documento autoriza apenas o workflow local sob demanda descrito
    aqui; ele nao autoriza features novas, execucao autonoma (Fase VII),
    novas integracoes, custo novo ou alteracoes de politica de seguranca.

### 7.1 Publicador instalado e separacao de identidade (16/11/2026)

- App privado **JungAgent PR Publisher Lucas**, slug
  `jungagent-pr-publisher-lucas`, App ID `5784582`, instalado somente em
  `lucasartel/JungAgent` (instalacao `182573078`).
- Permissoes: `contents: write`, `pull_requests: write` e `metadata: read`.
  Sem administracao, acesso a secrets, mudancas de workflows ou bypass.
  Essas permissoes nao distinguem abertura de PR de merge na API: a proibicao
  de merge pelo publicador e operacional, apoiada nas protecoes da `main`,
  nao uma permissao GitHub inexistente de "somente abrir PR".
- O pai controla um publicador confiavel fora do repositorio. Ele usa a chave
  privada para obter tokens de instalacao temporarios restritos ao JungAgent,
  mantidos somente na memoria do processo e revogados apos o uso.
- Nesta instalacao o helper fica em
  `~/.local/share/jungagent-publisher/publisher.py`; a chave fica no mesmo
  diretorio, com modo `0600`, e o diretorio com modo `0700`.
  O helper permite verificar a instalacao, migrar as PRs #92/#93 e publicar
  PRs de branches `publisher/*` ja enviadas pelo pai, exigindo a SHA esperada.
  Exemplo para o revisor, nunca para o desenvolvedor:
  `python3 ~/.local/share/jungagent-publisher/publisher.py publish --head
  publisher/<tarefa> --expected-sha <sha40> --title "<titulo>" --body-file
  /tmp/opencode/pr-body.md`.
  Instalar esse helper confiavel em cada maquina separadamente; nao presumir
  que faz parte do clone. Nao entregar tokens ao desenvolvedor ou em stdout.
- O helper nunca executa codigo ou testes do repositorio. Nao usar a chave em
  comandos de teste, logs, prompts ou arquivos versionados. Para isolamento
  forte do desenvolvedor, usar outro usuario/container sem acesso ao diretorio.
- As PRs sao de `jungagent-pr-publisher-lucas[bot]`; a revisao formal de
  CODEOWNER e registrada pela conta `lucasartel` apos revisao tecnica GPT.
  Verificar autor, SHA, CI e exigencias vigentes antes de aprovar/mergeiar.
- Isso habilita a separacao para o fluxo local. Nenhum workflow novo de
  GitHub Actions foi ativado; a automacao remota abaixo continua proposta.

## 8. FUTURO (conceitual — NAO implementado nesta entrega)

Fluxo de GitHub Actions pretendido para quando o mantenedor decidir tornar os
gates exigidos pelo GitHub. **Nada disto foi criado, ativado ou testado:**

- **Gatilhos apenas de dono confiavel**: `workflow_dispatch` restrito a
  maintainers ou ativacao por label aplicada por maintainer (`issue_comment`/
  `pull_request_target` so para ler metadados, nunca para rodar codigo da PR).
  Sem execucao automatica de codigo de PR nao confiavel com secrets.
- **Identidades e credenciais separadas**: autor (App/conta do desenvolvedor)
  e revisor (App/conta do revisor) distintos; credencial de merge/deploy nunca
  presente no job que roda codigo da PR; preferir OIDC/secret de ambiente com
  protecao.
- **Nunca `pull_request_target` com checkout do `head`**: nao executar codigo
   da branch forkada sob contexto privilegiado. Configurar a CI de
   `pull_request` com `permissions: contents: read`, sem secrets de merge e com
   `persist-credentials: false` no checkout, inclusive para branches internas.
- **Caveat do `GITHUB_TOKEN`**: eventos de PR (`pull_request`: `opened`,
  `synchronize`, `reopened`) **causados pelo proprio `GITHUB_TOKEN`** criam runs
   de workflow em **estado que exige aprovacao manual** antes de executarem;
   outros eventos causados pelo `GITHUB_TOKEN` geralmente **nao disparam**
   novos workflows, salvo `workflow_dispatch` e `repository_dispatch`.
   Um token de **App installation** pode disparar CI sem esse gate manual
   especifico, respeitando as demais politicas do repositorio.
   Nao se trata de "o `GITHUB_TOKEN` nunca dispara
  CI de PR": o comportamento depende do tipo de evento e de quem o causou.
  Fonte: docs oficiais de `github_token` do GitHub Actions. Se uma etapa
  precisar disparar outra workflow de forma confiavel, usar token de App/PAT
  dedicado com escopo minimo — nunca um token com permissao de merge no job de
  teste.
- **Check de revisor vinculado a SHA**: a workflow confere que existe
  aprovacao registrada para o SHA exato do `head_sha` da execucao; aprovacao
  para SHA antiga nao satisfaz o check.
- **Revisao fresca a cada push**: qualquer novo commit zera o estado de
  aprovacao; a check falha ate nova aprovacao na SHA nova.
- **Actions pinneadas**: `uses:` fixado por full commit SHA (nao tag
  flutuante), com comentarios apontando a tag correspondente; dependencias de
  teste fixadas.
- **Capacidade**: limites de tempo de execucao (`timeout-minutes`), limites de
  custo/quantidade de runs por PR, e `concurrency` com
  `cancel-in-progress: true` para cancelar runs obsoletos de pushes anteriores.
- A decisao de implementar disto e do mantenedor e exige revisao de seguranca
  previa; ate la, vale o fluxo procedural das Secoes 2-6.

## 9. Relacao com o protocolo original do Gemini (mimo-pr-coordinator)

O protocolo em `.agents/skills/mimo-pr-coordinator/SKILL.md` (Gemini Argon via
Antigravity Remote Agents) permanece com sua regra central: **o revisor
remoto nunca mergeia e nunca toca em producao**. A excecao autorizada deste
documento e **distinta e estreita**: vale apenas para o revisor GPT pai no
OpenCode local, apenas com aprovacao SHA + CI verde, e nao se estende ao
protocolo remoto do Gemini nem a nenhuma outra automacao. Os dois fluxos
compartilham a mesma checklist de revisao arquitetural.
