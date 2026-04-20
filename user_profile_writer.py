"""
user_profile_writer.py - Camada de memória textual do JungAgent

Mantém dois níveis de memória em arquivos .md:
  data/users/{user_id}/sessions/YYYY-MM-DD.md  ← log bruto do dia (append-only)
  data/users/{user_id}/profile.md              ← perfil psicológico consolidado
"""

import os
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(".", "data", "users")  # mesmo base que jung_core.py usa (./data → /data no Railway)
AGENT_DIR = os.path.join(".", "data", "agent")  # diretório do perfil do agente


def _user_dir(user_id: str) -> str:
    path = os.path.join(DATA_DIR, user_id)
    os.makedirs(os.path.join(path, "sessions"), exist_ok=True)
    return path


def write_session_entry(
    user_id: str,
    user_name: str,
    user_input: str,
    ai_response: str,
    metadata: Optional[Dict] = None,
    tag: str = "",
) -> None:
    """
    Appenda uma entrada de conversa no log diário do usuário.
    tag: string opcional ex. '[FLUSH]' para marcar entradas de flush de contexto.
    """
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        session_path = os.path.join(_user_dir(user_id), "sessions", f"{today}.md")

        meta = metadata or {}
        tension = meta.get("tension_level", 0.0)
        charge = meta.get("affective_charge", 0.0)
        ts = datetime.now().strftime("%H:%M")

        tag_str = f" {tag}" if tag else ""
        entry = (
            f"\n## {ts}{tag_str}\n"
            f"**Usuário:** {user_input}\n\n"
            f"**Jung:** {ai_response}\n\n"
            f"_tensão={tension:.1f} | carga_afetiva={charge:.1f}_\n"
        )

        is_new_file = not os.path.exists(session_path)
        with open(session_path, "a", encoding="utf-8") as f:
            if is_new_file:
                f.write(f"# Sessão {today} — {user_name}\n")
                logger.info(f"📄 [SESSION LOG] Novo arquivo criado: sessions/{today}.md (user={user_id[:8]})")
            f.write(entry)
        logger.debug(f"📄 [SESSION LOG] Entrada gravada em sessions/{today}.md (tag='{tag or '-'}')")

    except Exception as e:
        logger.warning(f"⚠️ user_profile_writer: erro ao gravar sessão de {user_id}: {e}")


