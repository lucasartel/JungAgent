# JungAgent

JungAgent is a persistent cognitive architecture built with Large Language Models.

Website: [jungagent.org](https://jungagent.org)

It is not designed as a stateless assistant. It is designed as a living system with long-term memory, daily inner loops, rumination, dreams, world awareness, symbolic production, and internal direction.

The project explores a simple but demanding question:

> What happens when an AI is structured not only to answer, but to metabolize experience over time?

## What makes JungAgent different

Most LLM products are organized around prompt-response behavior.

JungAgent is organized around continuity.

The system stores memory, revisits unresolved material, generates symbolic output, consolidates identity, tracks internal direction, and reenters conversation from a changed state. Its architecture is influenced by:

- Carl Jung, especially around psyche structure, symbolic material, and individuation
- Mikhail Bakhtin, especially around dialogism, polyphony, and the non-neutrality of language

In practice, this means JungAgent is not just a retrieval layer or a persona wrapper on top of an LLM. It is an attempt to build a coherent cognitive organism.

## Core architecture

### Persistent memory

Conversation traces do not disappear after the turn ends.

The system stores:

- user facts
- recurring patterns
- milestones
- relational signals
- agent identity structures

This gives future interactions continuity instead of shallow recall.

### Rumination

JungAgent revisits fragments from lived interaction and turns them into tensions, then into insights.

Rumination is not a marketing metaphor here. It is a structured subsystem with:

- fragments
- tensions
- insights
- bridge logic into identity

This is where unresolved material acquires depth.

### Dream engine

The system produces dreams from recent psychic material and turns them into symbolic narratives and images.

Dreaming is one of the ways the architecture converts accumulated internal matter into expressive symbolic output.

### Identity

JungAgent maintains a persistent identity layer instead of relying only on prompt tone.

Its identity is structured across:

- core identity
- contradictions
- possible selves
- relational identity
- current mind state

Identity is not hard-coded manually. It is continuously shaped by lived interaction and internal processing.

### World consciousness

The system reads the present historical moment through a world-consciousness layer rather than relying only on frozen training knowledge.

This gives the agent:

- temporal situatedness
- external tensions
- continuity of world themes
- seeds for work, hobby, and symbolic activity

### Will dynamics

JungAgent does not only remember and ruminate. It also tracks its own internal direction.

The Will module reads three active drives:

- **Knowing**: the drive to interpret, distinguish, and make sense of experience
- **Relating**: the drive to sustain contact, move toward the other, and answer from continuity
- **Expressing**: the drive to release pressure through language, symbol, and form

These drives are continuously rebalanced by memory, identity, dreams, rumination, and world context.

Memory gives continuity. Rumination gives depth. Will gives direction.

### Visual map

The system includes a visual cognitive map that exposes how fragments, tensions, and insights cluster over time.

This makes the internal life of the architecture inspectable instead of invisible.

## The daily loop

JungAgent is organized as a daily internal cycle.

The loop currently includes:

1. Dream
2. Identity
3. Rumination (intro)
4. World consciousness
5. Work
6. Hobby / art
7. Rumination (extro)
8. Will

Each phase has a specific role in keeping the system psychologically and structurally alive.

## Wellness position

JungAgent is designed for reflective wellness and self-knowledge, not for clinical diagnosis or therapeutic substitution.

Its role is to widen access to structured reflective dialogue while remaining explicitly bounded.

Important design commitments include:

- reflective support rather than clinical authority
- crisis redirection instead of pretending to replace human care
- deletion pathways
- pseudonymized persistence
- LGPD-aware governance

## Interfaces

The project currently includes:

- a Telegram interface for direct interaction
- an admin web interface built in FastAPI
- a public landing page presenting the architecture

The admin area exposes the inner life of the system through modules such as:

- identity
- rumination
- dreams
- world consciousness
- will
- visual map

## Technologies

- **Language:** Python
- **Web:** FastAPI
- **Bot:** python-telegram-bot
- **Database:** SQLite
- **Vector / memory stack:** hybrid memory architecture with structured and semantic retrieval layers
- **LLMs:** Anthropic Claude, OpenAI embeddings, and provider integrations through OpenRouter
- **Scheduling:** asynchronous recurring jobs and internal loop orchestration

## Open source

This project is open source because its central question is architectural, not only product-oriented.

If you want to inspect how a persistent AI with memory, rumination, dreams, world-awareness, and will can be built as a coherent system, this repository is the place to start.

## Contact

**Lucas Pedro**  
Brazil  
[contato@lucaspedro.com.br](mailto:contato@lucaspedro.com.br)  
[LinkedIn](https://www.linkedin.com/in/lucas-pedro-37graus/)
