# Privacy

The application is designed to run locally and does not connect to a brokerage or read personal holdings.

## Fiscal.ai API key

The Fiscal.ai API key can be entered for the current Streamlit session. If **Save locally** is selected, the key is written in plaintext to `.env` on the user's computer.

`.env` is excluded by `.gitignore` and should never be committed or shared. The key is sent to `https://api.fiscal.ai` using the `X-Api-Key` HTTP header.

The **Forget saved key** control clears the stored Fiscal key from `.env`.

## SEC contact email

When the user explicitly loads the optional SEC cross-check, the application asks for a contact email for the SEC request User-Agent. The application does not intentionally write that email to repository files.

## Cached market/API data

Streamlit may cache results in memory during the local session. `data_cache/` is excluded from Git. The current Fiscal integration does not intentionally write transcript payloads to a publishable repository file.

## Optional local Ollama interpretation

Version 0.9 can optionally call a user-run Ollama server, normally at `http://localhost:11434`. Only a small set of already-selected Fiscal.ai transcript excerpts or analyst Q&A pairs is sent to the local model. No embeddings, vector database or filing-scale RAG workflow is used. The request stays on the user's local machine unless the user has independently configured Ollama to use a non-local endpoint.

The local model is not given API keys and cannot modify saved DCF assumptions or financial-source data.
