-- migrations/002_user_subscriptions.sql
-- Adiciona tabelas para gerenciar assinaturas Stripe e cotas de uso diário

-- 1. Tabela de Assinaturas
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan_type TEXT NOT NULL CHECK(plan_type IN ('basic_7_days', 'premium_companion')),
    status TEXT NOT NULL CHECK(status IN ('active', 'expired', 'canceled', 'past_due')),
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Índice para busca rápida de assinatura ativa
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_active ON user_subscriptions(user_id, status, expires_at);

-- 2. Tabela de Cota Diária (para o plano básico)
CREATE TABLE IF NOT EXISTS user_daily_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date_str TEXT NOT NULL, -- Formato: YYYY-MM-DD
    message_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, date_str) -- Garante apenas 1 registro por usuário por dia
);

-- Índice para busca de uso diário
CREATE INDEX IF NOT EXISTS idx_user_daily_usage_date ON user_daily_usage(user_id, date_str);
