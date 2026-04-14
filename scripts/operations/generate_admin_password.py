#!/usr/bin/env python3
"""
generate_admin_password.py - Helper para gerar senhas de admin
===============================================================

Script utilitário para gerar hashes bcrypt de senhas para
uso no sistema de autenticação do admin web.

Uso:
    python generate_admin_password.py

Ou com senha como argumento:
    python generate_admin_password.py "minha_senha_secreta"

Autor: Sistema Jung
Data: 2025-11-29
"""

import sys
from pathlib import Path

# Adicionar diretório atual ao path para importar auth
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from admin_web.auth import generate_password_hash, verify_password_hash

def main():
    print("=" * 70)
    print("🔐 GERADOR DE SENHA DE ADMIN - JUNG AI")
    print("=" * 70)
    print()

    # Obter senha
    if len(sys.argv) > 1:
        # Modo não-interativo
        password = sys.argv[1]
        print(f"Gerando hash para senha fornecida...")
    else:
        # Modo interativo
        import getpass
        print("Digite a nova senha de admin:")
        password = getpass.getpass("Senha: ")

        print("Confirme a senha:")
        password_confirm = getpass.getpass("Confirmar: ")

        if password != password_confirm:
            print()
            print("❌ ERRO: As senhas não coincidem!")
            sys.exit(1)

    if not password or len(password) < 6:
        print()
        print("❌ ERRO: Senha deve ter pelo menos 6 caracteres!")
        sys.exit(1)

    # Gerar hash
    print()
    print("⏳ Gerando hash bcrypt (pode levar alguns segundos)...")
    hash_result = generate_password_hash(password)

    print()
    print("✅ Hash gerado com sucesso!")
    print()
    print("=" * 70)
    print("📋 CONFIGURAÇÃO PARA PRODUÇÃO (RAILWAY)")
    print("=" * 70)
    print()
    print("Adicione estas variáveis de ambiente no Railway:")
    print()
    print(f"ADMIN_USER=admin")
    print(f"ADMIN_PASSWORD={hash_result}")
    print()
    print("=" * 70)
    print()
    print("💡 DICAS IMPORTANTES:")
    print()
    print("1. NUNCA commit o hash em código")
    print("2. Use variáveis de ambiente no Railway")
    print("3. Cada deploy cria um hash diferente (salt aleatório)")
    print("4. O hash é seguro para armazenar - senha original não pode ser recuperada")
    print()
    print("=" * 70)
    print("📚 COMO CONFIGURAR NO RAILWAY:")
    print("=" * 70)
    print()
    print("1. Acesse seu projeto no Railway")
    print("2. Vá em 'Variables'")
    print("3. Adicione/edite as variáveis acima")
    print("4. Faça redeploy")
    print()
    print("=" * 70)
    print()

    # Teste de verificação
    print("🧪 Verificando hash...")
    if verify_password_hash(password, hash_result):
        print("✅ Hash válido e funcionando corretamente!")
    else:
        print("❌ ERRO: Hash inválido!")
        sys.exit(1)

    print()
    print("🎉 Tudo pronto! Use as credenciais acima para fazer login no admin web.")
    print()


if __name__ == "__main__":
    main()
