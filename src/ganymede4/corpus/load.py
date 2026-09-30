"""Load the real corpus: KonradFreeman's Reddit record and ChatGPT histories.

ADR-010. The corpus is 3,416 documents and 65 MB, held outside the repository
at ``~/Projects/Chris`` and read from there. It is the author's own record —
his Reddit account and his ChatGPT conversations.

The loader exists because of one property of that corpus that no amount of
care elsewhere would survive without addressing: **the ChatGPT half is mostly
not the author.** 12,185 assistant turns against 7,579 user turns. A model's
prior output, captured verbatim, is in there.

Ingested naively, that output becomes a compiled claim with a valid source
span, a Merkle root, and a provenance chain — structurally perfect and
epistemically worthless. The compiler's guarantees would all hold while the
artifact laundered a model's confident assertion into the historical record as
though the author had written it. That is the exact failure this project
exists to catch in other people's systems, so it is not acceptable here.

So every document is typed by *who was speaking*:

- ``reddit_comment`` / ``reddit_submission`` / ``chatgpt_user`` — the author.
  First-person testimony.
- ``chatgpt_assistant`` — a model. Retained, indexed, and findable, because
  "what did I argue to a model in 2024?" is a real question about the record.
  Never grounds a claim about the world on its own.

Dropping the assistant turns was considered and rejected: it would silently
omit most of what the author read and was told, distorting the corpus in the
opposite direction while making it look more epistemically clean than it is.
Keeping them and marking them is the honest move.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

__all__ = [
    "CorpusDocument",
    "CORPUS_ROOT",
    "AUTHOR",
    "TESTIMONY_TYPES",
    "MACHINE_TYPES",
    "load_corpus",
    "corpus_stats",
]

#: Where the corpus lives. A *default*, not a constant: the brief for this
#: project requires that no machine-specific absolute path be baked into the
#: code, only used when nothing else is specified. ``GANYMEDE4_CORPUS_ROOT``
#: overrides it, so a clean clone elsewhere reads the same artifact.
CORPUS_ROOT = Path(
    os.environ.get("GANYMEDE4_CORPUS_ROOT", Path.home() / "Projects" / "Chris")
).expanduser()
AUTHOR = "KonradFreeman"

#: The author speaking. First-person testimony.
TESTIMONY_TYPES = frozenset({"reddit_comment", "reddit_submission", "chatgpt_user"})

#: A model speaking. Kept, indexed, findable — and never testimony.
MACHINE_TYPES = frozenset({"chatgpt_assistant"})

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class CorpusDocument:
    """One document from the record, with its speaker typed.

    ``digest`` is over the content only, so re-ingesting an unchanged document
    is a no-op and a changed one produces a new Merkle leaf. ``meta`` keeps the
    Reddit metadata and conversation id, which is provenance-adjacent: it is
    how a reader checks where a claim came from without trusting the loader.
    """

    uri: str
    content: str
    source_type: str
    digest: str
    meta: dict


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` YAML block. Deliberately not a YAML parser.

    The export's frontmatter is a flat ``key: value`` map, and pulling in a
    YAML dependency for it would break the stdlib-only constraint. A real YAML
    parser would also be *wrong* here: silently accepting arbitrary YAML from a
    file that is about to become provenance is more power than this needs.

    So this accepts only top-level ``key: scalar`` and **stops at the first
    line it does not understand** — indentation, a list dash, a continuation.
    Stopping rather than skipping matters: a skip silently accepts a structure
    while claiming not to, so a nested key would be read as a top-level one and
    the caller would have no way to know the shape was misread. This parser
    says what it parsed, and says where it stopped.
    """
    match = _FM_RE.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # A nested block, list item, or block scalar is a structure this parser
        # does not model. Anything further is out of the block, not a key.
        if line[:1].isspace() or line.lstrip().startswith("-"):
            break
        if ":" not in line:
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
    return meta, text[match.end() :]


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _reddit_documents(subdir: str, kind: str) -> Iterator[CorpusDocument]:
    directory = CORPUS_ROOT / "reddit" / subdir
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, body = _split_frontmatter(raw)
        author = meta.get("author", "")
        if author and author != AUTHOR:
            # Someone else's words. Present in the export as quoted context,
            # not as this account's record, and it must not be typed as
            # testimony by KonradFreeman.
            source_type = f"reddit_{kind}_other"
        else:
            source_type = f"reddit_{kind}"
        yield CorpusDocument(
            uri=f"reddit://{subdir}/{path.stem}",
            content=body.strip(),
            source_type=source_type,
            digest=_digest(body.strip()),
            meta={
                "author": author,
                "created_utc": meta.get("created_utc", ""),
                "subreddit": meta.get("subreddit", ""),
                "reddit_id": meta.get("id", ""),
                "permalink": meta.get("permalink", ""),
            },
        )


