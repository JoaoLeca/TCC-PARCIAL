from pathlib import Path
from hashlib import sha256
from barber_bot.config import CHROMA_DIR, KNOWLEDGE_DIR, OPENAI_API_KEY


def ingest_knowledge() -> int:
    if not OPENAI_API_KEY:
        raise RuntimeError("Configure OPENAI_API_KEY no arquivo .env para criar o índice RAG.")
    from langchain_chroma import Chroma
    from langchain_openai import OpenAIEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not files:
        raise RuntimeError("Nenhum documento .md foi encontrado em data/knowledge.")
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="cl100k_base", chunk_size=700, chunk_overlap=100)
    docs = []
    for file in files:
        from langchain_core.documents import Document
        docs.extend(splitter.split_documents([Document(page_content=file.read_text(encoding="utf-8"), metadata={"source": file.name})]))
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    ids = [sha256((doc.metadata["source"] + "\n" + doc.page_content).encode("utf-8")).hexdigest() for doc in docs]
    Chroma.from_documents(docs, OpenAIEmbeddings(model="text-embedding-3-small"), persist_directory=str(CHROMA_DIR), collection_name="barbearia_imperial", ids=ids)
    return len(docs)


def search_knowledge(query: str, k: int = 4) -> list[dict]:
    if not OPENAI_API_KEY:
        raise RuntimeError("Configure OPENAI_API_KEY no arquivo .env para usar a busca semântica.")
    from langchain_chroma import Chroma
    from langchain_openai import OpenAIEmbeddings
    if not CHROMA_DIR.exists():
        return []
    store = Chroma(persist_directory=str(CHROMA_DIR), embedding_function=OpenAIEmbeddings(model="text-embedding-3-small"), collection_name="barbearia_imperial")
    matches = store.similarity_search_with_relevance_scores(query, k=k)
    return [{"text": doc.page_content, "source": doc.metadata.get("source", ""), "relevance": round(score, 3)} for doc, score in matches if score >= 0.25]
