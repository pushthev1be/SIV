"""The four JSON contracts. web/src/contracts.ts is generated from these; never edit it by hand."""

from contracts.cue import AudienceProfile, Band, OverlayCue
from contracts.event import BodyFrame, MatchEvent
from contracts.incident import Incident
from contracts.replay import ReplayPackage

ROOT_MODELS = (MatchEvent, Incident, OverlayCue, ReplayPackage)

__all__ = [
    "AudienceProfile",
    "Band",
    "BodyFrame",
    "Incident",
    "MatchEvent",
    "OverlayCue",
    "ReplayPackage",
    "ROOT_MODELS",
]
