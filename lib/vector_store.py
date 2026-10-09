import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

import chromadb
from chromadb.config import Settings

from lib.providers import LLMProvider

logger = logging.getLogger(__name__)

class WikiVectorStore:
    """
    Manages the ChromaDB vector storage for the LLM Wiki.
    Handles semantic search for the Retrieval-Augmented Validation (Linting) process.
    """
    def __init__(self, wiki_dir: Path, provider: LLMProvider):
        self.wiki_dir = wiki_dir
        self.provider = provider
        
        # We store the ChromaDB database invisibly inside the vault's .llm-wiki folder
        db_path = (wiki_dir / "wiki" / ".llm-wiki" / "chroma_db").resolve()
        db_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize the persistent Chroma client
        self.client = chromadb.PersistentClient(path=str(db_path), settings=Settings(anonymized_telemetry=False))
        
        # The main collection holding our wiki chunks
        self.collection = self.client.get_or_create_collection(
            name="wiki_pages",
            metadata={"hnsw:space": "cosine"} # Use cosine similarity for LLM embeddings
        )

    def embed_and_upsert(self, page_id: str, text: str, metadata: Dict[str, Any]):
        """
        Takes a page's text, generates an embedding using the LLMProvider (via litellm),
        and saves it to the local ChromaDB database.
        
        Args:
            page_id: Unique identifier (usually the relative file path)
            text: The text chunk to embed
            metadata: Any metadata to store (e.g. title, type)
        """
        # Call the configured embedding model via litellm
        embedding = self.provider.embed(text)
        
        if not embedding:
            logger.error("Failed to generate embedding for %s", page_id)
            return

        # Upsert will insert if new, or update if the page_id already exists
        self.collection.upsert(
            ids=[page_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata]
        )

    def search_similar(self, text: str, k: int = 10, distance_threshold: float = 0.4, min_results: int = 0) -> List[str]:
        """
        Finds the most semantically similar documents in the wiki to the given text.
        Filters results dynamically based on a distance threshold.
        
        Args:
            text: The text to search for
            k: Maximum number of documents to retrieve
            distance_threshold: Maximum allowable distance (lower is closer)
            min_results: Minimum floor of results to return regardless of distance (default: 0)
            
        Returns:
            List of raw markdown document strings that passed the threshold.
        """
        embedding = self.provider.embed(text)
        if not embedding:
            return []

        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            include=["documents", "distances"]
        )

        valid_docs = []
        if results and results.get("documents") and results.get("distances"):
            docs = results["documents"][0]
            distances = results["distances"][0]
            
            # Zip documents and their cosine distances together
            for doc, dist in zip(docs, distances):
                # If semantic distance is below threshold, or if under the configured min_results floor
                if dist < distance_threshold or len(valid_docs) < min_results:
                    valid_docs.append(doc)
                    
        return valid_docs
                    
    def search_similar_with_metadata(self, text: str, k: int = 10, distance_threshold: float = 0.4, min_results: int = 0) -> List[Dict[str, Any]]:
        """Finds similar documents and returns their metadata, ids, and content."""
        embedding = self.provider.embed(text)
        if not embedding:
            return []

        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"]
        )

        valid_docs = []
        if results and results.get("documents") and results.get("distances"):
            docs = results["documents"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]
            ids = results["ids"][0]
            
            for doc, dist, meta, id_ in zip(docs, distances, metadatas, ids):
                if dist < distance_threshold or len(valid_docs) < min_results:
                    valid_docs.append({
                        "path": id_,
                        "title": meta.get("title", "Untitled"),
                        "content": doc,
                        "distance": round(float(dist), 4),
                    })
                    
        return valid_docs