def _conversation_documents() -> Iterator[CorpusDocument]:
    """Split each conversation into one document **per turn**.

    A whole conversation file is not a source. It interleaves the author with a
    model, and a document that contains both cannot be typed — which is the
    whole problem. Splitting on turn markers is what makes speaker
    attribution possible at all, and it is the only reason this loader is more
    than a `glob`.

    Both roles are kept. The machine's half is real: it is a large sample of
    model output in the wild, and the corpus is more faithful with it in.
    """
    root = CORPUS_ROOT / "openai" / "conversations_markdown"
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.md")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, _ = _split_frontmatter(raw)
        conv_id = meta.get("Conversation ID") or path.stem
        created = meta.get("Date", "")

        # `### 👤 User` / `### 🤖 Assistant`, each followed by a timestamp line
        # and then the turn body up to the next marker.
        markers = list(re.finditer(r"^###\s+(\S+.*?)\s*$", raw, re.MULTILINE))
        for index, marker in enumerate(markers):
            role_line = marker.group(1).strip()
            if "User" in role_line:
                source_type = "chatgpt_user"
            elif "Assistant" in role_line:
                source_type = "chatgpt_assistant"
            else:
                continue
            end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
            body = raw[marker.end() : end].strip()
            # Drop the `**Timestamp**:` line and `---` rules that are export
            # scaffolding rather than anything the author wrote.
            body = re.sub(r"^\*\*Timestamp\*\*:.*$", "", body, flags=re.MULTILINE)
            body = body.replace("---", "").strip()
            if not body:
                continue
            yield CorpusDocument(
                uri=f"chatgpt://{conv_id}/{index:04d}",
                content=body,
                source_type=source_type,
                digest=_digest(body),
                meta={
                    "conversation_id": conv_id,
                    "created": created,
                    "role": source_type.split("_")[-1],
                },
            )


def load_corpus(*, limit: int | None = None) -> Iterator[CorpusDocument]:
    """Yield every document, testimony and machine output alike.

    Ordered deterministically: Reddit before ChatGPT, each sorted by path. The
    order does not affect the Merkle root (which sorts leaves), but a stable
    iteration makes a truncated run reproducible and therefore checkable.
    """
    count = 0
    for generator in (
        lambda: _reddit_documents("comments", "comment"),
        lambda: _reddit_documents("submissions", "submission"),
        _conversation_documents,
    ):
        for doc in generator():
            yield doc
            count += 1
            if limit is not None and count >= limit:
                return


def corpus_stats(limit: int | None = None) -> dict:
    """Counts and digests, for reporting rather than for testing.

    Includes a digest of every document id so two runs over the corpus can be
    compared without diffing 65 MB.
    """
    by_type: dict[str, int] = {}
    total_bytes = 0
    ids: list[str] = []
    for doc in load_corpus(limit=limit):
        by_type[doc.source_type] = by_type.get(doc.source_type, 0) + 1
        total_bytes += len(doc.content.encode("utf-8"))
        ids.append(f"{doc.source_type}:{doc.uri}:{doc.digest}")
    ids.sort()
    return {
        "documents": len(ids),
        "bytes": total_bytes,
        "by_type": dict(sorted(by_type.items())),
        "testimony_documents": sum(v for k, v in by_type.items() if k in TESTIMONY_TYPES),
        "machine_documents": sum(v for k, v in by_type.items() if k in MACHINE_TYPES),
        "corpus_digest": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
    }
