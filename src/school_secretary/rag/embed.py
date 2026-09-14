from __future__ import annotations

import hashlib
import math
import re

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

TOKEN_RE = re.compile(r"[a-z0-9%]+")
EMBED_DIM = 384
STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "for",
    "of",
    "in",
    "to",
    "and",
    "or",
    "on",
    "at",
    "what",
    "which",
    "who",
    "how",
    "does",
    "do",
    "with",
    "from",
    "this",
    "that",
    "it",
}


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def query_terms(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOP and len(token) > 1}


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic hashing trick. Offline fallback when no OpenAI key is set."""
    vec = [0.0] * dim
    tokens = tokenize(text)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]}_{tokens[i + 1]}"
        digest = hashlib.sha256(bigram.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        vec[idx] += 1.5
    lowered = text.lower()
    for i in range(0, min(len(lowered), 400), 4):
        gram = lowered[i : i + 5]
        if len(gram) < 3:
            continue
        digest = hashlib.sha256(gram.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        vec[idx] += 0.25
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [value / norm for value in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class HashingEmbeddingFunction(EmbeddingFunction[Documents]):
    """Chroma-compatible embedding function (no network, no API key)."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [hash_embed(text) for text in input]

    @staticmethod
    def name() -> str:
        return "school-secretary-hashing"

    def get_config(self) -> dict:
        return {"dim": EMBED_DIM}

    @staticmethod
    def build_from_config(config: dict) -> "HashingEmbeddingFunction":
        return HashingEmbeddingFunction()
