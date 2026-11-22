"""Registro de questoes candidatas em modo observacao (Complemento R1).

Primeira fronteira do **Complemento R1** do mestre ("Incompreensao
persistente e investigacao revisavel"): registra questoes candidatas e
verifica **sustentacao, pertinencia e duplicacao** — sem ligar novos
efeitos volitivos, sem tocar a cadencia do loop e sem disparar contatos.

Contrato:

* **Estende ``knowledge_gaps``** (nao cria um quarto sistema de memoria):
  colunas ``question_*`` aditivas; identidade, dedup R1a-4, admissao F-5
  e gate C12g do escritor seguem intactos.
* **Estados conceituais** (``QUESTION_STATES``) nao substituem
  ``status``: cada transicao e motivada, versionada (CAS) e gravada em
  ``knowledge_gap_revisions``; idade ou recorrencia nunca alteram
  ``importance_score``.
* **Sustentacao** (ver ``core/db/knowledge_question_anchors.py``):
  ``missing``/``unauthorized`` bloqueia a questao com motivo auditavel;
  ``unverifiable`` mantem ``active`` com ``pending_review`` (amostra
  revisavel, nunca consentimento). Sem ancora valida o registro e
  recusado (``source_refs_required``).
* **Duplicacao**: mesma pergunta no mesmo escopo reutiliza a linha do
  dedup — replay nao reabre, nao regrava estado, nao recria revisao.
* **Isolamento (modo observacao)**: ``get_active_knowledge_gaps`` nao
  serve ``question_kind`` — contexto, proatividade e pressao do WILL nao
  recebem questoes; a leitura dedicada e ``list_candidate_questions``
  (fail-closed como a do R1a).
* **Expurgo**: conteudo novo redigido em ``_ERASE_FIELDS``;
  ``knowledge_gap_revisions`` apagada com a Relation.
* **R1c**: o vinculo com unidades e versionado na projecao
  ``knowledge_question_unit_links``
  (``core/db/knowledge_question_links.py``) e a recuperacao de questoes
  pertinentes e ``search_knowledge_questions`` — ranking lexico local
  SEM idade, leitura pura; o ranker vetorial continua retido por decisao
  de custo do mantenedor.

Fora do escopo: efeito volitivo, prompt, envio, custo, politica
sensivel, migracao de producao e qualquer chamada de LLM/provedor.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.db.cognitive_ownership import is_globally_readable
from core.db.cognitive_provenance import (
    AUTHORIZED_AGGREGATE,
    INSTANCE_GLOBAL,
    cognitive_agent_instance,
    json_payload,
    normalize_source_refs,
    resolve_cognitive_origin,
)
from core.db.knowledge_question_anchors import (
    BLOCKING_VERDICTS,
    support_reason,
    validate_linked_units,
    verify_anchor_support,
)
from core.db.relations import require_eligible_relation

logger = logging.getLogger(__name__)

# Tipos fechados do mestre (factual ausente, controversia, limite de
# interpretacao, pergunta de sentido): nao recebem o mesmo tratamento.
QUESTION_KINDS = frozenset(
    {"factual_absence", "source_controversy", "interpretation_limit", "sense_question"}
)

# Estados conceituais (mestre, contrato minimo): nao substituem ``status``.
QUESTION_STATES = frozenset(
    {
        "active",
        "postponed",
        "investigating",
        "answered",
        "reformulated",
        "abandoned",
        "blocked",
    }
)

STATE_ACTIVE = "active"
# Motivo obrigatorio: adiamento, abandono, bloqueio e reformulacao
# sempre; reabertura sempre que a transicao sai de um estado encerrado.
_REASON_ALWAYS = frozenset({"postponed", "abandoned", "blocked", "reformulated"})
_REOPEN_FROM = frozenset({"answered", "abandoned", "blocked", "reformulated"})

# Transicoes permitidas: encerrados so reabrem para ``active`` com
# motivo; ``answered`` so reabre ou e reformulado (mestre).
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    "active": frozenset(
        {"postponed", "investigating", "answered", "reformulated", "abandoned", "blocked"}
    ),
    "postponed": frozenset(
        {"active", "investigating", "answered", "reformulated", "abandoned", "blocked"}
    ),
    "investigating": frozenset(
        {"active", "postponed", "answered", "reformulated", "abandoned", "blocked"}
    ),
    "answered": frozenset({"active", "reformulated"}),
    "abandoned": frozenset({"active"}),
    "blocked": frozenset({"active"}),
    "reformulated": frozenset({"active"}),
}

# Verificacao de ancoras: ``core/db/knowledge_question_anchors.py``.

_MAX_TRANSITION_ATTEMPTS = 3


@contextmanager
def _db_lock(db: Any) -> Iterator[None]:
    """Lock do manager quando existe (mesmo contrato dos mixins)."""
    lock = getattr(db, "_lock", None)
    if lock is None:
        yield
        return
    with lock:
        yield


def _append_revision(
    db: Any,
    parent: Dict[str, Any],
    *,
    version: int,
    state: str,
    change_kind: str,
    reason: Optional[str],
    snapshot: Any,
) -> None:
    """Historico de revisoes da questao (derivado: apagado com a Relation)."""
    db.conn.execute(
        """
        INSERT INTO knowledge_gap_revisions
            (gap_id, agent_instance, origin_relation_id, question_version,
             question_state, change_kind, reason, snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parent["id"],
            parent.get("agent_instance"),
            parent.get("origin_relation_id"),
            version,
            state,
            change_kind,
            reason,
            snapshot if isinstance(snapshot, str) else json_payload(snapshot, {}),
        ),
    )


