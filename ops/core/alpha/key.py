from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlphaKey:
    user: str
    date: str
    name: str

    def __str__(self) -> str:
        return f"{self.user}/{self.date}/{self.name}"