from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfWriter

from procureops.intake.service import FakeVisionExtractor, IntakeService


def test_text_intake_extracts_structured_line_and_evidence() -> None:
    result = IntakeService().from_text(
        "液压泵 DEMO-HYD-PUMP-001 x2 台",
        artifact_id="request.txt",
    )

    assert not result.questions
    assert result.lines[0].part_number == "DEMO-HYD-PUMP-001"
    assert result.lines[0].quantity == 2
    assert {item.field_name for item in result.evidence} >= {
        "description",
        "quantity",
        "part_number",
    }


def test_excel_intake_supports_chinese_headers(tmp_path: Path) -> None:
    path = tmp_path / "request.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["零件号", "品名", "数量", "单位", "设备型号"])
    sheet.append(["DEMO-FLT-KIT-001", "保养滤芯包", 3, "套", "SVC-2000H-A"])
    workbook.save(path)

    result = IntakeService().from_file(path)

    assert result.source_type == "excel"
    assert result.lines[0].equipment_model == "SVC-2000H-A"
    assert result.evidence[0].locator.endswith("!A2")


def test_image_intake_uses_injected_fake_vision(tmp_path: Path) -> None:
    path = tmp_path / "request.png"
    path.write_bytes(b"synthetic-image-bytes")
    vision = FakeVisionExtractor(
        [
            {
                "part_number": "DEMO-ENG-INJ-001",
                "description": "喷油器",
                "quantity": "6",
                "unit": "支",
            }
        ]
    )

    result = IntakeService(vision_extractor=vision).from_file(path)

    assert result.lines[0].quantity == 6
    assert vision.calls == [path]
    assert result.evidence[0].confidence == 0.85


def test_pdf_without_extractable_text_requests_input(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as handle:
        writer.write(handle)

    result = IntakeService().from_file(path)

    assert result.source_type == "pdf"
    assert result.questions


def test_scanned_pdf_falls_back_to_vision_extractor(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as handle:
        writer.write(handle)
    vision = FakeVisionExtractor(
        [
            {
                "part_number": "DEMO-FLT-KIT-001",
                "description": "滤芯包",
                "quantity": "2",
                "unit": "套",
            }
        ]
    )

    result = IntakeService(vision_extractor=vision).from_file(path)

    assert result.source_type == "pdf_vision"
    assert result.lines[0].part_number == "DEMO-FLT-KIT-001"
    assert vision.calls == [path]
