from __future__ import annotations

import logging
import json
import importlib.util
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from core.db import cognitive_provenance as _provenance
except ImportError:  # Direct-file tests deliberately avoid importing core.__init__.
    _path = Path(__file__).with_name("cognitive_provenance.py")
    _spec = importlib.util.spec_from_file_location("cognitive_provenance_for_gaps", _path)
    _provenance = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_provenance)

try:
    from core.db.source_kinds import validate_gap_source_kind
except ImportError:  # Same direct-file guard as above.
    _sk_path = Path(__file__).with_name("source_kinds.py")
    _sk_spec = importlib.util.spec_from_file_location("source_kinds_for_gaps", _sk_path)
    _sk_mod = importlib.util.module_from_spec(_sk_spec)
    assert _sk_spec.loader is not None
    _sk_spec.loader.exec_module(_sk_mod)
    validate_gap_source_kind = _sk_mod.validate_gap_source_kind

AUTHORIZED_AGGREGATE = _provenance.AUTHORIZED_AGGREGATE
INSTANCE_GLOBAL = _provenance.INSTANCE_GLOBAL
cognitive_agent_instance = _provenance.cognitive_agent_instance
cognitive_provenance = _provenance.cognitive_provenance
json_payload = _provenance.json_payload
normalize_source_refs = _provenance.normalize_source_refs
resolve_cognitive_origin = _provenance.resolve_cognitive_origin

logger = logging.getLogger(__name__)

# Marcador de proveniencia indeterminada (R1a-4). Nao e 'legacy': legado
# historico nunca recebeu origem; indeterminada e uma escrita NOVA cujo
# produtor nao pertence ao conjunto fechado. Nao promove a material global
# autorizado — so distingue as duas situacoes.
INDETERMINATE_ORIGIN_KEY = "origin_indeterminate"


def gap_fingerprint(text: Any) -> str:
    """Chave de identidade de uma pergunta — normalizacao conservadora.

    Ortografica, sem semantica: NFKC, casefold, pontuacao (categoria Unicode
    ``P*``) vira espaco, espacos colapsados. Sem stemming, sem stopwords, sem
    similaridade entre frases. O ``the_gap`` original nunca e reescrito.
    """
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    without_punct = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch for ch in normalized
    )
    return " ".join(without_punct.split())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gap_provenance(
    base: Dict[str, Any],
    *,
    source_kind: Optional[str],
    indeterminate_reason: Optional[str],
) -> Dict[str, Any]:
    """Proveniencia da lacuna, com marcador quando o produtor e indeterminado."""
    payload = dict(base or {})
    if indeterminate_reason:
        payload[INDETERMINATE_ORIGIN_KEY] = True
        payload[f"{INDETERMINATE_ORIGIN_KEY}_reason"] = indeterminate_reason
        payload[f"{INDETERMINATE_ORIGIN_KEY}_pending"] = "R1a-4"
    return payload


