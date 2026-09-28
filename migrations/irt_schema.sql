-- ============================================
-- SCHEMA TRI (Item Response Theory) - JungAgent
-- ============================================
-- Versão: 1.0
-- Data: Janeiro 2026
-- Baseado em: Roadmap TRI.md
--
-- Referências Teóricas:
-- - Samejima (1969) - Graded Response Model
-- - Embretson & Reise (2000) - Item Response Theory
-- - Costa & McCrae (1992) - NEO-PI-R (Big Five 30 facetas)
-- - Standards (2014) - Validação psicométrica
-- ============================================

-- ============================================
-- TABELA 1: irt_fragments
-- ============================================
-- Propósito: Banco de fragmentos comportamentais
-- mapeados às 30 facetas do Big Five (5 domínios × 6 facetas)
-- Total esperado: 150 fragmentos (5 por faceta)

CREATE TABLE IF NOT EXISTS irt_fragments (
    fragment_id TEXT PRIMARY KEY,           -- Ex: "EXT_E1_001"
    domain TEXT NOT NULL,                   -- "Extraversion", "Openness", etc.
    facet TEXT NOT NULL,                    -- "E1: Warmth", "O1: Fantasy", etc.
    facet_code TEXT NOT NULL,               -- "E1", "E2", ..., "N6"
    description TEXT NOT NULL,              -- "Expressa afeto caloroso por pessoas"
    description_en TEXT,                    -- English version for reference
    detection_pattern TEXT,                 -- Prompt estruturado para LLM
    example_phrases TEXT,                   -- JSON: ["eu amo pessoas", "adoro ajudar"]
    reverse_scored BOOLEAN DEFAULT FALSE,   -- Se item é invertido

    -- Validação de conteúdo (Standards 2014, Cap. 1)
    expert_review_status TEXT DEFAULT 'pending',  -- 'pending', 'approved', 'rejected'
    content_validity_ratio REAL,            -- CVR (-1 a 1)
    reviewed_by TEXT,                       -- IDs dos psicólogos revisores (JSON)

    -- Metadados
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',           -- 'active', 'calibrating', 'retired'

    UNIQUE(domain, facet, description)
);

-- Índices para irt_fragments
CREATE INDEX IF NOT EXISTS idx_fragments_domain ON irt_fragments(domain);
CREATE INDEX IF NOT EXISTS idx_fragments_facet ON irt_fragments(facet);
CREATE INDEX IF NOT EXISTS idx_fragments_facet_code ON irt_fragments(facet_code);
CREATE INDEX IF NOT EXISTS idx_fragments_status ON irt_fragments(status);


-- ============================================
-- TABELA 2: irt_item_parameters
-- ============================================
-- Propósito: Parâmetros calibrados do Graded Response Model
-- Fórmula GRM (Samejima 1969):
--   P*(k|θ) = 1 / (1 + exp(-a(θ - bₖ)))
--   P(X=k|θ) = P*(k|θ) - P*(k+1|θ)

CREATE TABLE IF NOT EXISTS irt_item_parameters (
    fragment_id TEXT PRIMARY KEY,

    -- Parâmetros do GRM (Samejima 1969)
    -- 'a' = discriminação (tipicamente 0.5 - 3.0)
    -- b₁-b₄ = thresholds entre categorias 1-2, 2-3, 3-4, 4-5
    discrimination REAL NOT NULL DEFAULT 1.0,  -- 'a' parameter
    threshold_1 REAL NOT NULL DEFAULT -1.5,    -- b₁: limiar 1→2
    threshold_2 REAL NOT NULL DEFAULT -0.5,    -- b₂: limiar 2→3
    threshold_3 REAL NOT NULL DEFAULT 0.5,     -- b₃: limiar 3→4
    threshold_4 REAL NOT NULL DEFAULT 1.5,     -- b₄: limiar 4→5

    -- Metadados de calibração
    calibration_n INTEGER DEFAULT 0,           -- Tamanho da amostra usada
    calibration_date TIMESTAMP,
    calibration_method TEXT DEFAULT 'default', -- 'MMLE', 'MCMC', 'MLE', 'default'
    calibration_software TEXT,                 -- 'mirt', 'pyirt', 'custom'

    -- Qualidade do item (Embretson & Reise 2000, Cap. 8)
    item_information_peak REAL,                -- θ onde item tem mais informação
    item_information_max REAL,                 -- Máxima informação fornecida
    discrimination_quality TEXT DEFAULT 'uncalibrated',  -- 'excellent', 'good', 'fair', 'poor', 'uncalibrated'

    -- Estabilidade (Standards 2014)
    test_retest_correlation REAL,              -- Estabilidade temporal
    inter_rater_reliability REAL,              -- Acordo entre avaliadores LLM

    FOREIGN KEY (fragment_id) REFERENCES irt_fragments(fragment_id)
);

