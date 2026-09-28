"""
irt_migration.py

Script de migração para criar as tabelas do Sistema TRI.
Executa o schema SQL e faz seed dos 150 fragmentos iniciais.

Uso:
    python migrations/irt_migration.py

Ou via import:
    from migrations.irt_migration import run_migration
    run_migration()
"""

import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime

# Adicionar diretório pai ao path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from migration_logger import (
    MigrationContext,
    log_info,
    log_error,
    log_warning,
    log_debug,
    get_log_file_path
)

# ========================================
# CONFIGURAÇÃO
# ========================================

# Caminho do banco de dados
DB_PATH = Path(__file__).parent.parent / "jung_claude.db"

# Caminho do schema SQL
SCHEMA_PATH = Path(__file__).parent / "irt_schema.sql"

# ========================================
# FUNÇÕES DE MIGRAÇÃO
# ========================================

def get_connection():
    """Obtém conexão com o banco de dados."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, table_name: str) -> bool:
    """Verifica se uma tabela existe."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name=?
    """, (table_name,))
    return cursor.fetchone() is not None


def count_rows(conn, table_name: str) -> int:
    """Conta registros em uma tabela."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]


def execute_schema(conn, ctx: MigrationContext):
    """Executa o schema SQL."""
    log_info("📄 Lendo arquivo de schema...")

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema não encontrado: {SCHEMA_PATH}")

    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    log_debug(f"Schema tem {len(schema_sql)} caracteres")

    # Executar schema
    cursor = conn.cursor()

    # Tabelas esperadas
    tables = [
        ("irt_fragments", 16),
        ("irt_item_parameters", 16),
        ("detected_fragments", 15),
        ("irt_trait_estimates", 20),
        ("facet_scores", 12),
        ("psychometric_quality_checks", 18)
    ]

    # Verificar estado antes
    existing_tables = []
    for table_name, _ in tables:
        if table_exists(conn, table_name):
            existing_tables.append(table_name)
            ctx.table_exists(table_name)

    if existing_tables:
        log_warning(f"Tabelas já existentes: {', '.join(existing_tables)}")

    # Executar schema (CREATE IF NOT EXISTS é idempotente)
    try:
        cursor.executescript(schema_sql)
        conn.commit()
        log_info("✅ Schema executado com sucesso")
    except sqlite3.Error as e:
        log_error("Erro ao executar schema", e)
        raise

    # C12c3: adaptar bancos existentes — coluna de particao agent_instance
    # (CREATE IF NOT EXISTS nao altera tabelas ja criadas).
    for table_name in (
        "detected_fragments",
        "irt_trait_estimates",
        "facet_scores",
        "psychometric_quality_checks",
    ):
        columns = {
            row[1]
            for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "agent_instance" not in columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN agent_instance TEXT")
            log_info(f"C12c3: coluna agent_instance adicionada a {table_name}")
    conn.commit()

    # Verificar estado depois
    for table_name, col_count in tables:
        if table_name not in existing_tables:
            if table_exists(conn, table_name):
                ctx.table_created(table_name, col_count)
            else:
                log_error(f"Tabela {table_name} não foi criada!")


def seed_fragments(conn, ctx: MigrationContext):
    """
    Insere os 150 fragmentos iniciais do Big Five.
    30 facetas × 5 fragmentos = 150 fragmentos
    """
    from irt_fragments_seed import get_all_fragments

    log_info("🌱 Iniciando seed de fragmentos...")

    # Verificar se já tem fragmentos
    existing_count = count_rows(conn, "irt_fragments")
    if existing_count > 0:
        log_warning(f"Tabela irt_fragments já tem {existing_count} registros")
        if existing_count >= 150:
            log_info("Seed já foi executado anteriormente. Pulando...")
            return

    fragments = get_all_fragments()
    total = len(fragments)
    log_info(f"📦 {total} fragmentos a inserir")

    cursor = conn.cursor()
    inserted = 0

    for i, fragment in enumerate(fragments, 1):
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO irt_fragments
                (fragment_id, domain, facet, facet_code, description,
                 description_en, detection_pattern, example_phrases,
                 reverse_scored, expert_review_status, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fragment['fragment_id'],
                fragment['domain'],
                fragment['facet'],
                fragment['facet_code'],
                fragment['description'],
                fragment.get('description_en'),
                fragment.get('detection_pattern'),
                fragment.get('example_phrases'),
                fragment.get('reverse_scored', False),
                'pending',
                'active'
            ))

            if cursor.rowcount > 0:
                inserted += 1

            # Log de progresso a cada 30 (uma faceta)
            if i % 30 == 0:
                ctx.seed_progress(i, total, "fragmentos")

        except sqlite3.Error as e:
            log_error(f"Erro ao inserir fragmento {fragment['fragment_id']}", e)

    conn.commit()

    # Log final
    ctx.seed_progress(inserted, total, "fragmentos inseridos")
    log_info(f"✅ Seed concluído: {inserted} novos fragmentos")


