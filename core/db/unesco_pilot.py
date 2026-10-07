"""Persistência do baseline do piloto UNESCO com origem verificável (C12f).

O onboarding (Telegram) grava por aqui a Relation elegível RESPONSÁVEL
PELA COLETA em ``unesco_pilot_data.origin_relation_id`` — o export por org
só libera registros com origem própria (r2): sem relation elegível grava
NULL (master-only). Registros antigos não passam por este caminho e
permanecem sem origem — nada é atribuído retroativamente.
"""
from __future__ import annotations

from typing import Any, Optional


def resolve_collection_origin_relation_id(
    db: Any, participant_user_id: str
) -> Optional[str]:
    """Relation elegível (gate C12g) responsável pela coleta, ou ``None``.

    Fail-closed: qualquer falha de lookup ou estado de relation não
    elegível (status/consentimento) resulta em ``None`` — o registro fica
    master-only em vez de receber origem não comprovada.
    """
    try:
        from engines.will_scope import resolve_instance

        instance = resolve_instance(getattr(db, "agent_instance", None))
    except Exception:
        instance = ""
    getter = getattr(db, "get_agent_relation_for_participant", None)
    if not instance or getter is None:
        return None
    try:
        relation = getter(
            agent_instance=instance, participant_user_id=participant_user_id
        )
    except Exception:
        return None
    from core.db.relations import is_relation_eligible

    if not is_relation_eligible(relation):
        return None
    relation_id = (relation or {}).get("relation_id")
    return str(relation_id) if relation_id else None


def save_unesco_pilot_baseline(
    db: Any,
    *,
    user_id: str,
    baseline_stress_score: int,
    baseline_trait_challenge: str,
    baseline_expectation: str,
) -> None:
    """Grava o baseline do piloto carimbando a origem da coleta atual.

    O ``INSERT OR REPLACE`` lista ``origin_relation_id`` explicitamente:
    repetir o onboarding regrava a origem da coleta elegível em vez de
    zerar a coluna (o INSERT direto antigo do Telegram não a listava).
    """
    origin = resolve_collection_origin_relation_id(db, user_id)
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO unesco_pilot_data (
            user_id, baseline_stress_score, baseline_trait_challenge,
            baseline_expectation, origin_relation_id
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            baseline_stress_score,
            baseline_trait_challenge,
            baseline_expectation,
            origin,
        ),
    )
    db.conn.commit()
