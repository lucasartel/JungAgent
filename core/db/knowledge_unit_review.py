"""Revisao e leitura de ``knowledge_units`` — R1b (segundo modulo).

Complementa ``core/db/knowledge_units.py``: a revisao registrada e a unica
via para ``validated_synthesis`` (revisor + motivo obrigatorios, replay
idempotente), com transicoes protegidas contra atualizacoes concorrentes
(CAS + releitura antes de gravar), e a leitura fail-closed por escopo — sem
Relation devolve apenas material global admitido; com Relation devolve o
privado da propria Relation mais o admitido da instancia, sempre apos o
gate de elegibilidade.

Fora do escopo: recuperacao semantica (R1c), sondas/cockpit e qualquer
efeito volitivo, prompt, envio ou custo.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from core.db.knowledge_units import (
    NON_VALIDATABLE_KINDS,
    REVIEW_DECISIONS,
    UNIT_STATUSES,
    VALIDATED_EPISTEMIC,
    _now_iso,
)
from core.db.relations import require_eligible_relation

logger = logging.getLogger(__name__)


class KnowledgeUnitReviewMixin:
    # ------------------------------------------------------------------
    # Revisao (unica via para validated_synthesis)
    # ------------------------------------------------------------------
    def review_knowledge_unit(
        self,
        unit_id: int,
        *,
        decision: str,
        reviewer: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Registra revisao humana/auditavel de uma unidade.

        Decisoes: ``validate`` (promove a ``validated_synthesis``), ``hold``
        (``under_review``) e ``retract`` (terminal). Revisao identica
        repetida nao regrava historico (idempotente). Falha fechada com
        ``ValueError``; revisor e motivo sao obrigatorios.

        Transicoes protegidas contra atualizacoes concorrentes: o ``UPDATE``
        so grava se o estado lido ainda e o vigente (CAS sobre status,
        epistemico e proveniencia); em corrida o metodo relê e revalida
        (maximo 3 tentativas) — ``review_transition_conflict`` quando o
        estado nao para de mudar. Uma escrita concorrente (retracao,
        supersede, expurgo, outra revisao) nunca e sobrescrita.
        """
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"invalid_review_decision:{decision}")
        if not str(reviewer or "").strip():
            raise ValueError("reviewer_required")
        if not str(reason or "").strip():
            raise ValueError("reason_required")

        instance = self._unit_instance()
        cursor = self.conn.cursor()
        try:
            for _ in range(3):
                cursor.execute(
                    """
                    SELECT id, status, epistemic_status, unit_kind,
                           provenance_json
                    FROM knowledge_units
                    WHERE id = ? AND agent_instance = ?
                    """,
                    (int(unit_id), instance),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("unit_not_found")
                row_dict = dict(row)
                current_status = str(row_dict["status"])
                current_epistemic = str(row_dict["epistemic_status"])

                if decision == "validate":
                    if row_dict["unit_kind"] in NON_VALIDATABLE_KINDS:
                        raise ValueError("validated_synthesis_not_applicable")
                    if current_status in {"superseded", "retracted"}:
                        raise ValueError(f"invalid_unit_transition:{current_status}")
                    if current_epistemic == VALIDATED_EPISTEMIC and current_status == "active":
                        return {
                            "unit_id": int(unit_id),
                            "changed": False,
                            "status": current_status,
                            "epistemic_status": current_epistemic,
                        }
                    new_status = "active"
                    new_epistemic = VALIDATED_EPISTEMIC
                elif decision == "hold":
                    if current_status in {"superseded", "retracted"}:
                        raise ValueError(f"invalid_unit_transition:{current_status}")
                    if current_status == "under_review":
                        return {
                            "unit_id": int(unit_id),
                            "changed": False,
                            "status": current_status,
                            "epistemic_status": current_epistemic,
                        }
                    new_status = "under_review"
                    new_epistemic = current_epistemic
                else:  # retract
                    if current_status == "superseded":
                        raise ValueError(f"invalid_unit_transition:{current_status}")
                    if current_status == "retracted":
                        return {
                            "unit_id": int(unit_id),
                            "changed": False,
                            "status": current_status,
                            "epistemic_status": current_epistemic,
                        }
                    new_status = "retracted"
                    new_epistemic = current_epistemic

                try:
                    provenance = json.loads(row_dict.get("provenance_json") or "{}")
                except ValueError:
                    provenance = {}
                if not isinstance(provenance, dict):
                    provenance = {}
                history = provenance.get("review_history")
                if not isinstance(history, list):
                    history = []
                history.append(
                    {
                        "decision": decision,
                        "reviewer": str(reviewer),
                        "reason": str(reason),
                        "at": _now_iso(),
                    }
                )
                provenance["review_history"] = history
                now = _now_iso()
                cursor.execute(
                    """
                    UPDATE knowledge_units
                    SET status = ?, epistemic_status = ?, provenance_json = ?,
                        updated_at = ?
                    WHERE id = ? AND agent_instance = ?
                      AND status = ? AND epistemic_status = ?
                      AND provenance_json = ?
                    """,
                    (
                        new_status,
                        new_epistemic,
                        json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                        now,
                        int(unit_id),
                        instance,
                        current_status,
                        current_epistemic,
                        row_dict["provenance_json"],
                    ),
                )
                if cursor.rowcount == 1:
                    self.conn.commit()
                    return {
                        "unit_id": int(unit_id),
                        "changed": True,
                        "status": new_status,
                        "epistemic_status": new_epistemic,
                    }
                # Corrida: o estado mudou entre a leitura e a escrita e o
                # UPDATE nao tocou nenhuma linha. Fecha a transacao vazia e
                # relê/revalida contra o estado novo — nada do escritor
                # concorrente e sobrescrito.
                self.conn.rollback()
            raise ValueError("review_transition_conflict")
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Leitura (fail-closed)
    # ------------------------------------------------------------------
    def list_knowledge_units(
        self,
        *,
        relation_id: Optional[str] = None,
        statuses: Iterable[str] = ("active",),
        validated_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Lista unidades do escopo pedido.

        Sem ``relation_id``: somente material global admitido da instancia
        (nunca privado de ninguem). Com ``relation_id``: gate de
        elegibilidade + privado da propria Relation + admitido da instancia.
        """
        instance = self._unit_instance()
        wanted = tuple(dict.fromkeys(str(s) for s in statuses))
        if not wanted or any(s not in UNIT_STATUSES for s in wanted):
            raise ValueError("invalid_unit_status")
        bounded = max(1, min(int(limit), 200))

        placeholders = ",".join("?" for _ in wanted)
        params: List[Any] = [instance, *wanted]
        if relation_id:
            relation = require_eligible_relation(self, relation_id)
            scope_sql = f"""
                AND (
                    (ownership_class = 'relation_private' AND origin_relation_id = ?)
                    OR (ownership_class IN ('authorized_aggregate', 'instance_global')
                        AND origin_quarantined = 0)
                )
            """
            params.append(str(relation["relation_id"]))
        else:
            scope_sql = """
                AND ownership_class IN ('authorized_aggregate', 'instance_global')
                AND origin_quarantined = 0
            """
        validated_sql = "AND epistemic_status = ?" if validated_only else ""
        if validated_only:
            params.append(VALIDATED_EPISTEMIC)

        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT id, content, unit_kind, content_version, status,
                   epistemic_status, ownership_class, origin_class,
                   origin_relation_id, source_kind, source_refs_json,
                   observation_count, created_at, updated_at
            FROM knowledge_units
            WHERE agent_instance = ?
              AND status IN ({placeholders})
              {scope_sql}
              {validated_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, bounded),
        )
        rows: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            item = dict(row)
            try:
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
            except ValueError:
                item["source_refs"] = []
            rows.append(item)
        return rows


