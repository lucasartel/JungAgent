"""Symbolic Knowledge Graph (SKG) Context Builder for Phase V Stage B.

Constructs active causal and relational context from the Symbolic Graph to inject into
the agent's prompt context during conversations, enabling deep causal reasoning,
contradiction awareness, and evidence-grounded responses.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

logger = logging.getLogger(__name__)


class SymbolicGraphContextBuilder:
    """Builds prompt context from the Symbolic Knowledge Graph for active reasoning."""

    def __init__(
        self,
        db_manager: Any,
        *,
        agent_instance: Optional[str] = None,
        max_hops: int = 2,
        max_triples: int = 12,
    ):
        self.db = db_manager
        self.agent_instance = agent_instance or getattr(db_manager, "agent_instance", AGENT_INSTANCE)
        self.max_hops = max(1, min(3, int(max_hops)))
        self.max_triples = max(1, min(30, int(max_triples)))

    def find_seed_nodes(self, user_id: str, message_text: str = "") -> List[str]:
        """Identifies relevant seed entities to query from the graph."""
        import re
        tokens = set(re.findall(r'\b\w{3,}\b', (message_text or "").lower()))
        if not tokens:
            return []

        is_admin = (user_id == ADMIN_USER_ID)
        seeds: List[str] = []
        if is_admin:
            seeds.append("Lucas")

        placeholders = ','.join('?' * len(tokens))
        query = f"""
            SELECT DISTINCT entity_name FROM symbolic_nodes
            WHERE agent_instance = ? AND LOWER(entity_name) IN ({placeholders})
            LIMIT 20
        """
        params = [self.agent_instance] + list(tokens)
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                if row[0] not in seeds:
                    seeds.append(row[0])
        except Exception as exc:
            logger.debug("SymbolicGraphContextBuilder: seed matching error: %s", exc)

        return seeds[:6]

    def build_causal_context(
        self,
        *,
        user_id: str,
        message_text: str = "",
    ) -> Dict[str, Any]:
        """Traverses the graph around seed nodes and formats the prompt context block."""
        if not hasattr(self.db, "query_causal_neighborhood"):
            return {"status": "unavailable", "reason": "symbolic_graph_mixin_missing", "context_block": ""}

        relation_id = None
        resolver = getattr(self.db, "resolve_relation_id", None)
        if callable(resolver):
            relation_id = resolver(
                agent_instance=self.agent_instance,
                participant_user_id=user_id,
            )
            if not relation_id and str(user_id) != str(ADMIN_USER_ID):
                return {
                    "status": "denied",
                    "reason": "relation_required_for_symbolic_context",
                    "context_block": "",
                    "triple_count": 0,
                    "triples": [],
                }

        seeds = self.find_seed_nodes(user_id=user_id, message_text=message_text)
        collected_triples: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[str, str, str]] = set()

        for seed in seeds:
            try:
                paths = self.db.query_causal_neighborhood(
                    agent_instance=self.agent_instance,
                    start_node_name=seed,
                    max_depth=self.max_hops,
                    limit=self.max_triples,
                    relation_id=relation_id,
                    include_legacy=not relation_id and str(user_id) == str(ADMIN_USER_ID),
                )
                for p in paths:
                    key = (p.get("subject", ""), p.get("predicate", ""), p.get("object", ""))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        collected_triples.append(p)
            except Exception as exc:
                logger.debug("SymbolicGraphContextBuilder: query error for seed %s: %s", seed, exc)

        # Se tiver poucas conexões por travessia, busca as triplas mais recentes
        # This is a deliberate warm-start strategy to ensure some context is available
        if len(collected_triples) < 4 and hasattr(self.db, "list_symbolic_triples"):
            try:
                recent = self.db.list_symbolic_triples(
                    agent_instance=self.agent_instance,
                    limit=min(3, self.max_triples),
                    relation_id=relation_id,
                    include_legacy=not relation_id and str(user_id) == str(ADMIN_USER_ID),
                )
                for t in recent:
                    key = (t.get("subject", ""), t.get("predicate", ""), t.get("object", ""))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        collected_triples.append(t)
            except Exception as exc:
                logger.debug("SymbolicGraphContextBuilder: fallback list error: %s", exc)

        # Ordenar por relevância / profundidade / confiança
        def _sort_key(item: Dict[str, Any]) -> Tuple[int, float]:
            depth = item.get("depth", 1)
            conf = float(item.get("confidence") or 1.0)
            return (depth, -conf)

        collected_triples.sort(key=_sort_key)
        final_triples = collected_triples[: self.max_triples]

        if not final_triples:
            return {"status": "empty", "context_block": "", "triple_count": 0, "triples": []}

        lines = [
            "=== [GRAFO SIMBÓLICO - CONEXÕES CAUSAIS & EVIDÊNCIAS (SKG)] ===",
            "(Estrutura causal verificada em SQLite. Use para fundamentar raciocínios dedutivos e dialéticos sem inventar fontes):",
        ]

        for t in final_triples:
            subj = t.get("subject", "")
            pred = t.get("predicate", "")
            obj = t.get("object", "")
            conf = float(t.get("confidence", 1.0))
            src = t.get("source_ref", "N/A")
            lines.append(f"- ({subj} -[{pred}]-> {obj}) [fonte: {src} | conf: {conf:.2f}]")

        lines.append(
            "Diretriz: Quando relevante, articule essas conexões causais de modo orgânico na conversa."
        )

        context_block = "\n".join(lines)
        return {
            "status": "available",
            "context_block": context_block,
            "triple_count": len(final_triples),
            "triples": final_triples,
        }
