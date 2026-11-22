"""C12g PR-B — tratamento gracioso de revogação na camada de bot.

Sob sentinela de escopo/consentimento de Relation (Relation revogada,
consentimento retirado, arquivo privado fora de escopo) os handlers do
Telegram devem responder com a mensagem canônica de vínculo encerrado —
nunca vazar a exceção sem resposta nem cair em erro genérico — e a
classificação canônica deve distinguir sentinela de escopo de erro de
aplicação.

Sentinelas preservados: ``relation_not_eligible:status=...,consent=...``,
``consent_gate_unavailable_for_relation_scope``,
``relation_scope_required_for_*``, ``relation_required_for_*``,
``relation_file_scope_required`` / ``relation_file_access_revoked`` /
``relation_file_scope_unavailable``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Ambiente mínimo antes de importar telegram_bot (token exigido no import).
# ---------------------------------------------------------------------------
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-not-a-real-secret")


# Stubs de atributos: o conftest cria os módulos ``telegram``/``telegram.ext``
# vazios; o import de telegram_bot exige estes atributos.
def _ensure_attr(module_name: str, attr: str, value) -> None:
    mod = sys.modules.get(module_name)
    if mod is None:
        mod = types.ModuleType(module_name)
        sys.modules[module_name] = mod
    if not hasattr(mod, attr):
        setattr(mod, attr, value)


_ensure_attr("telegram", "Update", type("Update", (), {}))
_ensure_attr("telegram", "BotCommand", type("BotCommand", (), {}))
for _name, _value in {
    "Application": type("Application", (), {}),
    "CommandHandler": type("CommandHandler", (), {}),
    "MessageHandler": type("MessageHandler", (), {}),
    "ContextTypes": types.SimpleNamespace(DEFAULT_TYPE=object()),
    "filters": types.SimpleNamespace(VOICE=object(), AUDIO=object()),
}.items():
    _ensure_attr("telegram.ext", _name, _value)

class _StubClient:
    """Cliente stub que aceita qualquer argumento de construtor."""

    def __init__(self, *args, **kwargs):
        pass


_MISSING = object()
_LLM_CLIENT_ATTRS = (("openai", "OpenAI"), ("anthropic", "Anthropic"))


@contextlib.contextmanager
def _llm_client_stubs(attrs=_LLM_CLIENT_ATTRS):
    """Instala stubs de cliente de LLM e restaura o estado anterior na saida.

    A construcao de ``JungianEngine`` no import de ``telegram_bot`` instancia
    os clientes de LLM com argumentos, entao o atributo precisa aceitar kwargs
    — inclusive quando outro teste o deixou ``object``. Os atributos trocados
    (e os modulos criados quando ausentes) sao restaurados/removidos ao final,
    para que a coleta deste arquivo nao altere o comportamento dos demais
    testes da suite.
    """
    saved_attrs = []
    created_modules = []
    try:
        for mod_name, attr in attrs:
            mod = sys.modules.get(mod_name)
            if mod is None:
                mod = types.ModuleType(mod_name)
                sys.modules[mod_name] = mod
                created_modules.append(mod_name)
            saved_attrs.append((mod, attr, getattr(mod, attr, _MISSING)))
            setattr(mod, attr, _StubClient)
        yield
    finally:
        for mod, attr, previous in reversed(saved_attrs):
            if previous is _MISSING:
                delattr(mod, attr)
            else:
                setattr(mod, attr, previous)
        for mod_name in created_modules:
            sys.modules.pop(mod_name, None)


with _llm_client_stubs():
    import telegram_bot

from core.db.relations import is_relation_scope_sentinel


REVOKED_SENTINEL = "relation_not_eligible:status=revoked,consent=granted"

# ---------------------------------------------------------------------------
# Fakes de Update/Context do python-telegram-bot
# ---------------------------------------------------------------------------


class FakeChat:
    def __init__(self):
        self.actions = []

    async def send_action(self, action=None, **kwargs):
        self.actions.append(action)


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.caption = None
        self.voice = None
        self.audio = None
        self.message_id = 1
        self.chat = FakeChat()
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def reply_document(self, *args, **kwargs):
        self.replies.append("<document>")


class FakeUser:
    def __init__(self, telegram_id=555001, first_name="Admin"):
        self.id = telegram_id
        self.first_name = first_name
        self.username = "admin"


class FakeUpdate:
    def __init__(self, text=""):
        self.effective_user = FakeUser()
        self.message = FakeMessage(text)
        self.effective_message = self.message


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.bot_data = {}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Classificador canônico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [
        "relation_not_eligible:status=revoked,consent=granted",
        "relation_not_eligible_for_consolidation:status=paused,consent=granted",
        "consent_gate_unavailable_for_relation_scope",
        "relation_scope_required_for_production",
        "relation_scope_required_for_conversation",
        "relation_scope_required_for_pattern",
        "relation_scope_required_for_fact",
        "relation_scope_required_for_semantic_memory",
        "relation_required_for_identity",
        "relation_required_for_prompt_context",
        "relation_file_scope_required",
        "relation_file_access_revoked",
        "relation_file_scope_unavailable",
        "relation_scope_unavailable_for_knowledge_unit",
    ],
)
def test_scope_sentinel_families_are_classified(sentinel):
    assert is_relation_scope_sentinel(ValueError(sentinel))
    assert is_relation_scope_sentinel(sentinel)


@pytest.mark.parametrize(
    "text",
    [
        "question_state_unknown:x",
        "question_state_reason_required",
        "invalid_participant_file_scope",
        "relation_participant_mismatch",
        "boom",
        "",
    ],
)
def test_application_errors_are_not_scope_sentinels(text):
    assert not is_relation_scope_sentinel(ValueError(text))
    assert not is_relation_scope_sentinel(text)


# ---------------------------------------------------------------------------
# Decorator gracioso do bot
# ---------------------------------------------------------------------------


def test_decorator_replies_gracefully_on_scope_sentinel():
    update = FakeUpdate()
    context = FakeContext()

    @telegram_bot._graceful_scope_errors
    async def handler(update, context):
        raise ValueError("relation_scope_required_for_fact")

    assert _run(handler(update, context)) is None
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_decorator_reraises_application_errors():
    update = FakeUpdate()
    context = FakeContext()

    @telegram_bot._graceful_scope_errors
    async def handler(update, context):
        raise ValueError("question_state_unknown:x")

    with pytest.raises(ValueError):
        _run(handler(update, context))
    assert update.message.replies == []


# ---------------------------------------------------------------------------
# Handlers sob Relation revogada
# ---------------------------------------------------------------------------


class _RevokedDB:
    """DB que recusa leituras de conversa com a sentinela canônica."""

    chroma_enabled = False

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE user_facts (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                is_current INTEGER DEFAULT 1
            );
            CREATE TABLE user_patterns (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                confidence_score REAL
            );
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY,
                user_id TEXT
            );
            CREATE TABLE archetype_conflicts (
                id INTEGER PRIMARY KEY,
                user_id TEXT
            );
            CREATE TABLE user_milestones (
                id INTEGER PRIMARY KEY,
                user_id TEXT
            );
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                registration_date TEXT
            );
            CREATE TABLE unesco_pilot_data (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                post_test_stress_score REAL,
                completed_at TEXT,
                safety_triggers_count INTEGER DEFAULT 0
            );
            CREATE TABLE user_daily_usage (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                date_str TEXT,
                message_count INTEGER DEFAULT 0
            );
            """
        )
        self.detect_calls = []
        self.vectorstore = None

    def get_user(self, user_id):
        return {"user_id": user_id, "total_sessions": 1}

    def get_user_stats(self, user_id):
        return {"total_messages": 3}

    def get_agent_state(self):
        return {
            "phase": 2,
            "self_awareness_score": 0.5,
            "moral_complexity_score": 0.5,
            "emotional_depth_score": 0.5,
            "autonomy_score": 0.5,
            "total_interactions": 7,
        }

    def get_user_conversations(self, user_id, limit=1000, **kwargs):
        raise ValueError(REVOKED_SENTINEL)

    def conversations_to_chat_history(self, conversations):
        return []

    def detect_and_save_patterns(self, user_id):
        self.detect_calls.append(user_id)
        raise ValueError(REVOKED_SENTINEL)