-- Índices para irt_item_parameters
CREATE INDEX IF NOT EXISTS idx_params_discrimination ON irt_item_parameters(discrimination);
CREATE INDEX IF NOT EXISTS idx_params_quality ON irt_item_parameters(discrimination_quality);
CREATE INDEX IF NOT EXISTS idx_params_calibration_date ON irt_item_parameters(calibration_date);


-- ============================================
-- TABELA 3: detected_fragments
-- ============================================
-- Propósito: Registro de cada fragmento detectado nas conversas
-- Cada detecção tem intensidade 1-5 (escala Likert)

CREATE TABLE IF NOT EXISTS detected_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    agent_instance TEXT,                       -- C12c3: particao cognitiva
    fragment_id TEXT NOT NULL,
    conversation_id INTEGER,                   -- FK: conversations.id

    -- Detecção
    intensity INTEGER NOT NULL,                -- 1-5 (escala Likert)
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    detection_confidence REAL,                 -- 0.0-1.0 (confiança do LLM)
    detection_method TEXT DEFAULT 'claude',    -- 'claude', 'grok', 'hybrid'

    -- Contexto
    source_quote TEXT,                         -- Citação literal da conversa
    context_window TEXT,                       -- Contexto ao redor (±50 palavras)

    -- Validação humana (opcional)
    human_verified BOOLEAN DEFAULT FALSE,
    human_intensity INTEGER,                   -- Se verificado, intensidade correta
    human_verifier_id TEXT,
    verification_date TIMESTAMP,

    -- Metadados
    llm_reasoning TEXT,                        -- Justificativa do LLM

    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (fragment_id) REFERENCES irt_fragments(fragment_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- Índices para detected_fragments
CREATE INDEX IF NOT EXISTS idx_detected_user ON detected_fragments(user_id);
CREATE INDEX IF NOT EXISTS idx_detected_fragment ON detected_fragments(fragment_id);
CREATE INDEX IF NOT EXISTS idx_detected_timestamp ON detected_fragments(detected_at);
CREATE INDEX IF NOT EXISTS idx_detected_conversation ON detected_fragments(conversation_id);
CREATE INDEX IF NOT EXISTS idx_detected_user_fragment ON detected_fragments(user_id, fragment_id);
CREATE INDEX IF NOT EXISTS idx_detected_user_instance ON detected_fragments(user_id, agent_instance);


-- ============================================
-- TABELA 4: irt_trait_estimates
-- ============================================
-- Propósito: Estimativas θ (theta) dos traços latentes
-- θ representa o nível do traço na escala padronizada (-3 a +3)
-- Inclui Standard Error para medir precisão

CREATE TABLE IF NOT EXISTS irt_trait_estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    agent_instance TEXT,                       -- C12c3: particao cognitiva
    domain TEXT NOT NULL,                      -- "Extraversion", etc.

    -- Estimativa TRI
    theta REAL NOT NULL,                       -- Traço latente (-∞ a +∞, típico -3 a +3)
    standard_error REAL NOT NULL,              -- SE(θ) - precisão da estimativa

    -- Scores normalizados (compatíveis com user_psychometrics)
    score_0_100 INTEGER NOT NULL,              -- Score 0-100 (T-score normalizado)
    t_score INTEGER,                           -- T-score (M=50, SD=10)
    percentile INTEGER,                        -- Percentil 0-99

    -- Qualidade da estimativa
    n_fragments INTEGER NOT NULL,              -- Quantos fragmentos usados
    test_information REAL,                     -- I(θ) - Informação total
    confidence_level TEXT,                     -- 'low', 'medium', 'high', 'excellent'
    reliability_estimate REAL,                 -- α aproximado (se calculável)

    -- Interpretação (Costa & McCrae 1992, Cap. 6)
    level_label TEXT,                          -- "Very High", "High", "Average", "Low", "Very Low"
    level_description TEXT,                    -- Texto descritivo

    -- Metadados
    estimated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    estimation_method TEXT DEFAULT 'MLE',      -- 'MLE', 'MAP', 'EAP'

    -- Validação externa (se comparado com teste padrão)
    external_validation_score REAL,            -- Score do teste externo
    external_validation_test TEXT,             -- "NEO-PI-R", "BFI-2", etc.
    external_validation_date TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Índices para irt_trait_estimates
CREATE INDEX IF NOT EXISTS idx_estimates_user_domain ON irt_trait_estimates(user_id, domain);
CREATE INDEX IF NOT EXISTS idx_estimates_theta ON irt_trait_estimates(theta);
CREATE INDEX IF NOT EXISTS idx_estimates_date ON irt_trait_estimates(estimated_at);
CREATE INDEX IF NOT EXISTS idx_estimates_user ON irt_trait_estimates(user_id);
CREATE INDEX IF NOT EXISTS idx_estimates_user_instance ON irt_trait_estimates(user_id, agent_instance);


-- ============================================
-- TABELA 5: facet_scores
-- ============================================
-- Propósito: Scores das 30 facetas do Big Five
-- Permite análise mais granular que apenas os 5 domínios

CREATE TABLE IF NOT EXISTS facet_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    agent_instance TEXT,                       -- C12c3: particao cognitiva
    domain TEXT NOT NULL,                      -- "Extraversion", etc.
    facet_code TEXT NOT NULL,                  -- "E1", "E2", ..., "N6"
    facet_name TEXT NOT NULL,                  -- "Warmth", "Gregariousness", etc.

    -- Estimativas
    theta REAL NOT NULL,
    standard_error REAL NOT NULL,
    score_0_100 INTEGER NOT NULL,
    t_score INTEGER,

    -- Qualidade
    n_fragments INTEGER,
    confidence_level TEXT,

    estimated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Índices para facet_scores
CREATE INDEX IF NOT EXISTS idx_facets_user_domain ON facet_scores(user_id, domain);
CREATE INDEX IF NOT EXISTS idx_facets_code ON facet_scores(facet_code);
CREATE INDEX IF NOT EXISTS idx_facets_user ON facet_scores(user_id);
CREATE INDEX IF NOT EXISTS idx_facets_user_instance ON facet_scores(user_id, agent_instance);


-- ============================================
-- TABELA 6: psychometric_quality_checks
-- ============================================
-- Propósito: Verificações de qualidade conforme Standards (2014)
-- Armazena métricas de confiabilidade, validade e equidade

CREATE TABLE IF NOT EXISTS psychometric_quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    agent_instance TEXT,                       -- C12c3: particao cognitiva
    domain TEXT NOT NULL,
    check_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Confiabilidade (Standards 2014, Cap. 2)
    cronbach_alpha REAL,                       -- α de Cronbach
    mcdonald_omega REAL,                       -- ω de McDonald
    test_information REAL,                     -- I(θ)
    sem REAL,                                  -- Standard Error of Measurement

    -- Validade (Standards 2014, Cap. 1)
    content_validity_score REAL,               -- Média CVR dos itens
    construct_validity_score REAL,             -- CFA fit (se disponível)
    criterion_validity_r REAL,                 -- Correlação com critério externo

    -- Equidade (Standards 2014, Cap. 3)
    dif_analysis_result TEXT,                  -- JSON com DIF por grupo
    bias_detected BOOLEAN DEFAULT FALSE,
    bias_type TEXT,                            -- 'gender', 'age', 'culture', 'none'
    bias_severity TEXT,                        -- 'low', 'medium', 'high'

    -- Classificação geral
    quality_score REAL,                        -- 0-100
    quality_level TEXT,                        -- 'excellent', 'good', 'fair', 'poor'
    meets_minimum_standard BOOLEAN,
    warning_flags TEXT,                        -- JSON com avisos

    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Índices para psychometric_quality_checks
CREATE INDEX IF NOT EXISTS idx_quality_user_domain ON psychometric_quality_checks(user_id, domain);
CREATE INDEX IF NOT EXISTS idx_quality_level ON psychometric_quality_checks(quality_level);
CREATE INDEX IF NOT EXISTS idx_quality_date ON psychometric_quality_checks(check_date);
CREATE INDEX IF NOT EXISTS idx_quality_user_instance ON psychometric_quality_checks(user_id, agent_instance);


-- ============================================
-- VIEWS ÚTEIS
-- ============================================

-- View: Resumo de fragmentos por domínio
CREATE VIEW IF NOT EXISTS v_fragments_summary AS
SELECT
    domain,
    COUNT(*) as total_fragments,
    COUNT(CASE WHEN status = 'active' THEN 1 END) as active_fragments,
    COUNT(DISTINCT facet_code) as facets_covered
FROM irt_fragments
GROUP BY domain;

-- View: Último score TRI por usuário/domínio
CREATE VIEW IF NOT EXISTS v_latest_trait_estimates AS
SELECT
    e.user_id,
    e.domain,
    e.theta,
    e.standard_error,
    e.score_0_100,
    e.confidence_level,
    e.n_fragments,
    e.estimated_at
FROM irt_trait_estimates e
INNER JOIN (
    SELECT user_id, domain, MAX(estimated_at) as max_date
    FROM irt_trait_estimates
    GROUP BY user_id, domain
) latest ON e.user_id = latest.user_id
    AND e.domain = latest.domain
    AND e.estimated_at = latest.max_date;

-- View: Detecções recentes com info do fragmento
CREATE VIEW IF NOT EXISTS v_recent_detections AS
SELECT
    d.id,
    d.user_id,
    d.fragment_id,
    f.domain,
    f.facet,
    f.description,
    d.intensity,
    d.detection_confidence,
    d.source_quote,
    d.detected_at
FROM detected_fragments d
JOIN irt_fragments f ON d.fragment_id = f.fragment_id
ORDER BY d.detected_at DESC
LIMIT 100;


-- ============================================
-- FIM DO SCHEMA TRI
-- ============================================
