"""Unidades de conhecimento persistidas — R1b (consolidacao conceitual).

Primeiro subcorte do **R1b** (§10.1.2 do Documento Mestre): unidades de
conhecimento persistidas a partir de material elaborado, com referencia a
fonte verificavel e selecao de compreensao distinta de extracao.

Contrato: escopo C12 por linha (``agent_instance`` + propriedade/
``origin_relation_id``); material privado exige Relation elegivel e material
global nasce so pela admissao F-5 (``assess_global_admission``), nunca por
declaracao do chamador — inclusive no reforco de uma unidade global ja
existente (escrita recusada nao estende referencias alheias). Toda unidade
nova carrega ao menos uma ancora ``tipo#id`` valida. A admissao so grava
``source_claim``, ``extractive_only`` ou ``uncertain``: ``validated_synthesis`` nasce exclusivamente da revisao
registrada (``review_knowledge_unit``, revisor e motivo obrigatorios) —
extracao contingente ou hipotese interna nunca e promovida; sem
``confidence`` fabricado. Recorrencia reforca a unidade existente
(``observation_count`` +1, ancoras mescladas) e nunca altera epistemico,
status ou propriedade; ``supersedes_unit_id`` cria nova versao marcando a
anterior ``superseded`` na mesma transacao. Leitura fail-closed em
``core/db/knowledge_unit_review.py``; ``knowledge_units`` entrou no mapa de
redacao de ``core/db/erase.py`` (tombstone auditavel).

Fora do escopo: a recuperacao por consulta e a fila de indexacao (R1c-1,
em ``core/db/knowledge_unit_index.py``), confianca justificada,
sondas/cockpit de observabilidade e qualquer efeito volitivo, prompt,
envio ou custo.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from core.db.cognitive_provenance import (
        cognitive_agent_instance,
        normalize_source_refs,
    )
except ImportError:  # Direct-file tests deliberately avoid importing core.__init__.
    _path = Path(__file__).with_name("cognitive_provenance.py")
    _spec = importlib.util.spec_from_file_location("cognitive_provenance_for_units", _path)
    _provenance = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_provenance)
    cognitive_agent_instance = _provenance.cognitive_agent_instance
    normalize_source_refs = _provenance.normalize_source_refs

try:
    from core.db.source_kinds import validate_unit_source_kind
except ImportError:  # Same direct-file guard as above.
    _sk_path = Path(__file__).with_name("source_kinds.py")
    _sk_spec = importlib.util.spec_from_file_location("source_kinds_for_units", _sk_path)
    _sk_mod = importlib.util.module_from_spec(_sk_spec)
    assert _sk_spec.loader is not None
    _sk_spec.loader.exec_module(_sk_mod)
    validate_unit_source_kind = _sk_mod.validate_unit_source_kind

try:
    from core.db.relations import require_eligible_relation
except ImportError:  # Same direct-file guard (gate canonico C12g).
    _rel_path = Path(__file__).with_name("relations.py")
    _rel_spec = importlib.util.spec_from_file_location("relations_for_units", _rel_path)
    _rel_mod = importlib.util.module_from_spec(_rel_spec)
    assert _rel_spec.loader is not None
    _rel_spec.loader.exec_module(_rel_mod)
    require_eligible_relation = _rel_mod.require_eligible_relation

try:
    from core.db.cognitive_ownership import (  # noqa: F401
        OUTCOME_ADMIT_GLOBAL,
        ROUTE_PUBLIC_SOURCE,
        assess_global_admission,
    )
except ImportError:  # Same direct-file guard (admissao F-5).
    _own_path = Path(__file__).with_name("cognitive_ownership.py")
    _own_spec = importlib.util.spec_from_file_location(
        "cognitive_ownership_for_units", _own_path
    )
    _own_mod = importlib.util.module_from_spec(_own_spec)
    assert _own_spec.loader is not None
    _own_spec.loader.exec_module(_own_mod)
    OUTCOME_ADMIT_GLOBAL = _own_mod.OUTCOME_ADMIT_GLOBAL
    ROUTE_PUBLIC_SOURCE = _own_mod.ROUTE_PUBLIC_SOURCE
    assess_global_admission = _own_mod.assess_global_admission

logger = logging.getLogger(__name__)

# Conjuntos fechados (R1b): kind, estado epistemico e status de linha.
UNIT_KINDS = frozenset(
    {"concept", "distinction", "argument", "example", "open_question", "controversy"}
)
# Kind que NUNCA vira sintese validada: pergunta aberta continua aberta e
# controversia continua contestada — promover seria fechar artificialmente.
NON_VALIDATABLE_KINDS = frozenset({"open_question", "controversy"})
ADMISSION_EPISTEMIC_STATUSES = frozenset(
    {"source_claim", "extractive_only", "uncertain"}
)
VALIDATED_EPISTEMIC = "validated_synthesis"
UNIT_EPISTEMIC_STATUSES = ADMISSION_EPISTEMIC_STATUSES | {VALIDATED_EPISTEMIC}
UNIT_STATUSES = frozenset({"active", "under_review", "superseded", "retracted"})
OWNERSHIP_INTENTS = frozenset({"relation_private", "global"})

REVIEW_DECISIONS = frozenset({"validate", "hold", "retract"})

# Piso de legibilidade — criterio do R1b, NAO prova de validacao: conteudo
# trivial nao vira unidade, mas passar aqui nunca promove a validated.
MIN_UNIT_CONTENT_CHARS = 40

# Mesma normalizacao conservadora de core/db/knowledge_gaps.py: NFKC,
# casefold, espacos colapsados, pontuacao de frase removida so nas
# extremidades. Sem semantica, sem stemming — o conteudo original nunca e
# reescrito.
_EDGE_SENTENCE_PUNCT = ".,;:!?…\"'“”‘’"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def unit_fingerprint(text: Any) -> str:
    """Chave de identidade do conteudo de uma unidade (ortografica)."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    collapsed = " ".join(normalized.split())
    return collapsed.strip(_EDGE_SENTENCE_PUNCT).strip()


