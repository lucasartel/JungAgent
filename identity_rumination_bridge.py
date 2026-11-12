"""
identity_rumination_bridge.py

Bridge Bidirecional: Sistema de Identidade ↔ Sistema de Ruminação

Sincroniza dados entre os dois sistemas:
- Ruminação → Identidade: Tensões maduras viram contradições, insights viram nuclear
- Identidade → Ruminação: Contradições não resolvidas alimentam ruminação

Ambos os sistemas operam apenas com o usuário master admin.
"""

import logging
import json
import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db.cognitive_ownership import (
    IDENTITY_PROMOTION_POLICY,
    assess_identity_promotion,
)
from core.db.relations import require_eligible_relation
from instance_config import AGENT_INSTANCE, ADMIN_USER_ID
from identity_config import MIN_CERTAINTY_FOR_NUCLEAR
from rumination_config import (
    MAX_IDENTITY_CONTRADICTIONS_PER_BRIDGE_RUN,
    MAX_OPEN_TENSIONS_PER_USER,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def find_database():
    """Encontra o banco de dados automaticamente"""
    possible_paths = [
        Path("/data/jung_hybrid.db"),
        Path("data/jung_hybrid.db"),
        Path("jung_hybrid.db")
    ]

    for path in possible_paths:
        if path.exists():
            return path

    return possible_paths[0]


class IdentityRuminationBridge:
    """
    Ponte bidirecional entre Identidade e Ruminação

    Fluxos:
    1. Tensões de ruminação maduras → Contradições de identidade
    2. Insights de ruminação maduros → Crenças nucleares
    3. Fragmentos recorrentes → Selves possíveis (temidos/perdidos)
    4. Contradições não resolvidas → Novas tensões de ruminação
    """

    def __init__(self, db_connection):
        """
        Args:
            db_connection: Conexão SQLite (HybridDatabaseManager)
        """
        self.db = db_connection

    def _scope(
        self,
        user_id: Optional[str] = None,
        relation_id: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        participant = str(user_id or ADMIN_USER_ID)
        instance = (getattr(self.db, "agent_instance", None) or AGENT_INSTANCE).strip()
        resolver = getattr(self.db, "resolve_relation_id", None)
        resolved_relation = relation_id
        if callable(resolver):
            resolved_relation = resolver(
                agent_instance=instance,
                participant_user_id=participant,
                relation_id=relation_id,
            )
            if not resolved_relation:
                if participant == str(ADMIN_USER_ID):
                    raise ValueError("admin_relation_required_for_identity_rumination_bridge")
                raise ValueError("relation_required_for_identity_rumination_bridge")
        if resolved_relation and callable(getattr(self.db, "get_agent_relation", None)):
            require_eligible_relation(self.db, resolved_relation)
        return instance, resolved_relation

    def _ensure_identity_provenance(self, cursor) -> None:
        for table in (
            "agent_identity_core",
            "agent_identity_contradictions",
            "agent_possible_selves",
        ):
            if not cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
            for column, definition in (
                ("ownership_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_relation_id", "TEXT"),
                ("origin_participant_user_id", "TEXT"),
                ("source_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in columns:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if table == "agent_identity_core":
                for col, defn in (
                    ("contradiction_count", "INTEGER DEFAULT 0"),
                    ("affirmation_count", "INTEGER DEFAULT 1"),
                ):
                    if col not in columns:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")

    def _ensure_insight_sync_columns(self, cursor) -> set[str]:
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(rumination_insights)")}
        for column, definition in (
            ("identity_sync_status", "TEXT"),
            ("identity_sync_reason", "TEXT"),
            ("identity_synced_at", "TEXT"),
            ("epistemic_status", "TEXT"),
        ):
            if column not in columns:
                cursor.execute(f"ALTER TABLE rumination_insights ADD COLUMN {column} {definition}")
                columns.add(column)
        return columns

    def _stamp_identity_origin(
        self,
        cursor,
        *,
        table: str,
        row_id: int,
        relation_id: Optional[str],
        source_ref: str,
        participant_user_id: Optional[str] = None,
        extra_provenance: Optional[Dict[str, Any]] = None,
    ) -> None:
        participant = str(participant_user_id or ADMIN_USER_ID)
        origin_class = "relation_private" if relation_id else "legacy_unscoped"
        provenance: Dict[str, Any] = {
            "private_derived": True,
            "origin_class": origin_class,
            "origin_relation_id": relation_id,
            "origin_participant_user_id": participant,
            "source_refs": [source_ref],
        }
        if extra_provenance:
            provenance.update(extra_provenance)
        cursor.execute(
            f"""UPDATE {table}
                SET ownership_class = 'instance_global', origin_class = ?,
                    origin_relation_id = ?, origin_participant_user_id = ?,
                    source_refs_json = ?, provenance_json = ?
                WHERE id = ?""",
            (
                origin_class,
                relation_id,
                participant,
                json.dumps([source_ref]),
                json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                row_id,
            ),
        )

    def sync_mature_tensions_to_contradictions(self) -> int:
        """
        Ruminação → Identidade: Tensões maduras viram contradições

        Busca tensões com maturity_score > 0.6 e ainda não exportadas,
        cria contradições correspondentes no sistema de identidade.

        Returns:
            int: Número de tensões sincronizadas
        """
        cursor = self.db.conn.cursor()

        try:
            instance, relation_id = self._scope()
            self._ensure_identity_provenance(cursor)
            # Verificar se tabela de ruminação existe
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='rumination_tensions'
            """)
            if not cursor.fetchone():
                logger.warning("⚠️ Tabela rumination_tensions não existe - pulando sync")
                return 0

            # Buscar tensões maduras (schema real: pole_a_content, pole_b_content)
            cursor.execute("""
                SELECT id, pole_a_content, pole_b_content, tension_type, intensity,
                       first_detected_at
                FROM rumination_tensions
                WHERE user_id = ?
                  AND agent_instance = ?
                  AND COALESCE(relation_id, '') = COALESCE(?, '')
                  AND maturity_score > 0.6
                  AND status IN ('open', 'maturing', 'ready_for_synthesis')
            """, (str(ADMIN_USER_ID), instance, relation_id))

            tensions = cursor.fetchall()

            if not tensions:
                return 0

            logger.info(f"   🔄 Sincronizando {len(tensions)} tensões → contradições")

            synced_count = 0
            for row in tensions:
                tension_id, pole_a, pole_b, tension_type, intensity, first_detected = row

                # Verificar idempotência: contradição com mesmo polo já existe?
                cursor.execute("""
                    SELECT id FROM agent_identity_contradictions
                    WHERE agent_instance = ? AND pole_a = ? AND pole_b = ?
                      AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
                """, (instance, pole_a, pole_b, relation_id))
                if cursor.fetchone():
                    continue

                # Criar contradição identitária
                cursor.execute("""
                    INSERT INTO agent_identity_contradictions (
                        agent_instance, pole_a, pole_b, contradiction_type,
                        tension_level, salience, first_detected_at, last_activated_at,
                        supporting_conversation_ids, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                """, (
                    instance,
                    pole_a,
                    pole_b,
                    tension_type,
                    intensity,
                    intensity,
                    first_detected,
                    json.dumps([]),
                    'unresolved'
                ))
                self._stamp_identity_origin(
                    cursor,
                    table="agent_identity_contradictions",
                    row_id=int(cursor.lastrowid),
                    relation_id=relation_id,
                    source_ref=f"rumination_fragment#{tension_id}",
                )

                synced_count += 1

            self.db.conn.commit()
            logger.info(f"   ✅ {synced_count} tensões sincronizadas")
            return synced_count

        except Exception as e:
            self.db.conn.rollback()
            logger.error(f"   ❌ Erro ao sincronizar tensões: {e}")
            return 0

    def _resolve_insight_evidence_count(
        self,
        cursor,
        *,
        instance: str,
        relation_id: Optional[str],
        source_tension_id: Optional[int],
        insight_columns: set[str],
        row_dict: Dict[str, Any],
    ) -> int:
        if "evidence_count" in insight_columns and row_dict.get("evidence_count") is not None:
            return int(row_dict["evidence_count"])
        if not source_tension_id:
            return 0
        has_tensions = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rumination_tensions'"
        ).fetchone()
        if not has_tensions:
            return 0
        tension_cols = {row[1] for row in cursor.execute("PRAGMA table_info(rumination_tensions)")}
        if "evidence_count" not in tension_cols:
            return 0
        t_row = cursor.execute(
            """
            SELECT evidence_count
            FROM rumination_tensions
            WHERE id = ?
              AND (agent_instance = ? OR agent_instance IS NULL)
              AND COALESCE(relation_id, '') = COALESCE(?, '')
            """,
            (source_tension_id, instance, relation_id),
        ).fetchone()
        return int(t_row[0] or 0) if t_row else 0

    @staticmethod
    def _negation_signature(text: str) -> tuple[bool, str]:
        cleaned = re.sub(r"[^\w\sÀ-ÿ]", " ", (text or "").lower())
        tokens = [tok for tok in cleaned.split() if tok]
        has_neg = any(tok in {"nao", "não", "nunca", "jamais", "evito", "recuso"} for tok in tokens)
        subject_tokens = [
            tok for tok in tokens
            if tok not in {
                "eu", "sou", "nao", "não", "nunca", "jamais", "sempre",
                "valorizo", "priorizo", "evito", "recuso", "sustento", "busco",
                "prefiro", "escolho", "reconheco", "reconheço", "percebo",
                "de", "do", "da", "em", "no", "na", "para", "com", "por", "que",
                "o", "a", "os", "as", "um", "uma",
            }
        ]
        return has_neg, " ".join(subject_tokens[:6])

    def _record_contradiction_if_present(
        self,
        cursor,
        *,
        instance: str,
        relation_id: Optional[str],
        attribute_type: str,
        nuclear_content: str,
        source_ref: str,
    ) -> None:
        new_neg, new_sig = self._negation_signature(nuclear_content)
        if not new_sig:
            return
        cursor.execute(
            """
            SELECT id, content, provenance_json, contradiction_count
            FROM agent_identity_core
            WHERE agent_instance = ?
              AND attribute_type = ?
              AND is_current = 1
              AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
            """,
            (instance, attribute_type, relation_id),
        )
        for existing_row in cursor.fetchall():
            ex_id, ex_content, ex_prov_raw, ex_contra = existing_row
            if ex_content == nuclear_content:
                continue
            ex_neg, ex_sig = self._negation_signature(ex_content or "")
            if ex_sig and ex_sig == new_sig and ex_neg != new_neg:
                try:
                    ex_prov = json.loads(ex_prov_raw or "{}")
                except ValueError:
                    ex_prov = {}
                contra_refs = list(ex_prov.get("contradicting_source_refs") or [])
                if source_ref not in contra_refs:
                    contra_refs.append(source_ref)
                    ex_prov["contradicting_source_refs"] = contra_refs
                    cursor.execute(
                        """
                        UPDATE agent_identity_core
                        SET contradiction_count = COALESCE(contradiction_count, 0) + 1,
                            provenance_json = ?
                        WHERE id = ?
                        """,
                        (json.dumps(ex_prov, ensure_ascii=False, sort_keys=True), ex_id),
                    )

    def sync_mature_insights_to_core(
        self,
        user_id: Optional[str] = None,
        relation_id: Optional[str] = None,
    ) -> int:
        """
        Ruminação → Identidade: Insights maduros viram crenças nucleares sob gate R3a.

        Regras R3a:
        - Confere completude/legibilidade (sem punir reticências poéticas em cláusula completa).
        - Distingue símbolo episódico, conceito de domínio e afirmação identitária sustentada.
        - Nunca copia `symbol_content` como `trait` ou `role`.
        - Exige `depth_score >= 0.65`, `novelty_score >= 0.60` e `evidence_count >= 2`.
        - Calcula `certainty` pela política declarativa (nunca `0.75` fixo).
        - Registra incorporação em `identity_sync_status`/`identity_sync_reason` sem sobrescrever
          `status = 'delivered'` (que pertence exclusivamente à entrega externa no Telegram).
        """
        cursor = self.db.conn.cursor()
        participant = str(user_id or ADMIN_USER_ID)

        try:
            instance, resolved_relation = self._scope(user_id=participant, relation_id=relation_id)
            self._ensure_identity_provenance(cursor)
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='rumination_insights'
            """)
            if not cursor.fetchone():
                logger.warning("⚠️ Tabela rumination_insights não existe - pulando sync")
                return 0

            insight_columns = self._ensure_insight_sync_columns(cursor)
            select_cols = [
                "id",
                "full_message",
                "symbol_content",
                "crystallized_at",
                "source_tension_id",
            ]
            for optional_col in (
                "depth_score",
                "novelty_score",
                "maturation_days",
                "insight_type",
                "evidence_count",
                "identity_sync_status",
            ):
                if optional_col in insight_columns:
                    select_cols.append(optional_col)

            cursor.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM rumination_insights
                WHERE user_id = ?
                  AND agent_instance = ?
                  AND COALESCE(relation_id, '') = COALESCE(?, '')
                  AND status IN ('ready', 'delivered')
                  AND COALESCE(identity_sync_status, '') != 'synced'
                """,
                (participant, instance, resolved_relation),
            )

            insights = cursor.fetchall()
            if not insights:
                return 0

            logger.info(f"   🔄 Avaliando {len(insights)} insights sob gate R3a → nuclear")

            synced_count = 0
            for row in insights:
                row_dict = dict(zip(select_cols, row))
                insight_id = int(row_dict["id"])
                content = row_dict.get("full_message")
                symbolic = row_dict.get("symbol_content")
                crystallized = row_dict.get("crystallized_at")
                source_tension_id = row_dict.get("source_tension_id")
                depth_score = row_dict.get("depth_score")
                novelty_score = row_dict.get("novelty_score")
                maturation_days = int(row_dict.get("maturation_days") or 0)
                insight_type = row_dict.get("insight_type")

                evidence_count = self._resolve_insight_evidence_count(
                    cursor,
                    instance=instance,
                    relation_id=resolved_relation,
                    source_tension_id=source_tension_id,
                    insight_columns=insight_columns,
                    row_dict=row_dict,
                )

                decision = assess_identity_promotion(
                    content=content,
                    symbolic=symbolic,
                    insight_type=insight_type,
                    depth_score=depth_score,
                    novelty_score=novelty_score,
                    evidence_count=evidence_count,
                    maturation_days=maturation_days,
                    policy=IDENTITY_PROMOTION_POLICY,
                )

                if not decision.promotes:
                    cursor.execute(
                        """
                        UPDATE rumination_insights
                        SET identity_sync_status = 'deferred',
                            identity_sync_reason = ?,
                            epistemic_status = ?
                        WHERE id = ?
                        """,
                        (decision.reason, decision.epistemic_status, insight_id),
                    )
                    continue

                nuclear_content = decision.nuclear_content
                attribute_type = self._classify_insight_type(nuclear_content, None)
                source_ref = f"rumination_insight#{insight_id}"

                # Verificar se já existe atributo ativo idêntico no mesmo escopo
                cursor.execute(
                    """
                    SELECT id, certainty, source_refs_json, provenance_json
                    FROM agent_identity_core
                    WHERE agent_instance = ?
                      AND content = ?
                      AND is_current = 1
                      AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
                    """,
                    (instance, nuclear_content, resolved_relation),
                )
                existing = cursor.fetchone()
                if existing:
                    existing_id, existing_certainty, existing_refs_raw, existing_prov_raw = existing
                    try:
                        existing_refs = json.loads(existing_refs_raw or "[]")
                    except ValueError:
                        existing_refs = []
                    try:
                        existing_prov = json.loads(existing_prov_raw or "{}")
                    except ValueError:
                        existing_prov = {}

                    if source_ref in existing_refs:
                        cursor.execute(
                            """
                            UPDATE rumination_insights
                            SET identity_sync_status = 'synced',
                                identity_sync_reason = 'replay_already_synced',
                                epistemic_status = ?,
                                identity_synced_at = COALESCE(identity_synced_at, CURRENT_TIMESTAMP)
                            WHERE id = ?
                            """,
                            (decision.epistemic_status, insight_id),
                        )
                        continue

                    existing_refs.append(source_ref)
                    revised_certainty = round(
                        min(
                            IDENTITY_PROMOTION_POLICY.max_certainty,
                            max(float(existing_certainty or 0.0), float(decision.certainty or 0.0)) + 0.02,
                        ),
                        2,
                    )
                    existing_prov["source_refs"] = existing_refs
                    existing_prov["last_revised_by"] = source_ref
                    existing_prov["epistemic_status"] = decision.epistemic_status
                    existing_prov["confidence_policy"] = decision.policy_version
                    cursor.execute(
                        """
                        UPDATE agent_identity_core
                        SET certainty = ?,
                            affirmation_count = COALESCE(affirmation_count, 1) + 1,
                            last_reaffirmed_at = CURRENT_TIMESTAMP,
                            source_refs_json = ?,
                            provenance_json = ?
                        WHERE id = ?
                        """,
                        (
                            revised_certainty,
                            json.dumps(existing_refs),
                            json.dumps(existing_prov, ensure_ascii=False, sort_keys=True),
                            existing_id,
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE rumination_insights
                        SET identity_sync_status = 'synced',
                            identity_sync_reason = 'reaffirmed_existing_attribute',
                            epistemic_status = ?,
                            identity_synced_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (decision.epistemic_status, insight_id),
                    )
                    synced_count += 1
                    continue

                self._record_contradiction_if_present(
                    cursor,
                    instance=instance,
                    relation_id=resolved_relation,
                    attribute_type=attribute_type,
                    nuclear_content=nuclear_content,
                    source_ref=source_ref,
                )

                cursor.execute(
                    """
                    INSERT INTO agent_identity_core (
                        agent_instance, attribute_type, content, certainty,
                        first_crystallized_at, last_reaffirmed_at,
                        supporting_conversation_ids, emerged_in_relation_to
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                    """,
                    (
                        instance,
                        attribute_type,
                        nuclear_content,
                        decision.certainty,
                        crystallized if crystallized else datetime.now().isoformat(),
                        json.dumps([source_tension_id] if source_tension_id else []),
                        'ruminação sobre interações',
                    ),
                )
                self._stamp_identity_origin(
                    cursor,
                    table="agent_identity_core",
                    row_id=int(cursor.lastrowid),
                    relation_id=resolved_relation,
                    source_ref=source_ref,
                    participant_user_id=participant,
                    extra_provenance={
                        "epistemic_status": decision.epistemic_status,
                        "confidence_policy": decision.policy_version,
                        "promotion_reason": decision.reason,
                        "depth_score": depth_score,
                        "novelty_score": novelty_score,
                        "evidence_count": evidence_count,
                        "maturation_days": maturation_days,
                        "symbolic_image": symbolic or None,
                    },
                )

                cursor.execute(
                    """
                    UPDATE rumination_insights
                    SET identity_sync_status = 'synced',
                        identity_sync_reason = ?,
                        epistemic_status = ?,
                        identity_synced_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (decision.reason, decision.epistemic_status, insight_id),
                )

                synced_count += 1

            self.db.conn.commit()
            logger.info(f"   ✅ {synced_count} insights sincronizados")
            return synced_count

        except Exception as e:
            self.db.conn.rollback()
            logger.error(f"   ❌ Erro ao sincronizar insights: {e}")
            return 0

    def sync_fragments_to_possible_selves(self) -> int:
        """
        Ruminação → Identidade: Fragmentos recorrentes viram selves temidos/perdidos

        Busca fragmentos que aparecem 3+ vezes com alta carga emocional,
        cria selves possíveis correspondentes.

        Returns:
            int: Número de fragmentos sincronizados
        """
        cursor = self.db.conn.cursor()

        try:
            instance, relation_id = self._scope()
            self._ensure_identity_provenance(cursor)
            # Verificar se tabela existe
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='rumination_fragments'
            """)
            if not cursor.fetchone():
                logger.warning("⚠️ Tabela rumination_fragments não existe - pulando sync")
                return 0

            # Buscar fragmentos recorrentes (schema real: content, emotional_weight, created_at)
            cursor.execute("""
                SELECT content, AVG(emotional_weight) as avg_charge,
                       fragment_type, MIN(created_at) as first_occurrence,
                       COUNT(*) as occurrence_count, MIN(id) as source_fragment_id
                FROM rumination_fragments
                WHERE user_id = ?
                  AND agent_instance = ?
                  AND COALESCE(relation_id, '') = COALESCE(?, '')
                  AND processed = 1
                GROUP BY content
                HAVING COUNT(*) >= 3
                   AND AVG(emotional_weight) > 0.6
            """, (str(ADMIN_USER_ID), instance, relation_id))

            fragments = cursor.fetchall()

            if not fragments:
                return 0

            logger.info(f"   🔄 Sincronizando {len(fragments)} fragmentos → selves possíveis")

            synced_count = 0
            for row in fragments:
                content, avg_charge, frag_type, first_occurrence, count, source_fragment_id = row

                # Classificar tipo de self (feared ou lost baseado no tipo de fragmento)
                self_type = 'feared' if avg_charge > 0.75 else 'lost'

                # Verificar se já existe self similar
                cursor.execute("""
                    SELECT id FROM agent_possible_selves
                    WHERE agent_instance = ?
                      AND description = ?
                      AND status = 'active'
                      AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
                """, (instance, content, relation_id))

                if cursor.fetchone():
                    continue  # Já existe, pular

                # Criar novo self possível
                vividness = min(0.9, 0.5 + (count * 0.1))  # Aumenta com recorrência

                cursor.execute("""
                    INSERT INTO agent_possible_selves (
                        agent_instance, self_type, description, vividness,
                        likelihood, first_imagined_at, motivational_impact,
                        emotional_valence, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    instance,
                    self_type,
                    content,
                    vividness,
                    avg_charge,  # likelihood baseado em carga emocional
                    first_occurrence,
                    'avoidance',
                    'negative',
                    'active'
                ))
                self._stamp_identity_origin(
                    cursor,
                    table="agent_possible_selves",
                    row_id=int(cursor.lastrowid),
                    relation_id=relation_id,
                    source_ref=f"rumination_fragment#{source_fragment_id}",
                )

                synced_count += 1

            self.db.conn.commit()
            logger.info(f"   ✅ {synced_count} fragmentos sincronizados")
            return synced_count

        except Exception as e:
            self.db.conn.rollback()
            logger.error(f"   ❌ Erro ao sincronizar fragmentos: {e}")
            return 0

    def feed_contradictions_to_rumination(self) -> int:
        """
        Identidade → Ruminação: Contradições não resolvidas alimentam ruminação

        Busca contradições com alta tensão que ainda não foram alimentadas
        para o sistema de ruminação, criando novas tensões.

        Returns:
            int: Número de contradições alimentadas
        """
        cursor = self.db.conn.cursor()

        try:
            instance, relation_id = self._scope()
            self._ensure_identity_provenance(cursor)
            # Verificar se tabela de ruminação existe
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='rumination_tensions'
            """)
            if not cursor.fetchone():
                logger.warning("⚠️ Tabela rumination_tensions não existe - pulando feedback")
                return 0

            # Buscar um lote priorizado. Contradicoes antigas continuam elegiveis:
            # idade reduz prioridade, mas nao pode torna-las invisiveis para sempre.
            cursor.execute("""
                SELECT id, pole_a, pole_b, contradiction_type, tension_level
                FROM agent_identity_contradictions
                WHERE agent_instance = ?
                  AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
                  AND status IN ('unresolved', 'integrating')
                  AND tension_level > 0.55
                  AND (fed_to_rumination = 0 OR fed_to_rumination IS NULL)
                ORDER BY
                    CASE WHEN last_activated_at > datetime('now', '-14 days') THEN 0 ELSE 1 END,
                    tension_level DESC,
                    COALESCE(last_activated_at, first_detected_at) DESC,
                    id DESC
                LIMIT ?
            """, (instance, relation_id, MAX_IDENTITY_CONTRADICTIONS_PER_BRIDGE_RUN))

            contradictions = cursor.fetchall()

            if not contradictions:
                return 0

            logger.info(f"   🔄 Alimentando {len(contradictions)} contradições → ruminação")

            cursor.execute("""
                SELECT COUNT(*)
                FROM rumination_tensions
                WHERE user_id = ?
                  AND (agent_instance = ? OR agent_instance IS NULL)
                  AND COALESCE(relation_id, '') = COALESCE(?, '')
                  AND status IN ('open', 'maturing', 'ready_for_synthesis')
            """, (ADMIN_USER_ID, instance, relation_id))
            active_tension_count = int(cursor.fetchone()[0])

            fed_count = 0
            for row in contradictions:
                contradiction_id, pole_a, pole_b, contra_type, tension = row

                # Verificar idempotência em todo o historico. Uma tensao ja
                # sintetizada ou arquivada tambem representa esta contradicao.
                cursor.execute("""
                    SELECT id FROM rumination_tensions
                    WHERE user_id = ?
                      AND (agent_instance = ? OR agent_instance IS NULL)
                      AND COALESCE(relation_id, '') = COALESCE(?, '')
                      AND pole_a_content = ? AND pole_b_content = ?
                    LIMIT 1
                """, (ADMIN_USER_ID, instance, relation_id, pole_a, pole_b))
                if cursor.fetchone():
                    cursor.execute("""
                        UPDATE agent_identity_contradictions
                        SET fed_to_rumination = 1
                        WHERE id = ?
                    """, (contradiction_id,))
                    fed_count += 1
                    continue

                if active_tension_count >= MAX_OPEN_TENSIONS_PER_USER:
                    continue

                # Criar nova tensão de ruminação (schema real: pole_a_content, user_id obrigatório)
                cursor.execute("""
                    INSERT INTO rumination_tensions (
                        user_id, agent_instance, relation_id,
                        pole_a_content, pole_b_content, tension_type,
                        intensity, status, maturity_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0.0)
                """, (ADMIN_USER_ID, instance, relation_id, pole_a, pole_b, contra_type, tension))

                # Marcar contradição como alimentada
                cursor.execute("""
                    UPDATE agent_identity_contradictions
                    SET fed_to_rumination = 1
                    WHERE id = ?
                """, (contradiction_id,))

                fed_count += 1
                active_tension_count += 1

            self.db.conn.commit()
            logger.info(f"   ✅ {fed_count} contradições alimentadas")
            return fed_count

        except Exception as e:
            self.db.conn.rollback()
            logger.error(f"   ❌ Erro ao alimentar contradições: {e}")
            return 0

    def _classify_insight_type(self, content: str, symbolic: Optional[str]) -> str:
        """
        Classifica tipo de atributo nuclear baseado no conteúdo do insight

        Args:
            content: Conteúdo do insight
            symbolic: Interpretação simbólica (se houver)

        Returns:
            str: 'trait', 'value', 'boundary', 'continuity', ou 'role'
        """
        text = (symbolic or content).lower()

        # Heurísticas simples
        if any(word in text for word in ['sempre', 'consistentemente', 'desde']):
            return 'continuity'
        elif any(word in text for word in ['não sou', 'não faço', 'evito']):
            return 'boundary'
        elif any(word in text for word in ['valorizo', 'priorizo', 'importa']):
            return 'value'
        elif any(word in text for word in ['papel', 'função', 'como']):
            return 'role'
        else:
            return 'trait'


async def run_identity_rumination_sync():
    """
    Job de sincronização bidirecional

    Executa todos os fluxos de sincronização:
    1. Tensões → Contradições
    2. Insights → Nuclear
    3. Fragmentos → Selves Possíveis
    4. Contradições → Tensões (feedback)
    """
    logger.info("=" * 70)
    logger.info("🔗 SINCRONIZAÇÃO IDENTIDADE ↔ RUMINAÇÃO")
    logger.info("=" * 70)

    try:
        # Importar aqui para evitar import circular
        from jung_core import HybridDatabaseManager

        # Conectar ao banco
        db_path = find_database()
        if not db_path.exists():
            logger.error(f"❌ Banco de dados não encontrado: {db_path}")
            return

        db = HybridDatabaseManager()
        bridge = IdentityRuminationBridge(db)

        # Executar sincronizações
        logger.info("\n📥 RUMINAÇÃO → IDENTIDADE:")
        tensions_synced = bridge.sync_mature_tensions_to_contradictions()
        insights_synced = bridge.sync_mature_insights_to_core()
        fragments_synced = bridge.sync_fragments_to_possible_selves()

        logger.info("\n📤 IDENTIDADE → RUMINAÇÃO:")
        contradictions_fed = bridge.feed_contradictions_to_rumination()

        # Resumo
        total_synced = tensions_synced + insights_synced + fragments_synced + contradictions_fed

        logger.info("\n" + "=" * 70)
        logger.info("✅ SINCRONIZAÇÃO COMPLETA")
        logger.info(f"   📊 Total de sincronizações: {total_synced}")
        logger.info(f"      • Tensões → Contradições: {tensions_synced}")
        logger.info(f"      • Insights → Nuclear: {insights_synced}")
        logger.info(f"      • Fragmentos → Selves: {fragments_synced}")
        logger.info(f"      • Contradições → Tensões: {contradictions_fed}")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"❌ Erro na sincronização: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def identity_rumination_sync_scheduler():
    """
    Scheduler que roda sincronização a cada 6 horas

    Chamado pelo main.py como background task
    """
    logger.info("🔗 Scheduler de sincronização Identidade↔Ruminação iniciado (a cada 6h)")

    # Aguardar 5 minutos para garantir inicialização completa
    await asyncio.sleep(300)

    while True:
        try:
            await run_identity_rumination_sync()

            # Aguardar próximo ciclo (1 hora)
            logger.info("⏰ Próxima sincronização Identidade↔Ruminação em 1h")
            await asyncio.sleep(1 * 3600)

        except Exception as e:
            logger.error(f"❌ Erro no scheduler de sincronização: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Em caso de erro, aguardar 1 hora e tentar novamente
            await asyncio.sleep(3600)


if __name__ == "__main__":
    # Executar sincronização manualmente
    asyncio.run(run_identity_rumination_sync())
