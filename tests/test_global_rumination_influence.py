"""Cross-Relation interiority may carry influence, never another person's text."""
import json
import sqlite3
import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from core.cognitive_context import (
    CognitiveContextAssembler,
    CognitiveContextContribution,
    CognitiveContextScope,
)
from core.rumination_interiority import admin_reading_awareness, global_rumination_influence
from instance_config import AGENT_INSTANCE


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE agent_relations (
                relation_id TEXT, agent_instance TEXT, participant_user_id TEXT,
                status TEXT, consent_status TEXT, scope_json TEXT
            );
            CREATE TABLE rumination_fragments (
                id INTEGER, agent_instance TEXT, relation_id TEXT,
                fragment_type TEXT, content TEXT, created_at TEXT
            );
            CREATE TABLE rumination_tensions (
                id INTEGER, agent_instance TEXT, relation_id TEXT,
                tension_type TEXT, status TEXT
            );
            CREATE TABLE rumination_insights (
                id INTEGER, agent_instance TEXT, relation_id TEXT,
                source_tension_id INTEGER, full_message TEXT, crystallized_at TEXT
            );
            INSERT INTO agent_relations VALUES
                ('rel-a', 'instance-a', 'admin', 'active', 'granted', '{}'),
                ('rel-b', 'instance-a', 'user-b', 'active', 'granted', '{"global_interiority": true}'),
                ('rel-c', 'instance-b', 'admin', 'active', 'granted', '{}'),
                ('rel-revoked', 'instance-a', 'user-revoked', 'revoked', 'revoked', '{"global_interiority": true}');
            INSERT INTO rumination_fragments VALUES
                (1, 'instance-a', 'rel-a', 'knowledge_fragment', 'segredo de A', datetime('now')),
                (2, 'instance-a', 'rel-b', 'emocao', 'segredo de B', datetime('now')),
                (3, 'instance-b', 'rel-c', 'knowledge_fragment', 'segredo de C', datetime('now')),
                (4, 'instance-a', 'rel-revoked', 'knowledge_fragment', 'segredo revogado', datetime('now')),
                (5, 'instance-a', NULL, 'knowledge_fragment', 'legado sem escopo', datetime('now'));
            INSERT INTO rumination_tensions VALUES
                (1, 'instance-a', 'rel-a', 'epistemic_reading', 'ready_for_synthesis'),
                (2, 'instance-a', 'rel-b', 'autonomia_vinculo', 'synthesized'),
                (3, 'instance-b', 'rel-c', 'epistemic_reading', 'ready_for_synthesis'),
                (4, 'instance-a', 'rel-revoked', 'epistemic_reading', 'ready_for_synthesis');
            INSERT INTO rumination_insights VALUES
                (1, 'instance-a', 'rel-a', 1, 'confissao privada A', datetime('now')),
                (2, 'instance-a', 'rel-b', 2, 'confissao privada B', datetime('now')),
                (3, 'instance-b', 'rel-c', 3, 'confissao privada C', datetime('now')),
                (4, 'instance-a', 'rel-revoked', 4, 'confissao revogada', datetime('now'));
            """
        )


def test_all_eligible_rumination_affects_instance_without_private_text():
    db = _DB()
    influence = global_rumination_influence(db, "instance-a", "admin")
    assert "questoes de conhecimento" in influence
    assert "tensoes ainda abertas" in influence
    assert "sinteses" in influence
    assert not any(word in influence for word in ("segredo", "confissao", "rel-a", "rel-b", "rel-c"))

    for relation_id in ("rel-a", "rel-b"):
        assembler = CognitiveContextAssembler(CognitiveContextScope("instance-a", "user", relation_id))
        assert assembler.add(CognitiveContextContribution(
            domain="rumination_influence", content=influence,
            agent_instance="instance-a", origin_class="authorized_aggregate",
            provenance={"raw_relational_text_used": False},
        ))
        assert "segredo" not in assembler.render()


def test_revocation_and_instance_isolation_recompute_influence():
    db = _DB()
    db.conn.execute("UPDATE agent_relations SET status='revoked' WHERE relation_id IN ('rel-a', 'rel-b')")
    assert global_rumination_influence(db, "instance-a", "admin") == ""
    assert "questoes de conhecimento" in global_rumination_influence(db, "instance-b", "admin")


def test_non_admin_relation_needs_explicit_global_interiority_consent():
    db = _DB()
    db.conn.execute("UPDATE agent_relations SET status='revoked' WHERE relation_id='rel-a'")
    db.conn.execute("UPDATE agent_relations SET scope_json='{}' WHERE relation_id='rel-b'")
    assert global_rumination_influence(db, "instance-a", "admin") == ""
    db.conn.execute(
        "UPDATE agent_relations SET scope_json=? WHERE relation_id='rel-b'",
        (json.dumps({"global_interiority": True}),),
    )
    assert "sinteses" in global_rumination_influence(db, "instance-a", "admin")


def test_malformed_relation_scope_fails_closed():
    db = _DB()
    db.conn.execute("UPDATE agent_relations SET status='revoked' WHERE relation_id='rel-a'")
    db.conn.execute("UPDATE agent_relations SET scope_json='not-json' WHERE relation_id='rel-b'")
    assert global_rumination_influence(db, "instance-a", "admin") == ""


def test_private_text_cannot_pose_as_global_influence():
    assembler = CognitiveContextAssembler(CognitiveContextScope("instance-a", "user-b", "rel-b"))
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination_influence", content="confissao privada A",
        agent_instance="instance-a", origin_class="relation_private", origin_relation_id="rel-a",
    ))
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination_influence", content="confissao privada A",
        agent_instance="instance-a", origin_class="authorized_aggregate",
        provenance={"raw_relational_text_used": True},
    ))


def test_work_reading_details_stay_in_admin_relation():
    db = _DB()
    db.conn.executescript(
        """
        CREATE TABLE work_projects (id INTEGER, name TEXT);
        CREATE TABLE work_artifacts (
            id INTEGER, project_id INTEGER, provider_payload_json TEXT,
            status TEXT, content_type TEXT, updated_at TEXT
        );
        INSERT INTO work_projects VALUES (10, 'Livro de teste');
        """
    )
    payload = {"package": {
        "generation_mode": "reading_assimilation",
        "reading_assimilation": {
            "verified": True, "source_mode": "stored_pdf", "source_hash": "sha256",
            "start_page": 10, "end_page": 20, "assimilation_mode": "llm_structured",
            "summary": "Ideia verificada no texto do livro.",
        },
    }}
    db.conn.execute(
        "INSERT INTO work_artifacts VALUES (1, 10, ?, 'assimilated', 'reading_note', datetime('now'))",
        (json.dumps(payload),),
    )

    private_text, refs = admin_reading_awareness(
        db, agent_instance=AGENT_INSTANCE, user_id="admin", admin_user_id="admin",
        user_message="O que leu nos livros?"
    )
    assert "Livro de teste" in private_text
    assert "Ideia verificada" in private_text
    assert refs == ("work_artifact#1",)
    other_text, other_refs = admin_reading_awareness(
        db, agent_instance=AGENT_INSTANCE, user_id="user-b", admin_user_id="admin",
        user_message="O que leu nos livros?"
    )
    assert (other_text, other_refs) == ("", ())

    assembler = CognitiveContextAssembler(CognitiveContextScope("instance-a", "admin", "rel-a"))
    assert assembler.add(CognitiveContextContribution(
        domain="private_source_recall", content=private_text,
        agent_instance="instance-a", origin_class="relation_private",
        origin_relation_id="rel-a", source_refs=refs,
    ))
    other_assembler = CognitiveContextAssembler(CognitiveContextScope("instance-a", "user-b", "rel-b"))
    assert not other_assembler.add(CognitiveContextContribution(
        domain="private_source_recall", content=private_text,
        agent_instance="instance-a", origin_class="relation_private",
        origin_relation_id="rel-a", source_refs=refs,
    ))

    assert admin_reading_awareness(
        db, agent_instance="other-instance", user_id="admin",
        admin_user_id="admin", user_message="Que livros leu?"
    ) == ("", ())


def test_fallback_reading_reports_pages_without_claiming_understanding():
    db = _DB()
    db.conn.executescript(
        """
        CREATE TABLE work_projects (id INTEGER, name TEXT);
        CREATE TABLE work_artifacts (
            id INTEGER, project_id INTEGER, provider_payload_json TEXT,
            status TEXT, content_type TEXT, updated_at TEXT
        );
        INSERT INTO work_projects VALUES (10, 'Livro em processamento');
        """
    )
    payload = {"package": {
        "generation_mode": "reading_assimilation",
        "reading_assimilation": {
            "verified": True, "source_mode": "stored_pdf", "source_hash": "sha256",
            "start_page": 21, "end_page": 30, "assimilation_mode": "extractive_fallback",
            "summary": "trecho que ainda nao foi sintetizado",
        },
    }}
    db.conn.execute(
        "INSERT INTO work_artifacts VALUES (1, 10, ?, 'assimilated', 'reading_note', datetime('now'))",
        (json.dumps(payload),),
    )
    text, _ = admin_reading_awareness(
        db, agent_instance=AGENT_INSTANCE, user_id="admin",
        admin_user_id="admin", user_message="Como estao as leituras?"
    )
    assert "paginas 21-30 verificadas" in text
    assert "sintese conceitual nao validada" in text
    assert "trecho que ainda nao foi sintetizado" not in text
