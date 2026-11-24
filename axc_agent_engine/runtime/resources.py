"""Shared resource registry for engine plugins. 引擎插件使用的共享资源注册表。"""

from __future__ import annotations

from typing import Any


class ResourceError(Exception):
	"""Base error for resource registry failures. 资源注册错误基类。"""


class ResourceNotFoundError(ResourceError):
	"""Raised when a required resource is missing. 缺少必要资源。"""


class ResourceTypeError(ResourceError):
	"""Raised when a resource does not match the requested type. 资源与请求的类型不匹配。"""


class DuplicateResourceError(ResourceError):
	"""Raised when registering a duplicate resource without replace=True. 重复注册资源且未显式允许替换。"""


class ResourceRegistry:
	"""Small typed-name container for host-owned shared resources. 按名称和类型查找宿主持有的共享资源。"""

	def __init__(self, initial: dict[str, object] | None = None) -> None:
		self._resources: dict[str, object] = {}
		for name, resource in (initial or {}).items():
			self.register(name, resource)

	def register(self, name: str, resource: object, *, replace: bool = False) -> None:
		if not name:
			raise ValueError("resource name must not be empty")
		if name in self._resources and not replace:
			raise DuplicateResourceError(f"Resource already registered: {name}")
		self._resources[name] = resource

	def get(self, name: str, expected_type: type | None = None) -> Any | None:
		resource = self._resources.get(name)
		if resource is None:
			return None
		self._validate_type(name, resource, expected_type)
		return resource

	def require(self, name: str, expected_type: type | None = None) -> Any:
		resource = self.get(name, expected_type)
		if resource is None:
			raise ResourceNotFoundError(f"Resource not found: {name}")
		return resource

	def names(self) -> tuple[str, ...]:
		return tuple(sorted(self._resources))

	def as_dict(self) -> dict[str, object]:
		return dict(self._resources)

	def _validate_type(self, name: str, resource: object, expected_type: type | None) -> None:
		if expected_type is not None and not isinstance(resource, expected_type):
			expected = expected_type.__name__
			actual = type(resource).__name__
			raise ResourceTypeError(f"Resource '{name}' expected {expected}, got {actual}")


def ensure_resource_registry(resources: dict[str, object] | ResourceRegistry | None) -> ResourceRegistry:
	"""Normalize public resource input into a ResourceRegistry. 将公共入口的资源转换为资源注册表。"""
	if resources is None:
		return ResourceRegistry()
	if isinstance(resources, ResourceRegistry):
		return resources
	return ResourceRegistry(resources)
