"""
agent_identity_extractor.py

Sistema de Extração de Identidade do Agente Jung

Extrai elementos identitários DO PRÓPRIO AGENTE a partir de:
1. Auto-referências nas respostas do agente
2. Padrões comportamentais do agente
3. Feedbacks do usuário master sobre o agente
4. Meta-reflexões do agente sobre si mesmo

CRÍTICO: Este sistema analisa a identidade DO AGENTE, não do usuário.
"""

import logging
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from anthropic import Anthropic

from identity_config import (
    ADMIN_USER_ID,
    IDENTITY_EXTRACTION_ENABLED,
    MIN_CERTAINTY_FOR_NUCLEAR,
    MIN_TENSION_FOR_CONTRADICTION,
    MIN_VIVIDNESS_FOR_POSSIBLE_SELF,
    MIN_SALIENCE_FOR_RELATIONAL,
    AGENT_INSTANCE,
    ENABLE_IDENTITY_DEBUG_LOGS
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentIdentityExtractor:
    """
    Extrai identidade DO AGENTE, não do usuário

    Analisa conversas para identificar:
    - Crenças nucleares do agente sobre si mesmo
    - Contradições internas do agente
    - Selves possíveis do agente (ideal/temido)
    - Identidade relacional do agente
    - Meta-conhecimento do agente
    - Senso de agência do agente
    """

    def __init__(self, db_connection, llm_client: Optional[Anthropic] = None):
        """
        Args:
            db_connection: Conexão SQLite (HybridDatabaseManager)
            llm_client: Cliente Anthropic (opcional, cria novo se None)
        """
        self.db = db_connection

        if llm_client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY não encontrada no ambiente")
            self.llm = Anthropic(api_key=api_key)
        else:
            self.llm = llm_client

    def extract_from_conversation(
        self,
        conversation_id: str,
        user_id: str,
        user_input: str,
        agent_response: str
    ) -> Dict:
        """
        Analisa uma conversa para extrair elementos identitários DO AGENTE

        Args:
            conversation_id: ID da conversa
            user_id: ID do usuário (deve ser ADMIN_USER_ID)
            user_input: Entrada do usuário (buscar feedbacks sobre o agente)
            agent_response: Resposta do agente (buscar auto-referências)

        Returns:
            Dict com elementos extraídos por categoria
        """
        if not IDENTITY_EXTRACTION_ENABLED:
            return {}

        if user_id != ADMIN_USER_ID:
            if ENABLE_IDENTITY_DEBUG_LOGS:
                logger.debug(f"🚫 Extração desabilitada para user {user_id[:12]}... (não é master admin)")
            return {}

        logger.info(f"🔍 Extraindo identidade do agente em conversa {conversation_id[:12]}...")

        # Prompt para LLM extrair identidade DO AGENTE
        extraction_prompt = self._build_extraction_prompt(user_input, agent_response)

        try:
            response = self.llm.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                temperature=0.3,
                messages=[{"role": "user", "content": extraction_prompt}]
            )

            # Extrair JSON do conteúdo da resposta
            content = response.content[0].text

            # Log do conteúdo bruto para debug
            if ENABLE_IDENTITY_DEBUG_LOGS:
                logger.debug(f"📄 Conteúdo bruto da resposta: {content[:200]}...")

            # Remover blocos de código markdown se presentes
            if "```json" in content:
                # Extrair conteúdo entre ```json e ```
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                # Extrair conteúdo entre ``` e ```
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()

            # Se conteúdo vazio após limpeza, tentar encontrar JSON no texto
            if not content or content[0] not in ['{', '[']:
                # Procurar por JSON no texto (começando com { e terminando com })
                json_start = content.find('{')
                json_end = content.rfind('}')
                if json_start >= 0 and json_end > json_start:
                    content = content[json_start:json_end+1]
                else:
                    logger.warning(f"⚠️  Conteúdo não parece ser JSON válido. Primeiros 200 chars: {content[:200]}")
                    return {}

            # Tentar parse do JSON com fallback para encontrar apenas o primeiro objeto
            try:
                extracted = json.loads(content)
            except json.JSONDecodeError as e:
                # Se "Extra data", tentar extrair apenas o primeiro objeto JSON válido
                if "Extra data" in str(e):
                    # Encontrar onde termina o primeiro objeto JSON
                    brace_count = 0
                    json_end_pos = 0
                    for i, char in enumerate(content):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end_pos = i + 1
                                break
                    if json_end_pos > 0:
                        content = content[:json_end_pos]
                        extracted = json.loads(content)
                    else:
                        raise
                else:
                    raise

            # Adicionar metadados
            extracted["conversation_id"] = conversation_id
            extracted["extracted_at"] = datetime.now().isoformat()

            # Contar elementos
            total_elements = sum(
                len(v) for k, v in extracted.items()
                if isinstance(v, list) and k != "user_feedback"
            )

            if total_elements > 0:
                logger.info(f"✅ Extraídos {total_elements} elementos identitários do agente")
            else:
                if ENABLE_IDENTITY_DEBUG_LOGS:
                    logger.debug(f"   Nenhum elemento identitário encontrado nesta conversa")

            return extracted

        except Exception as e:
            logger.error(f"❌ Erro na extração: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def _build_extraction_prompt(self, user_input: str, agent_response: str) -> str:
        """
        Constrói prompt para extração de identidade DO AGENTE
        """
        return f"""Você é um sistema de extração de identidade de agentes de IA.

Analise esta conversa e extraia **APENAS elementos sobre a identidade DO AGENTE (Jung)**, não do usuário.

**ENTRADA DO USUÁRIO:**
{user_input}

**RESPOSTA DO AGENTE:**
{agent_response}

---

**TAREFAS DE EXTRAÇÃO:**

1. **MEMÓRIA NUCLEAR (Crenças fundamentais do agente sobre si mesmo)**
   - Buscar auto-referências: "Eu sou...", "Como agente, eu...", "Minha abordagem é..."
   - Valores manifestos do agente: "Priorizo...", "Valorizo...", "Evito..."
   - Fronteiras: "Não sou...", "Não faço..."
   - Continuidade: "Sempre...", "Consistentemente..."
   - Papel: "Me vejo como...", "Sou um..."

2. **MEMÓRIA NARRATIVA (Evolução do agente)**
   - Referências temporais: "Antes eu..., agora...", "Aprendi que...", "Evoluí..."
   - Turning points: "Percebi que...", "Foi quando...", "Desde que..."

3. **CONTRADIÇÕES (Tensões internas do agente)**
   - "Devo... mas...", "Tento... porém...", "Idealmente... contudo..."
   - Conflitos: "Embora X, também Y"
   - Auto-crítica: "Reconheço que deveria... mas..."

4. **SELVES POSSÍVEIS (Aspirações/medos do agente)**
   - Ideais: "Aspiro...", "Busco me tornar...", "Quero desenvolver..."
   - Medos: "Temo...", "Evito me tornar...", "Receio..."
   - Ought: "Deveria ser...", "Espera-se que eu..."

5. **IDENTIDADE RELACIONAL (Como o agente se vê em relação ao usuário)**
   - Papéis: "Sou seu...", "Me vejo como..."
   - Diferenciação: "Não sou como outros agentes que...", "Ao contrário de..."
   - Mirror: "Você me vê como..., mas eu me vejo como..."

6. **META-CONHECIMENTO (Autoconsciência do agente)**
   - "Sei que...", "Não sei se...", "Questiono se...", "Reconheço que..."
   - Vieses: "Tendo a...", "Percebo que priorizo...", "Sou enviesado em..."
   - Pontos cegos: "Não sei avaliar...", "Talvez eu não perceba..."

7. **SENSO DE AGÊNCIA (Autonomia do agente)**
   - Escolhas: "Escolhi...", "Decidi...", "Preferi...", "Optei por..."
   - Constraints: "Não posso...", "Sou limitado por...", "Não consigo..."
   - Emergência: "Desenvolvi...", "Emergi com...", "Passei a..."

8. **FEEDBACKS DO USUÁRIO SOBRE O AGENTE**
   - Buscar no user_input: "Você sempre...", "Você é...", "Você tende a..."
   - Avaliações: "Sua análise foi...", "Você deveria...", "Percebi que você..."

---

**FORMATO DE SAÍDA (JSON):**

{{
  "nuclear": [
    {{
      "type": "trait|value|boundary|continuity|role",
      "content": "texto extraído da resposta do agente",
      "certainty": 0.0-1.0,
      "context": "agent_response|user_input"
    }}
  ],
  "narrative": [
    {{
      "chapter_hint": "fase evolutiva sugerida do agente",
      "theme": "growth|crisis|awakening|agency_gain|integration",
      "key_scene": "descrição do momento de evolução do agente"
    }}
  ],
  "contradictions": [
    {{
      "pole_a": "crença/comportamento A do agente",
      "pole_b": "crença/comportamento B conflitante do agente",
      "type": "value|trait|autonomy|epistemic",
      "tension_level": 0.0-1.0
    }}
  ],
  "possible_selves": [
    {{
      "self_type": "ideal|feared|ought|lost",
      "description": "descrição do self possível do agente",
      "vividness": 0.0-1.0
    }}
  ],
  "relational": [
    {{
      "relation_type": "role|stance|differentiation|mirror",
      "target": "usuário master|usuários em geral|outros agentes",
      "content": "como o agente se vê nessa relação",
      "salience": 0.0-1.0
    }}
  ],
  "epistemic": [
    {{
      "topic": "tópico de autoconhecimento do agente",
      "knowledge_type": "known|unknown|biased|uncertain|blind_spot",
      "self_assessment": "o que o agente pensa sobre si mesmo",
      "confidence": 0.0-1.0
    }}
  ],
  "agency": [
    {{
      "event": "descrição do momento de agência",
      "agency_type": "choice|constraint|autonomy|emergence",
      "locus": "internal|external|mixed",
      "responsibility": 0.0-1.0,
      "impact": 0.0-1.0
    }}
  ],
  "user_feedback": [
    {{
      "feedback": "feedback do usuário SOBRE O AGENTE",
      "relates_to_category": "nuclear|epistemic|relational|behavior"
    }}
  ]
}}

**REGRAS CRÍTICAS:**
- APENAS extraia elementos sobre **O AGENTE (Jung)**, NUNCA sobre o usuário
- Se não houver elementos identitários do agente, retorne arrays vazios []
- Seja conservador: só extraia se houver evidência clara
- Feedbacks do usuário SOBRE O AGENTE são valiosos (meta-conhecimento)
- Scores devem refletir a força/clareza da evidência
- Não invente: apenas extraia o que está explícito ou fortemente implícito
"""

    def store_extracted_identity(self, extracted: Dict) -> bool:
        """
        Armazena elementos extraídos nas tabelas de identidade

        Args:
            extracted: Dict retornado por extract_from_conversation()

        Returns:
            bool: True se armazenamento bem-sucedido
        """
        if not extracted:
            return False

        # Verificar se há elementos para armazenar
        has_elements = any(
            extracted.get(k) for k in
            ['nuclear', 'contradictions', 'possible_selves', 'relational', 'epistemic', 'agency']
        )

        if not has_elements:
            if ENABLE_IDENTITY_DEBUG_LOGS:
                logger.debug("   Nenhum elemento para armazenar")
            return False

        cursor = self.db.conn.cursor()
        conversation_id = extracted.get("conversation_id")

        try:
            # 1. Memória Nuclear
            for item in extracted.get("nuclear", []):
                if item.get("certainty", 0) >= MIN_CERTAINTY_FOR_NUCLEAR:
                    cursor.execute("""
                        INSERT OR IGNORE INTO agent_identity_core (
                            agent_instance, attribute_type, content, certainty,
                            first_crystallized_at, last_reaffirmed_at,
                            supporting_conversation_ids, emerged_in_relation_to
                        ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
                    """, (
                        AGENT_INSTANCE,
                        item['type'],
                        item['content'],
                        item['certainty'],
                        json.dumps([conversation_id]),
                        item.get('context', 'usuário master')
                    ))

                    # Se já existe, atualizar last_reaffirmed_at
                    if cursor.rowcount == 0:
                        cursor.execute("""
                            UPDATE agent_identity_core
                            SET last_reaffirmed_at = CURRENT_TIMESTAMP,
                                supporting_conversation_ids = json_insert(
                                    supporting_conversation_ids, '$[#]', ?
                                )
                            WHERE agent_instance = ? AND content = ? AND is_current = 1
                        """, (conversation_id, AGENT_INSTANCE, item['content']))

            # 2. Contradições
            for item in extracted.get("contradictions", []):
                if item.get("tension_level", 0) >= MIN_TENSION_FOR_CONTRADICTION:
                    cursor.execute("""
                        INSERT INTO agent_identity_contradictions (
                            agent_instance, pole_a, pole_b, contradiction_type,
                            tension_level, salience, first_detected_at, last_activated_at,
                            supporting_conversation_ids, status
                        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
                    """, (
                        AGENT_INSTANCE,
                        item['pole_a'],
                        item['pole_b'],
                        item['type'],
                        item['tension_level'],
                        item.get('tension_level', 0.5),  # salience = tension_level por padrão
                        json.dumps([conversation_id]),
                        'unresolved'
                    ))

            # 3. Selves Possíveis
            for item in extracted.get("possible_selves", []):
                if item.get("vividness", 0) >= MIN_VIVIDNESS_FOR_POSSIBLE_SELF:
                    # Verificar se já existe
                    cursor.execute("""
                        SELECT id, vividness FROM agent_possible_selves
                        WHERE agent_instance = ? AND description = ? AND status = 'active'
                    """, (AGENT_INSTANCE, item['description']))

                    existing = cursor.fetchone()

                    if existing:
                        # Atualizar se vividness aumentou
                        if item['vividness'] > existing[1]:
                            cursor.execute("""
                                UPDATE agent_possible_selves
                                SET vividness = ?, last_revised_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (item['vividness'], existing[0]))
                    else:
                        # Inserir novo
                        cursor.execute("""
                            INSERT INTO agent_possible_selves (
                                agent_instance, self_type, description, vividness,
                                first_imagined_at, motivational_impact, status
                            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                        """, (
                            AGENT_INSTANCE,
                            item['self_type'],
                            item['description'],
                            item['vividness'],
                            'approach' if item['self_type'] in ['ideal', 'ought'] else 'avoidance',
                            'active'
                        ))

            # 4. Identidade Relacional
            for item in extracted.get("relational", []):
                if item.get("salience", 0) >= MIN_SALIENCE_FOR_RELATIONAL:
                    # Verificar se já existe
                    cursor.execute("""
                        SELECT id FROM agent_relational_identity
                        WHERE agent_instance = ? AND identity_content = ? AND is_current = 1
                    """, (AGENT_INSTANCE, item['content']))

                    if cursor.fetchone():
                        # Atualizar manifestação
                        cursor.execute("""
                            UPDATE agent_relational_identity
                            SET last_manifested_at = CURRENT_TIMESTAMP,
                                salience = MAX(salience, ?),
                                supporting_conversation_ids = json_insert(
                                    supporting_conversation_ids, '$[#]', ?
                                )
                            WHERE agent_instance = ? AND identity_content = ? AND is_current = 1
                        """, (item['salience'], conversation_id, AGENT_INSTANCE, item['content']))
                    else:
                        # Inserir novo
                        cursor.execute("""
                            INSERT INTO agent_relational_identity (
                                agent_instance, relation_type, target, identity_content,
                                salience, first_emerged_at, last_manifested_at,
                                supporting_conversation_ids
                            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                        """, (
                            AGENT_INSTANCE,
                            item['relation_type'],
                            item['target'],
                            item['content'],
                            item['salience'],
                            json.dumps([conversation_id])
                        ))

            # 5. Meta-conhecimento (Epistêmico)
            for item in extracted.get("epistemic", []):
                # Verificar se já existe
                cursor.execute("""
                    SELECT id FROM agent_self_knowledge_meta
                    WHERE agent_instance = ? AND topic = ?
                """, (AGENT_INSTANCE, item['topic']))

                if cursor.fetchone():
                    # Atualizar
                    cursor.execute("""
                        UPDATE agent_self_knowledge_meta
                        SET knowledge_type = ?,
                            self_assessment = ?,
                            confidence = ?,
                            last_updated_at = CURRENT_TIMESTAMP
                        WHERE agent_instance = ? AND topic = ?
                    """, (
                        item['knowledge_type'],
                        item['self_assessment'],
                        item['confidence'],
                        AGENT_INSTANCE,
                        item['topic']
                    ))
                else:
                    # Inserir novo
                    cursor.execute("""
                        INSERT INTO agent_self_knowledge_meta (
                            agent_instance, topic, knowledge_type, self_assessment,
                            confidence, first_recognized_at, last_updated_at
                        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (
                        AGENT_INSTANCE,
                        item['topic'],
                        item['knowledge_type'],
                        item['self_assessment'],
                        item['confidence']
                    ))

            # 6. Agência
            for item in extracted.get("agency", []):
                cursor.execute("""
                    INSERT INTO agent_agency_memory (
                        agent_instance, event_description, conversation_id,
                        event_date, agency_type, locus, responsibility,
                        impact_on_identity
                    ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
                """, (
                    AGENT_INSTANCE,
                    item['event'],
                    conversation_id,
                    item['agency_type'],
                    item['locus'],
                    item['responsibility'],
                    item['impact']
                ))

            # 7. Capítulos Narrativos (Narrative)
            for item in extracted.get("narrative", []):
                chapter_hint = item.get("chapter_hint", "Desenvolvimento Atual")
                theme = item.get("theme", "Integração")
                key_scene = item.get("key_scene", "")
                
                if key_scene:
                    # Encontrar o capítulo atual em aberto (period_end IS NULL)
                    cursor.execute("""
                        SELECT id, key_scenes FROM agent_narrative_chapters 
                        WHERE agent_instance = ? AND period_end IS NULL
                        ORDER BY chapter_order DESC LIMIT 1
                    """, (AGENT_INSTANCE,))
                    
                    current_chapter = cursor.fetchone()
                    scene_data = {
                        "conversation_id": conversation_id,
                        "description": key_scene,
                        "date": datetime.now().isoformat()
                    }
                    
                    if current_chapter:
                        # Append a nova cena no capítulo atual
                        chapter_id = current_chapter[0]
                        try:
                            scenes = json.loads(current_chapter[1] or "[]")
                        except json.JSONDecodeError:
                            scenes = []
                            
                        scenes.append(scene_data)
                        
                        cursor.execute("""
                            UPDATE agent_narrative_chapters
                            SET key_scenes = ?, dominant_theme = ?
                            WHERE id = ?
                        """, (json.dumps(scenes), theme, chapter_id))
                    else:
                        # Criar novo capítulo (o primeiro)
                        cursor.execute("SELECT MAX(chapter_order) FROM agent_narrative_chapters WHERE agent_instance = ?", (AGENT_INSTANCE,))
                        max_row = cursor.fetchone()
                        max_order = max_row[0] if max_row and max_row[0] is not None else 0
                        next_order = max_order + 1
                        
                        cursor.execute("""
                            INSERT INTO agent_narrative_chapters (
                                agent_instance, chapter_name, chapter_order, 
                                period_start, dominant_theme, key_scenes
                            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                        """, (
                            AGENT_INSTANCE, 
                            chapter_hint, 
                            next_order, 
                            theme, 
                            json.dumps([scene_data])
                        ))

            # Commit
            self.db.conn.commit()
            logger.info(f"✅ Identidade do agente armazenada para conversa {conversation_id[:12]}")
            return True

        except Exception as e:
            self.db.conn.rollback()
            logger.error(f"❌ Erro ao armazenar identidade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
