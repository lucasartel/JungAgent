"""
memory_flush.py - Pre-compaction flush para o JungAgent

Antes de truncar um chat_history longo, extrai os fragmentos
psicologicamente relevantes das mensagens mais antigas e os persiste
tanto em user_facts_v2 (SQLite) quanto no log diário (.md).

Uso:
    from memory_flush import flush_if_needed
    chat_history = flush_if_needed(db, anthropic_client, user_id, user_name, chat_history)
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

FLUSH_THRESHOLD = 20          # mensagens no chat_history antes de disparar flush
KEEP_RECENT = 12              # quantas mensagens manter após flush


def flush_if_needed(
    db,
    anthropic_client,
    user_id: str,
    user_name: str,
    chat_history: List[Dict],
) -> List[Dict]:
    """
    Se chat_history tiver mais de FLUSH_THRESHOLD mensagens, extrai fragmentos
    relevantes das mensagens que serão descartadas e as persiste.

    Retorna o chat_history (possivelmente truncado).
    """
    if len(chat_history) <= FLUSH_THRESHOLD:
        return chat_history

    to_flush = chat_history[:-KEEP_RECENT]
    keep = chat_history[-KEEP_RECENT:]

    logger.info(
        f"🔄 [FLUSH] chat_history={len(chat_history)} msgs → "
        f"flushing {len(to_flush)}, mantendo {len(keep)}"
    )

    _extract_and_persist(db, anthropic_client, user_id, user_name, to_flush)

    return keep


def _extract_and_persist(
    db,
    anthropic_client,
    user_id: str,
    user_name: str,
    messages: List[Dict],
) -> None:
    """
    Usa o LLM interno para extrair fragmentos importantes das mensagens e
    os persiste em user_facts_v2 e no log diário.
    """
    # Montar texto das mensagens para o LLM analisar
    text_block = "\n".join(
        f"{'Usuário' if m['role'] == 'user' else 'Jung'}: {m['content'][:300]}"
        for m in messages
    )

    prompt = (
        "Analise o trecho de conversa abaixo e extraia APENAS os fragmentos "
        "psicologicamente relevantes que devem ser preservados a longo prazo "
        "(fatos sobre a vida do usuário, eventos importantes, sentimentos "
        "recorrentes, valores, decisões de vida). "
        "Responda como uma lista de frases curtas em português. "
        "Se não houver nada relevante, responda 'nenhum'.\n\n"
        f"{text_block}"
    )

    fragments: Optional[str] = None
    try:
        from anthropic import Anthropic
        msg = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        fragments = msg.content[0].text.strip()
    except Exception as e:
        logger.warning(f"⚠️ [FLUSH] LLM indisponível, salvando raw: {e}")
        fragments = None

    if not fragments or fragments.lower().startswith("nenhum"):
        logger.info("🔄 [FLUSH] Nenhum fragmento relevante encontrado")
        return

    logger.info(f"🔄 [FLUSH] Fragmentos extraídos:\n{fragments[:300]}")

    # Persistir como entrada especial no log diário
    try:
        from engines.participant_files import relation_file_scope
        from user_profile_writer import write_session_entry
        file_instance, file_relation = relation_file_scope(db, user_id)
        write_session_entry(
            user_id=user_id,
            user_name=user_name,
            user_input="[FRAGMENTOS RECUPERADOS ANTES DO FLUSH]",
            ai_response=fragments,
            tag="[FLUSH]",
            agent_instance=file_instance,
            relation_id=file_relation,
        )
    except Exception as e:
        logger.warning(f"⚠️ [FLUSH] Erro ao gravar log diário: {e}")

    # Tentar salvar como fatos via extrator existente
    try:
        if hasattr(db, 'fact_extractor') and db.fact_extractor:
            extracted, corrections = db.fact_extractor.extract_facts(
                user_input=fragments,
                user_id=user_id,
                existing_facts=db._get_current_facts(user_id),
            )
            for fact in extracted:
                db._save_fact_v2(
                    user_id=user_id,
                    category=fact.category,
                    fact_type=fact.fact_type,
                    attribute=fact.attribute,
                    value=fact.value,
                    confidence=fact.confidence * 0.8,  # confiança ligeiramente reduzida por ser flush
                    extraction_method="flush",
                    context=fact.context,
                    conversation_id=None,
                )
            logger.info(f"🔄 [FLUSH] {len(extracted)} fatos salvos de {len(messages)} msgs descartadas")
    except Exception as e:
        logger.warning(f"⚠️ [FLUSH] Erro ao salvar fatos: {e}")
