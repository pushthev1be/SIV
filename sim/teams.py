"""Two invented clubs. No real club, league or player is represented."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerSpec:
    number: int
    name: str
    role: str  # GK | DF | MF | FW
    # Formation slot in the team's attacking frame: x 0..1 from own goal, y 0..1 across.
    slot: tuple[float, float]
    top_speed_kmh: float
    skill: float  # 0..1, passing and finishing


@dataclass(frozen=True)
class TeamSpec:
    code: str
    name: str
    colour: str
    players: tuple[PlayerSpec, ...]


NORTHBRIDGE = TeamSpec(
    "NBR",
    "Northbridge",
    "#F2B33D",
    (  # 4-3-3
        PlayerSpec(1, "Halloran", "GK", (0.02, 0.50), 26.0, 0.70),
        PlayerSpec(2, "Brightwell", "DF", (0.22, 0.12), 33.4, 0.74),
        PlayerSpec(3, "Mbeki", "DF", (0.20, 0.88), 33.0, 0.73),
        PlayerSpec(4, "Varga", "DF", (0.18, 0.38), 31.5, 0.76),
        PlayerSpec(5, "Lindqvist", "DF", (0.18, 0.62), 31.2, 0.75),
        PlayerSpec(6, "Adeyemi", "MF", (0.38, 0.50), 31.8, 0.82),
        PlayerSpec(8, "Moreau", "MF", (0.48, 0.32), 32.2, 0.86),
        PlayerSpec(10, "Takahashi", "MF", (0.50, 0.68), 32.0, 0.88),
        PlayerSpec(7, "Castellanos", "FW", (0.70, 0.14), 34.6, 0.80),
        PlayerSpec(11, "Brennan", "FW", (0.70, 0.86), 34.1, 0.79),
        PlayerSpec(9, "Okafor", "FW", (0.78, 0.50), 33.8, 0.84),
    ),
)

ASHVALE = TeamSpec(
    "ASH",
    "Ashvale",
    "#5AA9E6",
    (  # 4-4-2
        PlayerSpec(1, "Kowalczyk", "GK", (0.02, 0.50), 25.5, 0.68),
        PlayerSpec(2, "Ferreira", "DF", (0.22, 0.12), 33.2, 0.72),
        PlayerSpec(3, "Doyle", "DF", (0.22, 0.88), 32.6, 0.71),
        PlayerSpec(4, "Achterberg", "DF", (0.18, 0.38), 31.0, 0.74),
        PlayerSpec(5, "Ruiz", "DF", (0.18, 0.62), 31.4, 0.73),
        PlayerSpec(7, "Whitlock", "MF", (0.45, 0.12), 33.9, 0.78),
        PlayerSpec(6, "Nakamura", "MF", (0.42, 0.40), 31.6, 0.83),
        PlayerSpec(8, "Osei", "MF", (0.42, 0.60), 32.4, 0.81),
        PlayerSpec(11, "Sorensen", "MF", (0.45, 0.88), 33.5, 0.77),
        PlayerSpec(9, "Marchetti", "FW", (0.72, 0.42), 33.6, 0.82),
        PlayerSpec(10, "Haddad", "FW", (0.72, 0.58), 34.0, 0.80),
    ),
)

TEAMS: dict[str, TeamSpec] = {t.code: t for t in (NORTHBRIDGE, ASHVALE)}
