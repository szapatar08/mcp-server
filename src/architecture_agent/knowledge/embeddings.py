"""Embedding port and implementations.

Two implementations exist, which is why the port is declared: a deterministic local
embedder that needs no model download, and the hook for a hosted provider. The
local embedder is a hashed bag-of-character-ngrams projection: it is not a
semantic model, and it is documented as such, because an embedding that claims
semantic meaning it does not have produces confident nonsense.

For retrieval quality, configure a real embedding provider. The interface does not
change when you do.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

_TOKEN: re.Pattern[str] = re.compile(r"[a-z0-9]+")
_NGRAM = 3
_DEFAULT_DIMENSIONS = 256


class Embedder(Protocol):
    """Turns text into a fixed-width vector."""

    dimensions: int
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch."""
        ...


class HashedNGramEmbedder:
    """A deterministic, dependency-free local embedder.

    Tokenises to lowercase alphanumeric tokens, hashes overlapping character
    n-grams of each token into buckets, and weights by term frequency. Lexical
    overlap produces similarity; paraphrase does not.
    """

    name = "hashed-ngram-local"

    def __init__(self, dimensions: int = _DEFAULT_DIMENSIONS) -> None:
        """Configure the output width."""
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN.findall(text.lower())
        for token in tokens:
            padded = f"^{token}$"
            for start in range(max(len(padded) - _NGRAM + 1, 1)):
                gram = padded[start : start + _NGRAM]
                digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[bucket] += sign
        norm = sum(value * value for value in vector) ** 0.5
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def build_embedder(provider: str, model: str) -> Embedder:
    """Return the embedder for a configured provider.

    Only ``local`` and ``fake`` are implemented. A hosted provider is added here
    without touching any caller.
    """
    from architecture_agent.config.settings import EmbeddingProvider

    del model
    if provider == EmbeddingProvider.FAKE.value:
        return HashedNGramEmbedder(dimensions=64)
    return HashedNGramEmbedder()


__all__ = ["Embedder", "HashedNGramEmbedder", "build_embedder"]
