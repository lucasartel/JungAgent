"""Jung Mind handlers for legacy research lab routes."""
import json

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import get_db, internal_error_response, logger, templates

async def jung_mind_page(request, admin=None):
    """
    Página do mapa mental Jung Mind
    Visualização hierárquica: Jung (centro) → Fragmentos → Tensões → Insights
    Com sinapses (conexões laterais) entre elementos com símbolos/temas comuns
    """
    return templates.TemplateResponse("jung_mind.html", {
        "request": request,
        "active_nav": "rumination",
    })

async def jung_mind_data(admin=None):
    """
    API que retorna dados do jung-lab formatados para Vis.js Network

    Retorna:
        {
            "nodes": [
                {"id": "jung", "label": "Jung", "type": "center", ...},
                {"id": "frag_1", "label": "...", "type": "fragment", "category": "valor", ...},
                {"id": "tension_1", "label": "...", "type": "tension", ...},
                {"id": "insight_1", "label": "...", "type": "insight", ...}
            ],
            "edges": [
                {"from": "jung", "to": "frag_1", "type": "hierarchy"},
                {"from": "frag_1", "to": "tension_1", "type": "hierarchy"},
                {"from": "tension_1", "to": "insight_1", "type": "hierarchy"},
                {"from": "frag_1", "to": "frag_5", "type": "synapse", "reason": "tema: trabalho"}
            ]
        }
    """
    try:
        # Importar ADMIN_USER_ID
        try:
            from instance_config import ADMIN_USER_ID
        except ImportError:
            logger.error("❌ Não foi possível importar ADMIN_USER_ID de rumination_config")
            raise HTTPException(500, "ADMIN_USER_ID não configurado para esta rota")

        db = get_db()
        conn = db.conn
        cursor = conn.cursor()

        # Verificar se tabelas existem
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('rumination_fragments', 'rumination_tensions', 'rumination_insights')
        """)
        existing_tables = [row[0] for row in cursor.fetchall()]

        if not existing_tables:
            logger.warning("⚠️ Nenhuma tabela de ruminação encontrada")
            return JSONResponse({
                "nodes": [{
                    "id": "jung",
                    "label": "JUNG",
                    "type": "center",
                    "title": "Sistema de Ruminação ainda não inicializado",
                    "level": 0,
                    "color": "#6b7280",
                    "shape": "star",
                    "size": 40
                }],
                "edges": [],
                "stats": {
                    "total_fragments": 0,
                    "total_tensions": 0,
                    "total_insights": 0,
                    "total_synapses": 0
                },
                "warning": "Tabelas de ruminação não encontradas. Sistema ainda não foi inicializado."
            })

        logger.info(f"✅ Tabelas encontradas: {existing_tables}")

        nodes = []
        edges = []

        # ===== NÓ CENTRAL: JUNG =====
        nodes.append({
            "id": "jung",
            "label": "JUNG",
            "type": "center",
            "title": "Sistema de Ruminação Cognitiva<br>Identidade do Agente",
            "level": 0,
            "color": "#a78bfa",
            "shape": "star",
            "size": 40
        })

        # ===== FRAGMENTOS =====
        cursor.execute("""
            SELECT id, fragment_type, content, emotional_weight, created_at, context
            FROM rumination_fragments
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 200
        """, (ADMIN_USER_ID,))

        fragments = cursor.fetchall()
        logger.info(f"📊 Fragmentos encontrados: {len(fragments)}")
        fragment_themes = {}  # Para detectar sinapses

        for frag in fragments:
            frag_id = f"frag_{frag[0]}"
            frag_type = frag[1]
            content = frag[2]
            weight = frag[3]
            created_at = frag[4]
            context = frag[5]

            # Extrair palavras-chave para sinapses (simplificado)
            keywords = set(word.lower() for word in content.split() if len(word) > 4)
            fragment_themes[frag_id] = keywords

            nodes.append({
                "id": frag_id,
                "label": content[:30] + "..." if len(content) > 30 else content,
                "type": "fragment",
                "category": frag_type,
                "title": f"<b>Fragmento ({frag_type})</b><br>" +
                         f"{content}<br><br>" +
                         f"Peso emocional: {weight:.2f}<br>" +
                         f"Criado: {created_at}",
                "level": 1,
                "color": {
                    "valor": "#10b981",
                    "crença": "#3b82f6",
                    "comportamento": "#f59e0b",
                    "desejo": "#ec4899",
                    "medo": "#ef4444"
                }.get(frag_type, "#6b7280"),
                "shape": "dot",
                "size": 10 + (weight * 15),
                "full_data": {
                    "type": frag_type,
                    "content": content,
                    "weight": weight,
                    "created_at": created_at,
                    "context": context
                }
            })

            # Conexão hierárquica: Jung → Fragmento
            edges.append({
                "from": "jung",
                "to": frag_id,
                "type": "hierarchy",
                "color": {"color": "#4b5563", "opacity": 0.3},
                "width": 1,
                "dashes": False
            })

        # ===== TENSÕES =====
        cursor.execute("""
            SELECT id, tension_type, pole_a_content, pole_b_content,
                   intensity, maturity_score, status, first_detected_at, last_evidence_at,
                   pole_a_fragment_ids, pole_b_fragment_ids
            FROM rumination_tensions
            WHERE user_id = ?
            ORDER BY maturity_score DESC, first_detected_at DESC
            LIMIT 100
        """, (ADMIN_USER_ID,))

        tensions = cursor.fetchall()
        logger.info(f"📊 Tensões encontradas: {len(tensions)}")
        tension_fragments = {}  # Mapear tensão → fragmentos relacionados

        for tension in tensions:
            tension_id = f"tension_{tension[0]}"
            t_type = tension[1]
            pole_a = tension[2]
            pole_b = tension[3]
            intensity = tension[4]
            maturity = tension[5]
            t_status = tension[6]
            first_detected_at = tension[7]
            last_evidence = tension[8]
            pole_a_fragment_ids = tension[9]
            pole_b_fragment_ids = tension[10]

            # Parse fragment IDs from JSON columns
            pole_a_ids = json.loads(pole_a_fragment_ids) if pole_a_fragment_ids else []
            pole_b_ids = json.loads(pole_b_fragment_ids) if pole_b_fragment_ids else []
            related_fragments = [f"frag_{fid}" for fid in (pole_a_ids + pole_b_ids)]
            tension_fragments[tension_id] = related_fragments

            nodes.append({
                "id": tension_id,
                "label": f"{t_type}\\n{intensity:.0%}",
                "type": "tension",
                "title": f"<b>Tensão: {t_type}</b><br><br>" +
                         f"Polo A: {pole_a}<br>" +
                         f"Polo B: {pole_b}<br><br>" +
                         f"Intensidade: {intensity:.0%}<br>" +
                         f"Maturidade: {maturity:.0%}<br>" +
                         f"Status: {t_status}<br>" +
                         f"Última evidência: {last_evidence}",
                "level": 2,
                "color": "#f59e0b" if t_status == "active" else "#6b7280",
                "shape": "diamond",
                "size": 15 + (maturity * 15),
                "full_data": {
                    "type": t_type,
                    "pole_a": pole_a,
                    "pole_b": pole_b,
                    "intensity": intensity,
                    "maturity": maturity,
                    "status": t_status,
                    "first_detected_at": first_detected_at
                }
            })

            # Conexões hierárquicas: Fragmentos → Tensão
            for frag_id in related_fragments:
                edges.append({
                    "from": frag_id,
                    "to": tension_id,
                    "type": "hierarchy",
                    "color": {"color": "#f59e0b", "opacity": 0.4},
                    "width": 2,
                    "dashes": False
                })

        # ===== INSIGHTS =====
        cursor.execute("""
            SELECT id, source_tension_id, symbol_content, question_content,
                   full_message, depth_score, status, crystallized_at
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY crystallized_at DESC
            LIMIT 50
        """, (ADMIN_USER_ID,))

        insights = cursor.fetchall()
        logger.info(f"📊 Insights encontrados: {len(insights)}")

        for insight in insights:
            insight_id = f"insight_{insight[0]}"
            source_tension_id = f"tension_{insight[1]}" if insight[1] else None
            symbol = insight[2] or ""
            question = insight[3] or ""
            thought = insight[4] or ""
            depth = insight[5] or 0.5
            i_status = insight[6]
            crystallized_at = insight[7]

            nodes.append({
                "id": insight_id,
                "label": symbol[:40] + "..." if len(symbol) > 40 else symbol,
                "type": "insight",
                "title": f"<b>Insight ({i_status})</b><br><br>" +
                         f"<i>{symbol}</i><br><br>" +
                         f"{thought[:200]}...<br><br>" +
                         f"Questão: {question}<br>" +
                         f"Profundidade: {depth:.0%}<br>" +
                         f"Cristalizado: {crystallized_at}",
                "level": 3,
                "color": "#ec4899" if i_status == "ready" else "#8b5cf6",
                "shape": "box",
                "size": 12 + (depth * 18),
                "full_data": {
                    "symbol": symbol,
                    "question": question,
                    "thought": thought,
                    "depth": depth,
                    "status": i_status,
                    "crystallized_at": crystallized_at
                }
            })

            # Conexão hierárquica: Tensão → Insight
            if source_tension_id:
                edges.append({
                    "from": source_tension_id,
                    "to": insight_id,
                    "type": "hierarchy",
                    "color": {"color": "#ec4899", "opacity": 0.5},
                    "width": 3,
                    "dashes": False
                })

        # ===== SINAPSES (CONEXÕES LATERAIS) =====
        # Conectar fragmentos com temas/palavras comuns
        fragment_ids = list(fragment_themes.keys())
        for i, frag_id_1 in enumerate(fragment_ids):
            for frag_id_2 in fragment_ids[i+1:]:
                # Calcular intersecção de keywords
                common = fragment_themes[frag_id_1] & fragment_themes[frag_id_2]
                if len(common) >= 2:  # Mínimo 2 palavras em comum
                    edges.append({
                        "from": frag_id_1,
                        "to": frag_id_2,
                        "type": "synapse",
                        "title": f"Temas comuns: {', '.join(list(common)[:3])}",
                        "color": {"color": "#8b5cf6", "opacity": 0.15},
                        "width": 1,
                        "dashes": True,
                        "smooth": {"type": "curvedCW", "roundness": 0.2}
                    })

        stats = {
            "total_fragments": len(fragments),
            "total_tensions": len(tensions),
            "total_insights": len(insights),
            "total_synapses": sum(1 for e in edges if e["type"] == "synapse")
        }

        logger.info(f"📊 TOTAIS: {len(nodes)} nós, {len(edges)} edges")
        logger.info(f"📊 STATS: {stats}")

        return JSONResponse({
            "nodes": nodes,
            "edges": edges,
            "stats": stats
        })

    except Exception as e:
        logger.error(f"❌ Erro ao gerar dados do jung-mind: {e}", exc_info=True)
        return internal_error_response("Erro ao gerar dados do mapa mental")
