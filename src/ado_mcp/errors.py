from dataclasses import dataclass
from typing import override


@dataclass(slots=True)
class AdoApiError(Exception):
    status_code: int
    operation: str

    @override
    def __str__(self) -> str:
        return (
            f"Azure DevOps request failed with HTTP {self.status_code} "
            f"during {self.operation}"
        )


@dataclass(slots=True)
class ConfigurationError(Exception):
    message: str

    @override
    def __str__(self) -> str:
        return self.message
