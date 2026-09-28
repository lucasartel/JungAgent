"""
Rotas Admin para Sistema IRT/TRI (Item Response Theory)

Endpoints:
    - GET /admin/irt/dashboard: Dashboard TRI para Master Admin
    - GET /admin/irt/user/{user_id}: Perfil TRI de um usuário
    - GET /admin/irt/comparison/{user_id}: Comparação TRI vs Legacy
    - POST /admin/irt/migration/run: Executar migração TRI
    - GET /admin/irt/migration/status: Status da migração
    - GET /admin/irt/fragments/stats: Estatísticas de fragmentos

Autor: Sistema TRI JungAgent
Data: 2025-01-19
Versão: 1.0.0
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Optional, List
from datetime import datetime
import logging
import json

# Importar middleware de autenticação
# C12c3: leituras abrem para org_admin com visão restrita à própria org;
# operações de schema (migração/seed) seguem master-only.
from admin_web.auth.middleware import require_master, require_org_admin, verify_user_access
from admin_web.auth.org_scope import org_user_scope_clause

router = APIRouter(prefix="/admin/irt", tags=["irt"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

# DatabaseManager será injetado pelo init
_db_manager = None
_irt_engine = None
_fragment_detector = None


def init_irt_routes(db_manager):
    """Inicializa rotas IRT com DatabaseManager"""
    global _db_manager, _irt_engine, _fragment_detector

    _db_manager = db_manager

    # Tentar inicializar componentes TRI
    try:
        from fragment_detector import FragmentDetector
        _fragment_detector = FragmentDetector(db_connection=None)
        logger.info("✅ IRT Routes: FragmentDetector inicializado")
    except Exception as e:
        logger.warning(f"⚠️ IRT Routes: FragmentDetector não disponível: {e}")

    logger.info("✅ Rotas IRT inicializadas")


# ============================================================================
# DASHBOARD TRI
# ============================================================================

@router.get("/dashboard", response_class=HTMLResponse)
async def irt_dashboard(
    request: Request,
    admin: Dict = Depends(require_org_admin)
):
    """
    Dashboard principal do sistema TRI.

    Mostra:
    - Status da migração
    - Estatísticas gerais de fragmentos
    - Top usuários por fragmentos detectados
    - Distribuição por domínio Big Five
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        # C12c3: org_admin vê apenas as detecções dos usuários da própria org.
        # C12c3: escopos qualificados — queries com JOIN precisam do alias
        # (user_id ambíguo com users); tabelas únicas usam o escopo plano.
        scope_df, params_df = org_user_scope_clause(admin, table_alias="df")
        scope_sql, scope_params = org_user_scope_clause(admin)
        logger.info("🔍 [IRT Dashboard] Iniciando verificação de tabelas...")

        # 1. Verificar se TODAS as tabelas TRI existem
        required_tables = [
            "irt_fragments",
            "irt_item_parameters",
            "detected_fragments",
            "irt_trait_estimates",
            "facet_scores",
            "psychometric_quality_checks"
        ]

        existing_tables = []
        for table in required_tables:
            try:
                cursor.execute(f"""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='{table}'
                """)
                result = cursor.fetchone()
                if result:
                    existing_tables.append(table)
                    logger.info(f"   ✅ Tabela '{table}' existe")
                else:
                    logger.info(f"   ❌ Tabela '{table}' NÃO existe")
            except Exception as table_err:
                logger.error(f"   ❌ Erro ao verificar tabela '{table}': {table_err}")

        tri_tables_exist = len(existing_tables) == len(required_tables)
        logger.info(f"🔍 [IRT Dashboard] Tabelas: {len(existing_tables)}/{len(required_tables)} existem")
        logger.info(f"🔍 [IRT Dashboard] tri_tables_exist = {tri_tables_exist}")

        if not tri_tables_exist:
            # TRI não migrado ainda (ou migração incompleta)
            logger.info("🔍 [IRT Dashboard] Retornando página de migração pendente")
            missing = [t for t in required_tables if t not in existing_tables]
            logger.info(f"🔍 [IRT Dashboard] Tabelas faltando: {missing}")
            return templates.TemplateResponse(
                "irt/dashboard.html",
                {
                    "request": request,
                    "admin": admin,
                    "tri_status": "not_migrated",
                    "stats": None,
                    "existing_tables": existing_tables,
                    "missing_tables": missing
                }
            )

        logger.info("🔍 [IRT Dashboard] Todas as tabelas existem, coletando estatísticas...")

        # 2. Estatísticas gerais
        stats = {}

        # Total de fragmentos no seed
        logger.info("🔍 [IRT Dashboard] Query 1: COUNT irt_fragments")
        cursor.execute("SELECT COUNT(*) FROM irt_fragments")
        stats["total_fragments_seed"] = cursor.fetchone()[0]
        logger.info(f"   → total_fragments_seed = {stats['total_fragments_seed']}")

        # Total de detecções
        logger.info("🔍 [IRT Dashboard] Query 2: COUNT detected_fragments")
        cursor.execute(
            f"SELECT COUNT(*) FROM detected_fragments df WHERE 1=1{scope_df}", params_df
        )
        row = cursor.fetchone()
        stats["total_detections"] = row[0] if row else 0
        logger.info(f"   → total_detections = {stats['total_detections']}")

        # Usuários únicos com detecções
        logger.info("🔍 [IRT Dashboard] Query 3: COUNT DISTINCT user_id")
        cursor.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM detected_fragments df WHERE 1=1{scope_df}",
            params_df,
        )
        row = cursor.fetchone()
        stats["unique_users_with_detections"] = row[0] if row else 0
        logger.info(f"   → unique_users = {stats['unique_users_with_detections']}")

        # Distribuição por domínio
        logger.info("🔍 [IRT Dashboard] Query 4: by_domain")
        cursor.execute(f"""
            SELECT f.domain, COUNT(*) as count
            FROM detected_fragments df
            JOIN irt_fragments f ON df.fragment_id = f.fragment_id
            WHERE 1=1{scope_df}
            GROUP BY f.domain
            ORDER BY count DESC
        """, params_df)
        stats["by_domain"] = {row[0]: row[1] for row in cursor.fetchall()}
        logger.info(f"   → by_domain = {stats['by_domain']}")

        # Top 10 usuários por fragmentos
        # C12c3 (revisão 2): lista nominal de pessoas — só master. Org_admin
        # fica com os agregados sem nominalidade.
        stats["top_users"] = []
        if admin.get("role") == "master":
            logger.info("🔍 [IRT Dashboard] Query 5: top_users")
            cursor.execute(f"""
                SELECT
                    df.user_id,
                    u.user_name,
                    COUNT(*) as fragment_count,
                    AVG(df.intensity) as avg_intensity
                FROM detected_fragments df
                LEFT JOIN users u ON df.user_id = u.user_id
                WHERE 1=1{scope_df}
                GROUP BY df.user_id
                ORDER BY fragment_count DESC
                LIMIT 10
            """, params_df)
            for row in cursor.fetchall():
                stats["top_users"].append({
                    "user_id": row[0],
                    "user_name": row[1] or "Unknown",
                    "fragment_count": row[2],
                    "avg_intensity": round(row[3], 2) if row[3] else 0
                })
            logger.info(f"   → top_users count = {len(stats['top_users'])}")

        # Estimativas de traço salvas
        logger.info("🔍 [IRT Dashboard] Query 6: COUNT irt_trait_estimates")
        cursor.execute(
            f"SELECT COUNT(*) FROM irt_trait_estimates WHERE 1=1{scope_sql}", scope_params
        )
        row = cursor.fetchone()
        stats["total_trait_estimates"] = row[0] if row else 0
        logger.info(f"   → total_trait_estimates = {stats['total_trait_estimates']}")

        # Quality checks - verificar schema primeiro
        logger.info("🔍 [IRT Dashboard] Query 7: Verificando schema de psychometric_quality_checks")
        cursor.execute("PRAGMA table_info(psychometric_quality_checks)")
        columns = [col[1] for col in cursor.fetchall()]
        logger.info(f"   → Colunas existentes: {columns}")

        if 'passed' in columns:
            logger.info("🔍 [IRT Dashboard] Query 7a: COUNT passed=1")
            cursor.execute(
                f"SELECT COUNT(*) FROM psychometric_quality_checks WHERE passed = 1{scope_sql}",
                scope_params,
            )
            row = cursor.fetchone()
            stats["quality_checks_passed"] = row[0] if row else 0

            logger.info("🔍 [IRT Dashboard] Query 7b: COUNT passed=0")
            cursor.execute(
                f"SELECT COUNT(*) FROM psychometric_quality_checks WHERE passed = 0{scope_sql}",
                scope_params,
            )
            row = cursor.fetchone()
            stats["quality_checks_failed"] = row[0] if row else 0
        else:
            logger.warning("🔍 [IRT Dashboard] Coluna 'passed' não existe - usando defaults")
            stats["quality_checks_passed"] = 0
            stats["quality_checks_failed"] = 0

        logger.info(f"   → quality_checks_passed = {stats['quality_checks_passed']}")
        logger.info(f"   → quality_checks_failed = {stats.get('quality_checks_failed', 0)}")
        logger.info("🔍 [IRT Dashboard] Estatísticas coletadas com sucesso!")

        return templates.TemplateResponse(
            "irt/dashboard.html",
            {
                "request": request,
                "admin": admin,
                "tri_status": "active",
                "stats": stats
            }
        )

    except Exception as e:
        logger.error(f"Erro no dashboard TRI: {e}")
        raise HTTPException(500, f"Erro ao carregar dashboard: {str(e)}")


