from dataclasses import dataclass
from typing import Any


@dataclass
class Metrics:
    ret: float
    tvr: float
    shrp: float
    mdd: float
    fitness: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ret%": self.ret,
            "tvr%": self.tvr,
            "shrp": self.shrp,
            "mdd%": self.mdd,
            "fitness": self.fitness,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metrics":
        return cls(
            ret=data["ret%"],
            tvr=data["tvr%"],
            shrp=data["shrp"],
            mdd=data["mdd%"],
            fitness=data["fitness"],
        )

    def __repr__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, mdd={self.mdd}%, tvr={self.tvr}%, fitness={self.fitness}"

    def __str__(self):
        return self.__repr__()
