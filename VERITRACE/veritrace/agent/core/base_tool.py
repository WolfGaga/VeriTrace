"""
Base Tool Framework with Pydantic + ABC pattern.

This module provides the foundation for creating tools that can be used by LLM agents.
It supports both synchronous and asynchronous execution, and generates OpenAI-compatible
function definitions for tool calling.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Standardized result from tool execution."""

    success: bool = Field(description="Whether the tool execution was successful")
    output: Any = Field(default=None, description="The output/result of the tool execution")
    error: Optional[str] = Field(default=None, description="Error message if execution failed")

    def __str__(self) -> str:
        if self.success:
            return str(self.output) if self.output is not None else "Success"
        return f"Error: {self.error}"


class BaseTool(BaseModel, ABC):
    """
    Abstract base class for all tools.

    Tools are defined using Pydantic fields with descriptions, which are automatically
    converted to JSON Schema for LLM function calling. Each tool must implement the
    `execute` method.

    Example:
        ```python
        class WebSearchTool(BaseTool):
            query: str = Field(description="The search query to execute")
            max_results: int = Field(default=10, description="Maximum number of results")

            @property
            def name(self) -> str:
                return "web_search"

            @property
            def description(self) -> str:
                return "Search the web for information"

            def execute(self) -> ToolResult:
                # Implementation here
                results = perform_search(self.query, self.max_results)
                return ToolResult(success=True, output=results)
        ```
    """

    model_config = ConfigDict(extra="forbid")

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the tool."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        pass

    @abstractmethod
    def execute(self) -> ToolResult:
        """
        Execute the tool with the current parameters.

        This method contains the actual business logic of the tool.
        Subclasses must implement this method.

        Returns:
            ToolResult: The result of the tool execution.
        """
        pass

    async def aexecute(self) -> ToolResult:
        """
        Asynchronous execution of the tool.

        By default, this wraps the synchronous `execute` method in a thread pool.
        Subclasses can override this for truly async implementations.

        Returns:
            ToolResult: The result of the tool execution.
        """
        return await asyncio.to_thread(self.execute)

    @classmethod
    def get_parameters_schema(cls) -> Dict[str, Any]:
        """
        Generate JSON Schema for the tool's parameters.

        This extracts the schema from Pydantic's model_json_schema(),
        filtering out internal fields (name, description).

        Returns:
            Dict containing the parameters schema in JSON Schema format.
        """
        schema = cls.model_json_schema()
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Filter out base class fields that shouldn't be parameters
        excluded_fields = {"name", "description"}
        filtered_properties = {k: v for k, v in properties.items() if k not in excluded_fields}
        filtered_required = [r for r in required if r not in excluded_fields]

        return {
            "type": "object",
            "properties": filtered_properties,
            "required": filtered_required,
        }

    @classmethod
    def to_openai_schema(cls) -> Dict[str, Any]:
        """
        Generate OpenAI-compatible function definition.

        Returns:
            Dict in OpenAI's function calling format:
            {
                "type": "function",
                "function": {
                    "name": "tool_name",
                    "description": "What the tool does",
                    "parameters": { ... JSON Schema ... }
                }
            }
        """
        # Create a temporary instance to get name and description
        # We need to handle this carefully for abstract properties
        instance = None
        try:
            # Try to get default values for all fields
            field_defaults = {}
            for field_name, field_info in cls.model_fields.items():
                if field_info.default is not None:
                    field_defaults[field_name] = field_info.default
                elif field_info.default_factory is not None:
                    field_defaults[field_name] = field_info.default_factory()
                else:
                    # Use placeholder values based on type
                    annotation = field_info.annotation
                    if annotation == str:
                        field_defaults[field_name] = ""
                    elif annotation == int:
                        field_defaults[field_name] = 0
                    elif annotation == float:
                        field_defaults[field_name] = 0.0
                    elif annotation == bool:
                        field_defaults[field_name] = False
                    elif annotation == list or str(annotation).startswith("typing.List"):
                        field_defaults[field_name] = []
                    elif annotation == dict or str(annotation).startswith("typing.Dict"):
                        field_defaults[field_name] = {}

            instance = cls(**field_defaults)
            name = instance.name
            description = instance.description
        except Exception:
            # Fallback to class name if instance creation fails
            name = cls.__name__.lower().replace("tool", "")
            description = cls.__doc__ or "No description available"

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": cls.get_parameters_schema(),
            },
        }

    @classmethod
    def to_anthropic_schema(cls) -> Dict[str, Any]:
        """
        Generate Anthropic/Claude-compatible tool definition.

        Returns:
            Dict in Anthropic's tool format:
            {
                "name": "tool_name",
                "description": "What the tool does",
                "input_schema": { ... JSON Schema ... }
            }
        """
        openai_schema = cls.to_openai_schema()
        return {
            "name": openai_schema["function"]["name"],
            "description": openai_schema["function"]["description"],
            "input_schema": openai_schema["function"]["parameters"],
        }

    @classmethod
    def from_dict(cls, params: Dict[str, Any]) -> "BaseTool":
        """
        Create a tool instance from a dictionary of parameters.

        Args:
            params: Dictionary mapping parameter names to values.

        Returns:
            An instance of the tool with the given parameters.
        """
        return cls(**params)


class ToolRegistry:
    """
    Registry for managing available tools.

    This provides a central place to register tools and look them up by name.
    """

    def __init__(self):
        self._tools: Dict[str, Type[BaseTool]] = {}

    def register(self, tool_cls: Type[BaseTool]) -> Type[BaseTool]:
        """
        Register a tool class.

        Can be used as a decorator:
            @registry.register
            class MyTool(BaseTool):
                ...

        Args:
            tool_cls: The tool class to register.

        Returns:
            The same tool class (for decorator use).
        """
        # Get tool name from a temporary instance or class
        try:
            instance = tool_cls()
            name = instance.name
        except Exception:
            name = tool_cls.__name__.lower().replace("tool", "")

        self._tools[name] = tool_cls
        return tool_cls

    def get(self, name: str) -> Optional[Type[BaseTool]]:
        """Get a tool class by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_all_schemas(self, format: str = "openai") -> List[Dict[str, Any]]:
        """
        Get schemas for all registered tools.

        Args:
            format: Schema format - "openai" or "anthropic"

        Returns:
            List of tool schemas.
        """
        schemas = []
        for tool_cls in self._tools.values():
            if format == "anthropic":
                schemas.append(tool_cls.to_anthropic_schema())
            else:
                schemas.append(tool_cls.to_openai_schema())
        return schemas

    def create_tool(self, name: str, params: Dict[str, Any]) -> Optional[BaseTool]:
        """
        Create a tool instance by name with given parameters.

        Args:
            name: The tool name.
            params: Parameters to pass to the tool.

        Returns:
            Tool instance, or None if tool not found.
        """
        tool_cls = self._tools.get(name)
        if tool_cls is None:
            return None
        return tool_cls.from_dict(params)


# Global registry instance
default_registry = ToolRegistry()


def register_tool(tool_cls: Type[BaseTool]) -> Type[BaseTool]:
    """
    Decorator to register a tool with the default registry.

    Example:
        @register_tool
        class MyTool(BaseTool):
            ...
    """
    return default_registry.register(tool_cls)