def _reused_result(gap_id: int, parent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gap_id": gap_id,
        "reused": True,
        "question_kind": parent.get("question_kind"),
        "question_state": parent.get("question_state"),
        "question_version": parent.get("question_version"),
        "reason": parent.get("question_state_reason"),
        "support": json.loads(parent.get("question_support_json") or "{}"),
    }


def register_candidate_question(
    db: Any,
    *,
    user_id: str,
    topic: str,
    the_gap: str,
    question_kind: str,
    source_kind: str,
    relevance_justification: str,
    source_refs: Any,
    importance: float = 0.5,
    relation_id: Optional[str] = None,
    origin_class: Optional[str] = None,
    agent_instance: Optional[str] = None,
    interpretation: Optional[Dict[str, Any]] = None,
    linked_unit_ids: Any = None,
    public_question: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Registra uma questao candidata em modo observacao.

    Reaproveita ``add_knowledge_gap`` (admissao F-5, gate C12g,
    quarentena e dedup R1a-4) e aplica os campos ``question_*`` com CAS em
    ``question_kind IS NULL``. ``None`` = politica recusou (nenhum falso
    sucesso); ``ValueError`` = erro de entrada, sem registro escrito;
    replay/recorrencia devolve ``reused=True`` sem tocar estado, versao,
    revisao nem ``importance_score``.
    """
    kind = str(question_kind or "").strip().lower()
    if kind not in QUESTION_KINDS:
        raise ValueError(f"question_kind_unknown:{question_kind}")
    if not str(the_gap or "").strip():
        raise ValueError("question_text_required")
    if not str(relevance_justification or "").strip():
        raise ValueError("relevance_justification_required")
    refs = normalize_source_refs(source_refs)
    if not refs:
        # A ancora sustenta o limite alegado: sem ela nao ha registro.
        raise ValueError("source_refs_required")

    # Mesma resolucao do writer interno: vale tambem para a sustentacao.
    instance, resolved_origin, resolved_relation, _participant = resolve_cognitive_origin(
        db,
        participant_user_id=user_id,
        relation_id=relation_id,
        origin_class=origin_class,
        agent_instance=agent_instance,
    )
    links = validate_linked_units(
        db, linked_unit_ids, agent_instance=instance, relation_id=resolved_relation
    )

    support = verify_anchor_support(
        db, refs, agent_instance=instance, relation_id=resolved_relation
    )
    blocking = [r for r, v in support["refs"].items() if v in BLOCKING_VERDICTS]
    state = "blocked" if blocking else STATE_ACTIVE
    reason = support_reason(support["refs"])

    gap_id = db.add_knowledge_gap(
        user_id,
        topic,
        the_gap,
        importance,
        relation_id=resolved_relation,
        origin_class=resolved_origin,
        agent_instance=instance,
        source_refs=refs,
        provenance=provenance,
        public_question=public_question,
        source_kind=source_kind,
        dedup_namespace="candidate_question",
    )
    if gap_id is None:
        # Politica recusou (gate C12g): falsa sucesso nenhum.
        return None

    cursor = db.conn.cursor()
    for _ in range(_MAX_TRANSITION_ATTEMPTS):
        row = cursor.execute(
            "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
        ).fetchone()
        if row is None:
            return None
        parent = dict(row)
        if parent.get("question_kind") is not None:
            # Recorrencia/replay: dedup ja mesclou ancoras; linha manda.
            return _reused_result(gap_id, parent)
        try:
            cursor.execute(
                """
                UPDATE knowledge_gaps SET question_kind = ?,
                    question_state = ?, question_version = 1,
                    question_interpretation_json = ?, relevance_justification = ?,
                    linked_unit_ids = ?, question_support_json = ?,
                    question_state_reason = ?
                WHERE id = ? AND question_kind IS NULL
                """,
                (
                    kind,
                    state,
                    json_payload(interpretation, {}),
                    str(relevance_justification).strip(),
                    json_payload(links, []),
                    json_payload(support, {}),
                    reason,
                    gap_id,
                ),
            )
            updated = cursor.rowcount
            if updated == 1:
                _append_revision(
                    db, parent,
                    version=1, state=state, change_kind="register",
                    reason=None,
                    snapshot=json_payload(interpretation, {}),
                )
            db.conn.commit()
        except sqlite3.Error:
            db.conn.rollback()
            logger.exception("falha ao registrar questao gap_id=%s", gap_id)
            raise
        if updated == 1:
            # R1c: a projecao de vinculos nasce junto com o registro (a
            # questao canonica ja esta commitada; falha aqui nunca propaga).
            _refresh_question_links_quietly(db, gap_id)
            return {
                "gap_id": gap_id,
                "reused": False,
                "question_kind": kind,
                "question_state": state,
                "question_version": 1,
                "reason": reason,
                "support": support,
            }
        # Outra conexao registrou antes: rele (replay, nunca regrava).
    logger.warning("questao candidata sem CAS gap_id=%s — releitura", gap_id)
    row = cursor.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()
    return _reused_result(gap_id, dict(row)) if row is not None else None


def transition_question_state(
    db: Any,
    gap_id: int,
    new_state: str,
    *,
    reason: Optional[str] = None,
    interpretation: Optional[Dict[str, Any]] = None,
    relevance_justification: Optional[str] = None,
    relation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Transiciona o estado conceitual de uma questao candidata.

    Motivo obrigatorio para ``postponed``/``abandoned``/``blocked``/
    ``reformulated`` e para qualquer reabertura de encerrado ->
    ``active``. Valida escopo da instancia/Relation (fail-closed),
    exige Relation elegivel e, na reabertura de questao bloqueada,
    revalida as ancoras e atualiza o veredito de sustentacao.
    """
    target = str(new_state or "").strip().lower()
    if target not in QUESTION_STATES:
        raise ValueError(f"question_state_unknown:{new_state}")
    clean_reason = str(reason or "").strip()
    new_interpretation: Any = None
    if interpretation is not None:
        if not isinstance(interpretation, dict):
            raise ValueError("question_interpretation_invalid")
        new_interpretation = json_payload(interpretation, {})
    new_relevance: Optional[str] = None
    if relevance_justification is not None:
        if not str(relevance_justification).strip():
            raise ValueError("relevance_justification_required")
        new_relevance = str(relevance_justification).strip()

    for _ in range(_MAX_TRANSITION_ATTEMPTS):
        with _db_lock(db):
            instance = cognitive_agent_instance(db, agent_instance)
            row = db.conn.execute(
                "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
            ).fetchone()
            if row is None:
                raise ValueError("question_not_found")
            parent = dict(row)
            if parent.get("question_kind") is None:
                raise ValueError("question_not_registered")
            if str(parent.get("agent_instance") or "") != str(instance):
                raise ValueError("question_out_of_scope")

            row_relation = parent.get("origin_relation_id")
            caller_relation = relation_id
            if user_id is not None:
                resolver = getattr(db, "resolve_relation_id", None)
                if callable(resolver):
                    caller_relation = resolver(
                        agent_instance=instance,
                        participant_user_id=user_id,
                        relation_id=relation_id,
                    )
            if row_relation:
                if caller_relation is not None and str(caller_relation) != str(row_relation):
                    raise ValueError("question_out_of_scope")
                if user_id is not None and caller_relation is None:
                    raise ValueError("question_out_of_scope")
                require_eligible_relation(db, str(row_relation))
            else:
                if caller_relation is not None:
                    raise ValueError("question_out_of_scope")

            current = str(parent.get("question_state") or STATE_ACTIVE)
            if target == current:
                raise ValueError(f"question_state_same:{target}")
            if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
                raise ValueError(f"question_transition_invalid:{current}->{target}")
            reason_required = target in _REASON_ALWAYS or current in _REOPEN_FROM
            if reason_required and not clean_reason:
                raise ValueError("question_state_reason_required")

            support_json_value = parent.get("question_support_json")
            support_obj = json.loads(support_json_value or "{}")
            if current == "blocked" and target == STATE_ACTIVE:
                stored_refs = json.loads(parent.get("source_refs_json") or "[]")
                if not stored_refs:
                    raise ValueError("source_refs_required")
                support_obj = verify_anchor_support(
                    db, stored_refs, agent_instance=instance, relation_id=row_relation
                )
                blocking = [
                    r for r, v in support_obj["refs"].items() if v in BLOCKING_VERDICTS
                ]
                if blocking:
                    block_reason = support_reason(support_obj["refs"])
                    raise ValueError(f"question_support_blocked:{block_reason}")
                support_json_value = json_payload(support_obj, {})

            version = int(parent.get("question_version") or 1) + 1
            if new_interpretation is not None:
                interpretation_value = new_interpretation
            else:
                interpretation_value = parent.get("question_interpretation_json")
            relevance_value = (
                new_relevance
                if new_relevance is not None
                else parent.get("relevance_justification")
            )
            cursor = db.conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE knowledge_gaps
                    SET question_state = ?, question_version = ?,
                        question_state_reason = ?,
                        question_interpretation_json = ?,
                        relevance_justification = ?,
                        question_support_json = ?
                    WHERE id = ? AND question_version = ? AND question_state = ?
                    """,
                    (
                        target,
                        version,
                        clean_reason or None,
                        interpretation_value,
                        relevance_value,
                        support_json_value,
                        gap_id,
                        version - 1,
                        current,
                    ),
                )
                if cursor.rowcount != 1:
                    # CAS perdida: outra conexao transicionou antes.
                    db.conn.rollback()
                    continue
                _append_revision(
                    db, parent,
                    version=version, state=target, change_kind=target,
                    reason=clean_reason or None,
                    snapshot=(
                        new_interpretation
                        if new_interpretation is not None
                        else (interpretation_value or "{}")
                    ),
                )
                db.conn.commit()
            except sqlite3.Error:
                db.conn.rollback()
                logger.exception(
                    "falha na transicao %s->%s gap_id=%s", current, target, gap_id
                )
                raise
            return {
                "gap_id": gap_id,
                "question_state": target,
                "question_version": version,
                "reason": clean_reason or None,
                "support": support_obj,
            }
    raise ValueError(f"question_transition_conflict:{gap_id}")


def question_read_scope(
    db: Any,
    user_id: str,
    *,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve o escopo fail-closed de leitura das questoes (R1a/R1c).

    Mesmo contrato de ``list_candidate_questions``: gate de elegibilidade
    da Relation ANTES de qualquer leitura; sem elegibilidade devolve
    ``None`` (o chamador decide o vazio), sem fallback global.
    """
    instance = cognitive_agent_instance(db, agent_instance)
    resolved_relation = None
    resolver = getattr(db, "resolve_relation_id", None)
    try:
        if callable(resolver):
            resolved_relation = resolver(
                agent_instance=instance,
                participant_user_id=user_id,
                relation_id=relation_id,
            )
        elif relation_id:
            resolved_relation = relation_id
        if resolved_relation:
            require_eligible_relation(db, resolved_relation)
    except ValueError as exc:
        logger.warning(
            "question_read_scope: escopo de Relation recusado "
            "user_id=%s relation=%s motivo=%s",
            user_id, relation_id, exc,
        )
        return None
    visibility = "origin_class IN (?, ?)"
    params: List[Any] = [instance, INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE]
    if resolved_relation:
        visibility = f"({visibility} OR origin_relation_id = ?)"
        params.append(resolved_relation)
    else:
        from instance_config import ADMIN_USER_ID
        if str(user_id) == str(ADMIN_USER_ID):
            visibility = (
                f"({visibility} OR (origin_relation_id IS NULL AND user_id = ?))"
            )
            params.append(user_id)
    return {
        "instance": instance,
        "relation": resolved_relation,
        "visibility": visibility,
        "params": params,
    }


def _question_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Apresenta a linha canonica com os campos decodificados."""
    item = dict(row)
    item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
    item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
    item["question_interpretation"] = json.loads(
        item.pop("question_interpretation_json") or "{}"
    )
    item["linked_unit_ids"] = json.loads(item.pop("linked_unit_ids") or "[]")
    item["question_support"] = json.loads(item.pop("question_support_json") or "{}")
    return item


def _refresh_question_links_quietly(db: Any, gap_id: int) -> None:
    """Pos-commit R1c: a projecao de vinculos acompanha o registro.

    Nunca propaga — falha vira log e o proximo evento de unidade ou o
    rebuild idempotente completa.
    """
    try:
        from core.db.knowledge_question_links import rebuild_question_unit_links

        rebuild_question_unit_links(db, int(gap_id))
    except Exception as exc:
        logger.warning(
            "knowledge_questions: projecao de vinculos falhou gap_id=%s tipo=%s",
            gap_id, type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Recuperacao de questoes pertinentes (R1c — Complemento R1)
# ---------------------------------------------------------------------------
# Estados conceituais recuperaveis por padrao: os encerrados
# (answered/abandoned/blocked) so entram por pedido explicito.
DEFAULT_RETRIEVAL_STATES = ("active", "investigating", "postponed", "reformulated")

# Mesma tecnica lexica local do R1c-1 (NFKC, caixa baixa, sem acentos);
# o ranker vetorial continua retido por decisao de custo do mantenedor.
# MAX_QUESTION_CANDIDATES e a janela de retencao APOS a pontuacao
# (revisao da PR #115): a selecao pontua TODA linha do escopo — o
# pre-filtro pelos primeiros 200 por id escondia questao pertinente de id
# posterior mesmo sem outras correspondencias. A janela guarda as
# melhores JA PONTUADAS (ordem de ranking, nunca de id) e sempre cobre o
# teto de saida (MAX_QUESTION_SEARCH_LIMIT), entao o corte nunca muda o
# resultado devolvido.
MAX_QUESTION_CANDIDATES = 200
MAX_QUESTION_SEARCH_LIMIT = 50


def _question_rank(row: Dict[str, Any]) -> Tuple[float, float, int]:
    """Chave de ranking SEM idade: pertinencia, importancia persistente,
    desempate deterministico por id. ``created_at`` nunca participa."""
    return (
        -float(row.get("pertinence_score") or 0.0),
        -float(row.get("importance_score") or 0.0),
        int(row.get("id") or 0),
    )


def search_knowledge_questions(
    db: Any,
    user_id: str,
    query: Optional[str] = None,
    *,
    limit: int = 10,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    states: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Recupera questoes pertinentes alem da janela de recencia (R1c).

    Ranking por pertinencia lexica LOCAL sobre o texto da questao e o
    texto das unidades vinculadas VIVAS (projecao ``active``); zero de
    sobreposicao com a consulta nao entra. **Idade nunca participa do
    ranking** (mestre: questao antiga nao ganha urgencia pelo tempo) e
    nada e gravado: leitura pura, ``importance_score`` intocado;
    ``age_days`` aparece so como metadado observavel. O suporte de cada
    resposta vem da projecao de vinculos (``support_state``), com a
    leitura reconfirmando o estado atual da unidade antes de tratar o
    vinculo como ativo: vinculo superado/em revisao nunca e apresentado
    como evidencia fresca. A selecao pontua TODA linha do escopo — nunca
    um pre-filtro por id — e o corte final e o ``limit`` do chamador.

    Escopo fail-closed igual ao de ``list_candidate_questions``;
    ``states`` aceita subconjunto de ``QUESTION_STATES``.
    """
    if states is None:
        wanted_states = DEFAULT_RETRIEVAL_STATES
    else:
        wanted_states = tuple(states)
        for state in wanted_states:
            if state not in QUESTION_STATES:
                raise ValueError(f"question_state_unknown:{state}")
        if not wanted_states:
            return []
    max_limit = max(1, min(int(limit), MAX_QUESTION_SEARCH_LIMIT))
    query_text = str(query or "").strip()
    with _db_lock(db):
        scope = question_read_scope(
            db, user_id, relation_id=relation_id, agent_instance=agent_instance
        )
        if scope is None:
            return []
        from core.db.knowledge_question_links import question_link_snapshot
        from core.db.knowledge_unit_index import search_tokens

        cursor = db.conn.cursor()
        state_marks = ",".join("?" * len(wanted_states))
        query_tokens = set(search_tokens(query_text)) if query_text else set()
        scored: List[Dict[str, Any]] = []
        offset = 0
        batch = 50
        # Varredura COMPLETA do escopo: cada linha e pontuada na hora e a
        # janela retem as melhores por ranking (revisao da PR #115 — o
        # pre-filtro pelos primeiros 200 por id escondia a pertinente de id
        # posterior). A janela cobre o teto de saida, entao nunca corta
        # resultado que o chamador veria.
        while True:
            cursor.execute(
                f"""
                SELECT * FROM knowledge_gaps
                WHERE agent_instance = ? AND question_kind IS NOT NULL
                  AND {scope["visibility"]} AND origin_quarantined = 0
                  AND question_state IN ({state_marks})
                ORDER BY id
                LIMIT ? OFFSET ?
                """,
                (*scope["params"], *wanted_states, batch, offset),
            )
            rows = cursor.fetchall()
            for row in rows:
                item = dict(row)
                # F-5e: afirmacao global passa pelo contrato REAL; a
                # recusa e exclusao na leitura, linha intacta.
                if not is_globally_readable(item, agent_instance=scope["instance"]):
                    continue
                presented = _question_item(item)
                snapshot = question_link_snapshot(db, presented["id"])
                score = 0.0
                if query_text:
                    own_tokens = set(
                        search_tokens(
                            " ".join(
                                str(presented.get(field) or "")
                                for field in (
                                    "topic",
                                    "the_gap",
                                    "public_question",
                                    "relevance_justification",
                                )
                            )
                        )
                    )
                    score = float(len(query_tokens & own_tokens))
                    best_unit = 0.0
                    for link in snapshot["links"]:
                        if str(link.get("link_state") or "") != "active":
                            continue
                        idx_row = cursor.execute(
                            "SELECT search_text FROM knowledge_unit_index"
                            " WHERE id = ?",
                            (int(link["unit_id"]),),
                        ).fetchone()
                        if idx_row is None:
                            continue
                        overlap = len(
                            query_tokens & set(search_tokens(idx_row["search_text"]))
                        )
                        best_unit = max(best_unit, float(overlap))
                    score += 0.5 * best_unit
                    if score <= 0.0:
                        # Sem pertinencia lexical a questao nao entra.
                        continue
                presented["pertinence_score"] = score
                presented["link_summary"] = snapshot["links"]
                presented["support_state"] = snapshot["support_state"]
                presented["age_days"] = _age_days(item.get("created_at"))
                scored.append(presented)
                if len(scored) > MAX_QUESTION_CANDIDATES:
                    scored.sort(key=_question_rank)
                    del scored[MAX_QUESTION_CANDIDATES:]
            if len(rows) < batch:
                break
            offset += batch

        # Ordenacao SEM idade: pertinencia, depois importancia persistente,
        # desempate deterministico por id. created_at nunca participa.
        scored.sort(key=_question_rank)
        return scored[:max_limit]


def _age_days(created_at: Any) -> Optional[int]:
    if not created_at:
        return None
    text = str(created_at).replace("Z", "+00:00")
    if "T" not in text:
        # CURRENT_TIMESTAMP do SQLite vem "YYYY-MM-DD HH:MM:SS".
        text = text.replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - moment).total_seconds() // 86400))


