from abc import ABC, abstractmethod
from typing import Dict, Any, List


class MediaEngine(ABC):
    @abstractmethod
    def extract_info(self, url: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def download(self, url: str, out_dest: str, **kwargs) -> List[str]:
        pass

