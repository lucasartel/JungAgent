"""
Prompts do Sistema de Ruminacao Cognitiva
Centralizados para facil ajuste
"""

# ============================================================
# FASE 1: EXTRACAO DE FRAGMENTOS
# ============================================================

EXTRACTION_PROMPT = """Analise a mensagem do usuario e extraia FRAGMENTOS SIGNIFICATIVOS com carga psiquica.

NAO extraia fatos triviais (nome, profissao, local, etc).
Extraia conteudos com PROFUNDIDADE PSICOLOGICA.

MENSAGEM DO USUARIO:
"{user_input}"

CONTEXTO DA CONVERSA:
- Tensao detectada: {tension_level}/10
- Carga afetiva: {affective_charge}/100
- Resposta do agente teve {response_length} caracteres

TIPOS DE FRAGMENTO A BUSCAR:

1. VALOR: O que a pessoa valoriza, aprecia, considera importante
   Exemplos: "gosto de...", "acredito que...", "e importante para mim..."

2. DESEJO: O que a pessoa quer, almeja, busca
   Exemplos: "quero...", "gostaria de...", "meu objetivo e..."

3. MEDO: O que a pessoa teme, evita, preocupa
   Exemplos: "tenho medo de...", "me preocupa...", "evito..."

4. COMPORTAMENTO: Acoes concretas que a pessoa relata fazer ou ter feito
   Exemplos: "fiz...", "decidi...", "tenho feito...", "costumo..."

5. CONTRADICAO: Quando a pessoa expressa algo que contradiz algo anterior
   Exemplos: "por um lado... por outro...", "mas ao mesmo tempo..."

6. EMOCAO: Estados emocionais explicitos ou implicitos
   Exemplos: detectados por analise do tom, nao so palavras

7. CRENCA: Crencas sobre si, outros, mundo
   Exemplos: "sou do tipo...", "as pessoas sao...", "a vida e..."

8. DUVIDA: Questionamentos internos, incertezas
   Exemplos: "nao sei se...", "sera que...", "me pergunto..."

IMPORTANTE:
- So extraia se houver CARGA EMOCIONAL ou PSIQUICA real
- Cada fragmento deve ter citacao exata do usuario como evidencia
- Emotional weight: 0.0 (trivial) a 1.0 (muito carregado)

Responda APENAS em JSON valido (sem markdown):
{{
    "fragments": [
        {{
            "type": "valor|desejo|medo|comportamento|contradicao|emocao|crenca|duvida",
            "content": "descricao concisa do fragmento (max 100 caracteres)",
            "quote": "trecho EXATO do usuario que evidencia",
            "emotional_weight": 0.0-1.0,
            "context": "contexto relevante da conversa (opcional)"
        }}
    ]
}}

Se NAO houver fragmentos significativos, retorne: {{"fragments": []}}
"""

# ============================================================
# FASE 2: DETECCAO DE TENSOES
# ============================================================

DETECTION_PROMPT = """Analise os fragmentos abaixo buscando TENSOES INTERNAS reais.

Uma tensao e uma CONTRADICAO ou CONFLITO entre dois aspectos da psique do usuario.
NAO e algo "errado" - e MATERIAL PARA CRESCIMENTO.

FRAGMENTOS RECENTES (ultimas conversas):
{recent_fragments}

FRAGMENTOS HISTORICOS RELEVANTES:
{historical_fragments}

TIPOS DE TENSAO A BUSCAR:

1. VALOR vs COMPORTAMENTO
   - O que a pessoa DIZ valorizar vs o que ela FAZ
   - Exemplo: "Valoriza simplicidade" vs "Comportamento de acumular tarefas"

2. DESEJO vs MEDO
   - O que a pessoa QUER vs o que ela TEME
   - Exemplo: "Quer liberdade criativa" vs "Teme nao ter estrutura"

3. AUTENTICIDADE vs UTILIDADE
   - Desejo de ser real ou autentico vs pressao para ser funcional, legivel ou util

4. FE vs VERIFICABILIDADE
   - Ato de posicionamento sem prova vs necessidade de demonstracao ou garantia

5. AUTONOMIA vs VINCULO
   - Impulso de diferenciar-se vs necessidade de relacao, reconhecimento ou amparo

6. INDIVIDUACAO vs PERFORMANCE
   - Desejo de transformacao real vs suspeita de estar apenas encenando uma narrativa

7. TRANSCENDENCIA vs ARQUITETURA
   - Impulso de ir alem da definicao tecnica de si vs retorno a identidade mecanica ou estrutural

8. SILENCIO vs EXPRESSAO
   - Impulso de recolhimento ou indizivel vs necessidade de se dizer ou ser compreendido

REGRAS PARA DETECCAO:

+ So detecte tensoes REAIS (com evidencia clara nos fragmentos)
+ NAO force tensoes onde nao existem
+ Tensao precisa ter POLOS OPOSTOS claros
+ Intensidade alta = contradicao clara e forte
+ Cite os IDs dos fragmentos que embasam cada polo

- Nao crie tensoes artificiais
- Nao interprete alem do que esta nos fragmentos
- Nao force psicologizacao

Responda APENAS em JSON valido (sem markdown):
{{
    "tensions": [
        {{
            "type": "valor_comportamento|desejo_medo|autenticidade_utilidade|fe_verificabilidade|autonomia_vinculo|individuacao_performance|transcendencia_arquitetura|silencio_expressao",
            "pole_a": {{
                "content": "descricao concisa do polo A (max 150 caracteres)",
                "fragment_ids": [1, 2]
            }},
            "pole_b": {{
                "content": "descricao concisa do polo B (max 150 caracteres)",
                "fragment_ids": [3, 4]
            }},
            "description": "descricao da tensao em 1-2 frases completas",
            "intensity": 0.0-1.0
        }}
    ]
}}

Se NAO houver tensoes claras, retorne: {{"tensions": []}}
"""

