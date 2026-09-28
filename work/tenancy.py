"""Tenancy do dominio Work (C12c4).

Contrato: ``core/db/cognitive_ownership.py`` (dominio ``work_and_actions``)
— colunas-alvo ``org_id``, ``agent_instance``, ``origin_class``,
``origin_relation_id``; escopo INSTANCE_GLOBAL com origem explicita.

Politica (decisao do mantenedor, 2026-09-28 — fail-closed por origem):
- master     : visao global (sem corte);
- org_admin  : apenas registros com ``org_id`` explicito da propria org;
               registro sem origem atribuivel (NULL ou legado) fica
               master-only — um registro nao pertence a org so porque um
               participante esta vinculado a ela;
- qualquer outro papel / admin ausente: fail-closed (``1 = 0``).

Nunca se infere a organizacao do admin ("Never infer organization
ownership from the admin user"): ``org_id`` nasce apenas de uma Relation
explicita que carrega ``org_id`` proprio.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, Optional, Tuple

WORK_TENANCY_TABLES: Tuple[str, ...] = (
    "work_projects",
    "work_destinations",
    "work_briefs",
    "work_runs",
    "work_artifacts",
    "work_approval_tickets",
    "work_delivery_events",
    "work_experience_events",
    "work_project_attachments",
    "work_skill_providers",
)

WORK_TENANCY_COLUMN_DEFS: Tuple[str, ...] = (
    "org_id TEXT",
    "agent_instance TEXT",
    "origin_class TEXT",
    "origin_relation_id TEXT",
)

# Rotaulos factuais de origem (vocabulario do projeto; nunca "global").
ORIGIN_CLASS_RELATION = "relation_scoped"
ORIGIN_CLASS_LEGACY = "legacy_unscoped"


def current_agent_instance(db: Optional[object] = None) -> str:
    """Instancia canonica — mesma cadeia do will_scope (AGENT_INSTANCE)."""
    instance = getattr(db, "agent_instance", None) or os.getenv("AGENT_INSTANCE") or "jung_v1"
    return str(instance).strip() or "jung_v1"


def work_scope_clause(
    admin: Optional[Dict],
    *,
    table_alias: str = "",
) -> Tuple[str, Tuple]:
    """Clausula SQL fail-closed de visibilidade Work para uma leitura admin.

    Registros sem ``org_id`` explicito (NULL ou ``legacy_unscoped``) nao
    aparecem para org_admin — origem nao atribuivel = master-only.
    """
    prefix = f"{table_alias}." if table_alias else ""
    role = (admin or {}).get("role")

    if role == "master":
        return "", ()

    if role == "org_admin":
        org_id = admin.get("org_id")
        if not org_id:
            # org_admin sem org: impossivel de fatiar por origem — recusar.
            return " AND 1 = 0", ()
        return f" AND {prefix}org_id = ?", (org_id,)

    # papel desconhecido ou admin ausente: fail-closed.
    return " AND 1 = 0", ()


def resolve_work_tenancy(
    db: Optional[object],
    *,
    origin_relation_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Valores de tenancy para um INSERT Work.

    ``org_id`` so preenche quando uma Relation explicita carrega org propria
    (lookup em ``agent_relations``). Sem Relation: origem fica NULL (nao
    classificada) — nunca se infere a org do admin que disparou o run.
    """
    columns: Dict[str, Optional[str]] = {
        "agent_instance": current_agent_instance(db),
        "org_id": None,
        "origin_class": None,
        "origin_relation_id": None,
    }
    relation_id = str(origin_relation_id).strip() if origin_relation_id else None
    if not relation_id:
        return columns

    org_id = None
    getter = getattr(db, "get_agent_relation", None)
    if callable(getter):
        try:
            relation = getter(relation_id)
            if isinstance(relation, dict):
                org_id = relation.get("org_id") or None
        except sqlite3.Error:
            org_id = None
    columns["origin_relation_id"] = relation_id
    columns["origin_class"] = ORIGIN_CLASS_RELATION
    columns["org_id"] = str(org_id) if org_id else None
    return columns


def tenancy_insert_columns() -> str:
    """Trecho de lista de colunas para INSERTs Work com tenancy."""
    return "org_id, agent_instance, origin_class, origin_relation_id"


def tenancy_insert_placeholders() -> str:
    """Placeholders (4) correspondentes a :func:`tenancy_insert_columns`."""
    return "?, ?, ?, ?"


def tenancy_insert_values(
    db: Optional[object],
    *,
    origin_relation_id: Optional[str] = None,
) -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    """Valores para INSERTs Work com tenancy (ordem do helper de colunas)."""
    tenancy = resolve_work_tenancy(db, origin_relation_id=origin_relation_id)
    return (
        tenancy["org_id"],
        tenancy["agent_instance"],
        tenancy["origin_class"],
        tenancy["origin_relation_id"],
    )


class WorkTenancyDatabaseMixin:
    """Migracao aditiva do tenancy Work (padrao will_scope)."""

    def _init_work_tenancy_schema(self) -> None:
        apply_work_tenancy(self.conn, instance=current_agent_instance(self))


def apply_work_tenancy(conn, *, instance: Optional[str] = None) -> None:
    """Aplica a migracao aditiva do tenancy numa conexao SQLite direta.

    Idempotente e tolerante a bancos legados (colunas ausentes ganham ALTER;
    tabelas ausentes sao puladas). Usada pelo mixin de schema e por testes
    que constroem schemas simplificados.
    """
    cursor = conn.cursor()
    instance = instance or current_agent_instance(None)

    for table in WORK_TENANCY_TABLES:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
        if not cursor.fetchone():
            continue
        for column_definition in WORK_TENANCY_COLUMN_DEFS:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        # Backfill honesto: linhas existentes sao legado pre-tenancy
        # (origem nao atribuivel). Org/Relation ficam NULL — nada e
        # inferido retroativamente.
        cursor.execute(
            f"UPDATE {table} SET agent_instance = ? "
            "WHERE (agent_instance IS NULL OR agent_instance = '')",
            (instance,),
        )
        cursor.execute(
            f"UPDATE {table} SET origin_class = ? "
            "WHERE origin_class IS NULL "
            "AND (org_id IS NULL OR org_id = '')",
            (ORIGIN_CLASS_LEGACY,),
        )
        cursor.execute(f"PRAGMA table_info({table})")
        table_columns = {row[1] for row in cursor.fetchall()}
        index_columns = ["org_id", "agent_instance"]
        if "created_at" in table_columns:
            index_columns.append("created_at DESC")
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_tenancy "
            f"ON {table}({', '.join(index_columns)})"
        )
