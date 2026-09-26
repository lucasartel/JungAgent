"""C12c2: exports legados com escopo explícito (revisão P1/P2 do revisor).

Fixtures no formato da base real: material já vinculado a Relations (a base
de produção é ~100% relation-stamped) e o resíduo NULL é work_reading/work —
origem NÃO classificada, nunca "global". Regras cobertas:

- research lab: visibilidade pessoal (sem Relation + Relation verificada),
  linhas rotuladas com o escopo real e diagnóstico que nunca afirma ausência
  falsa de tensões que existem fora da visibilidade;
- UNESCO: totais corretos (sem zero falso) + quebra explícita por escopo;
- snapshot: quarentena estrita mantida, rótulo honesto + contagem por
  source_kind.
"""
import json
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from instance_config import ADMIN_USER_ID


def _research_conn():
    """Base mista: global, Relation do admin, Relation alheia e outra instância."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY, user_id TEXT, content TEXT,
            emotional_weight REAL, context_type TEXT, detected_at TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT, source_kind TEXT DEFAULT 'conversation');
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY, user_id TEXT, tension_type TEXT,
            pole_a TEXT, pole_b TEXT, pole_a_fragment_ids TEXT, pole_b_fragment_ids TEXT,
            status TEXT, intensity REAL, maturity_score REAL, evidence_count INTEGER,
            revisit_count INTEGER, first_detected_at TEXT, last_revisited_at TEXT,
            last_evidence_at TEXT, resolved_at TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY, user_id TEXT, tension_id INTEGER,
            insight_type TEXT, content TEXT, confidence_score REAL, status TEXT,
            generated_at TEXT, delivered_at TEXT, user_feedback TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT);
        """
    )
    admin = str(ADMIN_USER_ID)
    conn.executemany(
        "INSERT INTO rumination_fragments (id, user_id, content, relation_id, agent_instance, source_kind)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, admin, "fragmento-sem-relation", None, None, "work_reading"),
            (2, admin, "fragmento-da-relacao", "rel-jungle", None, "dream"),
            (3, admin, "fragmento-relacao-alheia", "rel-alheia", None, "conversation"),
            (4, admin, "fragmento-outra-instancia", None, "outro_v9", "work"),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_tensions (id, user_id, tension_type, first_detected_at,"
        " maturity_score, relation_id, agent_instance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, admin, "tensao-sem-relation", "2026-09-20T10:00:00", 0.9, None, None),
            (2, admin, "tensao-da-relacao", "2026-09-21T10:00:00", 0.8, "rel-jungle", None),
            (3, admin, "tensao-alheia", "2026-09-22T10:00:00", 0.7, "rel-alheia", None),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_insights (id, user_id, content, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, admin, "insight-sem-relation", None, None),
            (2, admin, "insight-da-relacao", "rel-jungle", None),
            (3, admin, "insight-alheio", "rel-alheia", None),
        ],
    )
    conn.commit()
    return conn


def _relation_bound_conn():
    """Formato da produção: tudo vinculado à Relation, nada sem Relation."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY, user_id TEXT, tension_type TEXT,
            status TEXT, intensity REAL, maturity_score REAL, evidence_count INTEGER,
            revisit_count INTEGER, first_detected_at TEXT, last_revisited_at TEXT,
            last_evidence_at TEXT, relation_id TEXT, agent_instance TEXT);
        """
    )
    admin = str(ADMIN_USER_ID)
    conn.executemany(
        "INSERT INTO rumination_tensions (id, user_id, tension_type, first_detected_at,"
        " maturity_score, relation_id) VALUES (?, ?, ?, ?, ?, 'rel-jungle')",
        [
            (1, admin, "tensao-de-producao-1", "2026-09-20T10:00:00", 0.9),
            (2, admin, "tensao-de-producao-2", "2026-09-21T10:00:00", 0.8),
            (3, admin, "tensao-de-producao-3", "2026-09-22T10:00:00", 0.7),
        ],
    )
    conn.commit()
    return conn


def test_research_exports_personal_visibility_labels_scope():
    from core.db.legacy_exports import (
        fetch_research_fragments,
        fetch_research_insights,
        fetch_research_tensions,
        scope_counts,
    )

    conn = _research_conn()
    admin = str(ADMIN_USER_ID)
    for fetch, private_marker in (
        (fetch_research_fragments, "fragmento-relacao-alheia"),
        (fetch_research_tensions, "tensao-alheia"),
        (fetch_research_insights, "insight-alheio"),
    ):
        rows = fetch(conn, admin, None, "rel-jungle")
        dumped = json.dumps(rows)
        assert private_marker not in dumped, "Relation alheia vazou"
        assert "outra-instancia" not in dumped
        assert len(rows) == 2, "visibilidade pessoal = sem-Relation + Relation verificada"
        by_scope = {row["scope"] for row in rows}
        assert by_scope == {"no_relation", "relation"}, "cada linha rotulada com o escopo real"
        assert scope_counts(rows) == {"no_relation": 1, "relation": 1}


