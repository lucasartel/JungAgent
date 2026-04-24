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
- art / hobby
- visual map

## Installation

JungAgent is currently best installed as a **single-admin agent instance**:

- one deployed service
- one Telegram bot
- one central admin user
- one persistent memory database
- one admin dashboard for inspecting the agent's internal life

The older multi-tenant / organization surfaces still exist as legacy infrastructure, but they are not the recommended product path for new installations.

### Requirements

- Python 3.11+
- a Telegram bot token from BotFather
- at least one LLM provider key
- persistent storage for SQLite
- a Qdrant Cloud cluster or reachable Qdrant endpoint for semantic memory
- a master admin account for the web dashboard

### Environment

Copy the example environment file and fill in your local values:

```bash
cp .env.example .env
```

The most important variables are:

```bash
INSTANCE_NAME=JungAgent
AGENT_INSTANCE=jung_v1
INSTANCE_TIMEZONE=America/Sao_Paulo

TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_ADMIN_IDS=123456789

ADMIN_PLATFORM=telegram
ADMIN_PLATFORM_ID=123456789
ADMIN_USER_ID=

OPENROUTER_API_KEY=your-openrouter-key
OPENAI_API_KEY=your-openai-key
CONVERSATION_MODEL=google/gemini-2.5-flash-lite
INTERNAL_MODEL=google/gemini-2.5-flash-lite
DREAM_IMAGE_PROVIDER=openrouter_nano_banana
DREAM_IMAGE_MODEL=google/gemini-3.1-flash-image-preview

QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-qdrant-api-key
QDRANT_COLLECTION_NAME=jung_memories_jung_v1
MEM0_LLM_MODEL=openai/gpt-4o-mini
MEM0_LLM_BASE_URL=https://openrouter.ai/api/v1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

PROACTIVE_ENABLED=true
ACTIVE_CONSCIOUSNESS_ENABLED=true
ENABLE_UNSAFE_ADMIN_ENDPOINTS=false
```

For new Telegram-only installations, prefer setting `ADMIN_PLATFORM_ID` to the numeric Telegram id of the instance admin and leaving `ADMIN_USER_ID` empty. JungAgent will derive the internal admin memory id as `sha256(ADMIN_PLATFORM_ID)[:16]`.

Set `ADMIN_USER_ID` directly only when migrating an existing installation that already has memory under a known user id.

### Semantic Memory With Qdrant

JungAgent uses SQLite and Qdrant for different kinds of persistence.

SQLite is the operational source of truth: conversations, dreams, rumination, identity, will, loop state, admin users, and structured records.

Qdrant is the recommended production backend for semantic memory through `mem0`. It stores vectorized memories that can be searched by meaning and used as long-term continuity during conversation.

ChromaDB remains a legacy/local fallback for older or development installations. New production instances should prefer Qdrant.

Use one Qdrant collection per JungAgent instance:

```bash
QDRANT_COLLECTION_NAME=jung_memories_<agent_instance>
```

For example:

```bash
QDRANT_COLLECTION_NAME=jung_memories_jung_v1
```

Do not share a Qdrant collection between unrelated instances, because semantic memories can mix across agents. See [`docs/QDRANT_SEMANTIC_MEMORY.md`](docs/QDRANT_SEMANTIC_MEMORY.md) for the full setup and security notes.

### Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
python main.py
```

The service runs the FastAPI admin interface and the Telegram bot from the same process. By default, the web server listens on `PORT` or `8000`.

After the first boot, make sure the SQLite database exists and create a master admin account if your database does not already have one:

```bash
python setup_instance.py \
  --db-path ./data/jung_hybrid.db \
  --master-email admin@example.com \
  --master-password "change-this-password" \
  --master-name "Instance Admin"
```

Then open:

```text
http://localhost:8000/admin/login
```

### Instance Setup

After logging into the dashboard, open:

```text
/admin/instance/setup
```

This page checks whether the installation is coherently wired:

- the web admin exists
- the Telegram admin id is configured
- the Telegram admin is in the allowlist
- the derived `ADMIN_USER_ID` matches the Telegram identity
- the central admin user row exists in the JungAgent memory database
- proactive messaging is enabled or intentionally disabled

If the central admin user row is missing, use the safe repair button on that page. It creates or aligns only the admin user row and does not replace conversation history, memories, dreams, rumination, identity, or will state.

You can also run the post-deploy CLI healthcheck:

```bash
python instance_healthcheck.py
```

For machine-readable output:

```bash
python instance_healthcheck.py --json
```

Use `--db-path` when checking a database outside the configured environment.

### Railway Deploy

The repository includes a `Dockerfile`, `Procfile`, and `railway.toml`.

For Railway:

1. Create a new Railway project from this repository.
2. Add a persistent volume and point `SQLITE_DB_PATH` to that volume, for example `/data/jung_hybrid.db`.
3. Create or reuse a Qdrant Cloud cluster for semantic memory.
4. Set `QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION_NAME`.
5. Keep `CHROMA_DB_PATH` only if you intentionally need the legacy/local fallback.
6. Add the same environment variables described above.
7. Deploy the service.
8. Create the master admin user if needed with `python setup_instance.py --master-email admin@example.com`.
9. Run `python instance_healthcheck.py`.
10. Open `/admin/instance/setup` and verify the installation.

### Security Notes

- Keep `TELEGRAM_BOT_TOKEN` and LLM provider keys out of git.
- Keep `QDRANT_API_KEY` out of git and treat Qdrant collections as sensitive semantic memory stores.
- Keep `ENABLE_UNSAFE_ADMIN_ENDPOINTS=false` in production unless you are doing a short, controlled maintenance operation.
- Use `TELEGRAM_ADMIN_IDS` to prevent non-admin Telegram users from interacting with the private instance.
- Treat the SQLite database, Qdrant collection, and any ChromaDB fallback directory as sensitive memory stores.
- Keep a persistent backup strategy before running migrations or manual repair scripts.

## Repository layout

The repository root is intentionally kept focused on runtime code and deployment entrypoints.

- `admin_web/`: FastAPI admin interface, dashboards, auth, and templates
- `docs/`: architecture notes, deployment guides, and diagnostic references
- `scripts/diagnostics/`: offline inspection helpers for memory, rumination, and Railway exports
- `scripts/operations/`: operational utilities such as exports and admin setup helpers
- `archive/`: old versions, backups, and historical material kept out of the active runtime path
- `main.py`, `telegram_bot.py`, `jung_core.py`, `*_engine.py`: active runtime and orchestration

## Technologies

- **Language:** Python
- **Web:** FastAPI
- **Bot:** python-telegram-bot
- **Database:** SQLite
- **Semantic memory:** mem0 with Qdrant as the recommended production vector store
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
