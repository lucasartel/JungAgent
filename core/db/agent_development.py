from __future__ import annotations

import logging
from typing import Dict, List, Optional

from instance_config import AGENT_INSTANCE

logger = logging.getLogger(__name__)


def _development_scope(manager, user_id: str, relation_id: Optional[str] = None):
    """Resolve (instância, Relation, cláusula, params, colunas_presentes).

    C12b (T1-2a): ``agent_development`` ganhou colunas de escopo cognitivo e
    a unicidade passou a ser por (usuário, escopo). Bancos anteriores à
    migração não têm as colunas — presence-check mantém o comportamento
    antigo (mesma regra de compat das demais tabelas com escopo).
    """
    instance = (
        str(getattr(manager, "agent_instance", None) or AGENT_INSTANCE or "").strip()
        or None
    )
    if relation_id is None:
        resolver = getattr(manager, "resolve_relation_id", None)
        if callable(resolver):
            relation_id = (
                resolver(
                    agent_instance=instance, participant_user_id=str(user_id)
                )
                or None
            )
    # C12b r3: conexões SQLite cruas (CLI --db-path) não têm `.conn` — sem
    # resolver aqui, o PRAGMA falhava e o filtro ficava vazio mesmo em banco
    # já migrado (sobrescrita entre escopos pela conexão direta).
    conn = getattr(manager, "conn", manager)
    try:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(agent_development)"
            ).fetchall()
        }
    except Exception:  # pragma: no cover - banco indisponível
        columns = set()
    has_columns = "relation_id" in columns and "agent_instance" in columns
    if not has_columns:
        return instance, relation_id, "", [], False
    scope_method = getattr(manager, "_analysis_scope_clause", None)
    if callable(scope_method):
        clause, params = scope_method("agent_development", relation_id)
    else:
        from core.db.analysis_records import analysis_scope_clause

        clause, params = analysis_scope_clause(
            conn, "agent_development", relation_id, instance
        )
    return instance, relation_id, clause, params, True


def ensure_agent_state(
    manager, user_id: str, *, relation_id: Optional[str] = None
) -> None:
    """Ensure the user has one agent_development row for the target scope."""
    with manager._lock:
        instance, resolved, clause, params, has_columns = _development_scope(
            manager, user_id, relation_id
        )
        cursor = manager.conn.cursor()
        cursor.execute(
            "SELECT id FROM agent_development WHERE user_id = ?" + clause,
            (user_id, *params),
        )

        if not cursor.fetchone():
            # C12b r2: na transição para Relations, a linha legada
            # (relation/instance NULL) é VINCULADA ao escopo atual em vez de
            # deixar o histórico órfão atrás de uma linha nova zerada.
            if has_columns and _adopt_legacy_state(
                manager, user_id, instance, resolved
            ):
                return
            if has_columns:
                cursor.execute(
                    "INSERT INTO agent_development"
                    " (user_id, phase, relation_id, agent_instance)"
                    " VALUES (?, 0, ?, ?)",
                    (user_id, resolved, instance),
                )
            else:
                cursor.execute(
                    "INSERT INTO agent_development (user_id, phase) VALUES (?, 0)",
                    (user_id,),
                )
            manager.conn.commit()
            logger.info(
                "Agent state inicializado para user_id=%s (relacao=%s)",
                user_id,
                resolved,
            )


def _adopt_legacy_state(manager, user_id: str, instance, relation) -> bool:
    """Vincula a linha legada (relation/instance NULL) ao escopo atual.

    Chamada quando a leitura por escopo não encontra linha: a linha que
    preexistia à migração guarda fase/interações/scores e pertence ao
    primeiro escopo resolvido do usuário. Retorna True se adotou.

    C12b r3: adota também a linha da PRÓPRIA instância ainda sem Relation
    (criada antes do cadastro); linhas de outras instâncias nunca são
    adotadas. Conexões cruas não têm `.conn`.
    """
    conn = getattr(manager, "conn", manager)
    cursor = conn.execute(
        "UPDATE agent_development"
        " SET relation_id = ?, agent_instance = ?"
        " WHERE user_id = ?"
        "   AND relation_id IS NULL"
        "   AND (agent_instance IS NULL OR agent_instance = ?)",
        (relation, instance, user_id, instance),
    )
    adopted = bool(cursor.rowcount)
    if adopted:
        conn.commit()
        logger.info(
            "Agent state legado adotado para user_id=%s (relacao=%s, instancia=%s)",
            user_id,
            relation,
            instance,
        )
    return adopted


def update_agent_development(
    manager, user_id: str, *, relation_id: Optional[str] = None
) -> None:
    """Update lightweight metrics for one user without changing narrative phase."""
    ensure_agent_state(manager, user_id, relation_id=relation_id)

    with manager._lock:
        _, _, clause, params, _ = _development_scope(
            manager, user_id, relation_id
        )
        cursor = manager.conn.cursor()
        cursor.execute(
            f"""
            UPDATE agent_development
            SET total_interactions = total_interactions + 1,
                self_awareness_score = MIN(1.0, self_awareness_score + 0.001),
                moral_complexity_score = MIN(1.0, moral_complexity_score + 0.0008),
                emotional_depth_score = MIN(1.0, emotional_depth_score + 0.0012),
                autonomy_score = MIN(1.0, autonomy_score + 0.0005),
                depth_level = (self_awareness_score + moral_complexity_score + emotional_depth_score) / 3,
                autonomy_level = autonomy_score,
                last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            {clause}
            """,
            (user_id, *params),
        )
        manager.conn.commit()


def check_phase_progression(
    manager, user_id: str, *, relation_id: Optional[str] = None
) -> None:
    """Compatibility hook: narrative phase is governed by agent_development.py."""
    ensure_agent_state(manager, user_id, relation_id=relation_id)
    logger.debug("Linear phase progression disabled; narrative evaluator controls phase for user_id=%s", user_id)


def get_agent_state(
    manager, user_id: str, *, relation_id: Optional[str] = None
) -> Optional[Dict]:
    """Return the current agent_development row for one user."""
    ensure_agent_state(manager, user_id, relation_id=relation_id)

    _, _, clause, params, _ = _development_scope(manager, user_id, relation_id)
    cursor = manager.conn.cursor()
    cursor.execute(
        "SELECT * FROM agent_development WHERE user_id = ?" + clause,
        (user_id, *params),
    )
    result = cursor.fetchone()

    if not result:
        logger.warning("Agent state nao encontrado para user_id=%s", user_id)
        return None
    return dict(result)


def get_milestones(manager, limit: int = 20) -> List[Dict]:
    """Return recent development milestones."""
    cursor = manager.conn.cursor()
    cursor.execute(
        """
        SELECT * FROM milestones
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]
