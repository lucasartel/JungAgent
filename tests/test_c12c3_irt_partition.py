"""C12c3 Frente 2 — partição cognitiva da camada IRT/TRI.

Contratos verificados:
- as tabelas IRT com user_id carregam agent_instance + índices compostos
- a adaptação de migração adiciona a coluna a bancos existentes (idempotente)
- escritas carimbam a instância; leituras por usuário filtram a partição
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from irt_scope import instance_scope_sql, resolve_irt_instance  # noqa: E402


IRT_TABLES = (
    "detected_fragments",
    "irt_trait_estimates",
    "facet_scores",
    "psychometric_quality_checks",
)


def _apply_irt_schema(conn: sqlite3.Connection) -> None:
    schema_sql = (ROOT / "migrations" / "irt_schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_irt_tables_have_instance_partition_and_indexes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_irt_schema(conn)

    for table in IRT_TABLES:
        assert "agent_instance" in _columns(conn, table), table
        index_names = {
            row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        }
        assert any(name.endswith("_user_instance") for name in index_names), (
            table,
            index_names,
        )


def test_migration_adapts_legacy_table_without_instance_column():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE detected_fragments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, fragment_id TEXT NOT NULL)"
    )
    assert "agent_instance" not in _columns(conn, "detected_fragments")

    # Aplicar o mesmo bloco de adaptação do execute_schema (migrations/irt_migration.py).
    cursor = conn.cursor()
    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(detected_fragments)").fetchall()
    }
    if "agent_instance" not in columns:
        cursor.execute("ALTER TABLE detected_fragments ADD COLUMN agent_instance TEXT")
    conn.commit()

    assert "agent_instance" in _columns(conn, "detected_fragments")


def test_irt_scope_helpers_document_the_contract():
    clause = instance_scope_sql("df", 2)
    assert "df.agent_instance = $2" in clause
    assert "df.agent_instance IS NULL" in clause
    no_alias = instance_scope_sql("", 3)
    assert "agent_instance = $3" in no_alias
    assert instance_scope_sql("df", 2).startswith(" AND ")
    assert resolve_irt_instance("custom") == "custom"


def test_irt_access_points_stamp_and_filter_instance():
    engine = (ROOT / "irt_engine.py").read_text(encoding="utf-8")
    validator = (ROOT / "psychometric_validator.py").read_text(encoding="utf-8")
    detector = (ROOT / "fragment_detector.py").read_text(encoding="utf-8")
    proactive = (ROOT / "jung_proactive_advanced.py").read_text(encoding="utf-8")

    # Escritas carimbam a instância.
    assert engine.count("resolve_irt_instance()") >= 3
    assert "resolve_irt_instance()" in validator
    assert "resolve_irt_instance()" in detector
    assert "resolve_irt_instance()" in proactive

    # Leituras por usuário filtram a partição.
    assert "instance_scope_sql" in engine
    assert validator.count("instance_scope_sql(") >= 5
    assert "instance_scope_sql(" in detector

    # Caminho SQLite síncrono usa presence-check (bancos não migrados).
    assert "PRAGMA table_info(detected_fragments)" in proactive

    # Agregado de pesquisa segue sem identificáveis por usuário.
    assert "COUNT(DISTINCT user_id)" in validator
    assert "agent_instance: Optional[str] = None" in validator
