from pathlib import Path

from procureops.harness.audit import InMemoryAuditSink
from procureops.harness.budget import RunBudgetLedger
from procureops.harness.model_gateway import FakeModel, ModelGateway
from procureops.intake import IntakeService
from procureops.intake.model_extractors import GatewayTextExtractor, GatewayVisionExtractor


def test_gateway_text_extractor_is_used_only_after_deterministic_parser(
    run_context,
) -> None:
    fake = FakeModel(
        {
            "procurement_line_extraction": {
                "lines": [
                    {
                        "description": "液压泵",
                        "quantity": "2",
                        "unit": "台",
                        "part_number": "DEMO-HYD-PUMP-001",
                    }
                ]
            }
        }
    )
    extractor = GatewayTextExtractor(
        gateway=ModelGateway(client=fake, audit=InMemoryAuditSink()),
        context=run_context,
        ledger=RunBudgetLedger(run_context),
    )

    result = IntakeService(text_extractor=extractor).from_text("帮我采购附件里的主泵")

    assert result.lines[0].part_number == "DEMO-HYD-PUMP-001"
    assert len(fake.calls) == 1


def test_gateway_vision_extractor_sends_file_through_model_gateway(
    tmp_path: Path,
    run_context,
) -> None:
    image = tmp_path / "request.png"
    image.write_bytes(b"fake-image")
    fake = FakeModel(
        {
            "procurement_image_extraction": {
                "lines": [
                    {
                        "description": "喷油器",
                        "quantity": "6",
                        "unit": "支",
                        "part_number": "DEMO-ENG-INJ-001",
                    }
                ]
            }
        }
    )
    extractor = GatewayVisionExtractor(
        gateway=ModelGateway(client=fake, audit=InMemoryAuditSink()),
        context=run_context,
        ledger=RunBudgetLedger(run_context),
    )

    result = IntakeService(vision_extractor=extractor).from_file(image)

    assert result.lines[0].quantity == 6
    assert fake.calls[0].payload["file_base64"]
