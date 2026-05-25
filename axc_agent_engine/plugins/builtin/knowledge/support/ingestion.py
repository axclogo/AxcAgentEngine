"""Storage-neutral knowledge ingestion primitives."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.plugins.builtin.knowledge.support.chunker import SemanticChunker
from axc_agent_engine.plugins.builtin.knowledge.support.retrieval import KnowledgeDocument


@dataclass(frozen=True)
class SourceDocument:
	"""Parsed source document before chunking."""

	id: str
	text: str
	source: str
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionResult:
	"""Result of one ingestion pass."""

	documents: list[KnowledgeDocument]
	sources: list[str] = field(default_factory=list)
	errors: list[str] = field(default_factory=list)


@runtime_checkable
class DocumentParser(Protocol):
	"""Parses one source into plain text plus metadata."""

	def supports(self, path: str) -> bool: ...
	def parse(self, path: str) -> SourceDocument | None: ...


class TextDocumentParser:
	"""Built-in parser for text-like local files."""

	extensions = {".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}

	def supports(self, path: str) -> bool:
		return os.path.splitext(path)[1].lower() in self.extensions

	def parse(self, path: str) -> SourceDocument | None:
		with open(path, "r", encoding="utf-8") as handle:
			text = handle.read()
		return SourceDocument(id=path, text=text, source=path, metadata=_base_metadata(path))


class PdfDocumentParser:
	"""Optional PyMuPDF-backed PDF parser."""

	def supports(self, path: str) -> bool:
		return path.lower().endswith(".pdf")

	def parse(self, path: str) -> SourceDocument | None:
		try:
			import fitz
		except ImportError:
			return None
		doc = fitz.open(path)
		try:
			text_parts = [page.get_text() for page in doc]
		finally:
			doc.close()
		return SourceDocument(id=path, text="\n".join(text_parts), source=path, metadata=_base_metadata(path))


class LocalFileIngestionPipeline:
	"""Ingests local files/directories into KnowledgeDocument chunks."""

	def __init__(
		self,
		chunker: SemanticChunker | None = None,
		parsers: list[DocumentParser] | None = None,
		workspace: str = "",
		namespace: str = "",
		default_metadata: dict[str, Any] | None = None,
	) -> None:
		self.chunker = chunker or SemanticChunker()
		self.parsers = parsers or [TextDocumentParser(), PdfDocumentParser()]
		self.workspace = workspace
		self.namespace = namespace
		self.default_metadata = dict(default_metadata or {})

	def ingest(self, sources: list[str]) -> IngestionResult:
		documents: list[KnowledgeDocument] = []
		loaded_sources: list[str] = []
		errors: list[str] = []
		for source in sources:
			for path in self._iter_paths(source, errors):
				parser = self._parser_for(path)
				if not parser:
					continue
				try:
					parsed = parser.parse(path)
				except Exception as exc:
					errors.append(f"{path}: {exc}")
					continue
				if not parsed or not parsed.text.strip():
					continue
				loaded_sources.append(path)
				documents.extend(self._chunk(parsed))
		return IngestionResult(documents=documents, sources=loaded_sources, errors=errors)

	def _chunk(self, source_doc: SourceDocument) -> list[KnowledgeDocument]:
		chunks = self.chunker.chunk_document(source_doc.text, source=source_doc.source, title=str(source_doc.metadata.get("title", "")))
		documents: list[KnowledgeDocument] = []
		for chunk in chunks:
			metadata = {
				**self.default_metadata,
				**source_doc.metadata,
				**chunk.metadata,
				"namespace": self.namespace,
				"document_id": source_doc.id,
				"chunk_id": chunk.chunk_index,
				"content_hash": chunk.content_hash,
				"heading_path": chunk.heading_path,
			}
			doc_id = f"{source_doc.id}:{chunk.chunk_index}:{chunk.content_hash[:12]}"
			documents.append(KnowledgeDocument(
				id=doc_id,
				text=chunk.content_with_context,
				source=source_doc.source,
				metadata=metadata,
			))
		return documents

	def _iter_paths(self, source: str, errors: list[str]):
		resolved = self._resolve_path(source)
		if not resolved:
			errors.append(f"source not found or outside workspace: {source}")
			return
		if os.path.isfile(resolved):
			yield resolved
			return
		if os.path.isdir(resolved):
			for root, _, files in os.walk(resolved):
				for filename in files:
					path = os.path.join(root, filename)
					if self._parser_for(path):
						yield path

	def _resolve_path(self, path: str) -> str | None:
		if self.workspace:
			full_path = os.path.realpath(os.path.join(self.workspace, path))
			workspace_real = os.path.realpath(self.workspace)
			if not (full_path == workspace_real or full_path.startswith(workspace_real + os.sep)):
				return None
			return full_path if os.path.exists(full_path) else None
		return path if os.path.exists(path) else None

	def _parser_for(self, path: str) -> DocumentParser | None:
		return next((parser for parser in self.parsers if parser.supports(path)), None)


def _base_metadata(path: str) -> dict[str, Any]:
	try:
		stat = os.stat(path)
		updated_at = stat.st_mtime
	except OSError:
		updated_at = 0.0
	return {
		"source": path,
		"title": os.path.basename(path),
		"updated_at": updated_at,
	}
