"""Memory metric helpers for legacy research lab routes."""
from datetime import datetime
from typing import Dict, Optional

from fastapi import HTTPException

def _sqlite_table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _safe_iso_label(value: Optional[str]) -> str:
    if not value:
        return "N/A"
    return str(value).replace("T", " ")[:19]


def _group_counts(cursor, query: str, params: tuple = ()) -> Dict[str, int]:
    cursor.execute(query, params)
    return {
        str(row[0]): int(row[1] or 0)
        for row in cursor.fetchall()
        if row[0]
    }


def _fetch_current_sqlite_facts(cursor, user_id: str) -> Dict[str, object]:
    has_v2 = _sqlite_table_exists(cursor, "user_facts_v2")
    has_v1 = _sqlite_table_exists(cursor, "user_facts")

    facts_v2 = []
    if has_v2:
        cursor.execute("""
            SELECT fact_category, fact_type, fact_attribute, fact_value,
                   confidence, extraction_method, context, source_conversation_id, version
            FROM user_facts_v2
            WHERE user_id = ? AND is_current = 1
            ORDER BY fact_category, fact_type, fact_attribute, confidence DESC, version DESC
        """, (user_id,))
        facts_v2 = [
            {
                "category": row["fact_category"],
                "type": row["fact_type"],
                "attribute": row["fact_attribute"],
                "value": row["fact_value"],
                "confidence": row["confidence"],
                "extraction_method": row["extraction_method"],
                "context": row["context"],
                "source_conversation_id": row["source_conversation_id"],
                "version": row["version"],
            }
            for row in cursor.fetchall()
        ]

    facts_v1 = []
    if has_v1:
        cursor.execute("""
            SELECT fact_category, fact_key, fact_value, confidence,
                   source_conversation_id, version
            FROM user_facts
            WHERE user_id = ? AND is_current = 1
            ORDER BY fact_category, fact_key, version DESC
        """, (user_id,))
        facts_v1 = [
            {
                "category": row["fact_category"],
                "attribute": row["fact_key"],
                "value": row["fact_value"],
                "confidence": row["confidence"],
                "source_conversation_id": row["source_conversation_id"],
                "version": row["version"],
            }
            for row in cursor.fetchall()
        ]

    canonical_facts = facts_v2 if facts_v2 else facts_v1

    return {
        "has_v2": has_v2,
        "has_v1": has_v1,
        "canonical_source": "user_facts_v2" if facts_v2 else ("user_facts" if facts_v1 else None),
        "canonical_facts": canonical_facts,
        "facts_v2": facts_v2,
        "facts_v1": facts_v1,
    }


def _fetch_user_memory_detail(db, user_id: str) -> Dict[str, object]:
    cursor = db.conn.cursor()

    cursor.execute("""
        SELECT user_id,
               COALESCE(NULLIF(user_name, ''), NULLIF(first_name, ''), 'Sem nome') AS user_name,
               platform,
               platform_id,
               last_seen
        FROM users
        WHERE user_id = ?
    """, (user_id,))
    user_row = cursor.fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    cursor.execute("""
        SELECT COUNT(*) AS conversation_count,
               MAX(timestamp) AS last_conversation_at,
               SUM(CASE WHEN chroma_id IS NOT NULL AND chroma_id != '' THEN 1 ELSE 0 END) AS chroma_linked_conversations
        FROM conversations
        WHERE user_id = ?
    """, (user_id,))
    conversation_stats = cursor.fetchone()

    sqlite_facts = _fetch_current_sqlite_facts(cursor, user_id)

    knowledge_gaps = []
    if _sqlite_table_exists(cursor, "knowledge_gaps"):
        cursor.execute("""
            SELECT topic, the_gap, importance_score, status, created_at
            FROM knowledge_gaps
            WHERE user_id = ? AND status = 'open'
            ORDER BY importance_score DESC, created_at DESC
            LIMIT 20
        """, (user_id,))
        knowledge_gaps = [
            {
                "topic": row["topic"],
                "gap": row["the_gap"],
                "importance_score": row["importance_score"],
                "status": row["status"],
                "created_at": _safe_iso_label(row["created_at"]),
            }
            for row in cursor.fetchall()
        ]

    mem0_memories = []
    mem0_error = None
    if getattr(db, "mem0", None):
        try:
            mem0_memories = db.mem0.get_all_memories(user_id)
        except Exception as e:
            mem0_error = str(e)

    return {
        "user": {
            "user_id": user_row["user_id"],
            "user_name": user_row["user_name"],
            "platform": user_row["platform"],
            "platform_id": user_row["platform_id"],
            "last_seen": _safe_iso_label(user_row["last_seen"]),
        },
        "summary": {
            "conversation_count": int(conversation_stats["conversation_count"] or 0),
            "last_conversation_at": _safe_iso_label(conversation_stats["last_conversation_at"]),
            "chroma_linked_conversations": int(conversation_stats["chroma_linked_conversations"] or 0),
            "sqlite_current_facts": len(sqlite_facts["canonical_facts"]),
            "sqlite_v2_current_facts": len(sqlite_facts["facts_v2"]),
            "sqlite_v1_current_facts": len(sqlite_facts["facts_v1"]),
            "knowledge_gaps": len(knowledge_gaps),
            "mem0_memories": len(mem0_memories),
        },
        "sqlite": sqlite_facts,
        "knowledge_gaps": knowledge_gaps,
        "mem0": {
            "enabled": bool(getattr(db, "mem0", None)),
            "error": mem0_error,
            "memories": mem0_memories,
        },
        "chroma": {
            "enabled": bool(getattr(db, "chroma_enabled", False)),
            "legacy": True,
            "note": "ChromaDB é legado/local fallback. Em produção com Qdrant, deve permanecer desligado.",
        },
    }


