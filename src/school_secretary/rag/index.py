from __future__ import annotations

from llama_index.core.schema import TextNode

from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Announcement, Assignment, Document
from school_secretary.db.session import session_scope
from school_secretary.rag.embed import HashingEmbeddingFunction
from school_secretary.rag.extract import extract_pdf_text

COLLECTION = "course_docs"
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


def _chunk_text(text: str) -> list[str]:
    paragraphs = [block.strip() for block in text.split("\n") if block.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 > CHUNK_SIZE and current:
            chunks.append(current.strip())
            current = current[-CHUNK_OVERLAP:] + "\n" + paragraph
        else:
            current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current.strip())
    return chunks or ([text.strip()] if text.strip() else [])


def chroma_client(settings: Settings | None = None):
    import chromadb

    settings = settings or get_settings()
    settings.ensure_dirs()
    return chromadb.PersistentClient(path=str(settings.chroma_path))


def get_collection(settings: Settings | None = None):
    client = chroma_client(settings)
    return client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=HashingEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def documents_to_nodes(documents: list[Document]) -> list[TextNode]:
    nodes: list[TextNode] = []
    for document in documents:
        text = document.extracted_text
        if not text and document.path.lower().endswith(".pdf"):
            from pathlib import Path

            text = extract_pdf_text(Path(document.path))
        for index, chunk in enumerate(_chunk_text(text)):
            nodes.append(
                TextNode(
                    text=chunk,
                    metadata={
                        "document_id": document.id,
                        "course_id": document.course_id,
                        "filename": document.filename,
                        "doc_type": document.doc_type,
                        "chunk": index,
                    },
                )
            )
    return nodes


def sql_row_nodes(session) -> list[TextNode]:
    """Index announcement/assignment text when live ingest has no PDFs."""
    nodes: list[TextNode] = []
    for announcement in session.query(Announcement).all():
        text = f"{announcement.title}\n{announcement.body}".strip()
        for index, chunk in enumerate(_chunk_text(text)):
            nodes.append(
                TextNode(
                    text=chunk,
                    metadata={
                        "document_id": f"ann-{announcement.id}",
                        "course_id": announcement.course_id,
                        "filename": f"announcement-{announcement.d2l_id}.txt",
                        "doc_type": "announcement",
                        "chunk": index,
                    },
                )
            )
    for assignment in session.query(Assignment).all():
        text = f"{assignment.title}\n{assignment.instructions}".strip()
        for index, chunk in enumerate(_chunk_text(text)):
            nodes.append(
                TextNode(
                    text=chunk,
                    metadata={
                        "document_id": f"asg-{assignment.id}",
                        "course_id": assignment.course_id,
                        "filename": f"assignment-{assignment.d2l_id}.txt",
                        "doc_type": "assignment",
                        "chunk": index,
                    },
                )
            )
    return nodes


def _upsert_nodes(collection, nodes: list[TextNode]) -> None:
    if not nodes:
        return
    collection.upsert(
        ids=[f"doc-{node.metadata['document_id']}-{node.metadata['chunk']}" for node in nodes],
        documents=[node.text for node in nodes],
        metadatas=[{key: str(value) for key, value in node.metadata.items()} for node in nodes],
    )


def index_documents(settings: Settings | None = None, force: bool = False) -> int:
    """Embed PDFs / extracted text plus announcement and assignment rows into ChromaDB."""
    settings = settings or get_settings()
    collection = get_collection(settings)
    indexed = 0
    with session_scope(settings) as session:
        rows = session.query(Document).all()
        to_index = [row for row in rows if force or not row.indexed]
        _upsert_nodes(collection, documents_to_nodes(to_index))
        _upsert_nodes(collection, sql_row_nodes(session))
        for row in to_index:
            row.indexed = True
            indexed += 1
        indexed += session.query(Announcement).count() + session.query(Assignment).count()
    return indexed
