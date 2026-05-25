from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def ensure_agent_state(manager, user_id: str) -> None:
    """Ensure the user has one agent_development row."""
    with manager._lock:
        cursor = manager.conn.cursor()
        cursor.execute("SELECT id FROM agent_development WHERE user_id = ?", (user_id,))

        if not cursor.fetchone():
            cursor.execute("INSERT INTO agent_development (user_id) VALUES (?)", (user_id,))
            manager.conn.commit()
            logger.info("Agent state inicializado para user_id=%s", user_id)


def update_agent_development(manager, user_id: str) -> None:
    """Update linear development metrics for one user."""
    ensure_agent_state(manager, user_id)

    with manager._lock:
        cursor = manager.conn.cursor()
        cursor.execute(
            """
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
            """,
            (user_id,),
        )
        manager.conn.commit()
        check_phase_progression(manager, user_id)


def check_phase_progression(manager, user_id: str) -> None:
    """Check whether the user-specific agent development phase should advance."""
    with manager._lock:
        cursor = manager.conn.cursor()
        cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()

        if not result:
            logger.warning("Agent state nao encontrado para user_id=%s", user_id)
            return

        state = dict(result)
        avg_score = (
            state["self_awareness_score"]
            + state["moral_complexity_score"]
            + state["emotional_depth_score"]
            + state["autonomy_score"]
        ) / 4
        new_phase = min(5, int(avg_score * 5) + 1)

        if new_phase > state["phase"]:
            cursor.execute("UPDATE agent_development SET phase = ? WHERE user_id = ?", (new_phase, user_id))
            cursor.execute(
                """
                INSERT INTO milestones (milestone_type, description, phase, interaction_count)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "phase_progression",
                    f"Progressao para Fase {new_phase}",
                    new_phase,
                    state["total_interactions"],
                ),
            )
            manager.conn.commit()
            logger.info("AGENTE PROGREDIU PARA FASE %s!", new_phase)


def get_agent_state(manager, user_id: str) -> Optional[Dict]:
    """Return the current agent_development row for one user."""
    ensure_agent_state(manager, user_id)

    cursor = manager.conn.cursor()
    cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (user_id,))
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