def _build_memory_metrics_payload(db) -> Dict[str, object]:
    cursor = db.conn.cursor()

    cursor.execute("""
        SELECT
            u.user_id,
            COALESCE(NULLIF(u.user_name, ''), NULLIF(u.first_name, ''), 'Sem nome') AS user_name,
            u.platform,
            u.last_seen,
            COUNT(c.id) AS conversation_count,
            MAX(c.timestamp) AS last_conversation_at,
            SUM(CASE WHEN c.chroma_id IS NOT NULL AND c.chroma_id != '' THEN 1 ELSE 0 END) AS chroma_linked_conversations
        FROM users u
        LEFT JOIN conversations c ON c.user_id = u.user_id
        GROUP BY u.user_id, u.user_name, u.first_name, u.platform, u.last_seen
        ORDER BY conversation_count DESC, COALESCE(MAX(c.timestamp), u.last_seen) DESC, user_name ASC
    """)
    users = []
    for row in cursor.fetchall():
        user = {
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "platform": row["platform"],
            "last_seen": _safe_iso_label(row["last_seen"]),
            "last_conversation_at": _safe_iso_label(row["last_conversation_at"]),
            "conversation_count": int(row["conversation_count"] or 0),
            "chroma_linked_conversations": int(row["chroma_linked_conversations"] or 0),
            "sqlite_current_facts": 0,
            "sqlite_v2_current_facts": 0,
            "sqlite_v1_current_facts": 0,
            "knowledge_gaps": 0,
        }
        users.append(user)

    users_by_id = {user["user_id"]: user for user in users}

    has_v2 = _sqlite_table_exists(cursor, "user_facts_v2")
    has_v1 = _sqlite_table_exists(cursor, "user_facts")
    has_knowledge_gaps = _sqlite_table_exists(cursor, "knowledge_gaps")

    if has_v2:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM user_facts_v2 WHERE is_current = 1 GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["sqlite_v2_current_facts"] = count

    if has_v1:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM user_facts WHERE is_current = 1 GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["sqlite_v1_current_facts"] = count

    if has_knowledge_gaps:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM knowledge_gaps WHERE status = 'open' GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["knowledge_gaps"] = count

    for user in users:
        user["sqlite_current_facts"] = (
            user["sqlite_v2_current_facts"]
            if has_v2
            else user["sqlite_v1_current_facts"]
        )

    total_conversations = sum(user["conversation_count"] for user in users)
    total_sqlite_current_facts = sum(user["sqlite_current_facts"] for user in users)
    total_chroma_links = sum(user["chroma_linked_conversations"] for user in users)
    users_with_facts = sum(1 for user in users if user["sqlite_current_facts"] > 0)

    recent_conversations_30d = 0
    if _sqlite_table_exists(cursor, "conversations"):
        cursor.execute("""
            SELECT COUNT(*)
            FROM conversations
            WHERE timestamp >= datetime('now', '-30 day')
        """)
        recent_conversations_30d = int(cursor.fetchone()[0] or 0)

    chroma_documents = None
    chroma_error = None
    if getattr(db, "chroma_enabled", False):
        try:
            chroma_documents = int(db.vectorstore._collection.count())
        except Exception as e:
            chroma_error = str(e)

    mem0_enabled = bool(getattr(db, "mem0", None))
    mem0_healthy = False
    mem0_error = None
    if mem0_enabled:
        try:
            mem0_healthy = bool(db.mem0.health_check())
        except Exception as e:
            mem0_error = str(e)

    return {
        "generated_at": datetime.now().isoformat(),
        "overview": {
            "total_users": len(users),
            "total_conversations": total_conversations,
            "recent_conversations_30d": recent_conversations_30d,
            "total_sqlite_current_facts": total_sqlite_current_facts,
            "total_chroma_links": total_chroma_links,
            "users_with_facts": users_with_facts,
        },
        "layers": {
            "sqlite": {
                "has_v2": has_v2,
                "has_v1": has_v1,
                "canonical_source": "user_facts_v2" if has_v2 else ("user_facts" if has_v1 else None),
                "note": "SQLite é a camada canônica de fatos estruturados usada pelo sistema legado de recall.",
            },
            "mem0": {
                "enabled": mem0_enabled,
                "healthy": mem0_healthy,
                "note": "Mem0/Qdrant é memória semântica remota e extração automática; seus detalhes são carregados sob demanda por usuário.",
                "error": mem0_error,
            },
            "chroma": {
                "enabled": bool(getattr(db, "chroma_enabled", False)),
                "legacy": True,
                "documents": chroma_documents,
                "note": "ChromaDB é legado/local fallback. Com mem0/Qdrant configurado, fica desligado para evitar dupla memória vetorial.",
                "error": chroma_error,
            },
        },
        "users": users,
    }