def list_candidate_questions(
    db: Any,
    user_id: str,
    limit: int = 3,
    *,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Le as questoes candidatas com o escopo fail-closed do R1a.

    Gate de elegibilidade da Relation ANTES de qualquer leitura (mesmo
    contrato de ``get_active_knowledge_gaps``): sem elegibilidade devolve
    ``[]``, sem fallback global; quarentena e admissao F-5 mandam.
    """
    with _db_lock(db):
        cursor = db.conn.cursor()
        scope = question_read_scope(
            db, user_id, relation_id=relation_id, agent_instance=agent_instance
        )
        if scope is None:
            return []
        params = scope["params"]

        questions: List[Dict[str, Any]] = []
        offset = 0
        batch = max(int(limit) * 10, 50)
        while len(questions) < int(limit):
            cursor.execute(
                f"""
                SELECT * FROM knowledge_gaps
                WHERE agent_instance = ? AND question_kind IS NOT NULL
                  AND {scope["visibility"]} AND origin_quarantined = 0
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, batch, offset),
            )
            rows = cursor.fetchall()
            for row in rows:
                item = dict(row)
                # F-5e: afirmacao global passa pelo contrato REAL; a
                # recusa e exclusao na leitura, linha intacta.
                if not is_globally_readable(item, agent_instance=scope["instance"]):
                    continue
                questions.append(_question_item(item))
                if len(questions) >= int(limit):
                    break
            if len(rows) < batch:
                break
            offset += batch
        return questions