def test_diagnostic_sees_relation_bound_production_base():
    from core.db.legacy_exports import (
        count_out_of_personal_scope,
        fetch_research_tension_diagnostics,
    )

    conn = _relation_bound_conn()
    admin = str(ADMIN_USER_ID)
    rows = fetch_research_tension_diagnostics(conn, admin, None, "rel-jungle")
    assert len(rows) == 3, "diagnóstico não pode ficar cego para a base relation-bound"
    assert all(row["scope"] == "relation" for row in rows)
    assert count_out_of_personal_scope(conn, admin, "rumination_tensions", "rel-jungle") == 0


def test_diagnostic_never_falsely_claims_absence():
    from core.db.legacy_exports import (
        count_out_of_personal_scope,
        fetch_research_tension_diagnostics,
        no_tensions_diagnosis,
    )

    conn = _relation_bound_conn()
    admin = str(ADMIN_USER_ID)
    # Sem Relation resolvida o escopo cai para o sem-Relation: vazio.
    rows = fetch_research_tension_diagnostics(conn, admin, None, None)
    assert rows == []
    out_of_scope = count_out_of_personal_scope(conn, admin, "rumination_tensions", None)
    assert out_of_scope == 3

    diagnosis = no_tensions_diagnosis(out_of_scope)
    assert "Não há tensões detectadas" not in diagnosis["problem_identified"]
    assert "3" in diagnosis["problem_identified"]

    honest_empty = no_tensions_diagnosis(0)
    assert "Não há tensões detectadas" in honest_empty["problem_identified"]


