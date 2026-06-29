from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


class SchemaDatabaseMixin:
    def _init_sqlite_schema(self):
        """Cria schema SQLite completo"""
        cursor = self.conn.cursor()
        
        # ========== USUÃRIOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                registration_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_sessions INTEGER DEFAULT 1,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',
                platform_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ========== CONVERSAS (METADADOS) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                session_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                -- ConteÃºdo
                user_input TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                
                -- AnÃ¡lise arquetÃ­pica
                archetype_analyses TEXT,
                detected_conflicts TEXT,
                
                -- MÃ©tricas
                tension_level REAL DEFAULT 0.0,
                affective_charge REAL DEFAULT 0.0,
                existential_depth REAL DEFAULT 0.0,
                intensity_level INTEGER DEFAULT 5,
                complexity TEXT DEFAULT 'medium',
                
                -- ExtraÃ§Ã£o
                keywords TEXT,
                
                -- Linking ChromaDB
                chroma_id TEXT UNIQUE,
                
                platform TEXT DEFAULT 'telegram',
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # ========== FATOS ESTRUTURADOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                -- CategorizaÃ§Ã£o
                fact_category TEXT NOT NULL,
                fact_subcategory TEXT,
                
                -- ConteÃºdo
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                
                -- Rastreabilidade
                first_mentioned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source_conversation_id INTEGER,
                confidence REAL DEFAULT 1.0,
                
                -- Versionamento
                version INTEGER DEFAULT 1,
                is_current BOOLEAN DEFAULT 1,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (source_conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== PADRÃ•ES DETECTADOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                pattern_type TEXT NOT NULL,
                pattern_name TEXT NOT NULL,
                pattern_description TEXT,
                
                frequency_count INTEGER DEFAULT 1,
                first_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_occurrence_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                supporting_conversation_ids TEXT,
                confidence_score REAL DEFAULT 0.5,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # ========== MARCOS DO USUÃRIO ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                milestone_type TEXT NOT NULL,
                milestone_title TEXT NOT NULL,
                milestone_description TEXT,
                
                achieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                related_conversation_id INTEGER,
                
                before_state TEXT,
                after_state TEXT,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (related_conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== CONFLITOS ARQUETÃPICOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS archetype_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id INTEGER,
                
                archetype1 TEXT NOT NULL,
                archetype2 TEXT NOT NULL,
                conflict_type TEXT,
                tension_level REAL,
                description TEXT,
                
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== DESENVOLVIMENTO DO AGENTE ==========
        # MigraÃ§Ã£o: Verificar se tabela precisa ser recriada com user_id
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_development'")
        table_exists = cursor.fetchone() is not None

        if table_exists:
            # Verificar se coluna user_id existe
            cursor.execute("PRAGMA table_info(agent_development)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'user_id' not in columns:
                logger.warning("âš ï¸ Migrando agent_development para nova estrutura com user_id...")

                # 1. Salvar dados antigos
                cursor.execute("SELECT * FROM agent_development WHERE id = 1")
                old_data = cursor.fetchone()

                # 2. Dropar tabela antiga
                cursor.execute("DROP TABLE IF EXISTS agent_development")

                # 3. Criar nova tabela
                cursor.execute("""
                    CREATE TABLE agent_development (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,

                        phase INTEGER DEFAULT 1,
                        total_interactions INTEGER DEFAULT 0,

                        self_awareness_score REAL DEFAULT 0.0,
                        moral_complexity_score REAL DEFAULT 0.0,
                        emotional_depth_score REAL DEFAULT 0.0,
                        autonomy_score REAL DEFAULT 0.0,

                        depth_level REAL DEFAULT 0.0,
                        autonomy_level REAL DEFAULT 0.0,

                        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                """)

                # 4. Migrar dados para todos os usuÃ¡rios existentes
                if old_data:
                    cursor.execute("SELECT user_id FROM users")
                    users = cursor.fetchall()

                    for user_row in users:
                        user_id = user_row[0]
                        cursor.execute("""
                            INSERT INTO agent_development
                            (user_id, phase, total_interactions, self_awareness_score,
                             moral_complexity_score, emotional_depth_score, autonomy_score,
                             depth_level, autonomy_level, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            user_id,
                            old_data[1] if len(old_data) > 1 else 1,  # phase
                            old_data[2] if len(old_data) > 2 else 0,  # total_interactions
                            old_data[3] if len(old_data) > 3 else 0.0,  # self_awareness_score
                            old_data[4] if len(old_data) > 4 else 0.0,  # moral_complexity_score
                            old_data[5] if len(old_data) > 5 else 0.0,  # emotional_depth_score
                            old_data[6] if len(old_data) > 6 else 0.0,  # autonomy_score
                            old_data[7] if len(old_data) > 7 else 0.0,  # depth_level
                            old_data[8] if len(old_data) > 8 else 0.0,  # autonomy_level
                            old_data[9] if len(old_data) > 9 else 'CURRENT_TIMESTAMP'  # last_updated
                        ))

                    logger.info(f"âœ… Migrados dados de agent_development para {len(users)} usuÃ¡rios")

                self.conn.commit()
        else:
            # Tabela nÃ£o existe, criar nova estrutura
            cursor.execute("""
                CREATE TABLE agent_development (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,

                    phase INTEGER DEFAULT 1,
                    total_interactions INTEGER DEFAULT 0,

                    self_awareness_score REAL DEFAULT 0.0,
                    moral_complexity_score REAL DEFAULT 0.0,
                    emotional_depth_score REAL DEFAULT 0.0,
                    autonomy_score REAL DEFAULT 0.0,

                    depth_level REAL DEFAULT 0.0,
                    autonomy_level REAL DEFAULT 0.0,

                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

        # Criar Ã­ndice Ãºnico para garantir um registro por usuÃ¡rio
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_dev_user
            ON agent_development(user_id)
        """)
        
        # ========== MILESTONES DO AGENTE ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                milestone_type TEXT NOT NULL,
                description TEXT,
                phase INTEGER,
                interaction_count INTEGER,
                
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ========== ANÃLISES COMPLETAS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS full_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,

                mbti TEXT,
                dominant_archetypes TEXT,
                phase INTEGER DEFAULT 1,
                full_analysis TEXT,

                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== SONHOS DO AGENTE (MOTOR ONÃRICO) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                dream_content TEXT NOT NULL,
                symbolic_theme TEXT,
                extracted_insight TEXT,
                regulatory_function TEXT,
                compensated_attitude TEXT,
                dream_mood TEXT,
                
                status TEXT DEFAULT 'pending', -- 'pending', 'faded', 'delivered'
                
                image_url TEXT,
                image_prompt TEXT,
                image_provider TEXT,
                image_model TEXT,
                image_status TEXT,
                image_raw_response_json TEXT,

                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                delivered_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_hobby_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT,
                title TEXT,
                summary TEXT,
                image_prompt TEXT,
                image_url TEXT,
                provider TEXT DEFAULT 'minimax',
                status TEXT DEFAULT 'generated',
                critique_summary TEXT,
                critique_json TEXT,
                evaluation_model TEXT,
                evaluated_at DATETIME,
                inspirations_json TEXT,
                raw_response_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Auto-migraÃ§Ã£o para bancos antigos
        try:
            cursor.execute("ALTER TABLE agent_dreams ADD COLUMN image_url TEXT;")
        except sqlite3.OperationalError:
            pass # Coluna jÃ¡ existe
            
        try:
            cursor.execute("ALTER TABLE agent_dreams ADD COLUMN image_prompt TEXT;")
        except sqlite3.OperationalError:
            pass # Coluna jÃ¡ existe

        for column_name, column_type in (
            ("regulatory_function", "TEXT"),
            ("compensated_attitude", "TEXT"),
            ("dream_mood", "TEXT"),
            ("image_provider", "TEXT"),
            ("image_model", "TEXT"),
            ("image_status", "TEXT"),
            ("image_raw_response_json", "TEXT"),
        ):
            try:
                cursor.execute(f"ALTER TABLE agent_dreams ADD COLUMN {column_name} {column_type};")
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute("ALTER TABLE agent_hobby_artifacts ADD COLUMN critique_summary TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE agent_hobby_artifacts ADD COLUMN critique_json TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE agent_hobby_artifacts ADD COLUMN evaluation_model TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE agent_hobby_artifacts ADD COLUMN evaluated_at DATETIME;")
        except sqlite3.OperationalError:
            pass

        # ========== PESQUISA AUTÃ”NOMA (Caminho Extrovertido) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                topic TEXT NOT NULL,
                source_url TEXT,
                raw_excerpt TEXT,
                synthesized_insight TEXT,
                trigger_reason TEXT,
                research_lens TEXT,
                
                status TEXT DEFAULT 'active', -- 'active', 'archived'
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN status TEXT DEFAULT 'active';")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN raw_excerpt TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN source_url TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN trigger_reason TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN research_lens TEXT;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scholar_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                trigger_source TEXT DEFAULT 'unknown',
                status TEXT NOT NULL,
                topic TEXT,
                history_excerpt TEXT,
                result_summary TEXT,
                error_message TEXT,
                article_chars INTEGER DEFAULT 0,
                research_id INTEGER,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (research_id) REFERENCES external_research(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_will_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                trigger_source TEXT DEFAULT 'unknown',
                status TEXT DEFAULT 'generated',
                saber_score REAL DEFAULT 0.34,
                relacionar_score REAL DEFAULT 0.33,
                expressar_score REAL DEFAULT 0.33,
                dominant_will TEXT,
                secondary_will TEXT,
                constrained_will TEXT,
                will_conflict TEXT,
                attention_bias_note TEXT,
                daily_text TEXT,
                source_summary_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_will_message_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id INTEGER,
                cycle_id TEXT NOT NULL,
                phase TEXT DEFAULT 'conversation',
                source TEXT DEFAULT 'conversation',
                saber_delta REAL DEFAULT 0.34,
                relacionar_delta REAL DEFAULT 0.33,
                expressar_delta REAL DEFAULT 0.33,
                dominant_signal TEXT,
                signal_summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_will_pressure_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                saber_pressure REAL DEFAULT 0,
                relacionar_pressure REAL DEFAULT 0,
                expressar_pressure REAL DEFAULT 0,
                dominant_pressure TEXT,
                threshold_crossed BOOLEAN DEFAULT 0,
                refractory_until_saber DATETIME,
                refractory_until_relacionar DATETIME,
                refractory_until_expressar DATETIME,
                last_release_will TEXT,
                last_release_at DATETIME,
                last_action_status TEXT,
                last_action_summary TEXT,
                source_markers_json TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_will_pulse_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                trigger_source TEXT DEFAULT 'will_pulse',
                saber_pressure REAL DEFAULT 0,
                relacionar_pressure REAL DEFAULT 0,
                expressar_pressure REAL DEFAULT 0,
                winning_will TEXT,
                decision_reason TEXT,
                action_attempted TEXT,
                action_summary TEXT,
                status TEXT DEFAULT 'no_action',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== ANÃLISES PSICOMÃ‰TRICAS (RH) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_psychometrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                version INTEGER DEFAULT 1,

                -- Big Five (OCEAN) - scores 0-100
                openness_score INTEGER,
                openness_level TEXT,
                openness_description TEXT,

                conscientiousness_score INTEGER,
                conscientiousness_level TEXT,
                conscientiousness_description TEXT,

                extraversion_score INTEGER,
                extraversion_level TEXT,
                extraversion_description TEXT,

                agreeableness_score INTEGER,
                agreeableness_level TEXT,
                agreeableness_description TEXT,

                neuroticism_score INTEGER,
                neuroticism_level TEXT,
                neuroticism_description TEXT,

                big_five_confidence INTEGER,
                big_five_interpretation TEXT,

                -- InteligÃªncia Emocional (EQ) - scores 0-100
                eq_self_awareness INTEGER,
                eq_self_management INTEGER,
                eq_social_awareness INTEGER,
                eq_relationship_management INTEGER,
                eq_overall INTEGER,
                eq_leadership_potential TEXT,
                eq_details TEXT,

                -- Estilos de Aprendizagem (VARK) - scores 0-100
                vark_visual INTEGER,
                vark_auditory INTEGER,
                vark_reading INTEGER,
                vark_kinesthetic INTEGER,
                vark_dominant TEXT,
                vark_recommended_training TEXT,

                -- Valores Pessoais (Schwartz) - JSON
                schwartz_values TEXT,
                schwartz_top_3 TEXT,
                schwartz_cultural_fit TEXT,
                schwartz_retention_risk TEXT,

                -- Resumo Executivo
                executive_summary TEXT,

                -- Metadados
                analysis_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                conversations_analyzed INTEGER,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== LACUNAS DE CONHECIMENTO (CARÃŠNCIA DE SABERES) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                topic TEXT NOT NULL,
                the_gap TEXT NOT NULL,
                importance_score REAL DEFAULT 0.5,
                
                status TEXT DEFAULT 'open',
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== LOOP DE CONSCIENCIA ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'idle',
                cycle_id TEXT,
                loop_mode TEXT DEFAULT '24h',
                current_phase TEXT,
                next_phase TEXT,
                phase_started_at DATETIME,
                phase_deadline_at DATETIME,
                last_completed_phase TEXT,
                last_cycle_completed_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at DATETIME,
                completed_at DATETIME,
                duration_seconds REAL,
                trigger_name TEXT,
                trigger_source TEXT,
                execution_mode TEXT,
                input_summary TEXT,
                output_summary TEXT,
                warnings_json TEXT,
                errors_json TEXT,
                metrics_json TEXT,
                phase_result_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_phase_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                trigger_name TEXT,
                trigger_source TEXT,
                started_at DATETIME,
                completed_at DATETIME,
                duration_ms INTEGER,
                status TEXT NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                artifacts_created_json TEXT,
                warnings_json TEXT,
                errors_json TEXT,
                metrics_json TEXT,
                raw_result_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                artifact_type TEXT,
                artifact_id TEXT,
                artifact_table TEXT,
                summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_phase_config (
                phase TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                order_index INTEGER NOT NULL,
                default_duration_minutes INTEGER NOT NULL,
                retry_limit INTEGER DEFAULT 2,
                cooldown_minutes INTEGER DEFAULT 10,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_meta_consciousness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                agent_instance TEXT NOT NULL,
                cycle_id TEXT,
                phase TEXT DEFAULT 'identity',
                status TEXT DEFAULT 'generated',
                dominant_form TEXT,
                emergent_shift TEXT,
                dominant_gravity TEXT,
                blind_spot TEXT,
                integration_note TEXT,
                internal_questions_json TEXT,
                source_summary_json TEXT,
                trigger_source TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL UNIQUE,
                value_json TEXT NOT NULL,
                updated_by TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL,
                old_value_json TEXT,
                new_value_json TEXT NOT NULL,
                updated_by TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ========== WORK / INTEGRATIONS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_skill_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                credential_schema_json TEXT,
                capabilities_json TEXT,
                enabled BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                directive TEXT,
                status TEXT DEFAULT 'active',
                priority INTEGER DEFAULT 50,
                default_destination_id INTEGER,
                allowed_skills_json TEXT,
                editorial_policy TEXT,
                seo_policy TEXT,
                autonomy_policy_json TEXT,
                daily_action_limit INTEGER DEFAULT 3,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (default_destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination_key TEXT NOT NULL UNIQUE,
                provider_key TEXT NOT NULL,
                label TEXT NOT NULL,
                base_url TEXT NOT NULL,
                username TEXT NOT NULL,
                secret_ciphertext TEXT NOT NULL,
                default_voice_mode TEXT DEFAULT 'endojung',
                default_delivery_mode TEXT DEFAULT 'draft',
                last_test_status TEXT,
                last_test_message TEXT,
                last_tested_at DATETIME,
                config_json TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                trigger_source TEXT,
                priority INTEGER DEFAULT 50,
                destination_id INTEGER,
                voice_mode TEXT DEFAULT 'endojung',
                delivery_mode TEXT DEFAULT 'draft',
                content_type TEXT DEFAULT 'post',
                objective TEXT NOT NULL,
                source_seed TEXT,
                admin_telegram_id TEXT,
                title_hint TEXT,
                notes TEXT,
                raw_input TEXT,
                extracted_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                phase TEXT DEFAULT 'work',
                trigger_source TEXT,
                selected_brief_id INTEGER,
                destination_id INTEGER,
                status TEXT DEFAULT 'running',
                input_summary TEXT,
                output_summary TEXT,
                metrics_json TEXT,
                errors_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (selected_brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL,
                run_id INTEGER,
                destination_id INTEGER,
                status TEXT DEFAULT 'composed',
                title TEXT,
                excerpt TEXT,
                body TEXT,
                slug TEXT,
                tags_json TEXT,
                categories_json TEXT,
                cta TEXT,
                editorial_note TEXT,
                provider_payload_json TEXT,
                voice_mode TEXT DEFAULT 'endojung',
                content_type TEXT DEFAULT 'post',
                external_id TEXT,
                external_url TEXT,
                published_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (run_id) REFERENCES work_runs(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_approval_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL,
                artifact_id INTEGER NOT NULL,
                destination_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                requested_by TEXT,
                reviewed_by TEXT,
                review_note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME,
                executed_at DATETIME,
                FOREIGN KEY (brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (artifact_id) REFERENCES work_artifacts(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_delivery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                artifact_id INTEGER,
                destination_id INTEGER,
                provider_key TEXT,
                action TEXT,
                status TEXT,
                external_id TEXT,
                external_url TEXT,
                response_json TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES work_approval_tickets(id),
                FOREIGN KEY (artifact_id) REFERENCES work_artifacts(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_experience_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE,
                project_id INTEGER,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_table TEXT,
                source_id TEXT,
                source_kind TEXT DEFAULT 'work',
                metadata_json TEXT,
                rumination_fragment_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES work_projects(id)
            )
        """)

        for table, column_def in [
            ("work_briefs", "project_id INTEGER"),
            ("work_briefs", "action_type TEXT DEFAULT 'create_content'"),
            ("work_runs", "project_id INTEGER"),
            ("work_runs", "autonomy_decision_json TEXT"),
            ("work_artifacts", "project_id INTEGER"),
            ("work_artifacts", "provider_payload_json TEXT"),
            ("work_approval_tickets", "project_id INTEGER"),
            ("work_delivery_events", "project_id INTEGER"),
            ("rumination_fragments", "source_kind TEXT DEFAULT 'conversation'"),
            ("rumination_fragments", "source_table TEXT"),
            ("rumination_fragments", "source_id TEXT"),
            ("rumination_fragments", "source_metadata_json TEXT"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            except sqlite3.OperationalError:
                pass

        # ========== DADOS DO PILOTO UNESCO (JAISD) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unesco_pilot_data (
                user_id TEXT PRIMARY KEY,
                
                baseline_stress_score INTEGER,
                baseline_trait_challenge TEXT,
                baseline_expectation TEXT,
                
                post_test_stress_score INTEGER,
                dossier_accuracy_rating INTEGER,
                
                safety_triggers_count INTEGER DEFAULT 0,
                
                extracted_archetype TEXT,
                primary_cognitive_distortion TEXT,
                qualitative_feedback TEXT,
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== ÃNDICES DE PERFORMANCE ==========
        # Conversas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp DESC)")  # DESC para ORDER BY
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_user_timestamp ON conversations(user_id, timestamp DESC)")  # Composto
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_chroma ON conversations(chroma_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")

        # Conflitos
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_user ON archetype_conflicts(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_conversation ON archetype_conflicts(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_timestamp ON archetype_conflicts(timestamp DESC)")

        # UsuÃ¡rios
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_platform ON users(platform, platform_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC)")

        # Fatos
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_user_category ON user_facts(user_id, fact_category, is_current)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_current ON user_facts(is_current, user_id)")  # Para buscas de fatos atuais

        # PadrÃµes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patterns_user ON user_patterns(user_id, pattern_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patterns_confidence ON user_patterns(confidence_score DESC)")

        # Milestones
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_milestones_type ON milestones(milestone_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_milestones_timestamp ON milestones(timestamp DESC)")

        # AnÃ¡lises
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_user ON full_analyses(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_timestamp ON full_analyses(timestamp DESC)")

        # Psicometria
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_psychometrics_user ON user_psychometrics(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_psychometrics_version ON user_psychometrics(user_id, version DESC)")

        # Lacunas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gaps_user ON knowledge_gaps(user_id, status)")

        # Loop de consciencia
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_events_cycle ON consciousness_loop_events(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_results_cycle ON consciousness_loop_phase_results(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_artifacts_cycle ON consciousness_loop_artifacts(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_meta_consciousness_user_cycle ON agent_meta_consciousness(agent_instance, user_id, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_settings_key ON agent_settings(setting_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_settings_history_key ON agent_settings_history(setting_key, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_states_user_cycle ON agent_will_states(user_id, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_message_signals_cycle ON agent_will_message_signals(user_id, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_pressure_user_cycle ON agent_will_pressure_state(user_id, cycle_id, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_pulse_events_user_cycle ON agent_will_pulse_events(user_id, cycle_id, created_at DESC)")

        # Work / integrations
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_destinations_provider ON work_destinations(provider_key, is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_projects_status ON work_projects(status, priority DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_briefs_status ON work_briefs(status, origin, priority DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_briefs_project ON work_briefs(project_id, status, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_runs_cycle ON work_runs(cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_artifacts_brief ON work_artifacts(brief_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_tickets_status ON work_approval_tickets(status, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_delivery_status ON work_delivery_events(status, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_experience_project ON work_experience_events(project_id, created_at DESC)")
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rumination_fragments_source ON rumination_fragments(source_kind, source_table, source_id)")
        except sqlite3.OperationalError:
            pass

        if hasattr(self, "_init_working_memory_schema"):
            self._init_working_memory_schema()

        self.conn.commit()
        logger.info("âœ… Schema SQLite criado/verificado com Ã­ndices de performance")
