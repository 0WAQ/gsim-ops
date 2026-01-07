from abc import ABC, abstractmethod
from ...common.alpha.metadata import AlphaMetadata


class CheckFail(Exception):
    def __init__(self, stage: str, *args: object):
        self.stage = stage
        super().__init__(*args)
    
    def __repr__(self):
        if len(self.args) == 0:
            return ""
        if len(self.args) == 1:
            print(self.args[0])
            return repr(self.args[0])
        return repr(self.args)

class CheckSkip(Exception):
    def __init__(self, stage: str, *args: object):
        self.stage = stage
        super().__init__(*args)

    def __repr__(self):
        if len(self.args) == 0:
            return ""
        if len(self.args) == 1:
            return repr(self.args[0])
        return repr(self.args)


class Checker(ABC):
    @abstractmethod
    def check(self, factor: AlphaMetadata) -> None:
        ...
