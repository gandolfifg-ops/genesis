from __future__ import annotations

import re
from dataclasses import dataclass

from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from sqlalchemy.orm import Session

from school_secretary.agents.llm import complete
from school_secretary.config import Settings, get_settings
from school_secretary.db.live import course_by_code
from school_secretary.db.models import Course, Document
from school_secretary.db.session import session_scope
from school_secretary.rag.embed import cosine, hash_embed, query_terms
from school_secretary.rag.index import get_collection

COURSE_CODE_RE = re.compile(r"\b([A-Z]{3,4})\s*-?\s*(\d{3}[A-Z]?)\b", re.IGNORECASE)


@dataclass
class Retrieval:
    text: str
    filename: str
    course_code: str
    doc_type: str
    score: float


def detect_course(session: Session, question: str) -> Course | None:
    match = COURSE_CODE_RE.search(question)
    if match:
        code = f"{match.group(1).upper()} {match.group(2)}"
        course = course_by_code(session, code)
        if course:
            return course
        course = (
            session.query(Course)
            .filter(Course.code.ilike(f"%{match.group(2)}%"))
            .order_by(Course.id.asc())
            .first()
        )
        if course:
            return course
    lowered = question.lower()
    for course in session.query(Course).order_by(Course.id.asc()).all():
        if course.code.lower() in lowered or course.name.lower() in lowered:
            return course
    return None


def _keyword_score(question: str, text: str) -> float:
    terms = query_terms(question)
    if not terms:
        return 0.0
    tokens = set(query_terms(text))
    return len(terms & tokens) / len(terms)


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    return [part.strip() for part in parts if part.strip()]


def extractive_answer(question: str, retrievals: list[Retrieval]) -> str:
    terms = query_terms(question)
    ranked: list[tuple[float, str, str]] = []
    wants_late = "late" in terms or "penalty" in terms

    def score_span(text: str, item: Retrieval, is_sentence: bool) -> float:
        overlap = len(terms & query_terms(text))
        if overlap == 0:
            return 0.0
        points = overlap + item.score
        if is_sentence:
            points += 0.3
        if item.doc_type == "syllabus":
            points += 0.8
        lowered = text.lower()
        if wants_late and "late" in lowered and ("penalt" in lowered or "late policy" in lowered):
            points += 3.0
        if wants_late and re.search(r"\d+\s*%", text):
            points += 1.0
        return points

    for item in retrievals:
        chunk_score = score_span(item.text, item, is_sentence=False)
        if chunk_score:
            ranked.append((chunk_score, item.text.strip(), item.filename))
        for sentence in _sentence_split(item.text):
            sent_score = score_span(sentence, item, is_sentence=True)
            if sent_score:
                ranked.append((sent_score, sentence, item.filename))
    if not ranked:
        if not retrievals:
            return "I could not find that in the indexed course materials."
        snippet = retrievals[0].text.strip().split("\n")[0]
        course = retrievals[0].course_code
        suffix = f" ({course})" if course else ""
        return f"{snippet}{suffix}"
    ranked.sort(key=lambda row: row[0], reverse=True)
    best = ranked[0]
    extras = []
    seen = {best[1]}
    for _, sentence, _filename in ranked[1:4]:
        if sentence not in seen:
            extras.append(sentence)
            seen.add(sentence)
    course = retrievals[0].course_code
    lines = [best[1]]
    if course:
        lines.extend(["", f"_{course}_"])
    for sentence in extras[:2]:
        if sentence != best[1]:
            lines.append(sentence)
    return "\n".join(lines)