def knowledge_unit_key(
    *, declared_ownership: str, relation_id: Optional[str],
    participant: Optional[str], fingerprint: str,
) -> str:
    """Escopo declarado da unidade — a classe GRAVADA nao entra na chave.

    Mesma decisao do dedup R1a-4: se a admissao recusar o global e gravar
    ``relation_private``, uma escrita seguinte identica deve reforcar a
    mesma linha, nao criar uma segunda. Relation e participante diferenciam
    donos distintos com o mesmo conteudo.
    """
    return "|".join(
        [
            str(declared_ownership or ""),
            str(relation_id or ""),
            str(participant or ""),
            str(fingerprint or ""),
        ]
    )


def unit_scope_clause(
    db: Any, relation_id: Optional[str], *, alias: str = ""
) -> tuple[str, List[Any]]:
    """Clause SQL de escopo fail-closed — regra unica das leituras R1b/R1c.

    Sem ``relation_id``: somente material global admitido da instancia
    (nunca privado de ninguem). Com ``relation_id``: a Relation passa pelo
    gate de elegibilidade antes de qualquer leitura e a consulta ve o
    privado dela mais o admitido da instancia. ``alias`` qualifica as
    colunas quando a juncao usa mais de uma tabela.
    """
    prefix = f"{alias}." if alias else ""
    if relation_id:
        relation = require_eligible_relation(db, relation_id)
        scope_sql = f"""
                AND (
                    ({prefix}ownership_class = 'relation_private'
                     AND {prefix}origin_relation_id = ?)
                    OR ({prefix}ownership_class IN ('authorized_aggregate', 'instance_global')
                        AND {prefix}origin_quarantined = 0)
                )
            """
        return scope_sql, [str(relation["relation_id"])]
    scope_sql = f"""
            AND {prefix}ownership_class IN ('authorized_aggregate', 'instance_global')
            AND {prefix}origin_quarantined = 0
        """
    return scope_sql, []


