# LLM Wiki Toolkit — Installation Guide

This toolkit is completely OS and Linux-distro agnostic. It uses pure Python, standard `pathlib` for file paths, and local tools. 
Follow these steps to deploy it on any Linux system (Ubuntu, Fedora, Arch, etc.).

## 1. Prerequisites

You will need the following installed on your system:
- `python` (>= 3.11)
- `pip` (Python package manager)
- `git`

### Arch Linux example:
```bash
sudo pacman -S python python-pip git
```

### Ubuntu/Debian example:
```bash
sudo apt update && sudo apt install python3 python3-pip git
```

## 2. Install Local Ollama (Required for 100% Free Local Embeddings)

We use **Ollama** to run the embedding model locally. This keeps your `wiki lint` operations incredibly fast and $0.00 in API costs.

1. Install Ollama (official script):
```bash
curl -fsSL https://ollama.com/install.sh | sh
```
2. Start the Ollama service:
```bash
systemctl start ollama
# OR run manually if no systemd: ollama serve
```
3. Pull the embedding model and any local generation models you want to use:
```bash
ollama pull nomic-embed-text
ollama pull qwen3:32b  # (Optional: If you have a powerful GPU to run generation locally)
```

## 3. Install the LLM Wiki Toolkit

Clone the repository and install it in editable mode so the `wiki` CLI command is globally available.

```bash
git clone <repository_url> llm-wiki
cd llm-wiki
pip install -e .
```
> **Note:** The `setup.py` / `pyproject.toml` will automatically install `litellm`, `chromadb` (for vector storage), `click` (for the CLI), and other required Python dependencies.

## 4. Configure Provider API Keys

The toolkit uses a global `providers.yaml` file to manage all your keys and fallback chains.

1. Create the config directory:
```bash
mkdir -p ~/.config/llm-wiki
```
2. Copy the example configuration:
```bash
cp config/providers.example.yaml ~/.config/llm-wiki/providers.yaml
```
3. Edit the file to add your API keys:
```bash
nano ~/.config/llm-wiki/providers.yaml
```

**Example `providers.yaml` structure for Ollama + Google API:**
```yaml
providers:
  google:
    api_key: "YOUR_GOOGLE_AI_STUDIO_KEY"
    models:
      - gemini/gemini-3.8-pro
      - gemini/gemini-3.8-flash
  local:
    api_base: "http://localhost:11434"
    models:
      - ollama/qwen3:32b
      - ollama/nomic-embed-text

fallback_chain:
  ingest:
    - gemini/gemini-3.8-pro
    - gemini/gemini-3.8-flash
  query:
    - gemini/gemini-3.8-flash
  lint:
    - gemini/gemini-3.8-flash
  embeddings:
    - local/ollama/nomic-embed-text       # Primary: Free local embeddings!
    - google/gemini/text-embedding-004    # Fallback: Just in case Ollama crashes

token_limits:
  ingest_chunk: 8000
  query_context: 16000
  lint_batch: 32000
```

## 5. Usage

You are ready! 
1. `wiki init ~/my-wiki --domain "My Knowledge Base"`
2. Add a markdown file to `~/my-wiki/raw/file.md`
3. `wiki ingest --all`
4. `wiki lint` (This will now seamlessly use your local Ollama embeddings and ChromaDB vector store)
