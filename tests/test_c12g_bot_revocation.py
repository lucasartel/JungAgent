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


# Atributo (sem trocar o módulo): a construção de ``JungianEngine`` no import
# de ``telegram_bot`` instancia os clientes de LLM com argumentos, então o
# stub precisa aceitar kwargs — inclusive quando outro teste o deixou ``object``.
for _mod_name, _attr in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _mod = sys.modules.get(_mod_name)
    if _mod is None:
        _mod = types.ModuleType(_mod_name)
        sys.modules[_mod_name] = _mod
    setattr(_mod, _attr, _StubClient)

import telegram_bot
from core.db.relations import is_relation_scope_sentinel


REVOKED_SENTINEL = "relation_not_eligible:status=revoked,consent=granted"

# ---------------------------------------------------------------------------
# Fakes de Update/Context do python-telegram-bot
# ---------------------------------------------------------------------------


class FakeMessage:
    def __init__(self):
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
    def __init__(self):
        self.effective_user = FakeUser()
        self.message = FakeMessage()
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
            """
        )
        self.detect_calls = []

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

    def get_user_conversations(self, user_id, limit=1000):
        raise ValueError(REVOKED_SENTINEL)

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
