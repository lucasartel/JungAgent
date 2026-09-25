"""
jung_memory_consolidation.py - Sistema de Consolidação de Memórias

Responsável por:
- Agrupar memórias similares por período
- Gerar resumos temáticos com LLM
- Registrar padroes consolidados em SQLite/profile
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import asyncio
import json

logger = logging.getLogger(__name__)


class LLMSummaryError(RuntimeError):
    """Falha na chamada do LLM ao gerar um resumo.

    O ciclo NAO pode ser marcado como sucesso: sem isso, a marca de progresso
    registraria o resumo generico de fallback como definitivo e as proximas
    execucoes pulariam o LLM para sempre, impedindo a recuperacao do resumo.
    """


class MemoryConsolidator:
    """
    Consolida memórias similares em resumos temáticos
    """

    def __init__(self, db_manager):
        """
        Args:
            db_manager: HybridDatabaseManager instance
        """
        self.db = db_manager

    def _resolve_relation_scope(self, user_id: str, relation_id=None):
        """Resolve o escopo e valida a elegibilidade ANTES de ler conversas.

        A Relation e a fonte de elegibilidade do gate de arquivos (C12g): a
        consolidacao so pode ler, resumir em LLM ou reescrever padroes de uma
        Relation `active` com consentimento `granted`. O resolvedor do banco
        tambem devolve Relations revogadas — sem esta checagem, o job leria
        conversas e gastaria LLM antes de o gate de arquivos recusar. Estados
        `paused`/`revoked` e consentimento `pending`/`revoked` recusam o ciclo
        inteiro, com sentinela propria, antes de qualquer consulta.

        Fail-closed: sem o resolvedor de Relations o gate de consentimento nao
        pode ser avaliado, e a execucao e RECUSADA em vez de seguir so pelo
        usuario (a regra de consentimento nunca falha aberto).
        """
        resolver = getattr(self.db, "resolve_relation_id", None)
        getter = getattr(self.db, "get_agent_relation", None)
        if not callable(resolver) or not callable(getter):
            raise ValueError("consent_gate_unavailable_for_consolidation")
        relation_id = resolver(
            agent_instance=getattr(self.db, "agent_instance", None),
            participant_user_id=str(user_id),
            relation_id=relation_id,
        )
        if not relation_id:
            raise ValueError("relation_scope_required_for_consolidation")
        relation = getter(str(relation_id)) if callable(getter) else None
        if not relation:
            raise ValueError("relation_scope_required_for_consolidation")
        status = str(relation.get("status") or "")
        consent = str(relation.get("consent_status") or "")
        from core.db.relations import is_relation_eligible

        if not is_relation_eligible(relation):
            raise ValueError(
                f"relation_not_eligible_for_consolidation:status={status},consent={consent}"
            )
        return relation_id

    def consolidate_user_memories(
        self, user_id: str, lookback_days: int = 90, relation_id=None
    ):
        """
        Consolida memórias de um usuário nos últimos N dias

        Args:
            user_id: ID do usuário
            lookback_days: Período de lookback (default: 90 dias)
        """
        logger.info(f"📦 Iniciando consolidação de memórias para user_id={user_id} (lookback={lookback_days} dias)")

        # 0. Elegibilidade primeiro: sem Relation active+granted nada e lido,
        #    resumido ou reescrito (a checagem acontece antes de abrir o cursor).
        relation_id = self._resolve_relation_scope(user_id, relation_id)

        # 1. Buscar todas as memórias do período
        start_date = datetime.now() - timedelta(days=lookback_days)

        cursor = self.db.conn.cursor()
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(conversations)")}
        scope_sql = ""
        scope_params = []
        if relation_id and "relation_id" in columns:
            scope_sql = " AND relation_id = ?"
            scope_params.append(str(relation_id))
        elif callable(getattr(self.db, "resolve_relation_id", None)) and "relation_id" in columns:
            scope_sql = " AND relation_id IS NULL"
        instance = getattr(self.db, "agent_instance", None)
        if instance and "agent_instance" in columns:
            scope_sql += " AND agent_instance = ?"
            scope_params.append(str(instance))
        cursor.execute(f"""
            SELECT id, user_input, ai_response, timestamp, keywords,
                   tension_level, affective_charge, existential_depth
            FROM conversations
            WHERE user_id = ?
            AND timestamp >= ?{scope_sql}
            ORDER BY timestamp ASC
        """, (user_id, start_date.isoformat(), *scope_params))

        memories = [dict(row) for row in cursor.fetchall()]

        if len(memories) < 5:
            logger.info(f"   Menos de 5 memórias encontradas ({len(memories)}), consolidação não necessária")
            return

        logger.info(f"   Encontradas {len(memories)} memórias para consolidar")

        # 2. Idempotencia e controle de custo: "entrada nova" = conversa da
        #    janela que ainda nao foi consolidada (comparacao por id). Isso
        #    cobre os dois casos de borda sem re-pagar LLM nem perder dados:
        #    saida da janela rolante nao e entrada nova (nao reprocessa), e
        #    entrada retroativa com data anterior a mais recente e entrada
        #    nova sim (reprocessa) — contagem/data maxima iguais nao escondem
        #    trocas de membros na janela.
        window_ids = [str(m["id"]) for m in memories]
        window_max_ts = max((m.get("timestamp") or "") for m in memories)
        window_count = len(memories)
        progress = self._load_progress(user_id, relation_id)
        known_ids = set(progress.get("last_ids") or ()) if progress else set()
        new_ids = [i for i in window_ids if i not in known_ids]
        unchanged = progress is not None and not new_ids

        if unchanged:
            logger.info(
                "   Sem entradas novas desde %s (max_ts=%s): pulando resumos LLM (idempotente)",
                progress.get("last_run_at"),
                progress.get("last_max_ts"),
            )
        else:
            # 3. Agrupar por tópico usando keywords
            clusters = self._cluster_by_topic(memories)

            logger.info(f"   Identificados {len(clusters)} clusters temáticos")

            # 4. Para cada cluster grande (≥5 memórias), gerar resumo
            llm_failures = 0
            for topic, cluster_memories in clusters.items():
                if len(cluster_memories) >= 5:
                    logger.info(f"   Consolidando cluster '{topic}' ({len(cluster_memories)} memórias)")
                    try:
                        self._create_consolidated_memory(
                            user_id=user_id,
                            topic=topic,
                            memories=cluster_memories,
                            lookback_days=lookback_days,
                            relation_id=relation_id,
                        )
                    except LLMSummaryError as e:
                        llm_failures += 1
                        logger.warning(f"   Resumo de '{topic}' falhou ({e}); seguindo para os demais clusters")

            if llm_failures:
                # Falha do LLM nao e sucesso: sem marca de progresso, a
                # proxima execucao refaz o tema (o armazenamento e upsert por
                # periodo, entao o retry nao duplica padroes).
                logger.warning(
                    "   %d resumo(s) de LLM falharam; marca de progresso NAO salva — proxima consolidacao refaz",
                    llm_failures,
                )
            else:
                self._save_progress(user_id, relation_id, window_max_ts, window_count, window_ids)

        # 5. Reconstruir profile.md com dados atualizados
        try:
            from engines.participant_files import relation_file_scope
            from user_profile_writer import rebuild_profile_md
            file_instance, file_relation = relation_file_scope(self.db, user_id, relation_id)
            facts = self.db._get_current_facts(user_id, relation_id=file_relation)
            psychometrics = self.db.get_psychometrics(
                user_id, relation_id=file_relation, agent_instance=file_instance
            )
            patterns = self.db._get_relevant_patterns(user_id, "", relation_id=file_relation)
            user_row = self.db.conn.execute(
                "SELECT user_name FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            user_name = user_row[0] if user_row else user_id
            rebuild_profile_md(
                user_id=user_id,
                user_name=user_name,
                facts=facts,
                psychometrics=psychometrics,
                patterns=patterns,
                agent_instance=file_instance,
                relation_id=file_relation,
            )
        except Exception as e:
            logger.warning(f"⚠️ Erro ao reconstruir profile.md para {user_id}: {e}")

    @staticmethod
    def _progress_table(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consolidation_progress (
                user_id TEXT NOT NULL,
                relation_id TEXT NOT NULL,
                last_max_ts TEXT,
                last_count INTEGER NOT NULL DEFAULT 0,
                last_run_at TEXT NOT NULL,
                last_ids_json TEXT,
                PRIMARY KEY (user_id, relation_id)
            )
            """
        )
        conn.commit()

    def _load_progress(self, user_id: str, relation_id) -> Optional[Dict[str, Any]]:
        try:
            conn = self.db.conn
            self._progress_table(conn)
            row = conn.execute(
                "SELECT last_max_ts, last_count, last_run_at, last_ids_json"
                " FROM consolidation_progress WHERE user_id = ? AND relation_id = ?",
                (str(user_id), str(relation_id)),
            ).fetchone()
        except Exception as e:
            logger.warning(f"⚠️ Erro ao ler marca de progresso de {user_id}: {e}")
            return None
        if not row:
            return None
        try:
            last_ids = set(json.loads(row[3] or "[]"))
        except Exception:
            last_ids = set()
        return {
            "last_max_ts": row[0],
            "last_count": row[1],
            "last_run_at": row[2],
            "last_ids": last_ids,
        }

    def _save_progress(
        self, user_id: str, relation_id, max_ts, count: int, ids: Optional[List[Any]] = None
    ) -> None:
        try:
            conn = self.db.conn
            self._progress_table(conn)
            conn.execute(
                """
                INSERT INTO consolidation_progress
                    (user_id, relation_id, last_max_ts, last_count, last_run_at, last_ids_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, relation_id) DO UPDATE SET
                    last_max_ts = excluded.last_max_ts,
                    last_count = excluded.last_count,
                    last_run_at = excluded.last_run_at,
                    last_ids_json = excluded.last_ids_json
                """,
                (
                    str(user_id),
                    str(relation_id),
                    str(max_ts or ""),
                    int(count),
                    datetime.now().isoformat(),
                    json.dumps([str(i) for i in (ids or [])], ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"⚠️ Erro ao salvar marca de progresso de {user_id}: {e}")

    def _cluster_by_topic(self, memories: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Agrupa memórias por tópico baseado em keywords

        Args:
            memories: Lista de memórias

        Returns:
            Dict {topic: [memórias]}
        """
        clusters = {}

        for memory in memories:
            keywords = memory.get('keywords', '').split(',')

            # Detectar tópico principal
            topic = self._identify_main_topic(keywords)

            if topic not in clusters:
                clusters[topic] = []

            clusters[topic].append(memory)

        return clusters

    def _identify_main_topic(self, keywords: List[str]) -> str:
        """
        Identifica tópico principal baseado em keywords

        Args:
            keywords: Lista de keywords

        Returns:
            Nome do tópico
        """
        if not keywords or not keywords[0]:
            return "geral"

        keywords_lower = [k.lower().strip() for k in keywords if k]

        topic_mapping = {
            "trabalho": ["trabalho", "emprego", "empresa", "carreira", "chefe", "colega"],
            "família": ["esposa", "marido", "filho", "filha", "pai", "mae", "familia"],
            "saúde": ["saude", "doença", "ansiedade", "depressao", "insonia", "terapia"],
            "relacionamento": ["amigo", "namoro", "amor", "relacionamento"],
            "lazer": ["viagem", "hobby", "leitura"],
            "dinheiro": ["dinheiro", "financeiro", "salario", "conta", "divida"],
        }

        for topic, topic_keywords in topic_mapping.items():
            if any(kw in " ".join(keywords_lower) for kw in topic_keywords):
                return topic

        return "geral"

    def _create_consolidated_memory(
        self,
        user_id: str,
        topic: str,
        memories: List[Dict],
        lookback_days: int,
        relation_id=None,
    ):
        """
        Cria memória consolidada e salva no ChromaDB

        Args:
            user_id: ID do usuário
            topic: Tópico do cluster
            memories: Memórias do cluster
            lookback_days: Período de lookback
        """
        # Gerar resumo com LLM
        summary = self._generate_summary_with_llm(topic, memories)

        # IDs das conversas originais
        source_ids = [mem['id'] for mem in memories]

        # Calcular métricas agregadas
        avg_tension = sum(m.get('tension_level', 0) for m in memories) / len(memories)
        avg_affective = sum(m.get('affective_charge', 0) for m in memories) / len(memories)
        avg_depth = sum(m.get('existential_depth', 0) for m in memories) / len(memories)

        # Período da consolidação
        timestamps = [datetime.fromisoformat(m['timestamp']) for m in memories]
        period_start = min(timestamps).strftime("%Y-%m-%d")
        period_end = max(timestamps).strftime("%Y-%m-%d")

        # Construir documento consolidado
        doc_content = f"""
=== MEMÓRIA CONSOLIDADA ===
TÓPICO: {topic.upper()}
PERÍODO: {period_start} a {period_end} ({len(memories)} conversas)

{summary}

MÉTRICAS DO PERÍODO:
- Tensão média: {avg_tension:.2f}
- Carga afetiva média: {avg_affective:.2f}
- Profundidade média: {avg_depth:.2f}
"""

        # Metadata
        metadata = {
            "user_id": user_id,
            "user_name": "",  # Will be populated from first memory
            "type": "consolidated",
            "topic": topic,
            "period_start": period_start,
            "period_end": period_end,
            "count": len(memories),
            "source_ids": json.dumps(source_ids),
            "avg_tension": round(avg_tension, 2),
            "avg_affective": round(avg_affective, 2),
            "avg_depth": round(avg_depth, 2),
            "timestamp": datetime.now().isoformat(),
            "recency_tier": "consolidated",  # Tier especial
            "emotional_intensity": round(avg_affective + avg_tension, 2),
            "has_conflicts": False,
            "keywords": topic,
            "topics": topic,
        }

        pattern_name = f"consolidado_{topic}_{period_end}"
        payload = {
            "topic": topic,
            "period_start": period_start,
            "period_end": period_end,
            "source_ids": source_ids,
            "summary": summary,
            "metrics": metadata,
        }
        description = doc_content.strip()

        with self.db._lock:
            cursor = self.db.conn.cursor()
            pattern_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(user_patterns)")
            }
            instance = getattr(self.db, "agent_instance", None)
            scope_sql = ""
            scope_params = []
            if relation_id and "relation_id" in pattern_columns:
                scope_sql = " AND relation_id = ?"
                scope_params.append(str(relation_id))
            elif callable(getattr(self.db, "resolve_relation_id", None)) and "relation_id" in pattern_columns:
                scope_sql = " AND relation_id IS NULL"
            if instance and "agent_instance" in pattern_columns:
                scope_sql += " AND agent_instance = ?"
                scope_params.append(str(instance))
            cursor.execute(
                f"""
                SELECT id FROM user_patterns
                WHERE user_id = ? AND pattern_name = ?{scope_sql}
                """,
                (user_id, pattern_name, *scope_params),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE user_patterns
                    SET pattern_description = ?,
                        frequency_count = ?,
                        last_occurrence_at = CURRENT_TIMESTAMP,
                        supporting_conversation_ids = ?,
                        confidence_score = ?
                    WHERE id = ?
                    """,
                    (
                        description,
                        len(memories),
                        json.dumps(payload, ensure_ascii=False),
                        min(1.0, len(memories) * 0.08),
                        existing["id"],
                    ),
                )
            else:
                insert_columns = [
                    "user_id",
                    "pattern_type",
                    "pattern_name",
                    "pattern_description",
                    "frequency_count",
                    "supporting_conversation_ids",
                    "confidence_score",
                ]
                insert_values = [
                    user_id,
                    "CONSOLIDATED_MEMORY",
                    pattern_name,
                    description,
                    len(memories),
                    json.dumps(payload, ensure_ascii=False),
                    min(1.0, len(memories) * 0.08),
                ]
                if "relation_id" in pattern_columns:
                    insert_columns.append("relation_id")
                    insert_values.append(str(relation_id) if relation_id else None)
                if "agent_instance" in pattern_columns:
                    insert_columns.append("agent_instance")
                    insert_values.append(str(instance) if instance else None)
                placeholders = ", ".join("?" for _ in insert_columns)
                cursor.execute(
                    f"""
                    INSERT INTO user_patterns ({', '.join(insert_columns)})
                    VALUES ({placeholders})
                    """,
                    tuple(insert_values),
                )
            self.db.conn.commit()
        logger.info("Memoria consolidada salva em SQLite: %s", pattern_name)

    def _generate_summary_with_llm(self, topic: str, memories: List[Dict]) -> str:
        """
        Gera resumo temático das memórias usando LLM

        Args:
            topic: Tópico do cluster
            memories: Lista de memórias

        Returns:
            Resumo gerado
        """
        # Construir prompt com as memórias
        memories_text = "\n\n".join([
            f"[{mem['timestamp'][:10]}] Usuário: {mem['user_input'][:200]}\nJung: {mem['ai_response'][:200]}"
            for mem in memories[:10]  # Limitar a 10 para não estourar tokens
        ])

        prompt = f"""Você é um sistema de consolidação de memórias do Jung.

Analise as {len(memories)} conversas abaixo sobre o tema "{topic}" e gere um RESUMO CONSOLIDADO estruturado:

CONVERSAS:
{memories_text}

Gere um resumo seguindo este formato:

FATOS CONSOLIDADOS:
- [Liste 3-5 fatos principais mencionados repetidamente]

PADRÕES EMOCIONAIS:
- [Descreva padrões emocionais recorrentes, gatilhos, sentimentos]

EVOLUÇÃO:
- [Descreva como o tema evoluiu ao longo do período, se houve mudanças]

Seja conciso mas informativo. Máximo 200 palavras."""

        if not self.db.anthropic_client:
            # Fallback desenhado: sem LLM configurado, resumo manual basico.
            # (Ausencia de cliente nao e falha — nao ha o que recuperar.)
            return f"Consolidação de {len(memories)} conversas sobre {topic}."

        try:
            # Usar Claude Sonnet 4.5 (único provider)
            # max_tokens generoso: o client interno (AnthropicCompatWrapper
            # sobre OpenRouter/glm-5) e um modelo de raciocinio — orcamento
            # curto e consumido pelo "pensamento" e o content final volta
            # None, falhando o resumo sem motivo de conteudo.
            response = self.db.anthropic_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            summary = ""
            for block in getattr(response, "content", None) or []:
                text = getattr(block, "text", None)
                if isinstance(text, str) and text.strip():
                    summary = text.strip()
                    break
            if not summary:
                # Resposta vazia (content None, so reasoning, ou sem bloco de
                # texto) e FALHA, nunca resumo generico gravado como sucesso:
                # sem marca de progresso, a proxima consolidacao refaz.
                raise LLMSummaryError(
                    "llm_summary_failed: resposta vazia do LLM "
                    "(content=None ou sem bloco de texto)"
                )
            return summary
        except LLMSummaryError:
            raise
        except Exception as e:
            # Falha do LLM NAO vira resumo generico gravado como sucesso:
            # propaga para o ciclo nao salvar a marca de progresso, permitindo
            # que a proxima execucao refaca o resumo real.
            logger.error(f"Erro ao gerar resumo com LLM: {e}")
            raise LLMSummaryError(f"llm_summary_failed: {e}") from e


def run_consolidation_job(db_manager):
    """
    Job para rodar consolidação em todos os usuários (síncrono)

    Args:
        db_manager: HybridDatabaseManager instance
    """
    logger.info("🔄 Iniciando job de consolidação de memórias")

    # Fail-closed do gate de consentimento: sem o resolvedor de Relations
    # nenhuma elegibilidade pode ser avaliada — o job inteiro e recusado,
    # em vez de consolidar so por usuario.
    if not callable(getattr(db_manager, "resolve_relation_id", None)) or not callable(
        getattr(db_manager, "get_agent_relation", None)
    ):
        logger.error(
            "   Consolidação RECUSADA: resolvedor de Relations indisponível "
            "(gate de consentimento fail-closed)"
        )
        return

    consolidator = MemoryConsolidator(db_manager)

    # Buscar todos os usuários
    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM conversations")
    user_ids = [row[0] for row in cursor.fetchall()]

    logger.info(f"   Consolidando memórias para {len(user_ids)} usuários")

    for user_id in user_ids:
        try:
            consolidator.consolidate_user_memories(user_id, lookback_days=90)
        except ValueError as e:
            # Sem Relation elegivel (ausente, paused/revoked, consentimento
            # pendente/revogado) o pulo e esperado (C12g), nao um erro que
            # interrompe o job ou polui o log.
            if "consent_gate_unavailable_for_consolidation" in str(e):
                logger.error("   Consolidação recusada para %s: gate de consentimento indisponível", user_id)
            elif (
                "relation_scope_required_for_consolidation" in str(e)
                or "relation_not_eligible_for_consolidation" in str(e)
            ):
                logger.info("   Pulando %s: %s", user_id, e)
            else:
                logger.error(f"Erro ao consolidar memórias de {user_id}: {e}")
        except Exception as e:
            logger.error(f"Erro ao consolidar memórias de {user_id}: {e}")

    logger.info("✅ Job de consolidação concluído")


async def run_consolidation_job_async(db_manager):
    """
    Versão assíncrona do job de consolidação (para APScheduler AsyncIO)

    Args:
        db_manager: HybridDatabaseManager instance
    """
    import asyncio
    # Executar a versão síncrona em thread separada para não bloquear event loop
    await asyncio.to_thread(run_consolidation_job, db_manager)


async def memory_consolidation_scheduler(
    db_manager,
    interval_seconds: float = 86400,
    initial_delay: float = 600,
):
    """Agenda a consolidacao de memorias (que gera/reconstrói o profile.md).

    A promessa do bot ("/meu_perfil") e de que o perfil e gerado apos a
    consolidacao de memorias — este scheduler torna isso verdade: roda o job
    uma vez por dia, sem depender de trigger manual do painel admin.

    Args:
        db_manager: HybridDatabaseManager instance
        interval_seconds: intervalo entre consolidacoes (padrao: 24h)
        initial_delay: espera antes do primeiro ciclo (padrao: 10 min apos boot)
    """
    if initial_delay > 0:
        logger.info(
            "⏳ [MEM CONSOLIDATION] Aguardando %.0f s antes da primeira consolidacao...",
            initial_delay,
        )
        await asyncio.sleep(initial_delay)

    while True:
        try:
            await run_consolidation_job_async(db_manager)
            logger.info("🧠 [MEM CONSOLIDATION] Consolidação de memórias concluída.")
        except Exception as e:
            logger.error(f"❌ Erro no scheduler de Consolidação de Memórias: {e}")
        await asyncio.sleep(interval_seconds)