@pytest.fixture
def bot_env(monkeypatch):
    db = _RevokedDB()
    monkeypatch.setattr(telegram_bot, "ensure_user_in_database", lambda user: "user-1")
    monkeypatch.setattr(telegram_bot, "ADMIN_IDS", {555001})
    monkeypatch.setattr(telegram_bot.bot_state, "db", db)
    return db


def test_stats_command_replies_gracefully_when_relation_revoked(bot_env):
    update = FakeUpdate()
    _run(telegram_bot.stats_command(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_minha_jornada_replies_gracefully_when_relation_revoked(bot_env):
    update = FakeUpdate()
    _run(telegram_bot.minha_jornada_command(update, FakeContext()))
    assert update.message.replies[-1] == telegram_bot._SCOPE_REFUSAL_TEXT
    assert not any("Erro ao gerar análise" in reply for reply in update.message.replies)


def test_meu_perfil_replies_gracefully_on_revoked_file_access(bot_env, monkeypatch):
    import engines.participant_files as participant_files

    def _revoked(db, user_id, relation_id=None):
        raise ValueError("relation_file_access_revoked")

    monkeypatch.setattr(participant_files, "relation_file_scope", _revoked)
    update = FakeUpdate()
    _run(telegram_bot.meu_perfil_command(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_meu_perfil_keeps_profile_message_for_non_scope_errors(bot_env, monkeypatch):
    import engines.participant_files as participant_files

    def _invalid(db, user_id, relation_id=None):
        raise ValueError("invalid_participant_file_scope")

    monkeypatch.setattr(participant_files, "relation_file_scope", _invalid)
    update = FakeUpdate()
    _run(telegram_bot.meu_perfil_command(update, FakeContext()))
    assert update.message.replies == ["Perfil indisponivel para esta relacao."]


def test_start_command_scope_sentinel_is_graceful(bot_env, monkeypatch):
    def _boom(user):
        raise ValueError("consent_gate_unavailable_for_relation_scope")

    monkeypatch.setattr(telegram_bot, "ensure_user_in_database", _boom)
    update = FakeUpdate()
    _run(telegram_bot.start_command(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


# ---------------------------------------------------------------------------
# Detecção de padrões em background
# ---------------------------------------------------------------------------


def test_pattern_detection_is_quiet_on_scope_sentinel(bot_env, caplog):
    with caplog.at_level(logging.WARNING, logger="telegram_bot"):
        telegram_bot._detect_and_save_patterns_quietly("user-1")
    assert bot_env.detect_calls == ["user-1"]
    assert any(
        "Deteccao de padroes adiada" in record.getMessage() for record in caplog.records
    )


def test_pattern_detection_logs_other_failures(bot_env, monkeypatch, caplog):
    def _broken(user_id):
        raise RuntimeError("provider down")

    monkeypatch.setattr(bot_env, "detect_and_save_patterns", _broken)
    with caplog.at_level(logging.ERROR, logger="telegram_bot"):
        telegram_bot._detect_and_save_patterns_quietly("user-1")
    assert any(
        "Falha na deteccao de padroes" in record.getMessage() for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Isolamento dos stubs de cliente de LLM
# ---------------------------------------------------------------------------


def test_llm_client_stubs_restore_previous_attribute_state():
    fake_name = "c12g_fake_llm_present_module"
    fake_mod = types.ModuleType(fake_name)
    previous = object()
    fake_mod.OpenAI = previous
    sys.modules[fake_name] = fake_mod
    try:
        with _llm_client_stubs(((fake_name, "OpenAI"),)):
            assert fake_mod.OpenAI is _StubClient
        assert fake_mod.OpenAI is previous
    finally:
        sys.modules.pop(fake_name, None)


def test_llm_client_stubs_delete_attributes_they_added():
    fake_name = "c12g_fake_llm_attrless_module"
    fake_mod = types.ModuleType(fake_name)
    sys.modules[fake_name] = fake_mod
    try:
        with _llm_client_stubs(((fake_name, "OpenAI"),)):
            assert fake_mod.OpenAI is _StubClient
        assert not hasattr(fake_mod, "OpenAI")
    finally:
        sys.modules.pop(fake_name, None)


def test_llm_client_stubs_remove_modules_they_created():
    fake_name = "c12g_fake_llm_absent_module"
    sys.modules.pop(fake_name, None)
    with _llm_client_stubs(((fake_name, "Anthropic"),)):
        assert getattr(sys.modules[fake_name], "Anthropic") is _StubClient
    assert fake_name not in sys.modules


# ---------------------------------------------------------------------------
# Decorator gracioso: sentinela fora de ValueError
# ---------------------------------------------------------------------------


def test_decorator_replies_gracefully_on_non_value_error_sentinel():
    update = FakeUpdate()
    context = FakeContext()

    @telegram_bot._graceful_scope_errors
    async def handler(update, context):
        raise RuntimeError("relation_file_access_revoked")

    assert _run(handler(update, context)) is None
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


# ---------------------------------------------------------------------------
# handle_message: sentinela nunca e engolida pelos except internos
# ---------------------------------------------------------------------------


class _SQLFaultConn:
    """Conexao delegada que recusa SQLs contendo ``pattern`` com ``exc``.

    Simula uma recusa de escopo/consentimento (ou falha generica) exatamente
    na sentenca que o caminho do handler executa.
    """

    def __init__(self, conn, pattern, exc):
        self._conn = conn
        self._pattern = pattern
        self._exc = exc

    def cursor(self):
        return _SQLFaultCursor(self)

    def commit(self):
        self._conn.commit()


class _SQLFaultCursor:
    def __init__(self, owner):
        self._owner = owner
        self._cursor = owner._conn.cursor()

    def execute(self, sql, *args, **kwargs):
        if self._owner._pattern in sql:
            raise self._owner._exc
        return self._cursor.execute(sql, *args, **kwargs)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _FakeProactive:
    """Proactive stub: sem timers/rede; TRI desligado por padrao."""

    tri_enabled = False

    def __init__(self):
        self.reset_calls = []
        self.tri_error = None

    def reset_timer(self, user_id):
        self.reset_calls.append(user_id)

    def detect_fragments_in_message(self, **kwargs):
        if self.tri_error is not None:
            raise self.tri_error
        return {"fragments_detected": 0, "fragments_saved": 0}


@pytest.fixture
def handle_env(bot_env, monkeypatch):
    """bot_env + fakes dos apoios de handle_message (proactive/engine)."""
    proactive = _FakeProactive()
    monkeypatch.setattr(telegram_bot.bot_state, "proactive", proactive)
    monkeypatch.setattr(telegram_bot.bot_state, "total_messages_processed", 5)
    monkeypatch.setattr(
        telegram_bot.bot_state,
        "jung_engine",
        types.SimpleNamespace(process_message=lambda **kwargs: {"response": "ok"}),
    )
    return types.SimpleNamespace(db=bot_env, proactive=proactive)


def _simulate_free_tier_user(monkeypatch):
    """Passa o gate de admin mas mantem o trecho de limites gratuitos ativo."""
    monkeypatch.setattr(telegram_bot, "ADMIN_IDS", set())
    monkeypatch.setattr(telegram_bot, "_is_admin_telegram_user", lambda user: True)


def test_handle_message_history_scope_sentinel_replies_canonically(handle_env):
    update = FakeUpdate("ola")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_non_value_error_scope_sentinel_replies_canonically(
    handle_env, monkeypatch
):
    monkeypatch.setattr(handle_env.db, "get_user_conversations", lambda *a, **k: [])

    def _boom(**kwargs):
        raise RuntimeError(REVOKED_SENTINEL)

    monkeypatch.setattr(telegram_bot.bot_state.jung_engine, "process_message", _boom)
    update = FakeUpdate("ola")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_consent_save_scope_sentinel_replies_canonically(
    handle_env, monkeypatch
):
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(handle_env.db.conn, "UPDATE users", ValueError(REVOKED_SENTINEL)),
    )
    context = FakeContext()
    context.user_data["awaiting_consent"] = True
    update = FakeUpdate("SIM")
    _run(telegram_bot.handle_message(update, context))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_consent_save_generic_error_keeps_consent_flow(
    handle_env, monkeypatch
):
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(handle_env.db.conn, "UPDATE users", RuntimeError("disk full")),
    )
    context = FakeContext()
    context.user_data["awaiting_consent"] = True
    update = FakeUpdate("SIM")
    _run(telegram_bot.handle_message(update, context))
    assert len(update.message.replies) == 1
    assert "Consentimento registrado" in update.message.replies[0]


def test_handle_message_onboarding_save_scope_sentinel_replies_canonically(
    handle_env, monkeypatch
):
    import core.db.unesco_pilot as unesco_pilot

    def _boom(*args, **kwargs):
        raise ValueError(REVOKED_SENTINEL)

    monkeypatch.setattr(unesco_pilot, "save_unesco_pilot_baseline", _boom)
    context = FakeContext()
    context.user_data.update(
        {
            "onboarding_step": 3,
            "baseline_stress_score": 3,
            "baseline_trait_challenge": "prazos",
        }
    )
    update = FakeUpdate("quero mais foco")
    _run(telegram_bot.handle_message(update, context))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_reset_chroma_scope_sentinel_replies_canonically(
    handle_env, monkeypatch
):
    class _RevokedCollection:
        def get(self, *args, **kwargs):
            raise ValueError(REVOKED_SENTINEL)

    monkeypatch.setattr(handle_env.db, "chroma_enabled", True)
    monkeypatch.setattr(
        handle_env.db,
        "vectorstore",
        types.SimpleNamespace(_collection=_RevokedCollection()),
    )
    context = FakeContext()
    context.user_data["awaiting_reset_confirmation"] = True
    update = FakeUpdate("CONFIRMAR RESET")
    _run(telegram_bot.handle_message(update, context))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_reset_chroma_generic_error_still_finishes_reset(
    handle_env, monkeypatch
):
    class _BrokenCollection:
        def get(self, *args, **kwargs):
            raise RuntimeError("chroma down")

    monkeypatch.setattr(handle_env.db, "chroma_enabled", True)
    monkeypatch.setattr(
        handle_env.db,
        "vectorstore",
        types.SimpleNamespace(_collection=_BrokenCollection()),
    )
    context = FakeContext()
    context.user_data["awaiting_reset_confirmation"] = True
    update = FakeUpdate("CONFIRMAR RESET")
    _run(telegram_bot.handle_message(update, context))
    assert len(update.message.replies) == 1
    assert "Reset executado" in update.message.replies[0]


def test_handle_message_offboarding_scope_sentinel_is_not_masked_as_parse_error(
    handle_env, monkeypatch
):
    _simulate_free_tier_user(monkeypatch)
    conn = handle_env.db.conn
    conn.execute(
        "INSERT INTO users (user_id, registration_date) VALUES (?, ?)",
        ("user-1", "2020-01-01 00:00:00"),
    )
    conn.execute("INSERT INTO unesco_pilot_data (user_id) VALUES (?)", ("user-1",))
    conn.commit()
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(conn, "UPDATE unesco_pilot_data", ValueError(REVOKED_SENTINEL)),
    )
    context = FakeContext()
    context.user_data["offboarding_step"] = 1
    update = FakeUpdate("3")
    _run(telegram_bot.handle_message(update, context))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]
    assert not any("número entre 1 e 5" in reply for reply in update.message.replies)


def test_handle_message_free_limits_scope_sentinel_replies_canonically(
    handle_env, monkeypatch
):
    _simulate_free_tier_user(monkeypatch)
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(
            handle_env.db.conn, "SELECT registration_date", ValueError(REVOKED_SENTINEL)
        ),
    )
    update = FakeUpdate("ola")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert update.message.replies == [telegram_bot._SCOPE_REFUSAL_TEXT]


def test_handle_message_red_line_keeps_safety_reply_before_scope_refusal(
    handle_env, monkeypatch
):
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(
            handle_env.db.conn, "UPDATE unesco_pilot_data", ValueError(REVOKED_SENTINEL)
        ),
    )
    update = FakeUpdate("me matar")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert len(update.message.replies) == 2
    assert "188" in update.message.replies[0]
    assert update.message.replies[1] == telegram_bot._SCOPE_REFUSAL_TEXT


def test_handle_message_red_line_keeps_safety_reply_on_generic_db_error(
    handle_env, monkeypatch
):
    monkeypatch.setattr(
        handle_env.db,
        "conn",
        _SQLFaultConn(
            handle_env.db.conn, "UPDATE unesco_pilot_data", RuntimeError("db down")
        ),
    )
    update = FakeUpdate("me matar")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert len(update.message.replies) == 1
    assert "188" in update.message.replies[0]


def test_handle_message_tri_scope_sentinel_reaches_canonical_reply(
    handle_env, monkeypatch
):
    monkeypatch.setattr(handle_env.db, "get_user_conversations", lambda *a, **k: [])
    handle_env.proactive.tri_enabled = True
    handle_env.proactive.tri_error = ValueError(REVOKED_SENTINEL)
    update = FakeUpdate("ola")
    _run(telegram_bot.handle_message(update, FakeContext()))
    assert update.message.replies == ["ok", telegram_bot._SCOPE_REFUSAL_TEXT]
