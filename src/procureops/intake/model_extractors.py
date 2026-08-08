from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from procureops.domain.models import RunContext
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import ModelGateway, ModelRequest
from procureops.intake.prompts import (
    DEFAULT_TEXT_EXTRACTION_PROMPT,
    DEFAULT_VISION_EXTRACTION_PROMPT,
)


class GatewayTextExtractor:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        context: RunContext,
        ledger: RunBudgetLedger,
        instruction: str = DEFAULT_TEXT_EXTRACTION_PROMPT,
    ) -> None:
        self.gateway = gateway
        self.context = context
        self.ledger = ledger
        self.instruction = instruction

    def extract(self, text: str) -> list[dict[str, Any]]:
        response = self.gateway.invoke(
            context=self.context,
            ledger=self.ledger,
            request=ModelRequest(
                purpose="procurement_line_extraction",
                payload={
                    "source_text": text,
                    "instruction": self.instruction,
                },
                response_schema="ProcurementLineExtractionV1",
            ),
        )
        lines = response.output.get("lines", [])
        if not isinstance(lines, list):
            raise ValueError("model extraction lines must be a list")
        return lines


class GatewayVisionExtractor:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        context: RunContext,
        ledger: RunBudgetLedger,
        instruction: str = DEFAULT_VISION_EXTRACTION_PROMPT,
    ) -> None:
        self.gateway = gateway
        self.context = context
        self.ledger = ledger
        self.instruction = instruction

    def extract(self, path: Path) -> list[dict[str, Any]]:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self.gateway.invoke(
            context=self.context,
            ledger=self.ledger,
            request=ModelRequest(
                purpose="procurement_image_extraction",
                payload={
                    "file_name": path.name,
                    "mime_type": mime_type,
                    "file_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "instruction": self.instruction,
                },
                response_schema="ProcurementLineExtractionV1",
            ),
        )
        lines = response.output.get("lines", [])
        if not isinstance(lines, list):
            raise ValueError("vision extraction lines must be a list")
        return lines
