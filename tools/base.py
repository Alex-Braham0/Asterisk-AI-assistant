from abc import ABC, abstractmethod

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """The function name exposed to the AI."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """The description guiding the AI on when/how to use the tool."""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict:
        """The JSON schema defining required and optional arguments."""
        pass
    
    @property
    @abstractmethod
    def auth_level(self) -> int:
        """Required access level (0=Guest, 10=Standard, 100=Admin)."""
        pass

    @abstractmethod
    async def execute(self, session, args: dict) -> dict:
        """Executes the tool logic. Must return a dictionary."""
        pass

    def get_declaration(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }