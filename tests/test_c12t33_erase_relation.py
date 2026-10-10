"""T3-3 — expurgo verificável por Relation fora do Work (C12/T3-3).

Cobertura da auditoria (docs/auditoria_c12_fechamento.md, T3-3): só
existia expurgo do domínio Work (``work/retention.py``) e deleção por
usuário (``core/db/users.py``); as colunas de Relation já migraram para
famílias de tabela fora do Work, mas faltava rotina ``purge``/``verify``
/auditoria espelhando o expurgo do Work.

Regras espelhadas de ``work/retention.py``:
- endereçar **o que está carimbado** (``relation_id = ?``): NULL nunca é
  alcançado sem inferência (fail-closed);
- redigir campo a campo com substituto NOT NULL-aware (``''``/``NULL``);
- verificação campo a campo (sobreviventes derrubam o resultado);
- evento de auditoria imune a expurgos futuros (tabela fora do mapa);
- nenhuma apagação por ``user_id`` — só Relation explícita.
"""
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub


def _schema_db():
    import importlib
    import inspect

    from core.db.relations import RelationsDatabaseMixin
    from core.db.schema import SchemaDatabaseMixin
    from engines.will_scope import WillScopeDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(
        RelationsDatabaseMixin,
        WillScopeDatabaseMixin,
        SchemaDatabaseMixin,
        WorkTenancyDatabaseMixin,
    ):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            self._init_sqlite_schema()

    db = _RealSchema()
    # Inventario completo (Revisao P1 da PR #59): todos os inits que
    # criam tabelas com coluna de Relation fora do Work.
    for module_name in (
        "core.db.working_memory",
        "engines.will_expression",
        "core.db.relational_state",
        "core.db.essays",
        "core.db.meta_cognition",
        "core.db.integrative_self",
        "core.db.theory_of_mind",
        "core.db.symbolic_graph",
        "core.db.availability",
        "engines.will_phase_arbitration",
    ):
        module = importlib.import_module(module_name)
        for candidate in vars(module).values():
            if not inspect.isclass(candidate):
                continue
            for method_name in dir(candidate):
                if (
                    method_name.startswith("_init_")
                    and method_name.endswith("_schema")
                    and not hasattr(db, method_name)
                ):
                    method = getattr(candidate, method_name)
                    if callable(method):
                        method(db)
    import jung_rumination

    engine_cls = [
        value
        for value in vars(jung_rumination).values()
        if inspect.isclass(value)
        and "Rumination" in getattr(value, "__name__", "")
    ][0]
    engine_cls(db)
    return db


def _seed(db, *, relation_id, user_id="u1", titulo="obra"):
    """Semeia conteudo representativo em tres familias fora do Work."""
    db.conn.execute(
        "INSERT INTO conversations (user_id, user_name, user_input, ai_response,"
        " relation_id, agent_instance) VALUES (?, 'N', ?, ?, ?, 'jung_v1')",
        (user_id, f"entrada {titulo}", f"resposta {titulo}", relation_id),
    )
    db.conn.execute(
        "INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value,"
        " relation_id, agent_instance) VALUES (?, 'pref', 'k', ?, ?, 'jung_v1')",
        (user_id, f"fato {titulo}", relation_id),
    )
    db.conn.execute(
        "INSERT INTO agent_hobby_artifacts (user_id, title, summary,"
        " relation_id, agent_instance, scope_kind) VALUES (?, ?, ?, ?, 'jung_v1', 'relation')",
        (user_id, titulo, f"resumo {titulo}", relation_id),
    )
    db.conn.commit()


