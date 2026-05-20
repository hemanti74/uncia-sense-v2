import io
from typing import Any

import pandas as pd


def _fmt_currency(amount, currency) -> str:
    if amount is None:
        return ""
    try:
        return f"{currency or ''} {float(amount):,.2f}".strip()
    except (TypeError, ValueError):
        return str(amount)


def generate_excel(report: dict) -> bytes:
    summary = report.get("submission_summary", {})
    documents = report.get("documents", [])
    packages = report.get("packages", [])
    unassigned = report.get("unassigned_documents", [])

    # ── Summary sheet ──────────────────────────────────────────────────────────
    summary_rows = [
        {"Field": "Submission ID", "Value": report.get("submission_id", "")},
        {"Field": "Processed At", "Value": report.get("processed_at", "")},
        {"Field": "Model Version", "Value": report.get("model_version", "")},
        {"Field": "Total Documents", "Value": summary.get("total_documents", "")},
        {"Field": "Total Receivables", "Value": summary.get("total_receivables_identified", "")},
        {"Field": "Primary Languages", "Value": ", ".join(summary.get("primary_languages", []))},
        {"Field": "Orientation Issues", "Value": summary.get("orientation_issues_found", 0)},
        {"Field": "Submission Recommendation", "Value": summary.get("submission_recommendation", "")},
        {
            "Field": "Total Advance Eligible",
            "Value": _fmt_currency(
                summary.get("total_advance_eligible_amount"),
                summary.get("total_advance_eligible_currency"),
            ),
        },
    ]
    df_summary = pd.DataFrame(summary_rows)

    # ── Documents sheet ────────────────────────────────────────────────────────
    doc_rows = []
    for d in documents:
        doc_rows.append(
            {
                "Filename": d.get("filename", ""),
                "Doc Type": d.get("doc_type", ""),
                "Language": d.get("language", ""),
                "Languages Detected": ", ".join(d.get("languages_detected") or []),
                "Page Count": d.get("page_count", ""),
                "Page Rotation Metadata": d.get("page_rotation_metadata", 0),
                "Content Orientation": d.get("content_orientation", ""),
                "Needs Rotation": d.get("needs_rotation", False),
                "Suggested Rotation Degrees": d.get("suggested_rotation_degrees", ""),
                "OCR Quality": d.get("ocr_quality", ""),
                "Is Scan/Photo": d.get("is_scan_or_photo", False),
                "Doc Notes": d.get("doc_notes", ""),
                "Covers Receivable Indices": ", ".join(
                    str(i) for i in (d.get("covers_receivable_indices") or [])
                ),
            }
        )
    df_documents = pd.DataFrame(doc_rows) if doc_rows else pd.DataFrame(columns=["Filename"])

    # ── Packages sheet ─────────────────────────────────────────────────────────
    pkg_rows = []
    for pkg in packages:
        inv = pkg.get("primary_invoice") or {}
        seller = inv.get("seller") or {}
        buyer = inv.get("buyer") or {}
        pkg_rows.append(
            {
                "Receivable Index": pkg.get("receivable_index", ""),
                "Invoice Number": inv.get("invoice_number", ""),
                "UUID / Folio Fiscal": inv.get("uuid_folio_fiscal", ""),
                "Invoice Date": inv.get("invoice_date", ""),
                "Due Date": inv.get("due_date", ""),
                "Currency": inv.get("currency", ""),
                "Total Amount": inv.get("total_amount", ""),
                "Subtotal": inv.get("subtotal", ""),
                "Tax Amount": inv.get("tax_amount", ""),
                "Seller Name": seller.get("name", ""),
                "Seller Country": seller.get("country", ""),
                "Seller Tax ID": seller.get("tax_id", ""),
                "Buyer Name": buyer.get("name", ""),
                "Buyer Country": buyer.get("country", ""),
                "Buyer Tax ID": buyer.get("tax_id", ""),
                "PO Reference": inv.get("po_reference", ""),
                "Line PO Reference": inv.get("line_po_reference", ""),
                "Overall Confidence": pkg.get("overall_confidence", ""),
                "Recommendation": pkg.get("recommendation", ""),
                "Advance Eligible Amount": pkg.get("advance_eligible_amount", ""),
                "Advance Eligible Currency": pkg.get("advance_eligible_currency", ""),
                "Underwriter Summary": pkg.get("underwriter_summary", ""),
            }
        )
    df_packages = pd.DataFrame(pkg_rows) if pkg_rows else pd.DataFrame(columns=["Receivable Index"])

    # ── Discrepancies sheet ────────────────────────────────────────────────────
    disc_rows = []
    for pkg in packages:
        inv_num = (pkg.get("primary_invoice") or {}).get("invoice_number", "")
        for d in pkg.get("discrepancies") or []:
            disc_rows.append(
                {
                    "Receivable Index": pkg.get("receivable_index", ""),
                    "Invoice Number": inv_num,
                    "ID": d.get("id", ""),
                    "Field": d.get("field", ""),
                    "Description": d.get("description", ""),
                    "Direction": d.get("direction", ""),
                    "Severity": d.get("severity", ""),
                    "Disposition": d.get("disposition", ""),
                }
            )
    df_discrepancies = pd.DataFrame(disc_rows) if disc_rows else pd.DataFrame(
        columns=["Receivable Index", "Invoice Number", "ID", "Field", "Description", "Direction", "Severity", "Disposition"]
    )

    # ── Red Flags sheet ────────────────────────────────────────────────────────
    flag_rows = []
    for pkg in packages:
        inv_num = (pkg.get("primary_invoice") or {}).get("invoice_number", "")
        for f in pkg.get("red_flags") or []:
            flag_rows.append(
                {
                    "Receivable Index": pkg.get("receivable_index", ""),
                    "Invoice Number": inv_num,
                    "Rule ID": f.get("rule_id", ""),
                    "Description": f.get("description", ""),
                    "Severity": f.get("severity", ""),
                }
            )
    df_flags = pd.DataFrame(flag_rows) if flag_rows else pd.DataFrame(
        columns=["Receivable Index", "Invoice Number", "Rule ID", "Description", "Severity"]
    )

    # ── Match Matrix sheet ─────────────────────────────────────────────────────
    matrix_rows = []
    for pkg in packages:
        inv_num = (pkg.get("primary_invoice") or {}).get("invoice_number", "")
        for m in pkg.get("match_matrix") or []:
            values_by_doc = m.get("values_by_doc") or {}
            matrix_rows.append(
                {
                    "Receivable Index": pkg.get("receivable_index", ""),
                    "Invoice Number": inv_num,
                    "Field": m.get("field", ""),
                    "Invoice Value": m.get("invoice_value", ""),
                    "Values by Doc": "; ".join(f"{k}: {v}" for k, v in values_by_doc.items()),
                    "Confidence": m.get("confidence", ""),
                    "Status": m.get("status", ""),
                    "Note": m.get("note", ""),
                }
            )
    df_matrix = pd.DataFrame(matrix_rows) if matrix_rows else pd.DataFrame(
        columns=["Receivable Index", "Invoice Number", "Field", "Invoice Value", "Values by Doc", "Confidence", "Status", "Note"]
    )

    # ── Missing Documents sheet ────────────────────────────────────────────────
    missing_rows = []
    for pkg in packages:
        inv_num = (pkg.get("primary_invoice") or {}).get("invoice_number", "")
        for m in pkg.get("missing_documents") or []:
            missing_rows.append(
                {
                    "Receivable Index": pkg.get("receivable_index", ""),
                    "Invoice Number": inv_num,
                    "Doc Type": m.get("doc_type", ""),
                    "Required": m.get("required", ""),
                    "Reason": m.get("reason", ""),
                }
            )
    df_missing = pd.DataFrame(missing_rows) if missing_rows else pd.DataFrame(
        columns=["Receivable Index", "Invoice Number", "Doc Type", "Required", "Reason"]
    )

    # ── Unassigned Documents sheet ─────────────────────────────────────────────
    unassigned_rows = [
        {"Filename": u.get("filename", ""), "Reason": u.get("reason", "")}
        for u in unassigned
    ]
    df_unassigned = pd.DataFrame(unassigned_rows) if unassigned_rows else pd.DataFrame(
        columns=["Filename", "Reason"]
    )

    # ── Write workbook ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Summary", index=False)
        df_documents.to_excel(writer, sheet_name="Documents", index=False)
        df_packages.to_excel(writer, sheet_name="Packages", index=False)
        df_discrepancies.to_excel(writer, sheet_name="Discrepancies", index=False)
        df_flags.to_excel(writer, sheet_name="Red Flags", index=False)
        df_matrix.to_excel(writer, sheet_name="Match Matrix", index=False)
        df_missing.to_excel(writer, sheet_name="Missing Documents", index=False)
        df_unassigned.to_excel(writer, sheet_name="Unassigned", index=False)

        # Auto-fit column widths
        for sheet_name, df in [
            ("Summary", df_summary),
            ("Documents", df_documents),
            ("Packages", df_packages),
            ("Discrepancies", df_discrepancies),
            ("Red Flags", df_flags),
            ("Match Matrix", df_matrix),
            ("Missing Documents", df_missing),
            ("Unassigned", df_unassigned),
        ]:
            ws = writer.sheets[sheet_name]
            for col_idx, col in enumerate(df.columns, 1):
                max_len = max(
                    len(str(col)),
                    df[col].astype(str).str.len().max() if not df.empty else 0,
                )
                ws.column_dimensions[ws.cell(1, col_idx).column_letter].width = min(max_len + 4, 80)

    return buf.getvalue()
