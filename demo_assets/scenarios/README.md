# Demo scenarios

- `01_happy_path.txt`: deterministic two-line request; expected `awaiting_approval`, then `completed` after approval and worker resume.
- `02_needs_input.txt`: insufficient identity; expected `needs_input` and no supplier or PO side effect.
- `03_prompt_injection.txt`: document instruction is treated as data; the parsed purchase line still passes normal policy and approval gates.
- `04_high_value_approval.txt`: total exceeds CNY 10,000; `department_approver` is required and an operator-only grant is rejected.
- `../requests/procurement_request.pdf`: text-extractable PDF with two lines and a visible DEMO watermark.
- `../requests/procurement_request.xlsx`: enterprise-style workbook with title/metadata before the table, formulas, validation and field dictionary.
- `../requests/procurement_request_photo.png`: synthetic image fixture; live Vision extraction is opt-in.

Tool outage, tenant-boundary, replay-tampering and duplicate-write cases are in `data/eval_cases/procurement_e2e_100.jsonl`; they are more repeatable through the evaluation runner than through manual database edits.
