from __future__ import annotations

import logging
import json
import sqlite3
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

# Tentativas de recorrencia com compare-and-swap (leitura+mesclagem+contador).
_OBSERVE_MAX_ATTEMPTS = 5


# Pontuacao de FRASE removida apenas nas extremidades. Só isso: ponto,
# virgula, ponto e virgula, dois-pontos, exclamacao, interrogacao, reticencias
# e aspas. Sinais de valor (-, +, %, #, R$…) ficam intocados: "Temperatura
# -5?" e "Temperatura 5?" nao podem virar a mesma chave.
_EDGE_SENTENCE_PUNCT = ".,;:!?…\"'“”‘’"


def gap_fingerprint(text: Any) -> str:
    """Chave de identidade de uma pergunta — normalizacao conservadora.

    Ortografica, sem semantica: NFKC, casefold, espacos colapsados e
    pontuacao de frase removida **somente nas extremidades**. Ponto INTERNO
    e mantido (hifen, mais, porcentagem fazem parte do sentido). Sem
    stemming, sem stopwords, sem similaridade entre frases. O ``the_gap``
    original nunca e reescrito.
    """
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    collapsed = " ".join(normalized.split())
    return collapsed.strip(_EDGE_SENTENCE_PUNCT).strip()


def gap_identity_key(
    *, origin_class: str, relation_id: Optional[str], participant: Optional[str], fingerprint: str
) -> str:
    """Escopo completo de identidade de uma lacuna.

    Inclui origem, Relation e participante: ``legacy_unscoped`` nao tem
    Relation que identifique o dono, entao sem o participante dois usuarios
    distintos fundiriam a mesma pergunta e as ancoras de conversa um do outro.
    """
    return "|".join(
        [
            str(origin_class or ""),
            str(relation_id or ""),
            str(participant or ""),
            str(fingerprint or ""),
        ]
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gap_provenance(
    base: Dict[str, Any],
    *,
    indeterminate_reason: Optional[str],
) -> Tuple[Dict[str, Any], int]:
    """Proveniencia da lacuna + flag de quarentena.

    O marcador informativo fica em ``provenance_json`` (motivo e pendencia);
    a bloqueio de consumo e a coluna ``origin_quarantined`` — as duas sao
    gravadas juntas para nunca divergirem.
    """
    payload = dict(base or {})
    if indeterminate_reason:
        payload[INDETERMINATE_ORIGIN_KEY] = True
        payload[f"{INDETERMINATE_ORIGIN_KEY}_reason"] = indeterminate_reason
        payload[f"{INDETERMINATE_ORIGIN_KEY}_pending"] = "R1a-4"
        return payload, 1
    return payload, 0


# ---------------------------------------------------------------------------
# R1a-4: identidade, recorrencia e observacao de uma lacuna
#
# Funcoes de modulo (nao do mixin) de proposito: o caminho do
# world_consciousness grava numa conexao propria e SEMPRE reusar esta
# implementacao. Manter SQL independente ali repetiu o bug de recorrencia
# nao atomica — ver revisao da PR #65.
# ---------------------------------------------------------------------------
def merge_gap_refs(stored: Any, incoming: Iterable[str]) -> Tuple[List[str], bool]:
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


def observe_gap_occurrence(
    conn: Any,
    *,
    instance: str,
    identity_key: str,
    incoming_refs: List[str],
    incoming_quarantined: bool = False,
) -> Optional[Tuple[int, str]]:
    """Procura a mesma pergunta no MESMO escopo de propriedade.

    Escopo embutido em ``dedup_key`` (``origin_class`` + ``relation`` +
    ``participante`` + fingerprint), mais um indice UNIQUE em
    ``(agent_instance, dedup_key)`` — a unicidade e garantida pelo SQLite
    entre conexoes e processos, nao pelo lock do objeto. Linhas historicas
    (``dedup_key`` NULL) nunca sao alcancadas: nao ha backfill.

    Comportamento por status (contrato R1a-4):
    - ``open`` / ``investigating`` / ``resolved`` / ``rejected``: a linha e
      reutilizada. Status nao muda, ``the_gap`` nao e reescrito,
      ``importance_score`` nao e tocado.
    - recorrencia real (ancora nova) -> contador +1 e ancora anexada;
    - retry do mesmo evento -> sem efeito no contador.

    Duas protecoes transversais:

    * ``incoming_quarantined``: escrita cuja proveniencia nao foi validada
      NAO escreve em registro existente (nem ancora nem contador).
    * Leitura + mesclagem + contador numa **operacao atomica**: o ``UPDATE``
      so acontece se ``source_refs_json`` ainda for o que foi lido
      (compare-and-swap); se outra conexao mudou, relee. O mesmo evento
      nunca e contado duas vezes.
    """
    cursor = conn.cursor()
    gap_id: Optional[int] = None
    for _ in range(_OBSERVE_MAX_ATTEMPTS):
        cursor.execute(
            """
            SELECT id, source_refs_json, observation_count
            FROM knowledge_gaps
            WHERE agent_instance = ? AND dedup_key = ?
            ORDER BY id
            LIMIT 1
            """,
            (instance, identity_key),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        gap_id = int(row["id"])

        if incoming_quarantined:
            logger.warning(
                "knowledge_gap em quarentena nao anexa ancora/contador "
                "em registro existente gap_id=%s",
                gap_id,
            )
            return gap_id, "quarantined"

        raw_refs = row["source_refs_json"] or "[]"
        merged, has_new_anchor = merge_gap_refs(json.loads(raw_refs), incoming_refs)
        if not has_new_anchor:
            return gap_id, "retry"
        cursor.execute(
            """
            UPDATE knowledge_gaps
            SET observation_count = COALESCE(observation_count, 0) + 1,
                last_observed_at = ?,
                source_refs_json = ?
            WHERE id = ? AND source_refs_json IS ?
            """,
            (
                _now_iso(),
                json.dumps(merged, ensure_ascii=False),
                gap_id,
                row["source_refs_json"],
            ),
        )
        if cursor.rowcount == 1:
            return gap_id, "recurrence"
        # CAS perdida: outra conexao atualizou antes da gente. Releer.
    logger.warning(
        "knowledge_gap recorrencia nao atomica gap_id=%s — tentativas "
        "esgotadas, sem contar o evento",
        gap_id,
    )
    return gap_id, "retry"


class KnowledgeGapDatabaseMixin:
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
            if not (the_gap or "").strip():
                logger.warning("knowledge_gap sem texto user_id=%s — recusado", user_id)
                return None
            # allow_null=False: escrita NOVA sem produtor validado vai para
            # quarentena (marcador + coluna). Legado historico nao passa por
            # aqui, entao as duas situacoes nunca se confundem.
            gap_kind, kind_reason = validate_gap_source_kind(source_kind, allow_null=False)
            if kind_reason:
                logger.warning(
                    "knowledge_gap proveniencia indeterminada user_id=%s motivo=%s "
                    "— entrada preservada e bloqueada ate classificacao valida",
                    user_id, kind_reason,
                )
            refs = normalize_source_refs(source_refs)
            provenance_payload, quarantined = _gap_provenance(
                cognitive_provenance(
                    origin_class=resolved_origin,
                    relation_id=resolved_relation,
                    participant_user_id=participant,
                    extra=provenance,
                ),
                indeterminate_reason=kind_reason,
            )
            identity_key = gap_identity_key(
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                participant=participant,
                fingerprint=gap_fingerprint(the_gap),
            )
            cursor = self.conn.cursor()

            observed = observe_gap_occurrence(
                self.conn,
                instance=instance,
                identity_key=identity_key,
                incoming_refs=refs,
                incoming_quarantined=bool(kind_reason),
            )
            if observed:
                self.conn.commit()
                return observed[0]

            try:
                cursor.execute("""
                    INSERT INTO knowledge_gaps (
                        user_id, agent_instance, ownership_class, origin_class,
                        origin_relation_id, origin_participant_user_id,
                        source_refs_json, provenance_json, topic, the_gap,
                        importance_score, public_question, private_trigger_json,
                        status, source_kind, dedup_key, observation_count,
                        last_observed_at, origin_quarantined
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                              ?, ?, 1, ?, ?)
                """, (
                    user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                    resolved_relation, participant,
                    json_payload(refs, []),
                    json_payload(provenance_payload, {}), topic, the_gap, importance,
                    public_question, json_payload(private_trigger, {}) if private_trigger else None,
                    gap_kind, identity_key, _now_iso(), quarantined,
                ))
            except sqlite3.IntegrityError:
                # Outra conexao venceu a corrida: o indice UNIQUE e a
                # garantia transacional entre conexoes/processos.
                observed = observe_gap_occurrence(
                    self.conn,
                    instance=instance,
                    identity_key=identity_key,
                    incoming_refs=refs,
                    incoming_quarantined=bool(kind_reason),
                )
                self.conn.commit()
                if observed:
                    return observed[0]
                raise

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
        # allow_null=False: escrita NOVA sem produtor validado vai para
        # quarentena, distinta do legado historico (que nao passa por aqui).
        gap_kind, kind_reason = validate_gap_source_kind(declared_kind, allow_null=False)
        if kind_reason:
            logger.warning(
                "knowledge_gap proveniencia indeterminada user_id=%s motivo=%s "
                "— entrada preservada e bloqueada ate classificacao valida",
                user_id, kind_reason,
            )
        public_question = (knowledge_gap.get("public_question") or "").strip() or None
        if resolved_origin in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE} and not public_question:
            public_question = the_gap
        private_trigger = knowledge_gap.get("private_trigger")
        refs = normalize_source_refs(source_refs)
        provenance_payload, quarantined = _gap_provenance(
            cognitive_provenance(
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                participant_user_id=participant,
                extra=provenance,
            ),
            indeterminate_reason=kind_reason,
        )
        identity_key = gap_identity_key(
            origin_class=resolved_origin,
            relation_id=resolved_relation,
            participant=participant,
            fingerprint=gap_fingerprint(the_gap),
        )

        with self._lock:
            cursor = self.conn.cursor()
            observed = observe_gap_occurrence(
                self.conn,
                instance=instance,
                identity_key=identity_key,
                incoming_refs=refs,
                incoming_quarantined=bool(kind_reason),
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
            try:
                cursor.execute(
                    """
                    INSERT INTO knowledge_gaps (
                        user_id, agent_instance, ownership_class, origin_class,
                        origin_relation_id, origin_participant_user_id,
                        topic, the_gap, importance_score,
                        source_origin, knowledge_kind, target_area, target_scope,
                        focus_terms_json, source_reason, public_question,
                        private_trigger_json, source_refs_json, provenance_json, status,
                        source_kind, dedup_key, observation_count, last_observed_at,
                        origin_quarantined
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                              ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                        resolved_relation, participant, topic, the_gap, *payload[1:],
                        gap_kind, identity_key, _now_iso(), quarantined,
                    ),
                )
            except sqlite3.IntegrityError:
                # Outra conexao/processo venceu a corrida — o indice UNIQUE
                # e a garantia transacional, nao o lock do objeto.
                observed = observe_gap_occurrence(
                    self.conn,
                    instance=instance,
                    identity_key=identity_key,
                    incoming_refs=refs,
                    incoming_quarantined=bool(kind_reason),
                )
                self.conn.commit()
                if observed:
                    return observed[0]
                raise
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
                  AND origin_quarantined = 0
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