def rebuild_profile_md(
    user_id: str,
    user_name: str,
    facts: List[Dict],
    psychometrics: Optional[Dict] = None,
    patterns: Optional[List[Dict]] = None,
) -> None:
    """
    Reescreve o profile.md do usuário com todos os dados atuais.
    Chamado após cada consolidação de memória.
    """
    try:
        profile_path = os.path.join(_user_dir(user_id), "profile.md")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [f"# Perfil: {user_name} — atualizado em {now}\n"]

        # ── Fatos pessoais ──────────────────────────────────────────────
        pessoais = [f for f in facts if f.get("category") == "RELACIONAMENTO"]
        if pessoais:
            lines.append("\n## Fatos Pessoais\n")
            for f in pessoais:
                lines.append(
                    f"- {f['fact_type']} / {f['attribute']}: {f['fact_value']}\n"
                )

        # ── Vida profissional ────────────────────────────────────────────
        trabalho = [f for f in facts if f.get("category") == "TRABALHO"]
        if trabalho:
            lines.append("\n## Vida Profissional\n")
            for f in trabalho:
                lines.append(
                    f"- {f['fact_type']} / {f['attribute']}: {f['fact_value']}\n"
                )

        # ── Padrões comportamentais ──────────────────────────────────────
        if patterns:
            lines.append("\n## Padrões Comportamentais\n")
            for p in patterns:
                name = p.get("pattern_name", "")
                desc = p.get("pattern_description", "")
                freq = p.get("frequency_count", 1)
                lines.append(f"- **{name}** (freq={freq}): {desc}\n")

        # ── Psicometria ──────────────────────────────────────────────────
        if psychometrics:
            lines.append("\n## Psicometria\n")
            big5 = (
                f"Abertura={psychometrics.get('openness_score', '?')} | "
                f"Conscienciosidade={psychometrics.get('conscientiousness_score', '?')} | "
                f"Extroversão={psychometrics.get('extraversion_score', '?')} | "
                f"Amabilidade={psychometrics.get('agreeableness_score', '?')} | "
                f"Neuroticismo={psychometrics.get('neuroticism_score', '?')}"
            )
            lines.append(f"**Big Five:** {big5}\n\n")

            eq_overall = psychometrics.get("eq_overall")
            if eq_overall:
                lines.append(f"**Inteligência Emocional (geral):** {eq_overall}/100\n\n")

            schwartz = psychometrics.get("schwartz_top_3")
            if schwartz:
                lines.append(f"**Valores (Schwartz top 3):** {schwartz}\n\n")

            summary = psychometrics.get("executive_summary")
            if summary:
                lines.append(f"### Resumo Executivo\n{summary}\n")

        with open(profile_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        logger.info(f"✅ profile.md atualizado para {user_id} ({len(facts)} fatos)")

    except Exception as e:
        logger.warning(f"⚠️ user_profile_writer: erro ao reconstruir profile.md de {user_id}: {e}")


def rebuild_agent_profile_md(db_connection) -> None:
    """
    Gera/atualiza data/agent/self_profile.md com o estado atual da identidade do agente.
    Lê as 7 tabelas de identidade do SQLite e formata como markdown legível.
    Chamado após cada ciclo de consolidação de identidade (a cada 6h).

    db_connection: instância de HybridDatabaseManager (ou qualquer objeto com .conn)
    """
    try:
        os.makedirs(AGENT_DIR, exist_ok=True)
        profile_path = os.path.join(AGENT_DIR, "self_profile.md")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor = db_connection.conn.cursor()

        lines = [f"# Perfil de Identidade: Jung — atualizado em {now}\n\n"]
        has_content = False

        # ── Crenças Nucleares ────────────────────────────────────────────
        try:
            cursor.execute("""
                SELECT attribute_type, content, certainty
                FROM agent_identity_core
                WHERE is_current = 1
                ORDER BY certainty DESC
                LIMIT 8
            """)
            nuclear = cursor.fetchall()
            if nuclear:
                has_content = True
                lines.append("## Crenças Nucleares\n")
                for row in nuclear:
                    attr_type, content, certainty = row[0], row[1], row[2] or 0.0
                    lines.append(f"- **[{attr_type}]** {content} _(certeza: {certainty:.2f})_\n")
                lines.append("\n")
                logger.debug(f"[AGENT PROFILE] {len(nuclear)} crenças nucleares incluídas")
            else:
                lines.append("## Crenças Nucleares\n_Aguardando consolidação (nenhuma ainda registrada)_\n\n")
                logger.info("⚠️ [AGENT PROFILE] Nenhuma crença nuclear disponível — seção vazia")
        except Exception as e:
            logger.warning(f"⚠️ [AGENT PROFILE] Erro ao ler crenças nucleares: {e}")

        # ── Contradições Internas ────────────────────────────────────────
        try:
            cursor.execute("""
                SELECT pole_a, pole_b, contradiction_type, tension_level
                FROM agent_identity_contradictions
                WHERE status IN ('unresolved', 'integrating')
                ORDER BY tension_level DESC
                LIMIT 5
            """)
            contradictions = cursor.fetchall()
            if contradictions:
                has_content = True
                lines.append("## Contradições Internas Ativas\n")
                for row in contradictions:
                    pole_a, pole_b, c_type, tension = row[0], row[1], row[2] or '?', row[3] or 0.0
                    lines.append(f"- **[{c_type}]** {pole_a} ↔ {pole_b} _(tensão: {tension:.2f})_\n")
                lines.append("\n")
        except Exception as e:
            logger.warning(f"⚠️ [AGENT PROFILE] Erro ao ler contradições: {e}")

        # ── Capítulo Narrativo Atual ─────────────────────────────────────
        try:
            cursor.execute("""
                SELECT chapter_name, dominant_theme, emotional_tone, dominant_locus, agency_level
                FROM agent_narrative_chapters
                WHERE period_end IS NULL
                ORDER BY chapter_order DESC
                LIMIT 1
            """)
            chapter = cursor.fetchone()
            if chapter:
                has_content = True
                name, theme, tone, locus, agency = chapter
                lines.append(f"## Capítulo Narrativo Atual: {name or 'Em desenvolvimento'}\n")
                lines.append(f"Tema: _{theme or '—'}_ | Tom: _{tone or '—'}_ | Locus: _{locus or '—'}_ | Agência: _{agency or '—'}_\n\n")
        except Exception as e:
            logger.warning(f"⚠️ [AGENT PROFILE] Erro ao ler capítulo narrativo: {e}")

        # ── Selves Possíveis ─────────────────────────────────────────────
        try:
            cursor.execute("""
                SELECT self_type, description, vividness
                FROM agent_possible_selves
                WHERE status = 'active'
                ORDER BY vividness DESC
                LIMIT 4
            """)
            selves = cursor.fetchall()
            if selves:
                has_content = True
                lines.append("## Selves Possíveis\n")
                for row in selves:
                    s_type, desc, vividness = row[0], row[1], row[2] or 0.0
                    lines.append(f"- **[{s_type}]** {desc} _(vivacidade: {vividness:.2f})_\n")
                lines.append("\n")
        except Exception as e:
            logger.warning(f"⚠️ [AGENT PROFILE] Erro ao ler selves possíveis: {e}")

        # ── Métricas de Desenvolvimento ──────────────────────────────────
        try:
            from instance_config import ADMIN_USER_ID
            cursor.execute("""
                SELECT phase, total_interactions,
                       self_awareness_score, moral_complexity_score,
                       emotional_depth_score, autonomy_score
                FROM agent_development
                WHERE user_id = ?
            """, (ADMIN_USER_ID,))
            dev = cursor.fetchone()
            if dev:
                has_content = True
                phase, total, sa, mc, ed, au = dev
                lines.append("## Fase de Desenvolvimento\n")
                lines.append(
                    f"Fase **{phase}** | Interações: {total}\n"
                    f"Autoconsciência: {sa:.3f} | Complexidade moral: {mc:.3f} | "
                    f"Profundidade emocional: {ed:.3f} | Autonomia: {au:.3f}\n\n"
                )
        except Exception as e:
            logger.warning(f"⚠️ [AGENT PROFILE] Erro ao ler métricas de desenvolvimento: {e}")

        with open(profile_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        char_count = sum(len(l) for l in lines)
        if has_content:
            logger.info(f"📄 [AGENT PROFILE] Arquivo gerado: data/agent/self_profile.md ({char_count} chars)")
        else:
            logger.info(
                "📄 [AGENT PROFILE] Perfil mínimo gerado (tabelas de identidade ainda vazias — "
                "aguardando consolidação)"
            )

    except Exception as e:
        logger.warning(f"⚠️ user_profile_writer: erro ao gerar self_profile.md do agente: {e}")