class ChromaCourseRetriever:
    """LlamaIndex-style retriever over the persistent Chroma collection."""

    def __init__(
        self,
        settings: Settings | None = None,
        course_id: int | None = None,
        course_ids: list[int] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        ids = list(course_ids or [])
        if course_id is not None and course_id not in ids:
            ids.append(course_id)
        self.course_ids = ids
        self.course_id = ids[0] if len(ids) == 1 else course_id
        self.collection = get_collection(self.settings)

    def retrieve(self, query_bundle: QueryBundle | str, n: int = 6) -> list[NodeWithScore]:
        question = query_bundle.query_str if isinstance(query_bundle, QueryBundle) else query_bundle
        where = None
        if len(self.course_ids) == 1:
            where = {"course_id": str(self.course_ids[0])}
        elif len(self.course_ids) > 1:
            where = {"course_id": {"$in": [str(cid) for cid in self.course_ids]}}
        kwargs = {"query_texts": [question], "n_results": n}
        if where:
            kwargs["where"] = where
        try:
            raw = self.collection.query(**kwargs)
        except Exception:
            raw = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        nodes: list[NodeWithScore] = []
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        for text, meta, distance in zip(documents, metadatas, distances, strict=False):
            score = 1.0 - float(distance) if distance is not None else 0.0
            node = TextNode(text=text, metadata=meta or {})
            nodes.append(NodeWithScore(node=node, score=score))
        return nodes


def retrieve(question: str, settings: Settings | None = None, n: int = 8) -> list[Retrieval]:
    settings = settings or get_settings()
    with session_scope(settings) as session:
        course = detect_course(session, question)
        same_code_ids = None
        if course:
            same_code_ids = [
                row.id for row in session.query(Course).filter(Course.code == course.code).all()
            ]
        retriever = ChromaCourseRetriever(settings, course_ids=same_code_ids)
        nodes = retriever.retrieve(question, n=n)
        docs = session.query(Document).all()
        if same_code_ids:
            docs = [doc for doc in docs if doc.course_id in same_code_ids]
        hybrid: list[Retrieval] = []
        query_vec = hash_embed(question)
        seen: set[str] = set()
        code_by_id = {c.id: c.code for c in session.query(Course).all()}
        for node in nodes:
            meta = node.node.metadata or {}
            key = node.node.get_content()[:80]
            seen.add(key)
            doc_id = meta.get("document_id")
            filename = meta.get("filename") or "unknown"
            course_id = int(meta.get("course_id") or 0)
            hybrid.append(
                Retrieval(
                    text=node.node.get_content(),
                    filename=filename,
                    course_code=code_by_id.get(course_id, ""),
                    doc_type=meta.get("doc_type") or "",
                    score=float(node.score or 0.0),
                )
            )
        for doc in docs:
            for chunk_start in range(0, max(len(doc.extracted_text), 1), 800):
                chunk = doc.extracted_text[chunk_start : chunk_start + 900]
                if not chunk.strip():
                    continue
                key = chunk[:80]
                if key in seen:
                    continue
                kw = _keyword_score(question, chunk)
                emb = cosine(query_vec, hash_embed(chunk))
                score = 0.55 * emb + 0.45 * kw
                if kw > 0 or emb > 0.15:
                    hybrid.append(
                        Retrieval(
                            text=chunk,
                            filename=doc.filename,
                            course_code=code_by_id.get(doc.course_id, ""),
                            doc_type=doc.doc_type,
                            score=score,
                        )
                    )
        hybrid.sort(key=lambda item: item.score, reverse=True)
        return hybrid[:n]


def answer_question(question: str, settings: Settings | None = None) -> str:
    retrievals = retrieve(question, settings=settings)
    extractive = extractive_answer(question, retrievals)
    context = "\n\n".join(
        f"[{item.course_code or 'course materials'}]\n{item.text}" for item in retrievals[:4]
    )
    llm = complete(
        system=(
            "You are My School Secretary, a Queen's onQ executive assistant. "
            "Answer only from the provided syllabus/handout excerpts. "
            "Clean Markdown. Name the course, never filenames, local paths, or '(Source: …)' labels. "
            "If the excerpts do not contain the answer, say so. "
            "Never write finished assignment solutions."
        ),
        user=f"Question: {question}\n\nExcerpts:\n{context}",
    )
    from school_secretary.agents.persona import polish_outgoing

    return polish_outgoing(llm or extractive)
