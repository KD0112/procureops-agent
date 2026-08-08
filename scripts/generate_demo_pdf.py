"""Generate a text-extractable, fictional procurement request PDF fixture."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "demo_assets" / "requests" / "procurement_request.pdf"
FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    font = "MicrosoftYaHei" if FONT_PATH.is_file() else "Helvetica"
    if FONT_PATH.is_file():
        pdfmetrics.registerFont(TTFont(font, FONT_PATH))
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN",
        parent=styles["Title"],
        fontName=font,
        fontSize=23,
        leading=30,
        textColor=colors.HexColor("#10213B"),
        alignment=TA_LEFT,
        spaceAfter=6 * mm,
    )
    body = ParagraphStyle(
        "BodyCN",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#33445C"),
    )
    label = ParagraphStyle(
        "LabelCN",
        parent=body,
        fontSize=8,
        textColor=colors.HexColor("#64748B"),
    )
    right = ParagraphStyle("RightCN", parent=body, alignment=TA_RIGHT)
    story = [
        Table(
            [
                [
                    Paragraph("PROCUREOPS / DEMO FIXTURE", label),
                    Paragraph("DEMO · NOT FOR PURCHASE", right),
                ]
            ],
            colWidths=[105 * mm, 65 * mm],
        ),
        Spacer(1, 8 * mm),
        Paragraph("工程机械配件采购申请", title),
        Paragraph(
            "用于 ProcureOps Agent 的 PDF Intake、字段证据和审批演示。"
            "本文件为合成数据，不代表真实采购指令。",
            body,
        ),
        Spacer(1, 7 * mm),
    ]
    metadata = Table(
        [
            ["申请编号", "DEMO-PR-2026-0808", "申请部门", "设备维护部"],
            ["申请人", "本地演示用户", "期望到货", "2026-08-20"],
            ["成本中心", "DEMO-MAINT-01", "币种", "CNY"],
        ],
        colWidths=[25 * mm, 55 * mm, 25 * mm, 65 * mm],
        rowHeights=10 * mm,
    )
    metadata.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EDF4")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8EDF4")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#263A55")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BAC5D3")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([metadata, Spacer(1, 9 * mm), Paragraph("采购明细", title)])
    rows = [
        ["行", "零件号", "物料描述", "数量", "单位", "设备型号"],
        ["1", "DEMO-HYD-PUMP-001", "液压主泵", "2", "件", "EX200-A"],
        ["2", "DEMO-FLT-KIT-001", "保养滤芯包", "3", "套", "SVC-2000H-A"],
    ]
    items = Table(rows, colWidths=[10 * mm, 43 * mm, 38 * mm, 18 * mm, 18 * mm, 43 * mm])
    items.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#10213B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#20344F")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F8")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C2CE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (3, 1), (4, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            items,
            Spacer(1, 8 * mm),
            Paragraph("机器可读明细（字段顺序依次为零件号、描述、数量、单位、设备型号）", label),
            Paragraph("DEMO-HYD-PUMP-001,液压主泵,2,件,EX200-A", body),
            Paragraph("DEMO-FLT-KIT-001,保养滤芯包,3,套,SVC-2000H-A", body),
            Spacer(1, 10 * mm),
            Paragraph("业务说明", title),
            Paragraph(
                "只允许从准入供应商选择报价；价格、库存和报价有效期必须通过数据库工具读取。"
                "生成采购单草稿前必须按租户规则完成人工审批。",
                body,
            ),
            Spacer(1, 12 * mm),
            Table(
                [["申请人确认：________________", "部门审批：________________"]],
                colWidths=[85 * mm, 85 * mm],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#52657B")),
                    ]
                ),
            ),
        ]
    )
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="ProcureOps Demo Procurement Request",
        author="ProcureOps",
        subject="Synthetic procurement intake fixture",
    )
    document.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()
