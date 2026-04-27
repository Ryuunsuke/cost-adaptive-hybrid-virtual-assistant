from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseTool(ABC):
    """Base class for all available tools"""
    
    name: str
    description: str
    
    @abstractmethod
    async def execute(self, *args, **kwargs) -> Any:
        """Execute the tool with given arguments"""
        pass
    
    def get_info(self) -> Dict[str, str]:
        """Return tool metadata for LLM prompts"""
        return {
            "name": self.name,
            "description": self.description
        }