class KnowledgeUnitDatabaseMixin:
    """Escrita/leitura gateada de ``knowledge_units`` (R1b)."""

    # ------------------------------------------------------------------
    # Escopo
    # ------------------------------------------------------------------
    def _unit_instance(self) -> str:
        return cognitive_agent_instance(self)

    def _unit_relation_scope(
        self,
        *,
        instance: str,
        relation_id: Optional[str],
        participant_user_id: Optional[str],
    ) -> tuple[str, Dict[str, Any]]:
        """Resolve e exige elegibilidade da Relation (fail-closed)."""
        resolver = getattr(self, "resolve_relation_id", None)
        if not callable(resolver):
            raise ValueError("relation_scope_unavailable_for_knowledge_unit")
        resolved = resolver(
            agent_instance=instance,
            participant_user_id=participant_user_id,
            relation_id=relation_id,
        )
        if not resolved:
            raise ValueError("relation_required_for_knowledge_unit")
        relation = require_eligible_relation(self, resolved)
        return str(resolved), relation

    def _refresh_index_after_commit(self, unit_id: int, *more: int) -> None:
        """Pos-commit R1c-1: a projecao acompanha a escrita ja gravada.

        Roda so depois do commit e nunca propaga: falha de indexacao vira
        log + item pendente na fila (backfill do scheduler completa), sem
        desfazer a unidade persistida.
        """
        try:
            from core.db.knowledge_unit_index import refresh_unit_index
        except Exception as exc:
            logger.warning(
                "knowledge_units: modulo de indexacao indisponivel tipo=%s",
                type(exc).__name__,
            )
            return
        for uid in (unit_id, *more):
            refresh_unit_index(self, int(uid))
        self._refresh_question_links_after_commit(unit_id, *more)

    def _refresh_question_links_after_commit(self, unit_id: int, *more: int) -> None:
        """Pos-commit R1c: o vinculo das questoes acompanha a unidade.

        Mesmo contrato do refresh da projecao de indexacao: roda depois do
        commit e nunca propaga — falha vira log, e o proximo evento de
        unidade ou o rebuild idempotente por questao completa.
        """
        try:
            from core.db.knowledge_question_links import (
                refresh_question_links_for_unit,
            )
        except Exception as exc:
            logger.warning(
                "knowledge_units: modulo de vinculos de questao indisponivel tipo=%s",
                type(exc).__name__,
            )
            return
        for uid in (unit_id, *more):
            try:
                refresh_question_links_for_unit(self, int(uid))
            except Exception as exc:
                logger.warning(
                    "knowledge_question_links: refresh pos-commit falhou "
                    "unit_id=%s tipo=%s",
                    uid,
                    type(exc).__name__,
                )

    # ------------------------------------------------------------------
    # Admissao (R1b)
    # ------------------------------------------------------------------
    def consolidate_knowledge_unit(
        self,
        *,
        content: str,
        unit_kind: str,
        epistemic_status: str,
        source_kind: str,
        source_refs: Optional[Iterable[str]] = None,
        relation_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        ownership: str = "relation_private",
        supersedes_unit_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Grava (ou reforca) uma unidade de conhecimento sob os gates R1b.

        Retorna dict com ``unit_id``, ``reinforced``, status epistemico,
        classe gravada e ``origin_quarantined``. ``ValueError`` = recusa
        auditavel; nada e escrito quando a validacao falha.
        """
        instance = self._unit_instance()

        # --- validacoes puras (antes de qualquer escrita) ---
        if not isinstance(content, str) or len(content.strip()) < MIN_UNIT_CONTENT_CHARS:
            raise ValueError("unit_content_too_short")
        if unit_kind not in UNIT_KINDS:
            raise ValueError(f"invalid_unit_kind:{unit_kind}")
        if epistemic_status not in UNIT_EPISTEMIC_STATUSES:
            raise ValueError(f"invalid_epistemic_status:{epistemic_status}")
        if epistemic_status == VALIDATED_EPISTEMIC:
            raise ValueError("validated_synthesis_requires_review")
        if ownership not in OWNERSHIP_INTENTS:
            raise ValueError(f"invalid_ownership:{ownership}")
        if unit_kind in NON_VALIDATABLE_KINDS and epistemic_status == VALIDATED_EPISTEMIC:
            raise ValueError("validated_synthesis_not_applicable")
        checked_kind = validate_unit_source_kind(source_kind)
        refs = normalize_source_refs(source_refs)
        if not refs:
            raise ValueError("source_refs_required")

        declared = str(ownership)
        private_derived = ownership == "relation_private"
        stored_relation: Optional[str] = None
        stored_participant: Optional[str] = None
        ownership_class: str
        origin_class: str
        origin_quarantined = 0
        admission: Optional[Dict[str, str]] = None
        admission_refused: Optional[str] = None
        relation_row: Optional[Dict[str, Any]] = None

        if ownership == "relation_private":
            stored_relation, relation_row = self._unit_relation_scope(
                instance=instance,
                relation_id=relation_id,
                participant_user_id=participant_user_id,
            )
            ownership_class = "relation_private"
            origin_class = "relation_private"
            stored_participant = str(
                relation_row.get("participant_user_id") or participant_user_id or ""
            )
        else:
            # Global: somente a rota publica do F-5. Projetar conteudo
            # PRIVADO para o agregado exige revisao de conteudo e politica
            # de retratacao proprias (decisao do mantenedor, 05/11/2026:
            # "isso e o R1 completo (R1b/R1c) e exige decisao propria").
            # Opt-in de interioridade nao abre leitura interpessoal.
            if str(relation_id or "").strip() or str(participant_user_id or "").strip():
                raise ValueError("global_unit_requires_public_source")
            decision = assess_global_admission(
                agent_instance=instance,
                route=ROUTE_PUBLIC_SOURCE,
                source_kind=checked_kind,
                source_refs=refs,
                private_derived=False,
            )
            admission = {
                "route": decision.route,
                "outcome": decision.outcome,
                "reason": decision.reason,
            }
            if decision.outcome == OUTCOME_ADMIT_GLOBAL:
                ownership_class = "authorized_aggregate"
                origin_class = "authorized_aggregate"
            else:
                # Sem vinculo comprovado: quarentena explicita com motivo
                # persistido (condicao 1). A linha existe para auditoria,
                # mas nenhum leitor a serve.
                ownership_class = "instance_global"
                origin_class = "instance_global"
                origin_quarantined = 1
                admission_refused = decision.reason

        fingerprint = unit_fingerprint(content)
        unit_key = knowledge_unit_key(
            declared_ownership=declared,
            relation_id=stored_relation,
            participant=stored_participant,
            fingerprint=fingerprint,
        )

        # --- linhagem de revisao ---
        previous: Optional[Dict[str, Any]] = None
        if supersedes_unit_id is not None:
            previous = self._load_unit_for_supersede(
                instance=instance, unit_id=supersedes_unit_id
            )
            if unit_fingerprint(previous.get("content")) == fingerprint:
                raise ValueError("supersede_requires_revised_content")
            if str(previous.get("origin_relation_id") or "") != str(stored_relation or ""):
                raise ValueError("supersede_scope_mismatch")

        provenance: Dict[str, Any] = {
            "declared_ownership": declared,
            "private_derived": private_derived,
            "epistemic_status_at_write": epistemic_status,
            "review_history": [],
        }
        if admission is not None:
            provenance["admission"] = admission
        if admission_refused is not None:
            provenance["admission_refused"] = admission_refused
        if previous is not None:
            provenance["supersedes_unit_id"] = int(previous["id"])

        content_version = int(previous["content_version"]) + 1 if previous else 1
        now = _now_iso()
        cursor = self.conn.cursor()
        try:
            for _ in range(3):
                cursor.execute(
                    """
                    SELECT id, status, observation_count, source_refs_json,
                           epistemic_status, ownership_class, content_version,
                           origin_quarantined
                    FROM knowledge_units
                    WHERE agent_instance = ? AND unit_key = ?
                    """,
                    (instance, unit_key),
                )
                existing = cursor.fetchone()
                if existing is None:
                    break
                # Admissao F-5 tambem governa o REFORCO: uma escrita global
                # RECUSADA nao soma observacao nem acrescenta referencias a
                # unidade global existente — a recusa vira erro auditavel e
                # nada e escrito (mesma condicao 1 da quarentena na criacao).
                if admission_refused is not None:
                    raise ValueError(
                        f"reinforce_admission_refused:{admission_refused}"
                    )
                existing_dict = dict(existing)
                status = str(existing_dict["status"])
                if status in {"superseded", "retracted"}:
                    raise ValueError(f"unit_key_not_reinforceable:{status}")
                # Reforco idempotente: nunca muda epistemico/status/propriedade.
                merged_refs = _merge_refs(existing_dict.get("source_refs_json"), refs)
                cursor.execute(
                    """
                    UPDATE knowledge_units
                    SET observation_count = observation_count + 1,
                        last_observed_at = ?,
                        updated_at = ?,
                        source_refs_json = ?
                    WHERE id = ?
                    """,
                    (now, now, json.dumps(merged_refs, ensure_ascii=False), existing_dict["id"]),
                )
                self.conn.commit()
                self._refresh_index_after_commit(int(existing_dict["id"]))
                return {
                    "unit_id": int(existing_dict["id"]),
                    "reinforced": True,
                    "status": status,
                    "epistemic_status": existing_dict["epistemic_status"],
                    "ownership_class": existing_dict["ownership_class"],
                    "origin_quarantined": int(existing_dict.get("origin_quarantined") or 0),
                    "content_version": int(existing_dict["content_version"]),
                    "observation_count": int(existing_dict["observation_count"]) + 1,
                }
            else:
                raise ValueError("unit_reinforce_conflict")

            cursor.execute(
                """
                INSERT INTO knowledge_units (
                    unit_key, content, unit_kind, content_version, status,
                    epistemic_status, agent_instance, ownership_class,
                    origin_class, origin_relation_id,
                    origin_participant_user_id, source_kind, source_refs_json,
                    provenance_json, origin_quarantined, observation_count,
                    last_observed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    unit_key,
                    content.strip(),
                    unit_kind,
                    content_version,
                    epistemic_status,
                    instance,
                    ownership_class,
                    origin_class,
                    stored_relation,
                    stored_participant,
                    checked_kind,
                    json.dumps(refs, ensure_ascii=False),
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    origin_quarantined,
                    now,
                    now,
                    now,
                ),
            )
            unit_id = int(cursor.lastrowid)
            if previous is not None:
                cursor.execute(
                    """
                    UPDATE knowledge_units
                    SET status = 'superseded', superseded_by = ?, updated_at = ?
                    WHERE id = ? AND agent_instance = ?
                      AND status IN ('active', 'under_review')
                    """,
                    (unit_id, now, int(previous["id"]), instance),
                )
                if cursor.rowcount != 1:
                    raise ValueError("supersede_conflict")
            self.conn.commit()
            # Projecao da unidade nova; a superada sai da projecao na hora.
            if previous is not None:
                self._refresh_index_after_commit(unit_id, int(previous["id"]))
            else:
                self._refresh_index_after_commit(unit_id)
        except Exception:
            self.conn.rollback()
            raise

        return {
            "unit_id": unit_id,
            "reinforced": False,
            "status": "active",
            "epistemic_status": epistemic_status,
            "ownership_class": ownership_class,
            "origin_class": origin_class,
            "origin_quarantined": origin_quarantined,
            "content_version": content_version,
            "observation_count": 1,
        }

    def _load_unit_for_supersede(self, *, instance: str, unit_id: int) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, content, content_version, status, origin_relation_id
            FROM knowledge_units
            WHERE id = ? AND agent_instance = ?
            """,
            (int(unit_id), instance),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("invalid_supersede_source")
        row_dict = dict(row)
        if row_dict["status"] not in {"active", "under_review"}:
            raise ValueError(f"invalid_supersede_source:{row_dict['status']}")
        return row_dict

def _merge_refs(stored_json: Any, incoming: Iterable[str]) -> List[str]:
    """Mescla ancoras sem duplicar (reforco R1b — como o R1a-4)."""
    try:
        stored = json.loads(stored_json or "[]")
    except ValueError:
        stored = []
    merged: List[str] = []
    for ref in list(stored or []) + list(incoming or []):
        clean = str(ref or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged
