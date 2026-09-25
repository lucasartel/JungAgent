from __future__ import annotations

import json
import importlib.util
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from core.db import cognitive_provenance as _provenance
except ImportError:  # Direct-file tests deliberately avoid importing core.__init__.
    _path = Path(__file__).with_name("cognitive_provenance.py")
    _spec = importlib.util.spec_from_file_location("cognitive_provenance_for_dreams", _path)
    _provenance = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_provenance)

AUTHORIZED_AGGREGATE = _provenance.AUTHORIZED_AGGREGATE
INSTANCE_GLOBAL = _provenance.INSTANCE_GLOBAL
cognitive_agent_instance = _provenance.cognitive_agent_instance
cognitive_provenance = _provenance.cognitive_provenance
json_payload = _provenance.json_payload
normalize_source_refs = _provenance.normalize_source_refs
resolve_cognitive_origin = _provenance.resolve_cognitive_origin
from payload_storage import persistable_image_url, sanitize_json_text

logger = logging.getLogger(__name__)


class DreamDatabaseMixin:
    def save_dream(
        self,
        user_id: str,
        dream_content: str,
        symbolic_theme: str,
        regulatory_function: str = "",
        compensated_attitude: str = "",
        dream_mood: str = "",
        *,
        relation_id: Optional[str] = None,
        origin_class: Optional[str] = None,
        agent_instance: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Salva um sonho global preservando a origem privada ou agregada."""
        with self._lock:
            try:
                instance, resolved_origin, resolved_relation, participant = resolve_cognitive_origin(
                    self,
                    participant_user_id=user_id,
                    relation_id=relation_id,
                    origin_class=origin_class,
                    agent_instance=agent_instance,
                )
                provenance_payload = cognitive_provenance(
                    origin_class=resolved_origin,
                    relation_id=resolved_relation,
                    participant_user_id=participant,
                    extra=provenance,
                )
                cursor = self.conn.cursor()
                cursor.execute("""
                    INSERT INTO agent_dreams (
                        user_id,
                        agent_instance,
                        ownership_class,
                        origin_class,
                        origin_relation_id,
                        origin_participant_user_id,
                        source_refs_json,
                        provenance_json,
                        dream_content,
                        symbolic_theme,
                        regulatory_function,
                        compensated_attitude,
                        dream_mood,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """, (
                    user_id,
                    instance,
                    INSTANCE_GLOBAL,
                    resolved_origin,
                    resolved_relation,
                    participant,
                    json_payload(normalize_source_refs(source_refs), []),
                    json_payload(provenance_payload, {}),
                    dream_content,
                    symbolic_theme,
                    regulatory_function,
                    compensated_attitude,
                    dream_mood,
                ))
                self.conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"âŒ Erro ao salvar sonho: {e}")
                return None

    def update_dream_with_insight(self, dream_id: int, extracted_insight: str) -> bool:
        """Atualiza o sonho com o insight extraÃ­do pela ruminaÃ§Ã£o"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE agent_dreams 
                    SET extracted_insight = ?
                    WHERE id = ?
                """, (extracted_insight, dream_id))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao atualizar sonho com insight: {e}")
                return False

    def update_dream_image(
        self,
        dream_id: int,
        image_url: str,
        image_prompt: str,
        image_provider: str = "",
        image_model: str = "",
        image_status: str = "generated",
        image_raw_response_json: str = "",
    ) -> bool:
        """Salva a imagem gerada e seus metadados."""
        with self._lock:
            stored_image_url = persistable_image_url(image_url)
            stored_raw_response_json = sanitize_json_text(image_raw_response_json, max_string_chars=4000)
            for attempt in range(3):
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("""
                        UPDATE agent_dreams 
                        SET image_url = ?,
                            image_prompt = ?,
                            image_provider = ?,
                            image_model = ?,
                            image_status = ?,
                            image_raw_response_json = ?
                        WHERE id = ?
                    """, (
                        stored_image_url,
                        image_prompt,
                        image_provider,
                        image_model,
                        image_status,
                        stored_raw_response_json,
                        dream_id,
                    ))
                    self.conn.commit()
                    return cursor.rowcount > 0
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        wait_seconds = 0.4 * (attempt + 1)
                        logger.warning(
                            "âš ï¸ Banco ocupado ao atualizar imagem do sonho %s; retry em %.1fs",
                            dream_id,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue
                    logger.error(f"âŒ Erro ao atualizar imagem do sonho: {e}")
                    return False
                except Exception as e:
                    logger.error(f"âŒ Erro ao atualizar imagem do sonho: {e}")
                    return False

    def _dream_read_scope(
        self,
        *,
        user_id: str,
        relation_id: Optional[str],
        agent_instance: Optional[str],
    ) -> tuple[str, list[Any]]:
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
        if resolved_relation:
            # Revogacao C12g: residuos de sonho nao vazam de Relations
            # sem consentimento concedido.
            from core.db.relations import require_eligible_relation

            require_eligible_relation(self, resolved_relation)
            return (
                "agent_instance = ? AND (origin_relation_id = ? OR origin_class IN (?, ?))",
                [instance, resolved_relation, INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE],
            )
        if callable(resolver):
            from instance_config import ADMIN_USER_ID

            if str(user_id) != str(ADMIN_USER_ID):
                return "1 = 0", []
        return (
            "(agent_instance = ? OR agent_instance IS NULL) AND user_id = ? "
            "AND origin_relation_id IS NULL",
            [instance, user_id],
        )

    def get_latest_dream_insight(
        self,
        user_id: str,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
    ) -> Optional[Dict]:
        """Busca residuo visivel no escopo sem atravessar outra Relation."""
        with self._lock:
            cursor = self.conn.cursor()
            scope_sql, scope_params = self._dream_read_scope(
                user_id=user_id,
                relation_id=relation_id,
                agent_instance=agent_instance,
            )
            cursor.execute(f"""
                UPDATE agent_dreams
                SET status = 'faded'
                WHERE {scope_sql}
                  AND COALESCE(status, 'pending') = 'pending'
                  AND extracted_insight IS NOT NULL
                  AND created_at < datetime('now', '-24 hours')
            """, scope_params)
            cursor.execute(f"""
                SELECT id, dream_content, extracted_insight, symbolic_theme,
                       ownership_class, origin_class, origin_relation_id,
                       source_refs_json, provenance_json
                FROM agent_dreams
                WHERE {scope_sql}
                  AND extracted_insight IS NOT NULL
                  AND COALESCE(status, 'pending') = 'pending'
                  AND created_at >= datetime('now', '-24 hours')
                ORDER BY created_at DESC
                LIMIT 1
            """, scope_params)
            
            row = cursor.fetchone()
            if row:
                item = dict(row)
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
                item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
                return item
            return None

    def get_pending_unprocessed_dreams(
        self,
        user_id: Optional[str] = None,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
    ) -> List[Dict]:
        """Busca sonhos que ainda nÃ£o passaram pela ruminaÃ§Ã£o"""
        with self._lock:
            cursor = self.conn.cursor()
            query = """
                SELECT id, user_id, dream_content, symbolic_theme,
                       ownership_class, origin_class, origin_relation_id,
                       source_refs_json, provenance_json
                FROM agent_dreams
                WHERE status = 'pending' AND extracted_insight IS NULL
            """
            params: list[Any] = []
            if user_id:
                scope_sql, scope_params = self._dream_read_scope(
                    user_id=user_id,
                    relation_id=relation_id,
                    agent_instance=agent_instance,
                )
                query += f" AND {scope_sql}"
                params.extend(scope_params)
            else:
                query += " AND agent_instance = ? AND origin_relation_id IS NULL"
                params.append(cognitive_agent_instance(self, agent_instance))
                
            cursor.execute(query, params)
            dreams = []
            for row in cursor.fetchall():
                item = dict(row)
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
                item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
                dreams.append(item)
            return dreams

    def mark_dream_delivered(self, dream_id: int) -> bool:
        """Sinaliza que o insight onÃ­rico foi usado na conversa"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE agent_dreams 
                    SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (dream_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro marcar sonho como delivered: {e}")
                return False
