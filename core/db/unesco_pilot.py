"""Persistência do baseline do piloto UNESCO com origem verificável (C12f).

O onboarding (Telegram) grava por aqui a Relation elegível RESPONSÁVEL
PELA COLETA em ``unesco_pilot_data.origin_relation_id`` — o export por org
só libera registros com origem própria (r2): sem relation elegível grava
NULL (master-only). Registros antigos não passam por este caminho e
permanecem sem origem — nada é atribuído retroativamente.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def resolve_collection_origin_relation_id(
    db: Any, participant_user_id: str
) -> Optional[str]:
    """Relation elegível (gate C12g) responsável pela coleta, ou ``None``.

    Distinção r4: falha TÉCNICA (instância, capability ausente, exceção do
    lookup) é registrada e PROPAGADA antes de qualquer gravação — nunca
    vira origem ``NULL`` silenciosa (que apagaria origem existente).
    Relation realmente ausente ou inelegível (status/consentimento) segue
    produzindo ``None``: o registro fica master-only (fail-closed).
    """
    try:
        from engines.will_scope import resolve_instance

        instance = resolve_instance(getattr(db, "agent_instance", None))
    except Exception:
        logger.exception(
            "unesco_pilot: falha tecnica ao resolver instancia (user=%s) "
            "— coleta interrompida antes da gravacao",
            participant_user_id,
        )
        raise
    getter = getattr(db, "get_agent_relation_for_participant", None)
    if getter is None:
        logger.error(
            "unesco_pilot: capability de relations ausente no db (user=%s) "
            "— coleta interrompida antes da gravacao",
            participant_user_id,
        )
        raise LookupError("relations_capability_ausente")
    if not instance:
        logger.error(
            "unesco_pilot: instancia vazia na coleta (user=%s) "
            "— coleta interrompida antes da gravacao",
            participant_user_id,
        )
        raise LookupError("agent_instance_ausente")
    try:
        relation = getter(
            agent_instance=instance, participant_user_id=participant_user_id
        )
    except Exception:
        logger.exception(
            "unesco_pilot: falha no lookup da Relation (user=%s, "
            "instance=%s) — coleta interrompida antes da gravacao",
            participant_user_id,
            instance,
        )
        raise
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
