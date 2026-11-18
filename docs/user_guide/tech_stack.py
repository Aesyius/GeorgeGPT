r"""
# Technology stack

GeorgeGPT combines a local model server, a repository search pipeline, and a
chat interface. Each component has a specific job; the chat model is not
trained on your repository.

## At a glance

| Component | Role in GeorgeGPT |
| --- | --- |
| **Ollama** | Runs the chat model and embedding model locally. |
| **Qwen3.5:9B** | Generates answers using the question and retrieved repository evidence. |
| **Qwen3-Embedding:4B** | Converts repository passages and questions into vectors for semantic search. |
| **Open WebUI** | Provides the browser-based AI chat interface, accounts, and conversation history. |
| **LangChain integrations** | Connect Python to Ollama embeddings and provide text-splitting utilities. |
| **Chroma** | Stores passage vectors and metadata for semantic retrieval. |
| **SQLite FTS5/BM25** | Finds exact terms such as filenames, symbols, configuration keys, and error messages. |
| **FastAPI and Uvicorn** | Expose the authenticated, OpenAI-compatible GeorgeGPT API to WebUI. |
| **Python and PowerShell** | Implement indexing, retrieval, generation, setup, and service management. |

## Ollama runs two models

The default configuration in `config/models.json` selects:

1. **`qwen3.5:9b` — the chat model.** Reads the supplied question, conversation
   context, and retrieved passages, then generates the answer.
2. **`qwen3-embedding:4B` — the embedding model.** Produces numerical vectors
   that represent text for similarity search. It does not write chat answers.

### Why these models?

This pairing was chosen around the **CPT laptops' 12 GB of GPU VRAM**.
The goal is to keep both models GPU-resident together while leaving room for
chat generation at the configured **16384-token context window**. The 9B chat
model balances answer quality and memory use; the 4B embedder gives retrieval
a larger representation model than the smaller 0.6B option.

Keeping both models loaded avoids repeated model swapping between question
embedding and answer generation. Default retention is **1800 seconds**.
Startup warms both inference paths and checks that both models remain loaded.

> **Memory fit is a target, not a guarantee.** Ollama version, model
> quantization, context allocation, and other GPU applications affect VRAM use.
> Startup's loaded-model check does not prove full GPU residency. After asking
> a question, use `ollama ps` to check for **100% GPU** on both model rows.
> If either spills onto CPU, free GPU memory or reduce the configured context.

Changing the chat model does not rebuild repository vectors. Changing the
embedding model requires a matching model-specific index. Existing indexes
are preserved so a compatible previous model can be reused.
See [model configuration](configuration.html#model-configuration).

## Open WebUI is the AI interface

Open WebUI supplies the browser chat experience: login, model selection,
conversation display, and history. It connects to the local GeorgeGPT API
through an OpenAI-compatible connection, normally:

- **WebUI:** `http://127.0.0.1:8080`
- **GeorgeGPT API connection:** `http://127.0.0.1:8001/v1`
- **Model selected in WebUI:** `GeorgeGPT`

WebUI sends the conversation to GeorgeGPT; GeorgeGPT performs retrieval and
contacts Ollama. **WebUI's Knowledge/RAG feature is not used.** Leave it disabled
to avoid introducing a second, unrelated retrieval pipeline.

The API key authenticates WebUI to GeorgeGPT. WebUI stores its own account,
conversation, and saved-connection data in a local SQLite database, separate
from the repository index.

## LangChain and Ollama embeddings

GeorgeGPT uses specific LangChain components rather than a tool-using agent:

- **`langchain-ollama`:** its `OllamaEmbeddings` client sends document and
  query text to Ollama's embedding service.
- **`langchain-text-splitters`:** provides the recursive splitter used by the
  compatibility text loader.
- **LangChain document objects:** carry text and source metadata in that loader.

The main repository indexer uses GeorgeGPT's own structural chunker, preserving
paths, headings, symbols, and line ranges. Chat generation also belongs to
GeorgeGPT: it calls Ollama's chat API directly with a controlled system prompt
and bounded evidence. LangChain does not execute commands or give the chatbot
filesystem or infrastructure access.

### What does “Ollama embed” mean?

It means asking Ollama's embedding model to convert text into a vector.
The same embedding model must be used for stored passages and incoming
questions so their vectors can be compared.

- **During indexing:** new or changed passages are embedded and stored.
  Compatible unchanged vectors are reused.
- **During a question:** only the question is embedded for search. The
  repository is not embedded again for each chat request.

Embeddings are searchable representations, not model training. Refresh the
index after source changes so answers can use current repository content.

## Chroma and SQLite provide hybrid search

GeorgeGPT retrieves evidence in two complementary ways:

1. **Semantic search in Chroma** finds passages with similar meaning to the
   embedded question, even when wording differs.
2. **Keyword search in SQLite FTS5/BM25** finds exact terms that matter in code,
   including identifiers and error messages.
3. **Reciprocal-rank fusion** combines their rankings into a bounded set of
   passages for the chat model.

By default, retrieval selects up to **eight passages** within a character
budget. Their text and source metadata are supplied as evidence, rather than
sending the whole repository to the model.

Chroma stores vectors in WSL2 and serves the Windows client over localhost.
Collection-specific keyword databases persist under project-root
`chroma_db/` under port-specific filenames. Model catalogs track compatible indexes. The explicit verification
command audits stored vectors, chunk metadata, and keyword consistency; it
does not measure whether answers are correct.

## FastAPI and Uvicorn connect the components

**FastAPI** implements the authenticated API in `Interface/server.py`.
**Uvicorn** runs it as a local HTTP service. The API offers a restricted subset
of OpenAI-compatible requests, including streamed chat responses.

GeorgeGPT validates input, bounds history/evidence, queues requests, retrieves
passages, and forwards controlled chat messages to Ollama. Only one answer is
generated at a time, with a bounded waiting queue. Tool execution is not exposed.

## Follow one question through the stack

```text
User types a question in Open WebUI
    |
    v
GeorgeGPT API authenticates and validates the request
    |
    v
Ollama / Qwen3-Embedding:4B embeds the question
    |
    v
Chroma semantic search + SQLite keyword search
    |
    v
GeorgeGPT ranks and bounds the retrieved evidence
    |
    v
Ollama / Qwen3.5:9B generates an evidence-grounded answer
    |
    v
GeorgeGPT streams the answer back to Open WebUI
```

## Setup, storage, and local operation

PowerShell `setup.ps1` prepares the environments, dependencies, models, and
repository index. Python's `services` package manages startup, process ownership
checks, warmup, readiness, and logs. Runtime model choices are kept in JSON
rather than hardcoded into the UI.

Managed services bind to `127.0.0.1` or `localhost`. Initial dependency/model
downloads require internet, and WebUI may perform its own update/model checks;
local listeners alone are not an outbound-network block. Repository retrieval
and inference run locally.

**Next:** [First-time setup](setup.html) explains installation.
[Daily use and bootup](daily_use.html) covers later launches and index refreshes.
"""
