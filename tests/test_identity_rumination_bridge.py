from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from identity_rumination_bridge import IdentityRuminationBridge
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE
from rumination_config import MAX_OPEN_TENSIONS_PER_USER


class BridgeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE agent_identity_contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT,
                pole_a TEXT,
                pole_b TEXT,
                contradiction_type TEXT,
                tension_level REAL,
                status TEXT,
                fed_to_rumination INTEGER DEFAULT 0,
                first_detected_at TEXT,
                last_activated_at TEXT
            );

            CREATE TABLE rumination_tensions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                agent_instance TEXT,
                relation_id TEXT,
                pole_a_content TEXT,
                pole_b_content TEXT,
                tension_type TEXT,
                intensity REAL,
                status TEXT,
                maturity_score REAL
            );
            """
        )


def insert_contradiction(db, *, days_ago=30):
    timestamp = (datetime.now() - timedelta(days=days_ago)).isoformat()
    cursor = db.conn.execute(
        """
        INSERT INTO agent_identity_contradictions (
            agent_instance, pole_a, pole_b, contradiction_type,
            tension_level, status, fed_to_rumination,
            first_detected_at, last_activated_at
        ) VALUES (?, 'autonomia', 'vinculo', 'autonomia_vinculo',
                  0.9, 'unresolved', 0, ?, ?)
        """,
        (AGENT_INSTANCE, timestamp, timestamp),
    )
    db.conn.commit()
    return cursor.lastrowid


def test_old_contradiction_remains_eligible_and_is_scoped():
    db = BridgeDB()
    contradiction_id = insert_contradiction(db, days_ago=30)

    fed = IdentityRuminationBridge(db).feed_contradictions_to_rumination()

    assert fed == 1
    row = db.conn.execute(
        "SELECT user_id, agent_instance, relation_id, status FROM rumination_tensions"
    ).fetchone()
    assert dict(row) == {
        "user_id": ADMIN_USER_ID,
        "agent_instance": AGENT_INSTANCE,
        "relation_id": None,
        "status": "open",
    }
    assert db.conn.execute(
        "SELECT fed_to_rumination FROM agent_identity_contradictions WHERE id = ?",
        (contradiction_id,),
    ).fetchone()[0] == 1


def test_archived_equivalent_reconciles_without_duplicate():
    db = BridgeDB()
    contradiction_id = insert_contradiction(db)
    db.conn.execute(
        """
        INSERT INTO rumination_tensions (
            user_id, agent_instance, relation_id, pole_a_content, pole_b_content,
            tension_type, intensity, status, maturity_score
        ) VALUES (?, ?, NULL, 'autonomia', 'vinculo',
                  'autonomia_vinculo', 0.9, 'archived', 0.8)
        """,
        (ADMIN_USER_ID, AGENT_INSTANCE),
    )
    db.conn.commit()

    fed = IdentityRuminationBridge(db).feed_contradictions_to_rumination()

    assert fed == 1
    assert db.conn.execute("SELECT COUNT(*) FROM rumination_tensions").fetchone()[0] == 1
    assert db.conn.execute(
        "SELECT fed_to_rumination FROM agent_identity_contradictions WHERE id = ?",
        (contradiction_id,),
    ).fetchone()[0] == 1


def test_bridge_respects_active_tension_capacity():
    db = BridgeDB()
    contradiction_id = insert_contradiction(db)
    db.conn.executemany(
        """
        INSERT INTO rumination_tensions (
            user_id, agent_instance, relation_id, pole_a_content, pole_b_content,
            tension_type, intensity, status, maturity_score
        ) VALUES (?, ?, NULL, ?, ?, 'conflict', 0.7, 'maturing', 0.3)
        """,
        [
            (ADMIN_USER_ID, AGENT_INSTANCE, f"a-{index}", f"b-{index}")
            for index in range(MAX_OPEN_TENSIONS_PER_USER)
        ],
    )
    db.conn.commit()

    fed = IdentityRuminationBridge(db).feed_contradictions_to_rumination()

    assert fed == 0
    assert db.conn.execute("SELECT COUNT(*) FROM rumination_tensions").fetchone()[0] == MAX_OPEN_TENSIONS_PER_USER
    assert db.conn.execute(
        "SELECT fed_to_rumination FROM agent_identity_contradictions WHERE id = ?",
        (contradiction_id,),
    ).fetchone()[0] == 0
