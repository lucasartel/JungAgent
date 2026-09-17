"""Structured user fact lookup and ranking helpers."""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class FactLookupDatabaseMixin:
    @staticmethod
    def _legacy_admin_fact_scope_allowed(user_id: str) -> bool:
        try:
            from instance_config import ADMIN_USER_ID
            return str(user_id) == str(ADMIN_USER_ID)
        except ImportError:
            return False

    def _fact_relation_scope(self, table: str, user_id: str, relation_id=None):
        resolver = getattr(self, "resolve_relation_id", None)
        if callable(resolver):
            relation_id = resolver(
                agent_instance=getattr(self, "agent_instance", None),
                participant_user_id=user_id,
                relation_id=relation_id,
            )
        try:
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        except Exception as exc:
            logger.warning("Could not inspect fact table %s: %s", table, exc)
            columns = set()
        if relation_id and "relation_id" in columns:
            return " AND relation_id = ?", [str(relation_id)]
        if callable(resolver) and not self._legacy_admin_fact_scope_allowed(user_id):
            return " AND 1 = 0", []
        if callable(resolver) and "relation_id" in columns:
            return " AND relation_id IS NULL", []
        return "", []

    def _is_factual_memory_query(self, text: str) -> bool:
        """
        Detecta perguntas factuais diretas sobre o usuÃ¡rio.

        Serve para priorizar fatos canÃ´nicos antes da busca semÃ¢ntica.
        """
        text_lower = text.lower()

        memory_markers = [
            "vocÃª lembra",
            "vc lembra",
            "lembra",
            "sabe",
            "qual Ã©",
            "qual e",
            "quais sÃ£o",
            "quais sao",
            "como se chama",
            "quem Ã©",
            "quem e",
            "me diga",
            "me fala",
        ]

        identity_targets = [
            "meu nome",
            "minha esposa",
            "meu marido",
            "meus filhos",
            "minha filha",
            "meu filho",
            "minha profissã",
            "minha profissao",
            "onde trabalho",
            "meu trabalho",
            "minha idade",
            "meu pai",
            "minha mãe",
            "minha mae",
            "minha família",
            "minha familia",
            "sobre o nome",
            "sobre nome",
            "se lembra",
            "lembra de",
            "nome da minha",
            "nome do meu",
            "quem é",
            "quem e",
        ]

        has_memory_marker = any(marker in text_lower for marker in memory_markers) or "?" in text_lower
        has_identity_target = any(target in text_lower for target in identity_targets)

        return has_memory_marker and has_identity_target

    def _get_current_facts_any(self, user_id: str, relation_id=None) -> List[Dict]:
        """Retorna fatos atuais do usuário com fallback entre V2 e V1."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='user_facts_v2'
            """)
            use_v2 = cursor.fetchone() is not None

            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='user_facts'
            """)
            use_v1 = cursor.fetchone() is not None

            if use_v2:
                scope_sql, scope_params = self._fact_relation_scope("user_facts_v2", user_id, relation_id)
                cursor.execute(f"""
                    SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                    FROM user_facts_v2
                    WHERE user_id = ? AND is_current = 1{scope_sql}
                    ORDER BY confidence DESC, fact_type, fact_attribute
                """, (user_id, *scope_params))
                rows = cursor.fetchall()
                return [
                    {
                        'category': row[0],
                        'fact_type': row[1],
                        'attribute': row[2],
                        'fact_value': row[3],
                        'confidence': row[4]
                    }
                    for row in rows
                ]

            if not use_v1:
                return []

            scope_sql, scope_params = self._fact_relation_scope("user_facts", user_id, relation_id)
            cursor.execute(f"""
                SELECT fact_category, fact_key, fact_value, confidence
                FROM user_facts
                WHERE user_id = ? AND is_current = 1{scope_sql}
                ORDER BY confidence DESC, fact_category, fact_key
            """, (user_id, *scope_params))
            rows = cursor.fetchall()
            return [
                {
                    'category': row[0],
                    'fact_type': row[0],
                    'attribute': row[1],
                    'fact_value': row[2],
                    'confidence': row[3]
                }
                for row in rows
            ]

    def _get_priority_facts_for_query(self, user_id: str, query: str, limit: int = 8, relation_id=None) -> List[Dict]:
        """
        Ranqueia fatos canônicos para perguntas factuais diretas.
        """
        if not self._is_factual_memory_query(query):
            return []

        facts = self._get_current_facts_any(user_id, relation_id=relation_id)
        if not facts:
            return []

        query_lower = query.lower()
        query_topics = set(self._detect_topics_in_text(query))

        topic_aliases = {
            "familia": {"esposa", "marido", "filho", "filha", "filhos", "pai", "mãe", "mae", "família", "familia", "nome", "irmão", "irmao", "irmã", "irma"},
            "trabalho": {"profissão", "profissao", "trabalho", "empresa", "cargo", "função", "funcao"},
            "saude": {"saúde", "saude", "terapia", "ansiedade", "depressão", "depressao"},
        }

        ranked = []
        for fact in facts:
            fact_type = str(fact.get("fact_type", "")).lower()
            attribute = str(fact.get("attribute", "")).lower()
            value = str(fact.get("fact_value", "")).lower()
            category = str(fact.get("category", "")).lower()
            confidence = float(fact.get("confidence") or 0.0)

            score = confidence

            if fact_type and (fact_type in query_lower or query_lower in fact_type):
                score += 4
            if attribute and attribute in query_lower:
                score += 3
            if attribute == "nome" and "nome" in query_lower:
                score += 4
            if value and len(value) > 2 and value in query_lower:
                score += 3

            for topic in query_topics:
                aliases = topic_aliases.get(topic, set())
                if fact_type in aliases or attribute in aliases:
                    score += 3
                if topic == "trabalho" and category in {"trabalho", "profissional"}:
                    score += 2
                if topic == "familia" and category in {"relacionamento", "familia"}:
                    score += 2

            if "esposa" in query_lower and fact_type == "esposa":
                score += 5
            if ("filhos" in query_lower or "filho" in query_lower or "filha" in query_lower) and fact_type in {"filhos", "filho", "filha"}:
                score += 5
            if ("profissão" in query_lower or "profissao" in query_lower or "trabalho" in query_lower) and (category == "trabalho" or fact_type in {"profissao", "profissão", "cargo"}):
                score += 4
            if ("pai" in query_lower or "mãe" in query_lower or "mae" in query_lower) and fact_type in {"pai", "mãe", "mae"}:
                score += 5

            ranked.append((score, fact))

        ranked.sort(key=lambda item: (item[0], item[1].get("confidence", 0)), reverse=True)

        selected = []
        seen = set()
        for _, fact in ranked:
            key = (fact.get("fact_type"), fact.get("attribute"), fact.get("fact_value"))
            if key in seen:
                continue
            seen.add(key)
            selected.append(fact)
            if len(selected) >= limit:
                break

        return selected

    
    # ========================================
    # EXTRAÃ‡ÃƒO DE FATOS
    # ========================================
    
