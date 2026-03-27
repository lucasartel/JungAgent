"""
Camada simples de criptografia autenticada para segredos de integrações.

Usa uma chave mestre vinda de INTEGRATIONS_MASTER_KEY para cifrar segredos
antes de persistir no SQLite. O formato do token é:
    base64url(nonce || ciphertext || mac)

O algoritmo é um stream cipher derivado de HMAC-SHA256 com MAC separado.
Não depende de bibliotecas externas, o que facilita o deploy atual.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Optional


class IntegrationSecretsError(Exception):
    pass


class IntegrationSecretsManager:
    def __init__(self, master_key: Optional[str] = None):
        raw = (master_key or os.getenv("INTEGRATIONS_MASTER_KEY", "")).strip()
        if not raw:
            raise IntegrationSecretsError("INTEGRATIONS_MASTER_KEY nao configurada")
        self._key = hashlib.sha256(raw.encode("utf-8")).digest()

    @staticmethod
    def is_configured() -> bool:
        return bool(os.getenv("INTEGRATIONS_MASTER_KEY", "").strip())

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        blocks = []
        counter = 0
        while sum(len(block) for block in blocks) < length:
            counter_bytes = counter.to_bytes(4, "big")
            blocks.append(hmac.new(self._key, nonce + counter_bytes, hashlib.sha256).digest())
            counter += 1
        return b"".join(blocks)[:length]

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise IntegrationSecretsError("Segredo vazio")

        data = plaintext.encode("utf-8")
        nonce = secrets.token_bytes(16)
        keystream = self._keystream(nonce, len(data))
        ciphertext = bytes(a ^ b for a, b in zip(data, keystream))
        mac = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")
        return token

    def decrypt(self, token: str) -> str:
        if not token:
            raise IntegrationSecretsError("Token de segredo ausente")

        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
        except Exception as exc:
            raise IntegrationSecretsError(f"Token invalido: {exc}") from exc

        if len(raw) < 48:
            raise IntegrationSecretsError("Token de segredo corrompido")

        nonce = raw[:16]
        mac = raw[-32:]
        ciphertext = raw[16:-32]
        expected_mac = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise IntegrationSecretsError("Falha de autenticidade do segredo")

        keystream = self._keystream(nonce, len(ciphertext))
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream))
        return plaintext.decode("utf-8")
