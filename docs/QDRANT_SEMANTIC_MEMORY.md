# Qdrant Semantic Memory

JungAgent uses more than one persistence layer. This distinction matters during installation because the agent's operational life and its semantic memory do not live in the same place.

## Persistence Layers

SQLite is the operational source of truth. It stores conversations, users, dreams, rumination fragments, will states, identity records, loop state, art artifacts, admin accounts, and other structured runtime data.

Qdrant is the recommended production backend for semantic memory through `mem0`. It stores vectorized memories extracted from conversation and makes them searchable by meaning, not only by keyword.

ChromaDB remains a legacy/local fallback. It is useful for development or older installations, but new production instances should use Qdrant so semantic memory survives deploys cleanly and can be managed as an external service.

## Required Environment Variables

Set these variables when using Qdrant:

```bash
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-qdrant-api-key
QDRANT_COLLECTION_NAME=jung_memories_jung_v1

OPENAI_API_KEY=your-openai-key
OPENROUTER_API_KEY=your-openrouter-key
MEM0_LLM_MODEL=openai/gpt-4o-mini
MEM0_LLM_BASE_URL=https://openrouter.ai/api/v1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_BASE_URL=
```

`OPENAI_API_KEY` is used for embeddings by default. `OPENROUTER_API_KEY` is used by mem0's fact extraction model when `MEM0_LLM_BASE_URL` points to OpenRouter.

## Collection Naming

Use one Qdrant collection per JungAgent instance.

Recommended pattern:

```bash
QDRANT_COLLECTION_NAME=jung_memories_<agent_instance>
```

Examples:

```bash
QDRANT_COLLECTION_NAME=jung_memories_jung_v1
QDRANT_COLLECTION_NAME=jung_memories_lucas_prod
QDRANT_COLLECTION_NAME=jung_memories_family_lab
```

Do not reuse the same collection across unrelated installations. Sharing a collection can mix semantic memories between agents and makes deletion, migration, and audit much harder.

## Railway Setup

For Railway production installs:

1. Create or reuse a Qdrant Cloud cluster.
2. Create an API key with access to the cluster.
3. Add `QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION_NAME` to Railway variables.
4. Keep `SQLITE_DB_PATH` pointed to the Railway persistent volume, for example `/data/jung_hybrid.db`.
5. Keep `CHROMA_DB_PATH` only if you intentionally need the legacy/local fallback.
6. Redeploy the service.
7. Run `python instance_healthcheck.py`.
8. Open `/admin/instance/setup` and confirm the instance wiring.

## Security

Treat Qdrant as a memory store, not as disposable infrastructure. It can contain meaningful semantic traces extracted from private conversation.

Keep these rules:

- Never commit `QDRANT_API_KEY`.
- Use separate collections for separate instances.
- Rotate the Qdrant API key if it was exposed in logs, screenshots, or shared terminals.
- Back up SQLite separately from Qdrant; they store different parts of the agent.
- Before deleting a user or instance, verify both structured memory in SQLite and semantic memory in Qdrant were handled.

## Failure Behavior

If `QDRANT_URL` is not configured, JungAgent falls back to the existing SQLite/Chroma memory path.

If `QDRANT_URL` is configured but the Qdrant credentials, embedding key, or mem0 dependencies are invalid, semantic retrieval can fail while the rest of the agent continues to run. In that situation, check the logs for `[MEM0]` messages and validate:

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `QDRANT_COLLECTION_NAME`
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `MEM0_LLM_MODEL`
- `OPENAI_EMBEDDING_MODEL`

## Mental Model

For installation purposes, think of JungAgent like this:

```text
SQLite = lived operational history
Qdrant = semantic memory field
LLM    = latent language/world knowledge
Loop   = digestion and transformation over time
```

The external Qdrant service is not the agent's whole memory. It is the semantic retrieval layer that helps the agent remember by meaning.