def test_expurga_relation_e_preserva_as_demas_e_a_quarentena():
    """So o que esta carimbado com a Relation alvo e redigido: a Relation
    vizinha e a quarentena legada (NULL) ficam intactas — o enderecamento
    e `relation_id = ?`, nunca inferencia por user_id."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A", titulo="A")
    _seed(db, relation_id="rel-B", titulo="B")
    _seed(db, relation_id=None, titulo="legado")

    counts = erase_relation(db.conn, "rel-A")
    assert counts.get("conversations", 0) >= 2, "user_input + ai_response"
    assert counts.get("user_facts", 0) >= 1
    assert counts.get("agent_hobby_artifacts", 0) >= 2, "title + summary"

    assert verify_relation_erase(db.conn, "rel-A") == {}, "A fica limpa"

    row_b = db.conn.execute(
        "SELECT user_input, ai_response FROM conversations WHERE relation_id = 'rel-B'"
    ).fetchone()
    assert "entrada B" in row_b["user_input"], "Relation vizinha intacta"
    row_null = db.conn.execute(
        "SELECT user_input FROM conversations WHERE relation_id IS NULL"
    ).fetchone()
    assert "entrada legado" in row_null["user_input"], (
        "quarentena NULL nao e alcançavel sem inferencia (fail-closed)"
    )
    assert verify_relation_erase(db.conn, "rel-B") != {}, (
        "a vizinha tem conteudo (reporta sobreviventes, nao apagados)"
    )


def test_nao_excede_os_limites_do_user_id():
    """Mesmo user_id em duas Relations: apagar rel-A nao toca rel-B e
    deletar por user_id nunca entra no caminho do expurgo."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A", user_id="mesmo-user", titulo="A")
    _seed(db, relation_id="rel-B", user_id="mesmo-user", titulo="B")

    erase_relation(db.conn, "rel-A")
    surviving = verify_relation_erase(db.conn, "rel-B")
    assert surviving, "rel-B segue com conteudo proprio"
    intact = db.conn.execute(
        "SELECT user_input FROM conversations"
        " WHERE relation_id = 'rel-B' AND user_id = 'mesmo-user'"
    ).fetchone()
    assert "entrada B" in intact["user_input"]


def test_colunas_not_null_sao_redigidas_para_string_vazia():
    """user_facts.fact_value e NOT NULL: substituto NULL daria
    IntegrityError (P1 do PR #48 no Work) — espelha _replacement_for."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    _seed(db, relation_id="rel-A")
    counts = erase_relation(db.conn, "rel-A")
    assert counts["user_facts"] >= 3, "fact_category/fact_key/fact_value"
    fact = db.conn.execute(
        "SELECT fact_value, fact_key FROM user_facts WHERE relation_id = 'rel-A'"
    ).fetchone()
    assert fact["fact_value"] == "" and fact["fact_key"] == ""
    assert verify_relation_erase(db.conn, "rel-A") == {}


def test_auditoria_registrada_e_imune_a_expurgos_futuros():
    """Cada expurgo grava um evento em relation_erase_events — a tabela
    fica FORA do mapa, entao expurgos posteriores nunca a tocam (mesma
    imunidade do evento do Work)."""
    from core.db.erase import erase_relation

    db = _schema_db()
    _seed(db, relation_id="rel-A")
    erase_relation(db.conn, "rel-A")
    erase_relation(db.conn, "rel-A")  # idempotente: sem conteudo a redigir

    rows = db.conn.execute(
        "SELECT relation_id, event_key, counts_json FROM relation_erase_events"
        " WHERE relation_id = 'rel-A' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2, "um evento por execucao do expurgo"
    assert rows[0]["event_key"].startswith("erase_relation:rel-A:")
    assert "conversations" in rows[0]["counts_json"]
    assert rows[1]["counts_json"] == "{}", "segunda execucao: nada a redigir"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM relation_erase_events WHERE relation_id = 'rel-A'"
    ).fetchone()[0] == 2, "auditoria sobrevive aos proprios expurgos"


def test_exige_relation_e_pula_tabelas_ausentes():
    """relation_id obrigatorio (sem inferencia); tabelas fora do init
    padrao (rumination_*, relational_state) sao ignoradas com o schema
    que nao as criou — pragma-aware como o espelho."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    db.conn.execute("DROP TABLE rumination_fragments")
    db.conn.execute("DROP TABLE relational_state")
    with pytest.raises(ValueError, match="relation_id"):
        erase_relation(db.conn, "   ")
    with pytest.raises(ValueError, match="relation_id"):
        verify_relation_erase(db.conn, "")

    _seed(db, relation_id="rel-A")
    counts = erase_relation(db.conn, "rel-A")
    assert "rumination_fragments" not in counts, "tabela inexistente pulada"
    assert "relational_state" not in counts, "tabela inexistente pulada"