def _unesco_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE unesco_pilot_data (
            user_id TEXT PRIMARY KEY, baseline_stress_score INTEGER,
            baseline_trait_challenge TEXT, baseline_expectation TEXT,
            post_test_stress_score INTEGER, dossier_accuracy_rating INTEGER,
            safety_triggers_count INTEGER DEFAULT 0, created_at DATETIME,
            completed_at DATETIME);
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, timestamp TEXT,
            relation_id TEXT, agent_instance TEXT);
        """
    )
    # u1: mista (2 sem Relation + 2 com Relation); u2: toda vinculada (caso do zero falso).
    conn.execute(
        "INSERT INTO unesco_pilot_data (user_id, baseline_stress_score, baseline_trait_challenge,"
        " baseline_expectation, post_test_stress_score, dossier_accuracy_rating,"
        " safety_triggers_count, created_at, completed_at)"
        " VALUES ('u1', 30, 'desafio', 'expectativa', 12, 4, 0, '2026-09-01', '2026-09-20')"
    )
    conn.execute(
        "INSERT INTO unesco_pilot_data (user_id, baseline_stress_score, baseline_trait_challenge,"
        " baseline_expectation, post_test_stress_score, dossier_accuracy_rating,"
        " safety_triggers_count, created_at, completed_at)"
        " VALUES ('u2', 20, 'desafio2', 'expectativa2', 8, 3, 0, '2026-09-02', '2026-09-21')"
    )
    conn.executemany(
        "INSERT INTO conversations (id, user_id, timestamp, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, NULL)",
        [
            (1, "u1", "2026-09-02 10:00:00", None),
            (2, "u1", "2026-09-03 10:00:00", None),
            (3, "u1", "2026-09-04 10:00:00", "rel-jungle"),
            (4, "u1", "2026-09-05 10:00:00", "rel-jungle"),
            (5, "u2", "2026-09-06 10:00:00", "rel-jungle"),
            (6, "u2", "2026-09-07 10:00:00", "rel-jungle"),
        ],
    )
    conn.commit()
    return conn


def test_unesco_totals_are_correct_with_scope_breakdown():
    from core.db.legacy_exports import build_unesco_csv, fetch_unesco_participants

    rows = fetch_unesco_participants(_unesco_conn())
    by_user = {row[0]: row for row in rows}

    u1, u2 = by_user["u1"], by_user["u2"]
    assert (u1[7], u1[9], u1[10]) == (4, 2, 2), "u1: total correto + quebra por escopo"
    assert (u2[7], u2[9], u2[10]) == (2, 0, 2), "u2 vinculada: total 2, não zero falso"
    assert u2[8] == 2 and u2[12] == 2

    header, data_rows = build_unesco_csv(rows)
    for col in (
        "Total_Messages",
        "Messages_No_Relation",
        "Messages_With_Relation",
        "Days_No_Relation",
        "Days_With_Relation",
    ):
        assert col in header
    u2_csv = next(r for r in data_rows if r[0] == "Participant_002")
    total_col = header.index("Total_Messages")
    assert u2_csv[total_col] == 2, "CSV não pode mostrar zero para participante vinculada"


def test_unesco_view_payload_has_scope_fields():
    from core.db.legacy_exports import build_unesco_participants, fetch_unesco_participants

    participants = build_unesco_participants(fetch_unesco_participants(_unesco_conn()))
    assert participants[0]["msgs"] == 4
    assert participants[1]["msgs"] == 2
    assert participants[1]["msgs_with_relation"] == 2
    assert participants[1]["start"] is not None


def _snapshot_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, timestamp TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, dream_content TEXT,
            origin_relation_id TEXT, agent_instance TEXT);
        CREATE TABLE agent_identity_core (
            id INTEGER PRIMARY KEY, agent_instance TEXT, origin_relation_id TEXT, summary TEXT);
        CREATE TABLE agent_identity_extractions (
            id INTEGER PRIMARY KEY, conversation_id INTEGER, origin_relation_id TEXT,
            agent_instance TEXT, extracted_at TEXT);
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY, user_id TEXT, content TEXT,
            relation_id TEXT, agent_instance TEXT, source_kind TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO conversations (id, user_id, timestamp, relation_id, agent_instance)"
        " VALUES (?, 'admin1', ?, ?, ?)",
        [
            (1, "2026-09-01", None, "jung_v1"),
            (2, "2026-09-02", "rel-jungle", "jung_v1"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_dreams (id, user_id, dream_content, origin_relation_id, agent_instance)"
        " VALUES (?, 'admin1', ?, ?, 'jung_v1')",
        [
            (1, "sonho-sem-relation", None),
            (2, "sonho-da-relacao", "rel-jungle"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_core (id, agent_instance, origin_relation_id, summary)"
        " VALUES (?, 'jung_v1', ?, ?)",
        [
            (1, None, "identidade-sem-relation"),
            (2, "rel-jungle", "identidade-da-relacao"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_extractions (id, conversation_id, origin_relation_id,"
        " agent_instance, extracted_at) VALUES (?, ?, ?, 'jung_v1', '2026-09-25')",
        [
            (1, 1, None),
            (2, 2, "rel-jungle"),
        ],
    )
    # Origem real do material NULL: work_* (como em produção), não "global".
    conn.executemany(
        "INSERT INTO rumination_fragments (id, user_id, content, relation_id, agent_instance, source_kind)"
        " VALUES (?, 'admin1', ?, ?, 'jung_v1', ?)",
        [
            (1, "material-work-reading", None, "work_reading"),
            (2, "material-work", None, "work"),
            (3, "material-da-relacao", "rel-jungle", "conversation"),
        ],
    )
    conn.commit()
    return conn


def test_endojung_snapshot_quarantine_and_honest_labels():
    from endojung_snapshot_export import build_endojung_snapshot_from_connection

    snapshot = build_endojung_snapshot_from_connection(
        _snapshot_conn(), admin_user_id="admin1", agent_instance="jung_v1"
    )
    dumped = json.dumps(snapshot["tables"])
    for private in ("sonho-da-relacao", "identidade-da-relacao", "material-da-relacao"):
        assert private not in dumped, "quarentena estrita do snapshot quebrada"
    assert [row["id"] for row in snapshot["tables"]["conversations"]] == [1]
    assert [row["id"] for row in snapshot["tables"]["agent_identity_extractions"]] == [1]

    notes = " ".join(snapshot["meta"]["notes"])
    assert "Legacy-global" not in notes, "snapshot não pode prometer origem global"
    assert "UNCLASSIFIED" in notes
    assert snapshot["meta"]["included_source_kind_counts"] == {
        "rumination_fragments:work_reading": 1,
        "rumination_fragments:work": 1,
    }


def test_endojung_snapshot_mirror_uses_same_scope():
    from scripts.export_endojung_snapshot import build_endojung_snapshot

    snapshot = build_endojung_snapshot(
        SimpleNamespace(conn=_snapshot_conn()),
        admin_user_id="admin1",
        agent_instance="jung_v1",
    )
    assert [row["id"] for row in snapshot["tables"]["conversations"]] == [1]
    assert [row["id"] for row in snapshot["tables"]["agent_identity_core"]] == [1]
    notes = " ".join(snapshot["meta"]["notes"])
    assert "Legacy-global" not in notes


def test_export_handlers_wired_to_scoped_queries():
    root = Path(__file__).resolve().parents[1]
    research = (root / "admin_web/routes/research_lab_exports.py").read_text(encoding="utf-8")
    unesco = (root / "admin_web/routes/unesco_export_routes.py").read_text(encoding="utf-8")
    for fetch_name in (
        "fetch_research_fragments",
        "fetch_research_tensions",
        "fetch_research_insights",
        "fetch_research_tension_diagnostics",
        "no_tensions_diagnosis",
    ):
        assert fetch_name in research, fetch_name
    assert "fetch_unesco_participants" in unesco
    assert "build_unesco_csv" in unesco
    assert "FROM rumination_" not in research, "handler voltou a SQL cru sem escopo"


def test_download_scripts_have_no_hardcoded_credentials():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "scripts/operations/download_data.py",
        "scripts/operations/download_railway_data.py",
    ):
        source = (root / rel).read_text(encoding="utf-8")
        assert 'password = "admin"' not in source, rel
        assert "session.auth" not in source, f"{rel} ainda usa HTTP Basic"
        assert "JUNG_ADMIN_PASSWORD" in source, rel
        assert "JUNG_ADMIN_USER" in source, rel
        assert "/admin/login" in source, rel
