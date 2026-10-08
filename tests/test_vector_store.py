import pytest
from lib.vector_store import WikiVectorStore
from pathlib import Path

def test_vector_store_persistence(tmp_path):
    class MockProvider:
        def embed(self, text):
            return [0.1, 0.2, 0.3]
            
    store1 = WikiVectorStore(tmp_path, MockProvider())
    store1.embed_and_upsert("page1", "content 1", {"title": "Page 1"})
    
    assert store1.collection.count() == 1
    
    # Reload store
    store2 = WikiVectorStore(tmp_path, MockProvider())
    assert store2.collection.count() == 1

def test_search_similar(tmp_path):
    class MockProvider:
        def embed(self, text):
            # Return distinct dummy vectors so we can mock the distances (ChromaDB does it internally based on cosine)
            if "content 1" in text:
                return [1.0, 0.0, 0.0]
            elif "content 2" in text:
                return [0.9, 0.1, 0.0]
            else:
                return [0.0, 1.0, 0.0]

    store = WikiVectorStore(tmp_path, MockProvider())
    store.embed_and_upsert("page1", "content 1", {"title": "Page 1"})
    store.embed_and_upsert("page2", "content 2", {"title": "Page 2"})
    
    docs = store.search_similar("content 1", k=2, distance_threshold=0.5)
    # The first document should be content 1, the second might be content 2
    assert len(docs) >= 1
    assert "content 1" in docs[0]

def test_search_similar_with_metadata(tmp_path):
    class MockProvider:
        def embed(self, text):
            if "content 1" in text:
                return [1.0, 0.0, 0.0]
            elif "content 2" in text:
                return [0.9, 0.1, 0.0]
            else:
                return [0.0, 1.0, 0.0]

    store = WikiVectorStore(tmp_path, MockProvider())
    store.embed_and_upsert("page1", "content 1", {"title": "Page 1"})
    store.embed_and_upsert("page2", "content 2", {"title": "Page 2"})
    
    results = store.search_similar_with_metadata("content 1", k=2, distance_threshold=0.5)
    assert len(results) >= 1
    assert results[0]["path"] == "page1"
    assert results[0]["title"] == "Page 1"
    assert "content 1" in results[0]["content"]