# ============================================================================
# PERFIL TRI DE USUÁRIO
# ============================================================================

@router.get("/user/{user_id}")
async def get_user_tri_profile(
    user_id: str,
    admin: Dict = Depends(require_master)
):
    """
    Retorna perfil TRI completo de um usuário.

    Inclui:
    - Estimativas de traço por domínio
    - Scores de facetas
    - Fragmentos detectados
    - Histórico de qualidade
    """
    # C12c3 (revisão 2): dado individual nominal exige master. O vínculo de
    # org não comprova autorização para perfil/escores/fragmentos, e o
    # recorte por user_id não distingue a origem (org/Relation/instância)
    # dos registros — superfície master-only até existir origem por registro.
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        # Verificar se usuário existe
        cursor.execute("SELECT user_name FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            raise HTTPException(404, "Usuário não encontrado")

        profile = {
            "user_id": user_id,
            "user_name": user_row[0],
            "trait_estimates": {},
            "facet_scores": {},
            "detected_fragments": [],
            "quality_checks": []
        }

        # 1. Estimativas de traço (domínios)
        cursor.execute("""
            SELECT domain, theta, standard_error, n_items, updated_at
            FROM irt_trait_estimates
            WHERE user_id = ?
        """, (user_id,))

        for row in cursor.fetchall():
            # Converter theta para score 0-100
            theta = row[1]
            score = max(0, min(100, int(50 + theta * 12.5)))

            # Classificar confiabilidade
            se = row[2]
            if se <= 0.5:
                reliability = "high"
            elif se <= 0.7:
                reliability = "acceptable"
            else:
                reliability = "low"

            profile["trait_estimates"][row[0]] = {
                "theta": round(theta, 3),
                "standard_error": round(se, 3),
                "score_0_100": score,
                "n_items": row[3],
                "reliability": reliability,
                "updated_at": row[4]
            }

        # 2. Scores de facetas
        cursor.execute("""
            SELECT facet_code, theta, standard_error, n_items, updated_at
            FROM facet_scores
            WHERE user_id = ?
        """, (user_id,))

        for row in cursor.fetchall():
            theta = row[1]
            score = max(0, min(100, int(50 + theta * 12.5)))

            profile["facet_scores"][row[0]] = {
                "theta": round(theta, 3),
                "standard_error": round(row[2], 3),
                "score_0_100": score,
                "n_items": row[3],
                "updated_at": row[4]
            }

        # 3. Fragmentos detectados (últimos 50)
        cursor.execute("""
            SELECT
                df.fragment_id,
                f.domain,
                f.facet_code,
                f.description,
                df.intensity,
                df.confidence,
                df.detected_at,
                df.detection_count
            FROM detected_fragments df
            JOIN irt_fragments f ON df.fragment_id = f.fragment_id
            WHERE df.user_id = ?
            ORDER BY df.detected_at DESC
            LIMIT 50
        """, (user_id,))

        for row in cursor.fetchall():
            profile["detected_fragments"].append({
                "fragment_id": row[0],
                "domain": row[1],
                "facet_code": row[2],
                "description": row[3],
                "intensity": row[4],
                "confidence": round(row[5], 2) if row[5] else 0,
                "detected_at": row[6],
                "detection_count": row[7]
            })

        # 4. Quality checks
        cursor.execute("""
            SELECT check_type, check_value, threshold, passed, checked_at, details
            FROM psychometric_quality_checks
            WHERE user_id = ?
            ORDER BY checked_at DESC
            LIMIT 20
        """, (user_id,))

        for row in cursor.fetchall():
            profile["quality_checks"].append({
                "check_type": row[0],
                "check_value": round(row[1], 3) if row[1] else 0,
                "threshold": round(row[2], 3) if row[2] else 0,
                "passed": bool(row[3]),
                "checked_at": row[4],
                "details": row[5]
            })

        return JSONResponse(content=profile)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter perfil TRI: {e}")
        raise HTTPException(500, f"Erro: {str(e)}")


# ============================================================================
# COMPARAÇÃO TRI vs LEGACY
# ============================================================================

@router.get("/comparison/{user_id}")
async def compare_tri_legacy(
    user_id: str,
    admin: Dict = Depends(require_master)
):
    """
    Compara scores TRI com scores do sistema legado (user_psychometrics).

    Útil para validação do sistema TRI.
    """
    # C12c3 (revisão 2): dado individual nominal — master-only (mesmo
    # limite de /user/{user_id}: origem dos registros indistinguível).
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        comparison = {
            "user_id": user_id,
            "domains": {},
            "summary": {
                "tri_available": False,
                "legacy_available": False,
                "correlation": None,
                "mean_difference": None
            }
        }

        # 1. Obter scores legados
        cursor.execute("""
            SELECT
                big_five_extraversion,
                big_five_openness,
                big_five_conscientiousness,
                big_five_agreeableness,
                big_five_neuroticism
            FROM user_psychometrics
            WHERE user_id = ?
        """, (user_id,))

        legacy_row = cursor.fetchone()
        legacy_scores = {}

        if legacy_row:
            comparison["summary"]["legacy_available"] = True
            legacy_scores = {
                "extraversion": legacy_row[0],
                "openness": legacy_row[1],
                "conscientiousness": legacy_row[2],
                "agreeableness": legacy_row[3],
                "neuroticism": legacy_row[4]
            }

        # 2. Obter scores TRI
        cursor.execute("""
            SELECT domain, theta, standard_error
            FROM irt_trait_estimates
            WHERE user_id = ?
        """, (user_id,))

        tri_rows = cursor.fetchall()
        tri_scores = {}

        if tri_rows:
            comparison["summary"]["tri_available"] = True
            for row in tri_rows:
                theta = row[1]
                score = max(0, min(100, int(50 + theta * 12.5)))
                tri_scores[row[0]] = {
                    "theta": theta,
                    "score": score,
                    "se": row[2]
                }

        # 3. Comparar domínios
        domains = ["extraversion", "openness", "conscientiousness", "agreeableness", "neuroticism"]
        differences = []

        for domain in domains:
            comparison["domains"][domain] = {
                "legacy_score": legacy_scores.get(domain),
                "tri_score": tri_scores.get(domain, {}).get("score"),
                "tri_theta": tri_scores.get(domain, {}).get("theta"),
                "tri_se": tri_scores.get(domain, {}).get("se"),
                "difference": None
            }

            legacy = legacy_scores.get(domain)
            tri = tri_scores.get(domain, {}).get("score")

            if legacy is not None and tri is not None:
                diff = tri - legacy
                comparison["domains"][domain]["difference"] = diff
                differences.append(diff)

        # 4. Calcular estatísticas de comparação
        if differences:
            comparison["summary"]["mean_difference"] = round(sum(differences) / len(differences), 2)

            # Correlação simples se temos todos os 5 domínios
            if len(differences) == 5:
                tri_vals = [tri_scores.get(d, {}).get("score", 0) for d in domains]
                legacy_vals = [legacy_scores.get(d, 0) for d in domains]

                # Calcular correlação de Pearson
                n = len(tri_vals)
                mean_tri = sum(tri_vals) / n
                mean_legacy = sum(legacy_vals) / n

                numerator = sum((t - mean_tri) * (l - mean_legacy) for t, l in zip(tri_vals, legacy_vals))
                denom_tri = sum((t - mean_tri) ** 2 for t in tri_vals) ** 0.5
                denom_legacy = sum((l - mean_legacy) ** 2 for l in legacy_vals) ** 0.5

                if denom_tri > 0 and denom_legacy > 0:
                    correlation = numerator / (denom_tri * denom_legacy)
                    comparison["summary"]["correlation"] = round(correlation, 3)

        return JSONResponse(content=comparison)

    except Exception as e:
        logger.error(f"Erro na comparação TRI/Legacy: {e}")
        raise HTTPException(500, f"Erro: {str(e)}")


# ============================================================================
# MIGRAÇÃO TRI
# ============================================================================

@router.post("/migration/run")
async def run_tri_migration(
    admin: Dict = Depends(require_master)
):
    """
    Executa a migração das tabelas TRI.

    ATENÇÃO: Esta operação cria novas tabelas no banco.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Importar e executar migração
        from migrations.irt_migration import run_migration

        # Executar migração
        success = run_migration(_db_manager.conn)

        if success:
            return JSONResponse(content={
                "status": "success",
                "message": "Migração TRI executada com sucesso",
                "timestamp": datetime.now().isoformat()
            })
        else:
            raise HTTPException(500, "Migração falhou. Verifique os logs.")

    except ImportError as e:
        raise HTTPException(500, f"Módulo de migração não encontrado: {e}")
    except Exception as e:
        logger.error(f"Erro na migração TRI: {e}")
        raise HTTPException(500, f"Erro na migração: {str(e)}")


@router.post("/seed/fragments")
async def seed_tri_fragments(
    admin: Dict = Depends(require_master)
):
    """
    Popula a tabela irt_fragments com os 150 fragmentos Big Five.

    Usar quando as tabelas existem mas estão vazias.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        logger.info("🌱 [IRT Seed] Iniciando seed de fragmentos...")

        # Garantir que o diretório raiz está no path
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
            logger.info(f"🌱 [IRT Seed] Adicionado ao path: {project_root}")

        # Importar fragmentos
        from irt_fragments_seed import (
            EXTRAVERSION_FRAGMENTS,
            OPENNESS_FRAGMENTS,
            CONSCIENTIOUSNESS_FRAGMENTS,
            AGREEABLENESS_FRAGMENTS,
            NEUROTICISM_FRAGMENTS
        )

        all_fragments = (
            EXTRAVERSION_FRAGMENTS +
            OPENNESS_FRAGMENTS +
            CONSCIENTIOUSNESS_FRAGMENTS +
            AGREEABLENESS_FRAGMENTS +
            NEUROTICISM_FRAGMENTS
        )

        logger.info(f"🌱 [IRT Seed] Total de fragmentos a inserir: {len(all_fragments)}")

        cursor = _db_manager.conn.cursor()

        # Verificar quantos já existem
        cursor.execute("SELECT COUNT(*) FROM irt_fragments")
        existing_count = cursor.fetchone()[0]
        logger.info(f"🌱 [IRT Seed] Fragmentos existentes: {existing_count}")

        if existing_count >= 150:
            return JSONResponse(content={
                "status": "skipped",
                "message": f"Já existem {existing_count} fragmentos. Seed não necessário.",
                "existing_count": existing_count
            })

        # Limpar tabela se tiver dados parciais
        if existing_count > 0:
            logger.info("🌱 [IRT Seed] Limpando fragmentos parciais...")
            cursor.execute("DELETE FROM irt_fragments")

        # Inserir fragmentos
        inserted = 0
        for frag in all_fragments:
            try:
                # Converter example_phrases para string JSON
                import json
                example_phrases_json = json.dumps(frag.get("example_phrases", []), ensure_ascii=False)

                cursor.execute("""
                    INSERT INTO irt_fragments
                        (fragment_id, domain, facet, facet_code, description, detection_pattern, example_phrases)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    frag["fragment_id"],
                    frag["domain"],
                    frag["facet"],  # Campo obrigatório NOT NULL
                    frag["facet_code"],
                    frag["description"],
                    frag.get("detection_pattern", ""),
                    example_phrases_json
                ))
                inserted += 1

                if inserted % 30 == 0:
                    logger.info(f"🌱 [IRT Seed] Progresso: {inserted}/{len(all_fragments)}")

            except Exception as frag_err:
                logger.error(f"🌱 [IRT Seed] Erro no fragmento {frag.get('fragment_id')}: {frag_err}")

        _db_manager.conn.commit()
        logger.info(f"🌱 [IRT Seed] Seed completo! {inserted} fragmentos inseridos.")

        # Também inserir parâmetros padrão GRM
        logger.info("🌱 [IRT Seed] Inserindo parâmetros GRM padrão...")
        cursor.execute("SELECT COUNT(*) FROM irt_item_parameters")
        params_count = cursor.fetchone()[0]

        if params_count == 0:
            for frag in all_fragments:
                cursor.execute("""
                    INSERT INTO irt_item_parameters
                        (fragment_id, discrimination, threshold_1, threshold_2, threshold_3, threshold_4)
                    VALUES (?, 1.0, -1.5, -0.5, 0.5, 1.5)
                """, (frag["fragment_id"],))

            _db_manager.conn.commit()
            logger.info(f"🌱 [IRT Seed] {len(all_fragments)} parâmetros GRM inseridos.")

        return JSONResponse(content={
            "status": "success",
            "message": f"Seed completo! {inserted} fragmentos inseridos.",
            "fragments_inserted": inserted,
            "timestamp": datetime.now().isoformat()
        })

    except ImportError as e:
        logger.error(f"🌱 [IRT Seed] Erro de import: {e}")
        raise HTTPException(500, f"Módulo de fragmentos não encontrado: {e}")
    except Exception as e:
        logger.error(f"🌱 [IRT Seed] Erro: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro no seed: {str(e)}")


@router.get("/migration/status")
async def get_migration_status(
    admin: Dict = Depends(require_master)
):
    """
    Verifica status da migração TRI.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        status = {
            "tables": {},
            "all_tables_exist": True,
            "fragments_seeded": False,
            "fragment_count": 0
        }

        required_tables = [
            "irt_fragments",
            "irt_item_parameters",
            "detected_fragments",
            "irt_trait_estimates",
            "facet_scores",
            "psychometric_quality_checks"
        ]

        for table in required_tables:
            cursor.execute(f"""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{table}'
            """)
            exists = cursor.fetchone() is not None
            status["tables"][table] = exists

            if not exists:
                status["all_tables_exist"] = False

        # Contar fragmentos
        if status["tables"].get("irt_fragments"):
            cursor.execute("SELECT COUNT(*) FROM irt_fragments")
            count = cursor.fetchone()[0]
            status["fragment_count"] = count
            status["fragments_seeded"] = count >= 150  # Esperamos 150 fragmentos

        return JSONResponse(content=status)

    except Exception as e:
        logger.error(f"Erro ao verificar status: {e}")
        raise HTTPException(500, f"Erro: {str(e)}")


# ============================================================================
# ESTATÍSTICAS DE FRAGMENTOS
# ============================================================================

@router.get("/fragments/stats")
async def get_fragment_stats(
    admin: Dict = Depends(require_org_admin)
):
    """
    Estatísticas detalhadas dos fragmentos comportamentais.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        # C12c3: org_admin vê apenas as detecções dos usuários da própria org.
        scope_sql, scope_params = org_user_scope_clause(admin, table_alias="df")

        stats = {
            "seed_stats": {},
            "detection_stats": {},
            "by_facet": {}
        }

        # 1. Stats do seed (fragmentos cadastrados)
        cursor.execute("""
            SELECT domain, COUNT(*) as count
            FROM irt_fragments
            GROUP BY domain
        """)
        stats["seed_stats"]["by_domain"] = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(DISTINCT facet_code) FROM irt_fragments")
        stats["seed_stats"]["total_facets"] = cursor.fetchone()[0]

        # 2. Stats de detecções
        cursor.execute(f"""
            SELECT
                COUNT(*) as total_detections,
                COUNT(DISTINCT user_id) as unique_users,
                COUNT(DISTINCT fragment_id) as unique_fragments,
                AVG(intensity) as avg_intensity,
                AVG(confidence) as avg_confidence
            FROM detected_fragments df
            WHERE 1=1{scope_sql}
        """, scope_params)
        row = cursor.fetchone()
        if row:
            stats["detection_stats"] = {
                "total_detections": row[0],
                "unique_users": row[1],
                "unique_fragments": row[2],
                "avg_intensity": round(row[3], 2) if row[3] else 0,
                "avg_confidence": round(row[4], 2) if row[4] else 0
            }

        # 3. Top fragmentos mais detectados
        cursor.execute(f"""
            SELECT
                df.fragment_id,
                f.facet_code,
                f.description,
                COUNT(*) as detection_count,
                AVG(df.intensity) as avg_intensity
            FROM detected_fragments df
            JOIN irt_fragments f ON df.fragment_id = f.fragment_id
            WHERE 1=1{scope_sql}
            GROUP BY df.fragment_id
            ORDER BY detection_count DESC
            LIMIT 20
        """, scope_params)

        stats["top_fragments"] = []
        for row in cursor.fetchall():
            stats["top_fragments"].append({
                "fragment_id": row[0],
                "facet_code": row[1],
                "description": row[2][:50] + "..." if len(row[2]) > 50 else row[2],
                "detection_count": row[3],
                "avg_intensity": round(row[4], 2) if row[4] else 0
            })

        # 4. Stats por faceta
        cursor.execute(f"""
            SELECT
                f.facet_code,
                f.domain,
                COUNT(df.id) as detections,
                AVG(df.intensity) as avg_intensity
            FROM irt_fragments f
            LEFT JOIN detected_fragments df
                ON f.fragment_id = df.fragment_id{scope_sql}
            GROUP BY f.facet_code
            ORDER BY f.domain, f.facet_code
        """, scope_params)

        for row in cursor.fetchall():
            stats["by_facet"][row[0]] = {
                "domain": row[1],
                "detections": row[2] or 0,
                "avg_intensity": round(row[3], 2) if row[3] else 0
            }

        return JSONResponse(content=stats)

    except Exception as e:
        logger.error(f"Erro ao obter stats de fragmentos: {e}")
        raise HTTPException(500, f"Erro: {str(e)}")


# ============================================================================
# API PARA FRONTEND - Dados para gráficos
# ============================================================================

@router.get("/api/domain-distribution")
async def get_domain_distribution(
    admin: Dict = Depends(require_org_admin)
):
    """
    Distribuição de detecções por domínio Big Five.
    Formato para gráficos de pizza/barras.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        scope_sql, scope_params = org_user_scope_clause(admin, table_alias="df")

        cursor.execute(f"""
            SELECT f.domain, COUNT(*) as count
            FROM detected_fragments df
            JOIN irt_fragments f ON df.fragment_id = f.fragment_id
            WHERE 1=1{scope_sql}
            GROUP BY f.domain
        """, scope_params)

        data = {
            "labels": [],
            "values": [],
            "colors": {
                "extraversion": "#FF6B6B",
                "openness": "#4ECDC4",
                "conscientiousness": "#45B7D1",
                "agreeableness": "#96CEB4",
                "neuroticism": "#FFEAA7"
            }
        }

        for row in cursor.fetchall():
            data["labels"].append(row[0].capitalize())
            data["values"].append(row[1])

        return JSONResponse(content=data)

    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/detection-timeline")
async def get_detection_timeline(
    days: int = 30,
    admin: Dict = Depends(require_org_admin)
):
    """
    Timeline de detecções nos últimos N dias.
    Formato para gráficos de linha.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        scope_sql, scope_params = org_user_scope_clause(admin)

        cursor.execute(f"""
            SELECT
                DATE(detected_at) as date,
                COUNT(*) as count
            FROM detected_fragments
            WHERE detected_at >= DATE('now', '-{days} days'){scope_sql}
            GROUP BY DATE(detected_at)
            ORDER BY date
        """, scope_params)

        data = {
            "dates": [],
            "counts": []
        }

        for row in cursor.fetchall():
            data["dates"].append(row[0])
            data["counts"].append(row[1])

        return JSONResponse(content=data)

    except Exception as e:
        raise HTTPException(500, str(e))
