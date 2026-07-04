from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ServicePriority(Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


@dataclass
class ServiceConfig:
    """Simplified service configuration"""
    module_path: str
    class_name: str
    dependencies: List[str] = None
    priority: ServicePriority = ServicePriority.NORMAL
    factory_method: Optional[str] = None
    optional: bool = False
    singleton: bool = True

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
