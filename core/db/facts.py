"""Structured user fact lookup and ranking helpers."""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class FactLookupDatabaseMixin:
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
            "minha profissÃ£o",
            "minha profissao",
            "onde trabalho",
            "meu trabalho",
            "minha idade",
            "meu pai",
            "minha mÃ£e",
            "minha mae",
            "minha famÃ­lia",
            "minha familia",
        ]

        has_memory_marker = any(marker in text_lower for marker in memory_markers) or "?" in text_lower
        has_identity_target = any(target in text_lower for target in identity_targets)

        return has_memory_marker and has_identity_target

    def _get_current_facts_any(self, user_id: str) -> List[Dict]:
        """Retorna fatos atuais do usuÃ¡rio com fallback entre V2 e V1."""
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
                cursor.execute("""
                    SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                    FROM user_facts_v2
                    WHERE user_id = ? AND is_current = 1
                    ORDER BY confidence DESC, fact_type, fact_attribute
                """, (user_id,))
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

            cursor.execute("""
                SELECT fact_category, fact_key, fact_value, confidence
                FROM user_facts
                WHERE user_id = ? AND is_current = 1
                ORDER BY confidence DESC, fact_category, fact_key
            """, (user_id,))
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

    def _get_priority_facts_for_query(self, user_id: str, query: str, limit: int = 8) -> List[Dict]:
        """
        Ranqueia fatos canÃ´nicos para perguntas factuais diretas.
        """
        if not self._is_factual_memory_query(query):
            return []

        facts = self._get_current_facts_any(user_id)
        if not facts:
            return []

        query_lower = query.lower()
        query_topics = set(self._detect_topics_in_text(query))

        topic_aliases = {
            "familia": {"esposa", "marido", "filho", "filha", "pai", "mÃ£e", "mae", "famÃ­lia", "familia", "nome"},
            "trabalho": {"profissÃ£o", "profissao", "trabalho", "empresa", "cargo", "funÃ§Ã£o", "funcao"},
            "saude": {"saÃºde", "saude", "terapia", "ansiedade", "depressÃ£o", "depressao"},
        }

        ranked = []
        for fact in facts:
            fact_type = str(fact.get("fact_type", "")).lower()
            attribute = str(fact.get("attribute", "")).lower()
            value = str(fact.get("fact_value", "")).lower()
            category = str(fact.get("category", "")).lower()
            confidence = float(fact.get("confidence") or 0.0)

            score = confidence

            if fact_type and fact_type in query_lower:
                score += 4
            if attribute and attribute in query_lower:
                score += 3
            if attribute == "nome" and "nome" in query_lower:
                score += 4
            if any(token in query_lower for token in [value]) and len(value) > 2:
                score += 1

            for topic in query_topics:
                aliases = topic_aliases.get(topic, set())
                if fact_type in aliases or attribute in aliases:
                    score += 3
                if topic == "trabalho" and category == "trabalho":
                    score += 2
                if topic == "familia" and category == "relacionamento":
                    score += 2

            if "esposa" in query_lower and fact_type == "esposa":
                score += 5
            if ("filhos" in query_lower or "filho" in query_lower or "filha" in query_lower) and fact_type == "filhos":
                score += 5
            if ("profissÃ£o" in query_lower or "profissao" in query_lower or "trabalho" in query_lower) and category == "trabalho":
                score += 4
            if ("pai" in query_lower or "mÃ£e" in query_lower or "mae" in query_lower) and fact_type in {"pai", "mÃ£e", "mae"}:
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
    
