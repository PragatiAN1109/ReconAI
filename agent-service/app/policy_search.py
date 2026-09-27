"""Deterministic lexical search over the local policy corpus.

This is **not** semantic search. There are no embeddings, no vector store and no
model. It is term matching with a fixed scoring rule, chosen because the point
of this phase is the tool contract and the citation structure — where a policy
claim came from — rather than retrieval quality. The implementation can be
replaced later without changing what callers see.

The public surface is one operation, :meth:`PolicySearch.search`. There is no
``read_file``, no directory listing and no path argument, so a future agent
receives the ability to search policies and nothing else about the filesystem.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# Words too common to say anything about relevance. Deliberately short: a long
# stop list starts making editorial decisions about the corpus.
_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to",
        "was", "were", "which", "with",
    }
)

_TOKEN = re.compile(r"[a-z0-9_]+")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_EXCERPT_CHARACTERS = 400


def tokenize(text: str) -> list[str]:
    """Split text into lowercase terms, dropping punctuation and stop words.

    Underscores are kept inside tokens so identifiers such as
    ``NORTHSTAR_PAYMENTS`` and ``AMOUNT_MISMATCH`` survive as single terms
    rather than fragmenting into meaningless parts.
    """
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOP_WORDS]


class PolicyEvidence(BaseModel):
    """One policy excerpt, with enough provenance to cite it.

    Every field except the score exists so a later investigation can say where a
    claim came from. Evidence that cannot be attributed is not usable evidence.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    section: str
    excerpt: str
    score: float


@dataclass(frozen=True)
class _Section:
    """One heading and its body, the unit of retrieval."""

    document_id: str
    title: str
    section: str
    text: str
    # Position in the corpus, used only to break score ties reproducibly.
    ordinal: int


class PolicySearch:
    """Searches a fixed corpus of Markdown policy documents.

    The corpus is read once at construction and held in memory. It is small,
    changes rarely, and loading it per query would make results depend on
    filesystem timing.

    Scoping is the security boundary: the corpus directory is resolved at
    construction and only files inside it are ever read. A caller supplies a
    query string and nothing else — no path, no glob, no filename.
    """

    def __init__(self, corpus_path: Path) -> None:
        self._corpus_path = corpus_path.resolve()
        self._sections = self._load()
        logger.info(
            "Policy corpus loaded [path=%s documents=%d sections=%d]",
            self._corpus_path,
            len({section.document_id for section in self._sections}),
            len(self._sections),
        )

    @property
    def section_count(self) -> int:
        return len(self._sections)

    @property
    def document_ids(self) -> list[str]:
        """Every document in the corpus, in a stable order."""
        return sorted({section.document_id for section in self._sections})

    def search(self, query: str, *, limit: int = 5) -> list[PolicyEvidence]:
        """Return the policy excerpts most relevant to a query.

        Matching is case-insensitive and lexical. Scoring counts how many of the
        query's distinct terms appear in a section, weighted by how often, and
        gives extra weight to terms in the heading — a section titled
        "Duplicate Settlements" is about duplicate settlements in a way a passing
        mention is not.

        Ordering is fully deterministic: by score, then document ID, then
        position within the document. The same corpus and query always produce
        the same results in the same order, with no dependence on filesystem
        iteration order.

        No match returns an empty list. This is retrieval, not an oracle: it
        never asserts that an excerpt explains anything.
        """
        terms = set(tokenize(query))
        if not terms:
            return []

        scored: list[tuple[float, _Section]] = []
        for section in self._sections:
            score = self._score(section, terms)
            if score > 0:
                scored.append((score, section))

        # Sort by descending score, then by document ID and position. The two
        # tie-breakers are what make equal-scoring results reproducible.
        scored.sort(key=lambda item: (-item[0], item[1].document_id, item[1].ordinal))

        return [
            PolicyEvidence(
                document_id=section.document_id,
                title=section.title,
                section=section.section,
                excerpt=self._excerpt(section.text),
                score=round(score, 4),
            )
            for score, section in scored[:limit]
        ]

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _score(section: _Section, terms: set[str]) -> float:
        body_tokens = tokenize(section.text)
        heading_tokens = set(tokenize(section.section))
        if not body_tokens:
            return 0.0

        matched = 0
        occurrences = 0
        for term in terms:
            count = body_tokens.count(term)
            if count:
                matched += 1
                occurrences += count

        if matched == 0:
            return 0.0

        # Coverage dominates: a section mentioning three of the query's terms
        # beats one repeating a single term. Frequency is damped by length so
        # long sections are not rewarded for being long.
        coverage = matched / len(terms)
        density = occurrences / len(body_tokens)
        heading_bonus = 0.5 * (len(terms & heading_tokens) / len(terms))

        return coverage + density + heading_bonus

    @staticmethod
    def _excerpt(text: str) -> str:
        """A bounded, readable snippet, cut at a word boundary."""
        collapsed = " ".join(text.split())
        if len(collapsed) <= _EXCERPT_CHARACTERS:
            return collapsed
        cut = collapsed[:_EXCERPT_CHARACTERS].rsplit(" ", 1)[0]
        return f"{cut} ..."

    def _load(self) -> list[_Section]:
        """Read and split the corpus into sections.

        Files are sorted by name so the corpus is assembled identically on every
        run regardless of how the filesystem enumerates the directory.
        """
        if not self._corpus_path.is_dir():
            logger.warning("Policy corpus directory not found [path=%s]", self._corpus_path)
            return []

        sections: list[_Section] = []
        for path in sorted(self._corpus_path.glob("*.md")):
            document_id, title, body = self._parse_front_matter(path)
            if document_id is None:
                # No identifier means nothing could cite it, so it is not usable
                # as evidence. The corpus README is the expected case here.
                logger.debug("Skipping file without a document_id [path=%s]", path.name)
                continue
            sections.extend(self._split_sections(document_id, title, body))
        return sections

    @staticmethod
    def _parse_front_matter(path: Path) -> tuple[str | None, str, str]:
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        if match is None:
            return None, "", text

        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if separator:
                metadata[key.strip()] = value.strip().strip('"')

        return metadata.get("document_id"), metadata.get("title", path.stem), text[match.end():]

    @staticmethod
    def _split_sections(document_id: str, title: str, body: str) -> list[_Section]:
        """Split a document on its ``##`` headings.

        Sections rather than whole documents, so an excerpt can cite the heading
        it came from and a reader can find it.
        """
        sections: list[_Section] = []
        current_heading = "Introduction"
        current_lines: list[str] = []
        ordinal = 0

        def flush() -> None:
            nonlocal ordinal, current_lines
            content = "\n".join(current_lines).strip()
            if content:
                sections.append(
                    _Section(
                        document_id=document_id,
                        title=title,
                        section=current_heading,
                        text=content,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
            current_lines = []

        for line in body.splitlines():
            if line.startswith("## "):
                flush()
                current_heading = line.removeprefix("## ").strip()
            elif not line.startswith("# "):
                current_lines.append(line)

        flush()
        return sections