def seed_default_parameters(conn, ctx: MigrationContext):
    """
    Insere parâmetros default para os fragmentos.
    Parâmetros serão calibrados posteriormente com dados reais.
    """
    log_info("⚙️  Inserindo parâmetros default...")

    cursor = conn.cursor()

    # Buscar fragmentos sem parâmetros
    cursor.execute("""
        SELECT f.fragment_id
        FROM irt_fragments f
        LEFT JOIN irt_item_parameters p ON f.fragment_id = p.fragment_id
        WHERE p.fragment_id IS NULL
    """)

    fragments_without_params = [row[0] for row in cursor.fetchall()]

    if not fragments_without_params:
        log_info("Todos os fragmentos já têm parâmetros")
        return

    inserted = 0
    for fragment_id in fragments_without_params:
        try:
            cursor.execute("""
                INSERT INTO irt_item_parameters
                (fragment_id, discrimination, threshold_1, threshold_2,
                 threshold_3, threshold_4, calibration_method, discrimination_quality)
                VALUES (?, 1.0, -1.5, -0.5, 0.5, 1.5, 'default', 'uncalibrated')
            """, (fragment_id,))
            inserted += 1
        except sqlite3.Error as e:
            log_error(f"Erro ao inserir parâmetros para {fragment_id}", e)

    conn.commit()
    log_info(f"✅ {inserted} parâmetros default inseridos")


def validate_migration(conn) -> dict:
    """Valida que a migração foi bem-sucedida."""
    log_info("🔍 Validando migração...")

    validation = {
        "tables_ok": True,
        "fragments_count": 0,
        "parameters_count": 0,
        "issues": []
    }

    # Verificar tabelas
    expected_tables = [
        "irt_fragments",
        "irt_item_parameters",
        "detected_fragments",
        "irt_trait_estimates",
        "facet_scores",
        "psychometric_quality_checks"
    ]

    for table in expected_tables:
        if not table_exists(conn, table):
            validation["tables_ok"] = False
            validation["issues"].append(f"Tabela {table} não existe")

    # Contar registros
    if table_exists(conn, "irt_fragments"):
        validation["fragments_count"] = count_rows(conn, "irt_fragments")
        if validation["fragments_count"] < 150:
            validation["issues"].append(
                f"Apenas {validation['fragments_count']}/150 fragmentos"
            )

    if table_exists(conn, "irt_item_parameters"):
        validation["parameters_count"] = count_rows(conn, "irt_item_parameters")

    # Verificar integridade
    cursor = conn.cursor()

    # Fragmentos por domínio
    cursor.execute("""
        SELECT domain, COUNT(*) as count
        FROM irt_fragments
        GROUP BY domain
    """)
    domain_counts = dict(cursor.fetchall())

    expected_per_domain = 30  # 6 facetas × 5 fragmentos
    for domain in ["Extraversion", "Openness", "Conscientiousness",
                   "Agreeableness", "Neuroticism"]:
        count = domain_counts.get(domain, 0)
        if count != expected_per_domain:
            validation["issues"].append(
                f"{domain}: {count}/{expected_per_domain} fragmentos"
            )

    # Resultado
    if validation["issues"]:
        log_warning("⚠️  Problemas encontrados:")
        for issue in validation["issues"]:
            log_warning(f"   - {issue}")
    else:
        log_info("✅ Validação OK")

    return validation


def run_migration():
    """Executa a migração completa."""
    log_info(f"📂 Banco de dados: {DB_PATH}")

    # Verificar se banco existe
    if not DB_PATH.exists():
        log_error(f"Banco de dados não encontrado: {DB_PATH}")
        return False

    conn = get_connection()

    try:
        with MigrationContext("TRI System v1.0") as ctx:
            # 1. Executar schema
            execute_schema(conn, ctx)

            # 2. Seed de fragmentos
            seed_fragments(conn, ctx)

            # 3. Seed de parâmetros default
            seed_default_parameters(conn, ctx)

            # 4. Validar
            validation = validate_migration(conn)

            # Atualizar stats
            ctx.stats["fragmentos_inseridos"] = validation["fragments_count"]
            ctx.stats["parametros_inseridos"] = validation["parameters_count"]
            ctx.stats["problemas"] = len(validation["issues"])

        return len(validation["issues"]) == 0

    except Exception as e:
        log_error("Migration falhou", e)
        conn.rollback()
        return False

    finally:
        conn.close()


# ========================================
# EXECUÇÃO DIRETA
# ========================================

if __name__ == "__main__":
    print("=" * 60)
    print("TRI Migration - JungAgent")
    print("=" * 60)

    success = run_migration()

    print()
    print(f"Log salvo em: {get_log_file_path()}")
    print()

    if success:
        print("✅ Migration concluída com sucesso!")
        sys.exit(0)
    else:
        print("❌ Migration falhou. Verifique os logs.")
        sys.exit(1)
