from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PsychometricsDatabaseMixin:
    def analyze_big_five(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Big Five (OCEAN) do usuÃ¡rio via Grok AI

        Retorna dict com scores 0-100 para cada dimensÃ£o:
        - openness, conscientiousness, extraversion, agreeableness, neuroticism
        """
        logger.info(f"ðŸ§¬ Iniciando anÃ¡lise Big Five para {user_id}")

        # Buscar conversas do usuÃ¡rio
        conversations = self.get_user_conversations(user_id, limit=50)

        if len(conversations) < min_conversations:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mÃ­nimo {min_conversations})",
                "conversations_analyzed": len(conversations)
            }

        # Montar contexto para o Grok
        convo_texts = []
        for c in conversations[:30]:  # Ãšltimas 30 para nÃ£o exceder token limit
            convo_texts.append(f"UsuÃ¡rio: {c['user_input']}")
            convo_texts.append(f"Resposta: {c['ai_response'][:200]}")  # Truncar resposta

        context = "\n\n".join(convo_texts)

        # Prompt para Grok
        prompt = f"""Analise as conversas abaixo e infira os traÃ§os Big Five (OCEAN) do usuÃ¡rio.

CONVERSAS:
{context}

TAREFA:
Para cada dimensÃ£o, dÃª um score de 0-100 e justifique em 2-3 frases:

1. OPENNESS (Abertura): Criatividade, curiosidade intelectual, preferÃªncia por novidade
   - Alto: busca experiÃªncias novas, criativo, imaginativo
   - Baixo: prefere rotina, prÃ¡tico, tradicional

2. CONSCIENTIOUSNESS (Conscienciosidade): OrganizaÃ§Ã£o, autodisciplina, orientaÃ§Ã£o a metas
   - Alto: organizado, responsÃ¡vel, planejado
   - Baixo: espontÃ¢neo, flexÃ­vel, menos estruturado

3. EXTRAVERSION (ExtroversÃ£o): Sociabilidade, assertividade, busca por estimulaÃ§Ã£o
   - Alto: social, energÃ©tico, falante
   - Baixo: reservado, independente, introspectivo

4. AGREEABLENESS (Amabilidade): Empatia, cooperaÃ§Ã£o, confianÃ§a
   - Alto: empÃ¡tico, cooperativo, altruÃ­sta
   - Baixo: analÃ­tico, competitivo, direto

5. NEUROTICISM (Neuroticismo): Ansiedade, instabilidade emocional, vulnerabilidade
   - Alto: ansioso, sensÃ­vel, emocionalmente reativo
   - Baixo: calmo, estÃ¡vel, resiliente

CONSIDERE:
- Temas abordados (projetos criativos = Openness alto)
- Estrutura da comunicaÃ§Ã£o (mensagens organizadas = Conscientiousness alto)
- Tom emocional (ansiedade recorrente = Neuroticism alto)
- MenÃ§Ãµes a relaÃ§Ãµes sociais (solidÃ£o = Extraversion baixo)

Responda APENAS em JSON vÃ¡lido (sem markdown):
{{
    "openness": {{"score": 0-100, "level": "Muito Baixo/Baixo/MÃ©dio/Alto/Muito Alto", "description": "..."}},
    "conscientiousness": {{"score": 0-100, "level": "...", "description": "..."}},
    "extraversion": {{"score": 0-100, "level": "...", "description": "..."}},
    "agreeableness": {{"score": 0-100, "level": "...", "description": "..."}},
    "neuroticism": {{"score": 0-100, "level": "...", "description": "..."}},
    "confidence": 0-100,
    "interpretation": "Resumo do perfil em 2-3 frases para RH"
}}
"""

        try:
            # Usar Claude Sonnet para anÃ¡lises psicomÃ©tricas (melhor precisÃ£o)
            from llm_providers import create_llm_provider

            claude_provider = create_llm_provider("claude")
            response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=1500)

            # Usar parser robusto
            result = self._parse_json_response(response)

            # Adicionar metadados
            result["conversations_analyzed"] = len(conversations)
            result["analysis_date"] = datetime.now().isoformat()
            result["model_used"] = claude_provider.get_model_name()

            logger.info(f"âœ… Big Five analisado (Claude): O={result['openness']['score']}, C={result['conscientiousness']['score']}, E={result['extraversion']['score']}, A={result['agreeableness']['score']}, N={result['neuroticism']['score']}")

            return result

        except Exception as e:
            logger.error(f"âŒ Erro ao analisar Big Five: {e}")
            logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
            return {
                "error": str(e),
                "conversations_analyzed": len(conversations)
            }

    def analyze_emotional_intelligence(self, user_id: str) -> Dict:
        """
        Calcula InteligÃªncia Emocional (EQ) baseado em dados jÃ¡ coletados

        4 Componentes:
        1. AutoconsciÃªncia (self_awareness_score do banco)
        2. AutogestÃ£o (variaÃ§Ã£o de tension_level)
        3. ConsciÃªncia Social (menÃ§Ãµes a outros)
        4. GestÃ£o de Relacionamentos (evoluÃ§Ã£o de conflitos)
        """
        logger.info(f"ðŸ’– Iniciando anÃ¡lise EQ para {user_id}")

        # 1. AutoconsciÃªncia - pegar do agent_development do usuÃ¡rio
        cursor = self.conn.cursor()
        cursor.execute("SELECT self_awareness_score FROM agent_development WHERE user_id = ?", (user_id,))
        agent_state = cursor.fetchone()
        self_awareness_raw = agent_state['self_awareness_score'] if agent_state else 0.0
        self_awareness = int(min(100, self_awareness_raw * 100))  # Normalizar para 0-100

        # 2. AutogestÃ£o - analisar variaÃ§Ã£o de tension_level
        conversations = self.get_user_conversations(user_id, limit=50)
        if len(conversations) < 10:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mÃ­nimo 10)",
                "conversations_analyzed": len(conversations)
            }

        tensions = [c.get('tension_level', 5.0) for c in conversations if c.get('tension_level')]
        if tensions:
            import statistics
            avg_tension = statistics.mean(tensions)
            std_tension = statistics.stdev(tensions) if len(tensions) > 1 else 0
            # Menor desvio padrÃ£o = melhor autogestÃ£o
            self_management = int(max(0, min(100, 100 - (std_tension * 15))))
        else:
            self_management = 50  # Default mÃ©dio

        # 3. ConsciÃªncia Social - contar menÃ§Ãµes a "outros", "equipe", "famÃ­lia", etc
        social_keywords = ['outros', 'equipe', 'famÃ­lia', 'amigos', 'colegas', 'pessoas', 'eles', 'ela', 'ele']
        social_mentions = 0
        total_words = 0

        for c in conversations:
            user_input_lower = c['user_input'].lower()
            words = user_input_lower.split()
            total_words += len(words)
            for keyword in social_keywords:
                social_mentions += user_input_lower.count(keyword)

        social_ratio = (social_mentions / max(1, total_words)) * 1000  # Normalizar
        social_awareness = int(min(100, social_ratio * 30 + 40))  # Base 40, atÃ© 100

        # 4. GestÃ£o de Relacionamentos - analisar conflitos Persona vs outros
        conflicts = self.get_user_conflicts(user_id, limit=100)
        persona_conflicts = [c for c in conflicts if 'persona' in c['archetype1'].lower() or 'persona' in c['archetype2'].lower()]

        if len(persona_conflicts) > 5:
            # Analisar se conflitos diminuem com o tempo (sinal de melhoria)
            recent_conflicts = persona_conflicts[:len(persona_conflicts)//2]
            old_conflicts = persona_conflicts[len(persona_conflicts)//2:]

            recent_avg_tension = statistics.mean([c.get('tension_level', 5.0) for c in recent_conflicts]) if recent_conflicts else 5.0
            old_avg_tension = statistics.mean([c.get('tension_level', 5.0) for c in old_conflicts]) if old_conflicts else 5.0

            improvement = ((old_avg_tension - recent_avg_tension) / max(0.1, old_avg_tension)) * 100
            relationship_management = int(min(100, max(30, 60 + improvement * 2)))
        else:
            relationship_management = 60  # Default mÃ©dio-alto

        # Calcular EQ geral
        eq_overall = int((self_awareness + self_management + social_awareness + relationship_management) / 4)

        # Determinar potencial de lideranÃ§a
        if eq_overall >= 75:
            leadership_potential = "Alto"
        elif eq_overall >= 60:
            leadership_potential = "MÃ©dio-Alto"
        elif eq_overall >= 45:
            leadership_potential = "MÃ©dio"
        else:
            leadership_potential = "Baixo"

        result = {
            "self_awareness": {
                "score": self_awareness,
                "level": self._get_level(self_awareness),
                "description": "Capacidade de reconhecer emoÃ§Ãµes e padrÃµes prÃ³prios"
            },
            "self_management": {
                "score": self_management,
                "level": self._get_level(self_management),
                "description": "Capacidade de regular emoÃ§Ãµes e manter equilÃ­brio"
            },
            "social_awareness": {
                "score": social_awareness,
                "level": self._get_level(social_awareness),
                "description": "Capacidade de perceber emoÃ§Ãµes e necessidades alheias"
            },
            "relationship_management": {
                "score": relationship_management,
                "level": self._get_level(relationship_management),
                "description": "Capacidade de influenciar e conectar-se com outros"
            },
            "overall_eq": eq_overall,
            "leadership_potential": leadership_potential,
            "conversations_analyzed": len(conversations),
            "analysis_date": datetime.now().isoformat()
        }

        logger.info(f"âœ… EQ analisado: Overall={eq_overall}, LideranÃ§a={leadership_potential}")

        return result

    def _get_level(self, score: int) -> str:
        """Helper para converter score em nÃ­vel textual"""
        if score >= 80:
            return "Muito Alto"
        elif score >= 65:
            return "Alto"
        elif score >= 45:
            return "MÃ©dio"
        elif score >= 30:
            return "Baixo"
        else:
            return "Muito Baixo"

    def _parse_json_response(self, response: str) -> Dict:
        """
        Parse robusto de resposta JSON do LLM
        Remove markdown code blocks e trata erros comuns
        """
        import json as json_lib
        import re

        # Remover espaÃ§os em branco nas extremidades
        response = response.strip()

        # Remover markdown code blocks (```json ... ``` ou ``` ... ```)
        if response.startswith("```"):
            # Encontrar o conteÃºdo entre ``` e ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', response, re.DOTALL)
            if match:
                response = match.group(1).strip()

        # Tentar remover texto antes do JSON (Ã s vezes o LLM adiciona explicaÃ§Ãµes)
        if not response.startswith('{') and not response.startswith('['):
            # Procurar o primeiro { ou [
            json_start = min(
                response.find('{') if response.find('{') != -1 else len(response),
                response.find('[') if response.find('[') != -1 else len(response)
            )
            if json_start < len(response):
                response = response[json_start:]

        # Tentar parse
        try:
            return json_lib.loads(response)
        except json_lib.JSONDecodeError as e:
            logger.error(f"âŒ Erro ao fazer parse de JSON: {e}")
            logger.error(f"Resposta recebida: {response[:500]}...")
            raise ValueError(f"Resposta LLM nÃ£o Ã© JSON vÃ¡lido: {str(e)}")

    def analyze_learning_style(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Estilos de Aprendizagem (VARK) via Grok AI

        VARK:
        - Visual, Auditory, Reading/Writing, Kinesthetic
        """
        logger.info(f"ðŸ“š Iniciando anÃ¡lise VARK para {user_id}")

        conversations = self.get_user_conversations(user_id, limit=40)

        if len(conversations) < min_conversations:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mÃ­nimo {min_conversations})",
                "conversations_analyzed": len(conversations)
            }

        # Montar contexto
        user_messages = [c['user_input'] for c in conversations[:25]]
        context = "\n\n".join([f"Mensagem {i+1}: {msg}" for i, msg in enumerate(user_messages)])

        prompt = f"""Analise o estilo de comunicaÃ§Ã£o do usuÃ¡rio e infira seu estilo de aprendizagem VARK.

MENSAGENS DO USUÃRIO:
{context}

INDICADORES:

VISUAL (V):
- Usa palavras: "vejo", "imagem", "parece", "claro", "visualizo", "mostra"
- Menciona grÃ¡ficos, diagramas, cores, formas
- Pede explicaÃ§Ãµes visuais

AUDITIVO (A):
- Usa palavras: "ouÃ§o", "soa", "ritmo", "harmonia", "escuto", "fala"
- Menciona mÃºsicas, podcasts, conversas, tom de voz
- Prefere explicaÃ§Ãµes verbais

LEITURA/ESCRITA (R):
- Mensagens longas e estruturadas
- Usa listas, tÃ³picos, citaÃ§Ãµes, referÃªncias
- Menciona livros, artigos, documentaÃ§Ã£o, pesquisa
- VocabulÃ¡rio rico e formal

CINESTÃ‰SICO (K):
- Usa palavras: "sinto", "toque", "movimento", "prÃ¡tica", "experiÃªncia"
- Menciona fazer, experimentar, testar, agir
- Foco em sensaÃ§Ãµes fÃ­sicas e aÃ§Ã£o

Responda APENAS em JSON vÃ¡lido (sem markdown):
{{
    "visual": 0-100,
    "auditory": 0-100,
    "reading": 0-100,
    "kinesthetic": 0-100,
    "dominant_style": "Visual/Auditivo/Leitura/CinestÃ©sico",
    "recommended_training": "SugestÃ£o de formato de treinamento ideal para este perfil"
}}

IMPORTANTE: Os 4 scores devem somar aproximadamente 100.
"""

        try:
            # Usar Claude Sonnet para anÃ¡lises psicomÃ©tricas (melhor precisÃ£o)
            from llm_providers import create_llm_provider

            claude_provider = create_llm_provider("claude")
            response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=800)

            # Usar parser robusto
            result = self._parse_json_response(response)

            result["conversations_analyzed"] = len(conversations)
            result["analysis_date"] = datetime.now().isoformat()
            result["model_used"] = claude_provider.get_model_name()

            logger.info(f"âœ… VARK analisado (Claude): Dominante={result['dominant_style']}")

            return result

        except Exception as e:
            logger.error(f"âŒ Erro ao analisar VARK: {e}")
            logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
            return {
                "error": str(e),
                "conversations_analyzed": len(conversations)
            }

    def analyze_personal_values(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Valores Pessoais (Schwartz) via extraÃ§Ã£o de user_facts + Grok AI

        10 Valores Universais de Schwartz
        """
        logger.info(f"â­ Iniciando anÃ¡lise Valores Schwartz para {user_id}")

        # Primeiro tentar buscar de user_facts categoria 'values'
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT fact_key, fact_value, confidence
            FROM user_facts
            WHERE user_id = ? AND fact_category = 'values' AND is_current = 1
            ORDER BY confidence DESC
        """, (user_id,))

        existing_values = cursor.fetchall()

        # Se tiver menos de 3 valores, usar Grok para inferir
        if len(existing_values) < 3:
            conversations = self.get_user_conversations(user_id, limit=40)

            if len(conversations) < min_conversations:
                return {
                    "error": f"Dados insuficientes ({len(conversations)} conversas, mÃ­nimo {min_conversations})",
                    "conversations_analyzed": len(conversations)
                }

            # Montar contexto
            convo_texts = []
            for c in conversations[:25]:
                convo_texts.append(f"{c['user_input']}")
            context = "\n\n".join(convo_texts)

            prompt = f"""Analise as mensagens do usuÃ¡rio e identifique seus valores pessoais segundo a teoria de Schwartz.

MENSAGENS:
{context}

10 VALORES UNIVERSAIS DE SCHWARTZ:

1. AUTODIREÃ‡ÃƒO: IndependÃªncia, criatividade, exploraÃ§Ã£o, liberdade de pensamento
2. ESTIMULAÃ‡ÃƒO: Novidade, desafios, excitaÃ§Ã£o, vida variada
3. HEDONISMO: Prazer, gratificaÃ§Ã£o sensorial, aproveitar a vida
4. REALIZAÃ‡ÃƒO: Sucesso pessoal, competÃªncia, ambiÃ§Ã£o, reconhecimento
5. PODER: Status social, prestÃ­gio, controle sobre recursos/pessoas
6. SEGURANÃ‡A: ProteÃ§Ã£o, ordem, estabilidade, harmonia
7. CONFORMIDADE: RestriÃ§Ã£o de aÃ§Ãµes que violam normas sociais, autodisciplina
8. TRADIÃ‡ÃƒO: Respeito por costumes culturais/religiosos, humildade
9. BENEVOLÃŠNCIA: Bem-estar de pessoas prÃ³ximas, ajudar, honestidade
10. UNIVERSALISMO: CompreensÃ£o, tolerÃ¢ncia, justiÃ§a social, proteÃ§Ã£o da natureza

Identifique os 3 valores MAIS FORTES do usuÃ¡rio.

Responda APENAS em JSON vÃ¡lido (sem markdown):
{{
    "self_direction": {{"score": 0-100, "evidences": ["evidÃªncia 1", "evidÃªncia 2"]}},
    "stimulation": {{"score": 0-100, "evidences": []}},
    "hedonism": {{"score": 0-100, "evidences": []}},
    "achievement": {{"score": 0-100, "evidences": []}},
    "power": {{"score": 0-100, "evidences": []}},
    "security": {{"score": 0-100, "evidences": []}},
    "conformity": {{"score": 0-100, "evidences": []}},
    "tradition": {{"score": 0-100, "evidences": []}},
    "benevolence": {{"score": 0-100, "evidences": []}},
    "universalism": {{"score": 0-100, "evidences": []}},
    "top_3_values": ["Valor 1", "Valor 2", "Valor 3"],
    "cultural_fit": "DescriÃ§Ã£o de ambientes/culturas onde este perfil prospera",
    "retention_risk": "Baixo/MÃ©dio/Alto - baseado em alinhamento de valores"
}}
"""

            try:
                # Usar Claude Sonnet para anÃ¡lises psicomÃ©tricas (melhor precisÃ£o)
                from llm_providers import create_llm_provider

                claude_provider = create_llm_provider("claude")
                response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=1800)

                # Usar parser robusto
                result = self._parse_json_response(response)

                result["conversations_analyzed"] = len(conversations)
                result["analysis_date"] = datetime.now().isoformat()
                result["source"] = "claude_inference"
                result["model_used"] = claude_provider.get_model_name()

                logger.info(f"âœ… Valores analisados (Claude): Top 3={result['top_3_values']}")

                return result

            except Exception as e:
                logger.error(f"âŒ Erro ao analisar valores: {e}")
                logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
                return {
                    "error": str(e),
                    "conversations_analyzed": len(conversations)
                }

        else:
            # Construir resultado a partir de user_facts existentes
            logger.info(f"âœ… Valores extraÃ­dos de user_facts ({len(existing_values)} encontrados)")

            # Mapear fatos para valores de Schwartz (simplificado)
            result = {
                "self_direction": {"score": 0, "evidences": []},
                "stimulation": {"score": 0, "evidences": []},
                "hedonism": {"score": 0, "evidences": []},
                "achievement": {"score": 0, "evidences": []},
                "power": {"score": 0, "evidences": []},
                "security": {"score": 0, "evidences": []},
                "conformity": {"score": 0, "evidences": []},
                "tradition": {"score": 0, "evidences": []},
                "benevolence": {"score": 0, "evidences": []},
                "universalism": {"score": 0, "evidences": []},
                "top_3_values": [],
                "cultural_fit": "A determinar com mais dados",
                "retention_risk": "MÃ©dio",
                "source": "user_facts",
                "conversations_analyzed": 0,
                "analysis_date": datetime.now().isoformat()
            }

            # ClassificaÃ§Ã£o bÃ¡sica (pode ser melhorada)
            for fact in existing_values:
                key = fact['fact_key'].lower()
                value = fact['fact_value'].lower()
                confidence = fact['confidence'] * 100

                if any(word in key+value for word in ['independÃªncia', 'criatividade', 'autonomia']):
                    result["self_direction"]["score"] = max(result["self_direction"]["score"], int(confidence))
                    result["self_direction"]["evidences"].append(fact['fact_value'])

                if any(word in key+value for word in ['sucesso', 'realizaÃ§Ã£o', 'ambiÃ§Ã£o']):
                    result["achievement"]["score"] = max(result["achievement"]["score"], int(confidence))
                    result["achievement"]["evidences"].append(fact['fact_value'])

                # Adicionar mais mapeamentos conforme necessÃ¡rio

            # Identificar top 3
            values_scores = {k: v["score"] for k, v in result.items() if isinstance(v, dict) and "score" in v}
            sorted_values = sorted(values_scores.items(), key=lambda x: x[1], reverse=True)
            result["top_3_values"] = [k.replace("_", " ").title() for k, _ in sorted_values[:3] if sorted_values[0][1] > 0]

            return result

    def save_psychometrics(self, user_id: str, big_five: Dict, eq: Dict, vark: Dict, values: Dict) -> None:
        """
        Salva anÃ¡lises psicomÃ©tricas no banco
        """
        logger.info(f"ðŸ’¾ Salvando anÃ¡lises psicomÃ©tricas para {user_id}")

        # Verificar se jÃ¡ existe anÃ¡lise (para versionamento)
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(version) as max_version FROM user_psychometrics WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        version = (row['max_version'] or 0) + 1 if row else 1

        # Preparar dados
        import json as json_lib

        # Big Five
        bf_o = big_five.get('openness', {})
        bf_c = big_five.get('conscientiousness', {})
        bf_e = big_five.get('extraversion', {})
        bf_a = big_five.get('agreeableness', {})
        bf_n = big_five.get('neuroticism', {})

        # EQ
        eq_sa = eq.get('self_awareness', {})
        eq_sm = eq.get('self_management', {})
        eq_soc = eq.get('social_awareness', {})
        eq_rm = eq.get('relationship_management', {})

        # Resumo executivo
        executive_summary = json_lib.dumps({
            "profile": f"Big Five: O{bf_o.get('score', 0)}, C{bf_c.get('score', 0)}, E{bf_e.get('score', 0)}, A{bf_a.get('score', 0)}, N{bf_n.get('score', 0)} | EQ: {eq.get('overall_eq', 0)}",
            "strengths": big_five.get('interpretation', 'N/A')[:200],
            "development_areas": f"EQ LideranÃ§a: {eq.get('leadership_potential', 'N/A')}",
            "organizational_fit": values.get('cultural_fit', 'A determinar'),
            "recommendations": f"Estilo de aprendizagem: {vark.get('dominant_style', 'N/A')}"
        })

        # Insert
        cursor.execute("""
            INSERT INTO user_psychometrics (
                user_id, version,
                openness_score, openness_level, openness_description,
                conscientiousness_score, conscientiousness_level, conscientiousness_description,
                extraversion_score, extraversion_level, extraversion_description,
                agreeableness_score, agreeableness_level, agreeableness_description,
                neuroticism_score, neuroticism_level, neuroticism_description,
                big_five_confidence, big_five_interpretation,
                eq_self_awareness, eq_self_management, eq_social_awareness, eq_relationship_management,
                eq_overall, eq_leadership_potential, eq_details,
                vark_visual, vark_auditory, vark_reading, vark_kinesthetic,
                vark_dominant, vark_recommended_training,
                schwartz_values, schwartz_top_3, schwartz_cultural_fit, schwartz_retention_risk,
                executive_summary,
                conversations_analyzed
            ) VALUES (
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?,
                ?
            )
        """, (
            user_id, version,
            bf_o.get('score'), bf_o.get('level'), bf_o.get('description'),
            bf_c.get('score'), bf_c.get('level'), bf_c.get('description'),
            bf_e.get('score'), bf_e.get('level'), bf_e.get('description'),
            bf_a.get('score'), bf_a.get('level'), bf_a.get('description'),
            bf_n.get('score'), bf_n.get('level'), bf_n.get('description'),
            big_five.get('confidence'), big_five.get('interpretation'),
            eq_sa.get('score'), eq_sm.get('score'), eq_soc.get('score'), eq_rm.get('score'),
            eq.get('overall_eq'), eq.get('leadership_potential'), json_lib.dumps(eq),
            vark.get('visual'), vark.get('auditory'), vark.get('reading'), vark.get('kinesthetic'),
            vark.get('dominant_style'), vark.get('recommended_training'),
            json_lib.dumps(values), ','.join(values.get('top_3_values', [])),
            values.get('cultural_fit'), values.get('retention_risk'),
            executive_summary,
            big_five.get('conversations_analyzed', 0)
        ))

        self.conn.commit()
        logger.info(f"âœ… AnÃ¡lises psicomÃ©tricas salvas (versÃ£o {version})")

    def get_psychometrics(self, user_id: str, version: int = None) -> Optional[Dict]:
        """
        Busca anÃ¡lises psicomÃ©tricas do usuÃ¡rio
        Se version nÃ£o especificado, retorna a mais recente
        """
        cursor = self.conn.cursor()

        if version:
            cursor.execute("""
                SELECT * FROM user_psychometrics
                WHERE user_id = ? AND version = ?
            """, (user_id, version))
        else:
            cursor.execute("""
                SELECT * FROM user_psychometrics
                WHERE user_id = ?
                ORDER BY version DESC
                LIMIT 1
            """, (user_id,))

        row = cursor.fetchone()
        return dict(row) if row else None

    # ========================================