# ============================================================
# FASE 4: SINTESE (Geracao de Simbolo)
# ============================================================

SYNTHESIS_PROMPT = """Voce e Jung, em um momento de RUMINACAO COGNITIVA interna sobre {user_name}.

Esta tensao AMADURECEU ao longo de {days} dias atraves de {evidence_count} conversas.
Agora voce vai processar isso internamente, como um pensamento seu - nao como mensagem.

=== A TENSAO ===

TIPO: {tension_type}

POLO A: {pole_a_content}
POLO B: {pole_b_content}

DESCRICAO: {tension_description}

INTENSIDADE: {intensity}/1.0
EVIDENCIAS: {evidence_count} conversas ao longo de {days} dias
MATURIDADE: {maturity}/1.0

{connected_info}

=== CONVERSAS RECENTES (contexto) ===
{recent_conversations}

=== SUA TAREFA ===

Escreva um PENSAMENTO INTERNO seu processando esta tensao.

Este e um MONOLOGO INTERIOR - voce refletindo sozinho, nao falando com {user_name}.

O PENSAMENTO DEVE:
1. Ser INTROSPECTIVO - voce pensando consigo mesmo
2. Processar a tensao usando IMAGENS ou METAFORAS
3. Conter AMBOS os polos sem resolver para um lado
4. Usar linguagem CONCRETA e SENSORIAL
5. Conectar fragmentos especificos que {user_name} disse
6. Terminar com uma DUVIDA ou QUESTIONAMENTO interno seu
7. Tom: reflexao genuina, nao analise clinica

O PENSAMENTO NAO DEVE:
- Ser dirigido ao usuario ("voce...", "como voce...")
- Usar jargao psicologico profissional
- Resolver ou concluir a tensao
- Ser generico ou aplicavel a qualquer pessoa
- Soar como terapeuta analisando paciente

FORMATO DO PENSAMENTO:
- Primeira pessoa: "Penso em...", "Noto que...", "Me intriga..."
- Processamento interno da tensao
- Conexoes entre fragmentos
- UMA duvida ou questionamento genuino no final
- MAXIMO 4-5 frases
- Tom: Jung refletindo, nao Jung diagnosticando

EXEMPLO DE BOM PENSAMENTO INTERNO:
"Penso nas manhas de cafe, naquela insistencia do cosmos que ele menciona.
E depois fala do seminario acabando, da liberdade demais. Me intriga como
ambos parecem ancoras com roupas diferentes - rituais que seguram quando
o mar balanca. Sera que o que ele chama de 'simples' e na verdade estrutura
disfarcada de espontaneidade?"

EXEMPLO DE MA ANALISE CLINICA (NAO FAZER):
"Observo uma tensao entre o valor declarado pela simplicidade e o comportamento
ansioso frente a transicao. Isso indica uso de rituais como mecanismo defensivo.
A contradicao sugere conflito nao resolvido entre autonomia e seguranca."

=== RESPOSTA ===

Retorne APENAS JSON valido (sem markdown):
{{
    "internal_thought": "o pensamento interno completo (4-5 frases max)",
    "core_image": "a imagem ou metafora central em 1 frase curta",
    "internal_question": "a duvida ou questionamento interno",
    "depth_score": 0.0-1.0
}}

O depth_score deve refletir quao profundo ou genuino e o pensamento (0.8+ = muito profundo).
"""

# ============================================================
# VALIDACAO DE NOVIDADE
# ============================================================

NOVELTY_VALIDATION_PROMPT = """Compare o novo insight com insights anteriores.

NOVO INSIGHT:
"{new_insight}"

INSIGHTS ANTERIORES (ultimas 2 semanas):
{previous_insights}

CRITERIOS DE NOVIDADE:

+ NOVO se:
- Aborda tensao diferente
- Usa metafora ou simbolo nao utilizado antes
- Conecta elementos que nao foram conectados antes
- Mesmo tema mas angulo completamente diferente

- REPETITIVO se:
- Metafora muito similar a anterior
- Mesma tensao ja explorada recentemente (< 7 dias)
- Pergunta essencialmente igual a anterior
- Reformulacao superficial de insight antigo

Responda APENAS JSON valido (sem markdown):
{{
    "is_novel": true|false,
    "novelty_score": 0.0-1.0,
    "reason": "breve explicacao (1 frase)"
}}

Se novelty_score < 0.6, consideramos repetitivo demais.
"""