class KnowledgeGapDatabaseMixin:
    # ------------------------------------------------------------------
    # R1a-4: identidade, recorrencia e observacao de uma lacuna
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_gap_refs(stored: Any, incoming: Iterable[str]) -> Tuple[List[str], bool]:
        """Une ancoras preservando a ordem; ``(mescladas, ha_ancora_nova)``."""
        merged: List[str] = []
        known = set()
        for ref in list(stored or []):
            if ref not in known:
                known.add(ref)
                merged.append(ref)
        has_new = False
        for ref in list(incoming or []):
            if ref and ref not in known:
                known.add(ref)
                merged.append(ref)
                has_new = True
        return merged, has_new

    def _observe_existing_gap(
        self,
        cursor: Any,
        *,
        instance: str,
        origin_class: str,
        relation_id: Optional[str],
        fingerprint: str,
        incoming_refs: List[str],
    ) -> Optional[Tuple[int, str]]:
        """Procura a mesma pergunta no MESMO escopo de propriedade.

        Escopo: ``agent_instance`` + ``origin_class`` + ``relation_id`` + o
        ``fingerprint`` normalizado. Nunca cruza instancia, classe de
        propriedade ou Relation.

        Comportamento por status (contrato R1a-4):
        - ``open`` / ``investigating`` / ``resolved`` / ``rejected``: a linha
          e reutilizada. O status **nao muda**, ``the_gap`` **nao e
          reescrito**, ``importance_score`` **nao e tocado**.
        - recorrencia real (ancora nova) -> ``observation_count + 1``,
          ``last_observed_at`` e ancoras anexadas;
        - retry do mesmo evento (ancora ja conhecida ou nenhuma ancora nova)
          -> sem efeito no contador.

        Linhas historicas (``dedup_key`` NULL) nunca sao alcancadas: nao ha
        backfill, entao dados pre-existentes nao sao alterados.
        """
        cursor.execute(
            """
            SELECT id, source_refs_json, observation_count
            FROM knowledge_gaps
            WHERE agent_instance = ? AND origin_class = ?
              AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
              AND dedup_key = ?
            ORDER BY id
            LIMIT 1
            """,
            (instance, origin_class, relation_id, fingerprint),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        gap_id = int(row["id"])
        stored_refs = json.loads(row["source_refs_json"] or "[]")
        merged, has_new_anchor = self._merge_gap_refs(stored_refs, incoming_refs)
        if has_new_anchor:
            cursor.execute(
                """
                UPDATE knowledge_gaps
                SET observation_count = COALESCE(observation_count, 0) + 1,
                    last_observed_at = ?,
                    source_refs_json = ?
                WHERE id = ?
                """,
                (_now_iso(), json.dumps(merged, ensure_ascii=False), gap_id),
            )
            return gap_id, "recurrence"
        return gap_id, "retry"

    def add_knowledge_gap(
        self,
        user_id: str,
        topic: str,
        the_gap: str,
        importance: float = 0.5,
        *,
        relation_id: Optional[str] = None,
        origin_class: Optional[str] = None,
        agent_instance: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        public_question: Optional[str] = None,
        private_trigger: Optional[Dict[str, Any]] = None,
        source_kind: Optional[str] = None,
    ) -> Optional[int]:
        """Adiciona uma nova lacuna de conhecimento (gap) para o usuÃ¡rio"""
        with self._lock:
            instance, resolved_origin, resolved_relation, participant = resolve_cognitive_origin(
                self,
                participant_user_id=user_id,
                relation_id=relation_id,
                origin_class=origin_class,
                agent_instance=agent_instance,
            )
            gap_kind, kind_reason = validate_gap_source_kind(
                source_kind, allow_null=source_kind is None
            )
            if kind_reason and source_kind is not None:
                # Proveniencia desconhecida numa escrita nova: quarentena de
                # origem (marcador explicito), sem promover a global autorizado
                # e sem interromper quem chamou.
                logger.warning(
                    "knowledge_gap proveniencia indeterminada user_id=%s motivo=%s",
                    user_id, kind_reason,
                )
            refs = normalize_source_refs(source_refs)
            provenance_payload = _gap_provenance(
                cognitive_provenance(
                    origin_class=resolved_origin,
                    relation_id=resolved_relation,
                    participant_user_id=participant,
                    extra=provenance,
                ),
                source_kind=gap_kind,
                indeterminate_reason=kind_reason,
            )
            fingerprint = gap_fingerprint(the_gap)
            cursor = self.conn.cursor()

            observed = self._observe_existing_gap(
                cursor,
                instance=instance,
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                fingerprint=fingerprint,
                incoming_refs=refs,
            )
            if observed:
                self.conn.commit()
                return observed[0]

            cursor.execute("""
                INSERT INTO knowledge_gaps (
                    user_id, agent_instance, ownership_class, origin_class,
                    origin_relation_id, origin_participant_user_id,
                    source_refs_json, provenance_json, topic, the_gap,
                    importance_score, public_question, private_trigger_json,
                    status, source_kind, dedup_key, observation_count,
                    last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 1, ?)
            """, (
                user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                resolved_relation, participant,
                json_payload(refs, []),
                json_payload(provenance_payload, {}), topic, the_gap, importance,
                public_question, json_payload(private_trigger, {}) if private_trigger else None,
                gap_kind, fingerprint, _now_iso(),
            ))

            self.conn.commit()
            return cursor.lastrowid

    def upsert_epistemic_knowledge_gap(
        self,
        user_id: str,
        knowledge_gap: Dict,
        importance: float = 0.72,
        *,
        relation_id: Optional[str] = None,
        origin_class: Optional[str] = None,
        agent_instance: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        source_kind: Optional[str] = None,
    ) -> Optional[int]:
        """Cria ou atualiza uma lacuna epistemica do ciclo com metadados rastreaveis.

        R1a-4: a repeticao NAO cria linha nova e NAO sobrescreve a existente.
        Ela registra observacao (ancoras e contador). Status, ``the_gap`` e
        ``importance_score`` ficam como estao — recorrencia nao prova
        descoberta nem eleva confianca.
        """
        if not user_id or not knowledge_gap:
            return None
        topic = (knowledge_gap.get("gap_label") or knowledge_gap.get("target_area") or "saber").strip()
        the_gap = (knowledge_gap.get("gap_question") or knowledge_gap.get("gap_label") or "").strip()
        if not the_gap:
            return None

        instance, resolved_origin, resolved_relation, participant = resolve_cognitive_origin(
            self,
            participant_user_id=user_id,
            relation_id=relation_id,
            origin_class=origin_class or knowledge_gap.get("origin_class"),
            agent_instance=agent_instance,
        )
        declared_kind = source_kind or knowledge_gap.get("source_kind")
        gap_kind, kind_reason = validate_gap_source_kind(
            declared_kind, allow_null=declared_kind is None
        )
        if kind_reason and declared_kind is not None:
            logger.warning(
                "knowledge_gap proveniencia indeterminada user_id=%s motivo=%s",
                user_id, kind_reason,
            )
        public_question = (knowledge_gap.get("public_question") or "").strip() or None
        if resolved_origin in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE} and not public_question:
            public_question = the_gap
        private_trigger = knowledge_gap.get("private_trigger")
        refs = normalize_source_refs(source_refs)
        provenance_payload = _gap_provenance(
            cognitive_provenance(
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                participant_user_id=participant,
                extra=provenance,
            ),
            source_kind=gap_kind,
            indeterminate_reason=kind_reason,
        )
        fingerprint = gap_fingerprint(the_gap)

        with self._lock:
            cursor = self.conn.cursor()
            observed = self._observe_existing_gap(
                cursor,
                instance=instance,
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                fingerprint=fingerprint,
                incoming_refs=refs,
            )
            if observed:
                self.conn.commit()
                return observed[0]

            payload = (
                topic,
                max(0.0, min(1.0, float(importance))),
                knowledge_gap.get("source_origin"),
                knowledge_gap.get("knowledge_kind"),
                knowledge_gap.get("target_area"),
                knowledge_gap.get("target_scope"),
                json.dumps(knowledge_gap.get("focus_terms") or [], ensure_ascii=False),
                knowledge_gap.get("source_reason") or knowledge_gap.get("psychic_motive"),
                public_question,
                json_payload(private_trigger, {}) if private_trigger else None,
                json_payload(refs, []),
                json_payload(provenance_payload, {}),
            )
            cursor.execute(
                """
                INSERT INTO knowledge_gaps (
                    user_id, agent_instance, ownership_class, origin_class,
                    origin_relation_id, origin_participant_user_id,
                    topic, the_gap, importance_score,
                    source_origin, knowledge_kind, target_area, target_scope,
                    focus_terms_json, source_reason, public_question,
                    private_trigger_json, source_refs_json, provenance_json, status,
                    source_kind, dedup_key, observation_count, last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                          ?, ?, 1, ?)
                """,
                (
                    user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                    resolved_relation, participant, topic, the_gap, *payload[1:],
                    gap_kind, fingerprint, _now_iso(),
                ),
            )
            gap_id = int(cursor.lastrowid)
            self.conn.commit()
            return gap_id

    def close_knowledge_gap_with_evidence(
        self,
        gap_id: int,
        *,
        closure_summary: str,
        journal_entry: str,
        source_type: str,
        source_id: str,
        evidence: Dict,
    ) -> bool:
        """Fecha uma lacuna com fonte e payload de evidencia auditavel."""
        if not gap_id or not closure_summary or not source_type or not source_id:
            raise ValueError("gap_id_closure_summary_source_required")
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE knowledge_gaps
                SET status = 'resolved',
                    closure_summary = ?,
                    closure_journal_entry = ?,
                    closure_source_type = ?,
                    closure_source_id = ?,
                    closure_evidence_json = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    closure_summary.strip(),
                    (journal_entry or "").strip(),
                    source_type.strip(),
                    source_id.strip(),
                    json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True),
                    gap_id,
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def get_active_knowledge_gaps(
        self,
        user_id: str,
        limit: int = 3,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
    ) -> List[Dict]:
        """Busca lacunas globais e, quando explicitado, as da mesma Relation."""
        with self._lock:
            cursor = self.conn.cursor()
            instance = cognitive_agent_instance(self, agent_instance)
            resolved_relation = None
            resolver = getattr(self, "resolve_relation_id", None)
            if callable(resolver):
                resolved_relation = resolver(
                    agent_instance=instance,
                    participant_user_id=user_id,
                    relation_id=relation_id,
                )
            elif relation_id:
                resolved_relation = relation_id
            visibility = "origin_class IN (?, ?)"
            params: list[Any] = [instance, INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE]
            if resolved_relation:
                visibility = f"({visibility} OR origin_relation_id = ?)"
                params.append(resolved_relation)
            else:
                from instance_config import ADMIN_USER_ID

                if str(user_id) == str(ADMIN_USER_ID):
                    visibility = f"({visibility} OR (origin_relation_id IS NULL AND user_id = ?))"
                    params.append(user_id)
            params.append(limit)
            cursor.execute(f"""
                SELECT * FROM knowledge_gaps
                WHERE agent_instance = ? AND status = 'open' AND {visibility}
                ORDER BY importance_score DESC, created_at DESC
                LIMIT ?
            """, params)
            
            gaps = []
            for row in cursor.fetchall():
                item = dict(row)
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
                item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
                item["private_trigger"] = json.loads(item.pop("private_trigger_json") or "{}")
                gaps.append(item)
            return gaps

    def resolve_knowledge_gap(self, gap_id: int) -> bool:
        """Marca uma lacuna como resolvida"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE knowledge_gaps 
                    SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (gap_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao resolver knowledge gap {gap_id}: {e}")
                return False

    def reject_knowledge_gap(self, gap_id: int) -> bool:
        """Marca uma lacuna como rejeitada (irrelevante/invÃ¡lida)"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE knowledge_gaps 
                    SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (gap_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao rejeitar knowledge gap {gap_id}: {e}")
                return False

    # ========================================
