"""Legacy diagnostic admin API routes."""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from admin_web.auth.middleware import require_master
from security_config import unsafe_admin_endpoints_enabled

router = APIRouter(prefix="/admin", tags=["diagnostics"])
logger = logging.getLogger(__name__)
UNSAFE_ADMIN_ENDPOINTS_ENABLED = unsafe_admin_endpoints_enabled()

_db_manager = None


def init_diagnostics_routes(db_manager):
    """Inicializa rotas de diagnostico com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager
    logger.info("Rotas de diagnostico inicializadas")


def get_db():
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="DatabaseManager nao disponivel")
    return _db_manager


def internal_error_response(message: str = "Erro interno do servidor", status_code: int = 500) -> JSONResponse:
    """Retorna uma resposta de erro generica sem expor detalhes internos."""
    return JSONResponse({"error": message}, status_code=status_code)


@router.get("/api/diagnose")
async def run_diagnosis(admin: Dict = Depends(require_master)):
    """Roda diagnostico completo (SQLite vs Chroma)."""
    db = get_db()

    sqlite_users = db.get_all_users(platform="telegram")
    sqlite_count = sum(u.get("total_messages", 0) for u in sqlite_users)

    chroma_count = 0
    chroma_status = "Desconectado"

    if db.chroma_enabled:
        try:
            chroma_count = db.vectorstore._collection.count()
            chroma_status = "Conectado"
        except Exception as e:
            chroma_status = f"Erro: {str(e)}"

    html = f"""
    <div class="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div class="bg-white overflow-hidden shadow rounded-lg">
            <div class="px-4 py-5 sm:p-6">
                <dt class="text-sm font-medium text-gray-500 truncate">SQLite (Metadados)</dt>
                <dd class="mt-1 text-3xl font-semibold text-gray-900">{sqlite_count}</dd>
            </div>
        </div>
        <div class="bg-white overflow-hidden shadow rounded-lg">
            <div class="px-4 py-5 sm:p-6">
                <dt class="text-sm font-medium text-gray-500 truncate">ChromaDB (Vetores)</dt>
                <dd class="mt-1 text-3xl font-semibold text-gray-900">{chroma_count}</dd>
                <dd class="mt-1 text-sm text-gray-500">{chroma_status}</dd>
            </div>
        </div>
    </div>
    """

    if abs(sqlite_count - chroma_count) > 5:
        html += """
        <div class="mt-4 bg-red-50 border-l-4 border-red-400 p-4">
            <div class="flex">
                <div class="flex-shrink-0">⚠️</div>
                <div class="ml-3">
                    <p class="text-sm text-red-700">
                        Descasamento detectado! Diferença de {diff} registros.
                    </p>
                </div>
            </div>
        </div>
        """.format(diff=abs(sqlite_count - chroma_count))
    else:
        html += """
        <div class="mt-4 bg-green-50 border-l-4 border-green-400 p-4">
            <div class="flex">
                <div class="flex-shrink-0">✅</div>
                <div class="ml-3">
                    <p class="text-sm text-green-700">
                        Sincronização saudável.
                    </p>
                </div>
            </div>
        </div>
        """

    return HTMLResponse(html)


@router.get("/api/diagnose-facts")
async def diagnose_facts(admin: Dict = Depends(require_master)):
    """
    API para diagnosticar vazamento de memoria entre usuarios.
    Retorna todos os fatos de todos os usuarios para analise.
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute("SELECT user_id, user_name, platform FROM users ORDER BY user_name")
        users = cursor.fetchall()

        users_list = []
        for user in users:
            users_list.append(
                {
                    "user_id": user["user_id"],
                    "user_name": user["user_name"],
                    "platform": user["platform"],
                }
            )

        facts_by_user = {}
        for user in users:
            user_id = user["user_id"]

            cursor.execute(
                """
                SELECT fact_category, fact_key, fact_value, is_current, version,
                       source_conversation_id
                FROM user_facts
                WHERE user_id = ?
                ORDER BY fact_category, fact_key, version DESC
            """,
                (user_id,),
            )

            facts = cursor.fetchall()

            facts_by_user[user_id] = {
                "user_name": user["user_name"],
                "facts": [],
            }

            for fact in facts:
                facts_by_user[user_id]["facts"].append(
                    {
                        "category": fact["fact_category"],
                        "key": fact["fact_key"],
                        "value": fact["fact_value"],
                        "is_current": bool(fact["is_current"]),
                        "version": fact["version"],
                        "source_conversation_id": fact["source_conversation_id"],
                    }
                )

        cursor.execute(
            """
            SELECT COUNT(*) as count FROM user_facts WHERE user_id IS NULL OR user_id = ''
        """
        )
        null_facts_count = cursor.fetchone()["count"]

        cursor.execute(
            """
            SELECT fact_category, fact_key, fact_value, COUNT(DISTINCT user_id) as user_count,
                   GROUP_CONCAT(DISTINCT user_id) as user_ids
            FROM user_facts
            WHERE is_current = 1
            GROUP BY fact_category, fact_key, fact_value
            HAVING user_count > 1
        """
        )

        duplicates = cursor.fetchall()
        duplicates_list = []
        for dup in duplicates:
            duplicates_list.append(
                {
                    "category": dup["fact_category"],
                    "key": dup["fact_key"],
                    "value": dup["fact_value"],
                    "user_count": dup["user_count"],
                    "user_ids": dup["user_ids"].split(",") if dup["user_ids"] else [],
                }
            )

        return JSONResponse(
            {
                "success": True,
                "users": users_list,
                "facts_by_user": facts_by_user,
                "integrity": {
                    "null_facts_count": null_facts_count,
                    "has_null_facts": null_facts_count > 0,
                },
                "duplicates": duplicates_list,
                "has_leaks": len(duplicates_list) > 0,
            }
        )

    except Exception as e:
        logger.error(f"❌ Erro ao diagnosticar fatos: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao diagnosticar fatos")


