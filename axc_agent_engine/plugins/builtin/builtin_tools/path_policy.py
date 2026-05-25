"""Workspace path policy for builtin file and command tools."""
import os
from datetime import datetime, timezone
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput


class PathValidationError(ValueError):
	"""Raised when a path does not pass workspace boundary validation."""


class BuiltinPathPolicy:
	def workspace_required_error(self, tool_name: str) -> ToolOutput:
		return ToolOutput.error(f"{tool_name} requires a configured workspace")

	def unsafe_workspace_allowed(self, context: dict[str, Any]) -> bool:
		return bool(context.get("allow_unsafe_workspace"))

	def get_workspace(self, context: dict[str, Any], tool_name: str) -> str | ToolOutput:
		workspace = context.get("workspace", "")
		if workspace or self.unsafe_workspace_allowed(context):
			return workspace
		return self.workspace_required_error(tool_name)

	def resolve_workspace_path(self, path: str, context: dict[str, Any]) -> str:
		workspace = context.get("workspace", "")
		if not workspace:
			if not self.unsafe_workspace_allowed(context):
				raise PathValidationError("Workspace is required for file tools")
			return path
		full_path = os.path.realpath(os.path.join(workspace, path))
		workspace_real = os.path.realpath(workspace)
		if not (full_path == workspace_real or full_path.startswith(workspace_real + os.sep)):
			raise PathValidationError("Path outside workspace boundary")
		return full_path

	def file_entry(self, full_path: str, context: dict[str, Any]) -> dict[str, Any]:
		workspace = context.get("workspace", "")
		workspace_real = os.path.realpath(workspace) if workspace else ""
		full_real = os.path.realpath(full_path)
		if workspace_real and (full_real == workspace_real or full_real.startswith(workspace_real + os.sep)):
			display_path = os.path.relpath(full_real, workspace_real)
			if display_path == ".":
				display_path = ""
		else:
			display_path = full_path
		stat = os.stat(full_real)
		is_dir = os.path.isdir(full_real)
		return {
			"path": display_path,
			"name": os.path.basename(full_real),
			"type": "directory" if is_dir else "file",
			"size": stat.st_size,
			"modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
		}