def test_p1_revisao_redige_sobreviventes_de_campos_e_tabelas():
    """Revisao P1 da PR #59 (Rodada 1): campos de conteudo sobreviviam
    em tabelas ja contempladas (synthesis das tensao, scores do UNESCO)
    e familias inteiras ficavam fora do mapa (working_memory_items,
    will_expressions) — o verificador declarava clean mesmo assim."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    db.conn.execute(
        "INSERT INTO rumination_tensions"
        " (user_id, relation_id, tension_type, pole_a_content, pole_b_content,"
        " intensity, maturity_score, synthesis_symbol, synthesis_question)"
        " VALUES ('u1', 'rel-P1', 'synthesis', 'pole A', 'pole B',"
        " 0.9, 0.8, 'SIMBOLO', 'PERGUNTA')"
    )
    db.conn.execute(
        "INSERT INTO unesco_pilot_data"
        " (user_id, origin_relation_id, baseline_stress_score,"
        " post_test_stress_score, dossier_accuracy_rating)"
        " VALUES ('u1', 'rel-P1', 8, 6, 4)"
    )
    db.conn.execute(
        "INSERT INTO working_memory_items"
        " (agent_instance, phase, item_type, title, summary, ownership_class,"
        " relation_id, source_refs_json, created_at, updated_at)"
        " VALUES ('jung_v1', 'hmm', 'nota', 'titulo privado', 'resumo privado',"
        " 'relation_private', 'rel-P1', '{}', '2026-10-09', '2026-10-09')"
    )
    db.conn.execute(
        "INSERT INTO will_expressions"
        " (agent_instance, relation_id, user_id, cycle_id, will_name,"
        " capability_key, gate_level, cost_class, idempotency_key,"
        " reason, intent_json, prepared_payload_json)"
        " VALUES ('jung_v1', 'rel-P1', 'u1', 'c1', 'expressar', 'msg.send',"
        " 'L1', 'low', 'idem-1', 'motivo privado', '{\"i\": 1}',"
        " '{\"mensagem completa\": \"ola\"}')"
    )
    db.conn.commit()

    erase_relation(db.conn, "rel-P1")

    tension = db.conn.execute(
        "SELECT intensity, maturity_score, synthesis_symbol,"
        " synthesis_question FROM rumination_tensions WHERE relation_id='rel-P1'"
    ).fetchone()
    assert tension["synthesis_symbol"] is None, "synthesis_symbol sobreviveu"
    assert tension["synthesis_question"] is None, "synthesis_question sobreviveu"
    assert tension["intensity"] is None and tension["maturity_score"] is None

    unesco = db.conn.execute(
        "SELECT baseline_stress_score, post_test_stress_score,"
        " dossier_accuracy_rating FROM unesco_pilot_data"
        " WHERE origin_relation_id='rel-P1'"
    ).fetchone()
    assert unesco["baseline_stress_score"] is None, "score de estresse sobreviveu"
    assert unesco["post_test_stress_score"] is None
    assert unesco["dossier_accuracy_rating"] is None

    wm = db.conn.execute(
        "SELECT title, summary FROM working_memory_items"
        " WHERE relation_id='rel-P1'"
    ).fetchone()
    assert wm["title"] == "" and wm["summary"] == "", "wm privada sobreviveu"

    will = db.conn.execute(
        "SELECT reason, intent_json, prepared_payload_json"
        " FROM will_expressions WHERE relation_id='rel-P1'"
    ).fetchone()
    assert will["reason"] is None, "reason nullable: sobreviveu como NULL"
    assert will["intent_json"] == ""
    assert will["prepared_payload_json"] == "", "payload preparado sobreviveu"

    assert verify_relation_erase(db.conn, "rel-P1") == {}


def test_cobertura_completa_do_inventario_de_relation():
    """Inventario verificavel (Revisao P1 da PR #59): TODA coluna de
    TODA tabela com Relation (fora do Work) tem decidao explicita —
    ou e conteudo (redigido: marcador some) ou e audit-safe (padrao
    ``preserved_field``). Coluna sem decisao falha o teste listando-a."""
    from core.db.erase import (
        _ERASE_FIELDS,
        _ERASE_CHILD_FIELDS,
        _ERASE_VIA_PARENT,
        erase_relation,
        preserved_field,
        verify_relation_erase,
    )

    db = _schema_db()
    cur = db.conn.cursor()
    marcador = "CONTEUDO-PRIVADO-XYZ"
    alvo = "rel-COV"
    tabelas = []
    nomes = [
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    for table in nomes:
        if table.startswith("work_") or table in (
            "agent_relations",
            "relation_erase_events",
        ):
            continue
        columns = [row[1] for row in cur.execute(f"PRAGMA table_info({table})")]
        if not set(columns) & {"relation_id", "origin_relation_id"}:
            continue
        mapped = set(_ERASE_FIELDS.get(table, []))
        undecided = [
            col
            for col in columns
            if col not in mapped and not preserved_field(col)
        ]
        assert not undecided, f"{table}: colunas sem decisao: {undecided}"
        assert not (mapped - set(columns)), (
            f"{table}: mapa aponta coluna inexistente: {mapped - set(columns)}"
        )
        tabelas.append((table, columns, mapped))
    assert len(tabelas) >= 30, "inventario incompleto no schema da fixture"

    # linha-marcador em cada tabela: marcador em todo campo nao-chave.
    for table, columns, _ in tabelas:
        insert_columns = [col for col in columns if col != "id"]
        values = [
            alvo
            if col in ("relation_id", "origin_relation_id")
            else marcador
            for col in insert_columns
        ]
        placeholders = ",".join("?" * len(insert_columns))
        cur.execute(
            f"INSERT INTO {table} ({', '.join(insert_columns)})"
            f" VALUES ({placeholders})",
            values,
        )
    db.conn.commit()

    # vinculos indiretos: decisao de coluna + linha-marcador via pai.
    filho_campos: dict = {}
    for child, spec in _ERASE_VIA_PARENT.items():
        child_columns = [
            row[1] for row in cur.execute(f"PRAGMA table_info({child})")
        ]
        if not child_columns:
            continue
        child_mapped = set(_ERASE_CHILD_FIELDS.get(child, []))
        undecided = [
            col
            for col in child_columns
            if col not in child_mapped and not preserved_field(col)
        ]
        assert not undecided, f"{child}: colunas sem decisao: {undecided}"
        assert not (child_mapped - set(child_columns)), (
            f"{child}: campos inexistentes: {child_mapped - set(child_columns)}"
        )
        filho_campos[child] = (spec, child_columns, child_mapped)

    goal_ids = [
        row[0]
        for row in cur.execute(
            "SELECT id FROM goal_threads WHERE relation_id = ?", (alvo,)
        ).fetchall()
    ]
    will_ids = [
        row[0]
        for row in cur.execute(
            "SELECT id FROM will_expressions WHERE relation_id = ?", (alvo,)
        ).fetchall()
    ]
    assert goal_ids and will_ids, "pais de referencia nao criados"
    if "goal_steps" in filho_campos:
        cur.execute(
            "INSERT INTO goal_steps (goal_id, status, step_order, title,"
            " expected_evidence, result_summary, created_at)"
            " VALUES (?, 'pending', 1, ?, ?, ?, '2026-10-09')",
            (goal_ids[0], marcador, marcador, marcador),
        )
    if "will_expression_receipts" in filho_campos:
        cur.execute(
            "INSERT INTO will_expression_receipts"
            " (expression_id, status, result_code, summary, evidence_json)"
            " VALUES (?, 'done', 'ok', ?, ?)",
            (will_ids[0], marcador, marcador),
        )
    if "will_proactive_effects" in filho_campos:
        cur.execute(
            "INSERT INTO will_proactive_effects"
            " (expression_id, effect, status)"
            " VALUES (?, ?, 'pending')",
            (will_ids[0], marcador),
        )
    db.conn.commit()

    erase_relation(db.conn, alvo)

    sobreviventes = []
    for table, columns, mapped in tabelas:
        for col in sorted(mapped):
            row = cur.execute(
                f"SELECT count(*) FROM {table} WHERE {col} = ?",
                (marcador,),
            ).fetchone()
            if row[0]:
                sobreviventes.append(f"{table}.{col}")
    assert not sobreviventes, (
        f"conteudo privado sobreviveu ao expurgo: {sobreviventes}"
    )
    sobreviventes_filhos = []
    for child, (spec, _, child_mapped) in filho_campos.items():
        parent = spec["parent"]
        via = spec["via"]
        for col in sorted(child_mapped):
            row = cur.execute(
                f"SELECT count(*) FROM {child} WHERE {via} IN ("
                f"SELECT id FROM {parent} WHERE relation_id = ?)"
                f" AND {col} = ?",
                (alvo, marcador),
            ).fetchone()
            if row[0]:
                sobreviventes_filhos.append(f"{child}.{col}")
    assert not sobreviventes_filhos, (
        f"conteudo privado sobreviveu via vinculo indireto: "
        f"{sobreviventes_filhos}"
    )
    assert verify_relation_erase(db.conn, alvo) == {}


def test_p1_triplas_com_predicados_distintos_nao_quebram():
    """Revisao r2 da PR #59 (Rodada 2): triplas com mesmo sujeito/objeto/
    fonte e predicados diferentes sao validas (UNIQUE inclui predicate).
    Redigir ambos a '' violava o UNIQUE com IntegrityError — agora a
    linha da Relation e apagada, sem tocar em outras Relations."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()

    def _triple(rel, pred, subject, source):
        db.conn.execute(
            "INSERT INTO symbolic_triples"
            " (agent_instance, subject_id, predicate, object_id, source_ref,"
            " ownership_class, origin_class, origin_relation_id,"
            " source_refs_json, provenance_json)"
            " VALUES ('jung_v1', ?, ?, ?, ?, 'relation_private',"
            " 'Relation', ?, '{}', '{}')",
            (subject, pred, subject + 10, source, rel),
        )

    _triple("rel-A", "significa", 10, "ref")
    _triple("rel-A", "relaciona", 10, "ref")
    _triple("rel-B", "significa", 50, "ref-B")
    db.conn.commit()

    counts = erase_relation(db.conn, "rel-A")

    assert counts.get("symbolic_triples") == 2, "as duas triplas de A apagadas"
    restantes = db.conn.execute(
        "SELECT origin_relation_id, predicate FROM symbolic_triples"
        " ORDER BY origin_relation_id"
    ).fetchall()
    assert len(restantes) == 1, "somente a tripla da Relation vizinha resta"
    assert restantes[0]["origin_relation_id"] == "rel-B"
    assert restantes[0]["predicate"] == "significa", "conteudo de B intacto"
    assert verify_relation_erase(db.conn, "rel-A") == {}


def test_p1_filhas_indiretas_sao_alcancadas_pelo_pai():
    """Revisao r2 da PR #59 (Rodada 2): goal_steps (filha de goal_threads)
    e receipts/effects (filhas de will_expressions) nao tem coluna de
    Relation — o conteudo privado delas sobrevivia com clean=True. Agora
    sao alcancadas pelo vinculo com o pai carimbado."""
    from core.db.erase import erase_relation, verify_relation_erase

    db = _schema_db()
    db.conn.execute(
        "INSERT INTO goal_threads"
        " (agent_instance, status, title, objective, source_refs_json,"
        " ownership_class, relation_id, created_at, updated_at)"
        " VALUES ('jung_v1', 'active', 'objetivo privado A', 'meta A', '{}',"
        " 'relation_private', 'rel-A', '2026-10-09', '2026-10-09')"
    )
    goal_a = db.conn.execute("SELECT id FROM goal_threads").fetchone()[0]
    db.conn.execute(
        "INSERT INTO goal_steps (goal_id, status, step_order, title,"
        " expected_evidence, result_summary, created_at)"
        " VALUES (?, 'done', 1, 'passo privado', 'evidencia privada',"
        " 'resultado privado', '2026-10-09')",
        (goal_a,),
    )
    db.conn.execute(
        "INSERT INTO goal_threads"
        " (agent_instance, status, title, objective, source_refs_json,"
        " ownership_class, relation_id, created_at, updated_at)"
        " VALUES ('jung_v1', 'active', 'objetivo privado B', 'meta B', '{}',"
        " 'relation_private', 'rel-B', '2026-10-09', '2026-10-09')"
    )
    goal_b = db.conn.execute(
        "SELECT id FROM goal_threads WHERE relation_id='rel-B'"
    ).fetchone()[0]
    db.conn.execute(
        "INSERT INTO goal_steps (goal_id, status, step_order, title,"
        " created_at)"
        " VALUES (?, 'pending', 1, 'passo privado B', '2026-10-09')",
        (goal_b,),
    )
    db.conn.execute(
        "INSERT INTO will_expressions"
        " (agent_instance, relation_id, user_id, cycle_id, will_name,"
        " capability_key, gate_level, cost_class, idempotency_key,"
        " intent_json, prepared_payload_json)"
        " VALUES ('jung_v1', 'rel-A', 'u1', 'c1', 'expressar', 'msg.send',"
        " 'L1', 'low', 'idem-filho', '{}', '{}')"
    )
    will_a = db.conn.execute(
        "SELECT id FROM will_expressions WHERE relation_id='rel-A'"
    ).fetchone()[0]
    db.conn.execute(
        "INSERT INTO will_expression_receipts"
        " (expression_id, status, summary, evidence_json)"
        " VALUES (?, 'done', 'resumo privado', '{\"e\": 1}')",
        (will_a,),
    )
    db.conn.execute(
        "INSERT INTO will_proactive_effects (expression_id, effect, status)"
        " VALUES (?, 'efeito privado', 'pending')",
        (will_a,),
    )
    db.conn.commit()

    counts = erase_relation(db.conn, "rel-A")

    step_a = db.conn.execute(
        "SELECT title, expected_evidence, result_summary FROM goal_steps"
        " WHERE goal_id = ?",
        (goal_a,),
    ).fetchone()
    assert step_a["title"] == "", "titulo do passo privado sobreviveu"
    assert step_a["expected_evidence"] is None
    assert step_a["result_summary"] is None
    assert counts.get("goal_steps", 0) >= 3

    receipt = db.conn.execute(
        "SELECT summary, evidence_json FROM will_expression_receipts"
        " WHERE expression_id = ?",
        (will_a,),
    ).fetchone()
    assert receipt["summary"] is None
    assert receipt["evidence_json"] == ""

    effect = db.conn.execute(
        "SELECT effect FROM will_proactive_effects WHERE expression_id = ?",
        (will_a,),
    ).fetchone()
    assert effect["effect"] == "efeito privado", (
        "classificador operacional fixo: preservado"
    )

    step_b = db.conn.execute(
        "SELECT title, status FROM goal_steps WHERE goal_id = ?",
        (goal_b,),
    ).fetchone()
    assert step_b["title"] == "passo privado B", "filha de B intacta"
    assert step_b["status"] == "pending"
    assert verify_relation_erase(db.conn, "rel-A") == {}


def test_p1_quatro_efeitos_reais_nao_colidem_e_isolam_relations():
    """Revisao r3 da PR #59 (Rodada 4): `effect` integra a PK
    (expression_id, effect) — redigir os quatro efeitos reais a ''
    causava IntegrityError e interrompia o expurgo. `effect` e
    classificador operacional fixo (development/facts/session_log/
    semantic_memory): audit-safe e preservado. Teste cobre os 4 efeitos
    reais e o isolamento de outra Relation."""
    from core.db.erase import erase_relation, verify_relation_erase
    from engines.will_proactive_record import EFFECTS

    assert len(EFFECTS) == 4, "os quatro efeitos reais do motor"
    db = _schema_db()
    for rel, chave in (("rel-A", "efeitos-a"), ("rel-B", "efeitos-b")):
        db.conn.execute(
            "INSERT INTO will_expressions"
            " (agent_instance, relation_id, user_id, cycle_id, will_name,"
            " capability_key, gate_level, cost_class, idempotency_key,"
            " intent_json, prepared_payload_json)"
            " VALUES ('jung_v1', ?, 'u1', 'c1', 'expressar', 'msg.send',"
            " 'L1', 'low', ?, '{}', '{}')",
            (rel, chave),
        )
        expr_id = db.conn.execute(
            "SELECT id FROM will_expressions WHERE idempotency_key = ?",
            (chave,),
        ).fetchone()[0]
        for effect in EFFECTS:
            db.conn.execute(
                "INSERT INTO will_proactive_effects"
                " (expression_id, effect, status)"
                " VALUES (?, ?, 'pending')",
                (expr_id, effect),
            )
    db.conn.commit()

    counts = erase_relation(db.conn, "rel-A")

    assert counts.get("will_proactive_effects") is None, (
        "classificadores operacionais nao sao conteudo redigido"
    )
    expr_a = db.conn.execute(
        "SELECT id FROM will_expressions WHERE idempotency_key='efeitos-a'"
    ).fetchone()[0]
    effects_a = [
        row["effect"]
        for row in db.conn.execute(
            "SELECT effect FROM will_proactive_effects"
            " WHERE expression_id = ? ORDER BY effect",
            (expr_a,),
        )
    ]
    assert effects_a == sorted(EFFECTS), (
        "os 4 classificadores de A intactos, sem IntegrityError"
    )
    expr_b = db.conn.execute(
        "SELECT id FROM will_expressions WHERE idempotency_key='efeitos-b'"
    ).fetchone()[0]
    effects_b = [
        row["effect"]
        for row in db.conn.execute(
            "SELECT effect FROM will_proactive_effects"
            " WHERE expression_id = ? ORDER BY effect",
            (expr_b,),
        )
    ]
    assert effects_b == sorted(EFFECTS), "Relation B isolada e intacta"
    assert verify_relation_erase(db.conn, "rel-A") == {}