@router.get("/api/diagnose-chromadb")
async def diagnose_chromadb(admin: Dict = Depends(require_master)):
    """
    API para diagnosticar vazamento de memoria no ChromaDB.
    Retorna todas as conversas salvas no ChromaDB com seus metadados.
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()

        if not db.chroma_enabled:
            return JSONResponse(
                {
                    "success": False,
                    "error": "ChromaDB está desabilitado",
                    "chroma_enabled": False,
                }
            )

        try:
            collection = db.vectorstore._collection
            all_docs = collection.get(include=["metadatas", "documents"])

            docs_by_user = {}
            total_docs = len(all_docs["ids"])

            for i in range(total_docs):
                doc_id = all_docs["ids"][i]
                metadata = all_docs["metadatas"][i]
                document = all_docs["documents"][i]

                user_id = metadata.get("user_id", "N/A")
                user_name = metadata.get("user_name", "N/A")

                if user_id not in docs_by_user:
                    docs_by_user[user_id] = {
                        "user_name": user_name,
                        "document_count": 0,
                        "documents": [],
                    }

                docs_by_user[user_id]["document_count"] += 1
                docs_by_user[user_id]["documents"].append(
                    {
                        "doc_id": doc_id,
                        "user_input": metadata.get("user_input", ""),
                        "ai_response": metadata.get("ai_response", ""),
                        "conversation_id": metadata.get("conversation_id", "N/A"),
                        "timestamp": metadata.get("timestamp", "N/A"),
                        "preview": document[:200] if document else "",
                    }
                )

            cursor = db.conn.cursor()
            cursor.execute("SELECT user_id, user_name FROM users")
            registered_users = {row["user_id"]: row["user_name"] for row in cursor.fetchall()}

            orphan_docs = []
            for user_id in docs_by_user.keys():
                if user_id not in registered_users and user_id != "N/A":
                    orphan_docs.append(
                        {
                            "user_id": user_id,
                            "document_count": docs_by_user[user_id]["document_count"],
                        }
                    )

            return JSONResponse(
                {
                    "success": True,
                    "chroma_enabled": True,
                    "total_documents": total_docs,
                    "registered_users": list(registered_users.keys()),
                    "users_with_documents": list(docs_by_user.keys()),
                    "docs_by_user": docs_by_user,
                    "orphan_docs": orphan_docs,
                    "has_orphans": len(orphan_docs) > 0,
                }
            )

        except Exception as e:
            logger.error(f"❌ Erro ao acessar ChromaDB: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return internal_error_response("Erro ao acessar ChromaDB")

    except Exception as e:
        logger.error(f"❌ Erro ao diagnosticar ChromaDB: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao diagnosticar ChromaDB")


@router.get("/api/conversation/{conversation_id}")
async def get_conversation_detail(conversation_id: int, admin: Dict = Depends(require_master)):
    """Retorna detalhes completos de uma conversa especifica."""
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute(
            """
            SELECT c.id, c.user_id, c.user_input, c.ai_response, c.timestamp,
                   u.user_name, u.platform
            FROM conversations c
            LEFT JOIN users u ON c.user_id = u.user_id
            WHERE c.id = ?
        """,
            (conversation_id,),
        )

        conv = cursor.fetchone()

        if not conv:
            return JSONResponse({"error": "Conversa não encontrada"}, status_code=404)

        return JSONResponse(
            {
                "success": True,
                "conversation": {
                    "id": conv["id"],
                    "user_id": conv["user_id"],
                    "user_name": conv["user_name"],
                    "platform": conv["platform"],
                    "timestamp": conv["timestamp"],
                    "user_input": conv["user_input"],
                    "ai_response": conv["ai_response"],
                },
            }
        )

    except Exception as e:
        logger.error(f"❌ Erro ao buscar conversa: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao buscar conversa")
