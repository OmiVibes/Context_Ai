# Context AI

Context AI is a local developer assistant that indexes software repositories and answers project-specific questions with retrieval-augmented generation. It combines file loading, chunking, embeddings, vector search, repository profiling, MCP tooling, FastAPI endpoints, and a Streamlit UI.

## Features

- Index local or GitHub repositories into searchable chunks
- Generate embeddings and store vectors with FAISS
- Ask natural-language questions about indexed codebases
- Expose context tools through an MCP server
- Use FastAPI endpoints for indexing, querying, risks, milestones, and metrics
- Provide a Streamlit interface for repository chat workflows
- Route prompts to local LLM backends such as Ollama

## Project Structure

```text
app.py                  FastAPI app for indexing and RAG queries
app_processing/         File loading, filtering, chunking, and embeddings
github/                 Repository sync and webhook helpers
llm_service/            Local LLM inference service
mcp/                    MCP JSON-RPC server and schemas
rag/                    Retrieval, prompt building, metrics, risks, milestones
ui/                     Streamlit frontend
utils/                  MCP client, tests, and project fingerprint helpers
vector_store/           FAISS vector store implementation
```

## Requirements

- Python 3.10+
- Ollama, if using local LLM inference
- Dependencies listed in `requirements.txt`

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file for local configuration if needed. The `.env` file is intentionally ignored by git.

## Run The API

```bash
uvicorn app:app --reload
```

The main API starts from `app.py` and exposes repository indexing and RAG query endpoints.

## Run The Streamlit UI

```bash
streamlit run ui/streamlit_app.py
```

Use the sidebar to provide a repository ID and GitHub repository URL, rebuild the index, and ask questions about the project.

## Run The LLM Service

```bash
uvicorn llm_service.server:app --reload
```

The LLM service routes prompts to a configured local model backend.

## Notes

Generated data such as virtual environments, caches, repository indexes, vector files, and local profiles are excluded from version control through `.gitignore`.
