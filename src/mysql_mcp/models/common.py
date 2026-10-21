"""Result models shared by every tool."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Refusal(BaseModel):
    """A policy refusal. Returned as a normal tool result, never raised."""

    model_config = ConfigDict(frozen=True)

    refused: Literal[True] = True
    code: str
    reason: str
    suggestion: str
