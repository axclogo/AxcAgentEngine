"""Semantic document chunking.

The chunker is storage-neutral and intentionally has no dependency on the
Knowledge plugin. It preserves heading context for Markdown documents and
falls back to sentence-aware splitting for plain text.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

DEFAULT_MAX_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class Chunk:
	"""A semantically bounded text chunk."""
	content: str
	content_with_context: str
	source: str = ""
	heading_path: str = ""
	chunk_index: int = 0
	content_hash: str = ""
	metadata: dict = field(default_factory=dict)

	def to_dict(self) -> dict:
		return {
			"text": self.content_with_context,
			"content": self.content,
			"source": self.source,
			"heading_path": self.heading_path,
			"chunk_index": self.chunk_index,
			"content_hash": self.content_hash,
			"metadata": dict(self.metadata),
		}


class SemanticChunker:
	"""Markdown heading-aware and sentence-boundary chunker."""

	def __init__(self, max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
				 chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> None:
		self.max_chunk_size = max(128, int(max_chunk_size))
		self.chunk_overlap = max(0, int(chunk_overlap))

	def chunk_document(self, content: str, source: str = "", title: str = "") -> list[Chunk]:
		"""Split one document into semantic chunks."""
		if not content or not content.strip():
			return []
		sections = self._parse_markdown_sections(content)
		if not sections:
			return self._fallback_split(content, source, title)
		chunks: list[Chunk] = []
		idx = 0
		for header_chain, body in sections:
			heading_path = " > ".join(h.lstrip("#").strip() for h in header_chain)
			context_prefix = f"[{heading_path}]" if heading_path else (f"[{title}]" if title else "")
			full_text = f"{context_prefix}\n{body}" if context_prefix else body
			if len(full_text) <= self.max_chunk_size:
				chunks.append(self._make_chunk(body, context_prefix, heading_path or title, idx, source))
				idx += 1
				continue
			max_body = max(64, self.max_chunk_size - len(context_prefix) - 10)
			for piece in self._split_by_sentence(body, max_body):
				chunks.append(self._make_chunk(piece, context_prefix, heading_path or title, idx, source))
				idx += 1
		return chunks

	def _parse_markdown_sections(self, content: str) -> list[tuple[list[str], str]]:
		header_pattern = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)
		headers = [(m.start(), m.end(), len(m.group(1)), m.group(0)) for m in header_pattern.finditer(content)]
		if not headers:
			return []
		sections: list[tuple[list[str], str]] = []
		header_stack: list[tuple[int, str]] = []
		if headers[0][0] > 0:
			preamble = content[:headers[0][0]].strip()
			if preamble:
				sections.append(([], preamble))
		for i, (start, end, level, header_line) in enumerate(headers):
			_ = start
			next_start = headers[i + 1][0] if i + 1 < len(headers) else len(content)
			body = content[end:next_start].strip()
			while header_stack and header_stack[-1][0] >= level:
				header_stack.pop()
			header_stack.append((level, header_line))
			if body:
				sections.append(([h for _, h in header_stack], body))
		return sections

	def _fallback_split(self, content: str, source: str, title: str) -> list[Chunk]:
		context_prefix = f"[{title}]" if title else ""
		return [
			self._make_chunk(piece, context_prefix, title, idx, source)
			for idx, piece in enumerate(self._split_by_sentence(content, self.max_chunk_size))
		]

	def _split_by_sentence(self, text: str, max_size: int) -> list[str]:
		pieces = self._recursive_split(text, ["\n\n", "\n", "。", "；", "，", ". ", " "], max_size)
		merged: list[str] = []
		buf = ""
		prev_tail = ""
		for piece in pieces:
			candidate = buf + piece if buf else piece
			if len(candidate) > max_size and buf:
				chunk_text = prev_tail + buf if prev_tail else buf
				if len(chunk_text) > max_size * 1.5:
					chunk_text = buf
				merged.append(chunk_text.strip())
				prev_tail = buf[-self.chunk_overlap:] + "\n" if self.chunk_overlap and len(buf) > self.chunk_overlap else ""
				buf = piece
			else:
				buf = candidate
		if buf.strip():
			chunk_text = prev_tail + buf if prev_tail else buf
			if len(chunk_text) > max_size * 1.5:
				chunk_text = buf
			merged.append(chunk_text.strip())
		return [m for m in merged if m]

	def _recursive_split(self, text: str, separators: list[str], max_size: int) -> list[str]:
		if len(text) <= max_size or not separators:
			return [text] if text.strip() else []
		sep = separators[0]
		remaining = separators[1:]
		result: list[str] = []
		for part in text.split(sep):
			if not part.strip():
				continue
			piece = part + sep if sep in ("\n\n", "\n", ". ") else part
			if len(piece) <= max_size:
				result.append(piece)
			else:
				result.extend(self._recursive_split(piece, remaining, max_size))
		return result

	def _make_chunk(self, content: str, context_prefix: str, heading_path: str,
					idx: int, source: str) -> Chunk:
		content = content.strip()
		content_with_context = f"{context_prefix}\n{content}" if context_prefix else content
		return Chunk(
			content=content,
			content_with_context=content_with_context,
			source=source,
			heading_path=heading_path[:500],
			chunk_index=idx,
			content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
			metadata={"source": source, "heading_path": heading_path[:500]},
		)
