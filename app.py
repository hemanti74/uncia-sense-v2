import json
import time
from datetime import datetime

import streamlit as st

from api_client import DEFAULT_MODEL, MODEL_PRICING, analyze_submission, parse_report
from excel_exporter import generate_excel

UNCIA_LOGO_URL = (
    "https://uncia.ai/wp-content/uploads/2026/04/"
    "Uncia-Logo-w-Tagline-Colour-TM.svg"
)
UNCIA_PURPLE = "#552E8C"
UNCIA_PURPLE_DARK = "#2E1A51"

st.set_page_config(
    page_title="Uncia · Invoice Factoring Verification",
    page_icon=UNCIA_LOGO_URL,
    layout="wide",
)

st.logo(UNCIA_LOGO_URL, link="https://uncia.ai", size="large")

st.markdown(
    f"""
    <style>
      /* Hide the main menu (settings → theme picker lives here) and footer */
      #MainMenu {{ visibility: hidden; }}
      header [data-testid="stMainMenu"] {{ display: none !important; }}
      footer {{ visibility: hidden; }}

      /* Brand accents */
      h1, h2, h3 {{ color: {UNCIA_PURPLE_DARK}; }}
      .stButton > button[kind="primary"] {{
        background-color: {UNCIA_PURPLE};
        border-color: {UNCIA_PURPLE};
      }}
      .stButton > button[kind="primary"]:hover {{
        background-color: {UNCIA_PURPLE_DARK};
        border-color: {UNCIA_PURPLE_DARK};
      }}
      .stTabs [aria-selected="true"] {{ color: {UNCIA_PURPLE} !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Invoice Factoring Verification Engine")
st.caption("v2.0")

# ── Sidebar: upload + inputs ───────────────────────────────────────────────────
with st.sidebar:
    st.header("Submission")
    uploaded_files = st.file_uploader(
        "Upload documents",
        type=["pdf", "xml", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        help="PDF, XML (CFDI), JPG, or PNG files",
    )

    submission_id = st.text_input(
        "Submission ID",
        value=f"SUB-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}",
    )
    notes = st.text_area("Submission Notes (optional)", height=80)

    model_options = list(MODEL_PRICING.keys())
    selected_model = st.selectbox(
        "Model",
        options=model_options,
        index=model_options.index(DEFAULT_MODEL),
        format_func=lambda m: MODEL_PRICING[m]["label"],
        help="Pricing per 1M tokens: "
        + " · ".join(
            f"{MODEL_PRICING[m]['label']}: "
            f"${MODEL_PRICING[m]['input']}/${MODEL_PRICING[m]['output']}"
            for m in model_options
        ),
    )
    pricing = MODEL_PRICING[selected_model]
    st.caption(
        f"${pricing['input']:.2f} input / ${pricing['output']:.2f} output per 1M tokens"
    )

    analyze_btn = st.button(
        "Analyze",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    )

# ── Analysis ───────────────────────────────────────────────────────────────────
if analyze_btn and uploaded_files:
    files = [(f.name, f.read()) for f in uploaded_files]

    with st.status(
        f"Analyzing {len(files)} document(s)…", expanded=True
    ) as status:
        log_lines: list[str] = []
        log_placeholder = st.empty()

        STREAM_PREFIX = "Streaming response…"

        def on_progress(msg: str) -> None:
            if (
                msg.startswith(STREAM_PREFIX)
                and log_lines
                and log_lines[-1].startswith(STREAM_PREFIX)
            ):
                log_lines[-1] = msg
            else:
                log_lines.append(msg)
            log_placeholder.code("\n".join(log_lines), language="log")

        # List the uploaded files up front
        on_progress("Uploaded files:")
        for f in uploaded_files:
            on_progress(f"  • {f.name} ({f.size:,} bytes)")

        # Always clear stale state up front so the UI reflects this run only
        for key in ("report", "conversation", "cost", "elapsed_seconds"):
            st.session_state.pop(key, None)
        st.session_state["submission_id"] = submission_id
        st.session_state["model_used"] = selected_model

        try:
            t_start = time.perf_counter()
            raw, cost, conversation = analyze_submission(
                files,
                submission_id,
                notes,
                model=selected_model,
                progress_cb=on_progress,
            )
            elapsed = time.perf_counter() - t_start
            # Stash conversation + cost immediately so the Response tab is available
            # even if JSON parsing fails below.
            st.session_state["cost"] = cost
            st.session_state["conversation"] = conversation
            st.session_state["elapsed_seconds"] = elapsed

            on_progress("Parsing JSON underwriting report…")
            try:
                report = parse_report(raw)
            except json.JSONDecodeError as e:
                on_progress(f"ERROR: model returned non-JSON output: {e}")
                status.update(label="Failed: invalid JSON response", state="error")
                st.error(
                    "Model response was not valid JSON. See the **Response** tab "
                    "below to inspect the raw output."
                )
            else:
                st.session_state["report"] = report
                on_progress(f"Analysis complete in {elapsed:.1f}s.")
                status.update(
                    label=f"Analysis complete in {elapsed:.1f}s",
                    state="complete",
                    expanded=False,
                )
        except Exception as e:
            on_progress(f"ERROR: {e}")
            status.update(label=f"Failed: {e}", state="error")
            st.stop()

# ── Results ────────────────────────────────────────────────────────────────────
SEVERITY_COLOR = {"LOW": "🟡", "MEDIUM": "🟠", "HIGH": "🔴", "CRITICAL": "🚨"}
REC_COLOR = {
    "APPROVE": "🟢",
    "APPROVE_WITH_NOTE": "🟡",
    "REVIEW": "🟠",
    "DECLINE": "🔴",
    "INSUFFICIENT_DOCS": "⚪",
}

if "report" in st.session_state:
    report: dict = st.session_state["report"]
    summary = report.get("submission_summary", {})
    packages = report.get("packages", [])
    documents = report.get("documents", [])
    unassigned = report.get("unassigned_documents", [])

    # ── Download Excel ─────────────────────────────────────────────────────────
    excel_bytes = generate_excel(report)
    st.download_button(
        "⬇ Download Excel Report",
        data=excel_bytes,
        file_name=f"{st.session_state['submission_id']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ── Elapsed ────────────────────────────────────────────────────────────────
    elapsed = st.session_state.get("elapsed_seconds")
    if elapsed is not None:
        if elapsed < 60:
            elapsed_str = f"{elapsed:.1f}s"
        else:
            m, s = divmod(int(elapsed), 60)
            elapsed_str = f"{m}m {s}s"
        st.caption(f"⏱ Elapsed: {elapsed_str}")

    # ── Cost ───────────────────────────────────────────────────────────────────
    cost = st.session_state.get("cost")
    model_used = st.session_state.get("model_used", "")
    if cost:
        total_tokens = (
            cost["input_tokens"]
            + cost["output_tokens"]
            + cost["cache_read_tokens"]
            + cost["cache_write_tokens"]
        )
        with st.expander(
            f"💰 Cost: ${cost['total_cost']:.4f} USD · "
            f"{total_tokens:,} tokens · {MODEL_PRICING.get(model_used, {}).get('label', model_used)}",
            expanded=False,
        ):
            c1, c2 = st.columns(2)
            with c1:
                st.write("**Tokens**")
                st.write(f"Input (uncached): {cost['input_tokens']:,}")
                st.write(f"Output: {cost['output_tokens']:,}")
                st.write(f"Cache read: {cost['cache_read_tokens']:,}")
                st.write(f"Cache write: {cost['cache_write_tokens']:,}")
            with c2:
                st.write("**Cost breakdown (USD)**")
                st.write(f"Input: ${cost['input_cost']:.4f}")
                st.write(f"Output: ${cost['output_cost']:.4f}")
                st.write(f"Cache read: ${cost['cache_read_cost']:.4f}")
                st.write(f"Cache write: ${cost['cache_write_cost']:.4f}")
                st.write(f"**Total: ${cost['total_cost']:.4f}**")

    st.divider()

    # ── Submission-level metrics ───────────────────────────────────────────────
    rec = summary.get("submission_recommendation", "")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Documents", summary.get("total_documents", 0))
    col2.metric("Receivables", summary.get("total_receivables_identified", 0))
    col3.metric("Orientation Issues", summary.get("orientation_issues_found", 0))
    col4.metric(
        "Advance Eligible",
        f"{summary.get('total_advance_eligible_currency', '')} "
        f"{summary.get('total_advance_eligible_amount', 0):,.2f}",
    )
    col5.metric("Recommendation", f"{REC_COLOR.get(rec, '')} {rec}")

    st.divider()

    # ── Tabs ───────────────────────────────────────────────────────────────────
    tab_labels = ["Packages", "Documents", "Unassigned", "Raw JSON", "Conversation", "Response"]
    tabs = st.tabs(tab_labels)

    # ── Packages tab ───────────────────────────────────────────────────────────
    with tabs[0]:
        if not packages:
            st.info("No packages identified.")
        for pkg in packages:
            inv = pkg.get("primary_invoice") or {}
            rec_pkg = pkg.get("recommendation", "")
            inv_num = inv.get("invoice_number") or f"Receivable {pkg.get('receivable_index', '')}"
            confidence = pkg.get("overall_confidence", 0) or 0
            advance = pkg.get("advance_eligible_amount", 0) or 0
            advance_cur = pkg.get("advance_eligible_currency", "")

            header = (
                f"{REC_COLOR.get(rec_pkg, '')} **{inv_num}** · "
                f"Confidence: {confidence:.0%} · "
                f"Advance: {advance_cur} {advance:,.2f}"
            )
            with st.expander(header, expanded=True):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Recommendation:** {REC_COLOR.get(rec_pkg,'')} {rec_pkg}")
                    st.write(f"**Invoice Date:** {inv.get('invoice_date','')}")
                    st.write(f"**Due Date:** {inv.get('due_date','')}")
                    st.write(f"**Currency:** {inv.get('currency','')}")
                    st.write(f"**Total Amount:** {inv.get('total_amount','')}")
                with c2:
                    seller = inv.get("seller") or {}
                    buyer = inv.get("buyer") or {}
                    st.write(f"**Seller:** {seller.get('name','')}")
                    st.write(f"**Buyer:** {buyer.get('name','')}")
                    st.write(f"**PO Reference:** {inv.get('po_reference','')}")
                    st.write(f"**UUID/Folio Fiscal:** {inv.get('uuid_folio_fiscal','')}")

                summary_text = pkg.get("underwriter_summary", "")
                if summary_text:
                    st.info(summary_text)

                sub1, sub2, sub3, sub4, sub5 = st.tabs(
                    ["Discrepancies", "Red Flags", "Missing Docs", "Match Matrix", "Compliance"]
                )

                with sub1:
                    discs = pkg.get("discrepancies") or []
                    if not discs:
                        st.write("None.")
                    for d in discs:
                        sev = d.get("severity", "")
                        st.write(
                            f"{SEVERITY_COLOR.get(sev, '')} **{sev}** · {d.get('direction','')} · "
                            f"{d.get('field','')} — {d.get('description','')}"
                        )
                        if d.get("disposition"):
                            st.caption(f"Disposition: {d['disposition']}")

                with sub2:
                    flags = pkg.get("red_flags") or []
                    if not flags:
                        st.write("None.")
                    for f in flags:
                        sev = f.get("severity", "")
                        st.write(
                            f"{SEVERITY_COLOR.get(sev, '')} **{sev}** · "
                            f"Rule {f.get('rule_id','')} — {f.get('description','')}"
                        )

                with sub3:
                    missing = pkg.get("missing_documents") or []
                    if not missing:
                        st.write("None.")
                    for m in missing:
                        req = "Required" if m.get("required") else "Optional"
                        st.write(f"• **{m.get('doc_type','')}** ({req}) — {m.get('reason','')}")

                with sub4:
                    matrix = pkg.get("match_matrix") or []
                    if not matrix:
                        st.write("None.")
                    else:
                        import pandas as pd

                        matrix_data = [
                            {
                                "Field": m.get("field", ""),
                                "Invoice Value": m.get("invoice_value", ""),
                                "Confidence": m.get("confidence", ""),
                                "Status": m.get("status", ""),
                                "Note": m.get("note", ""),
                            }
                            for m in matrix
                        ]
                        st.dataframe(pd.DataFrame(matrix_data), use_container_width=True)

                with sub5:
                    comp = pkg.get("compliance") or {}
                    st.write(f"**Jurisdictions:** {', '.join(comp.get('jurisdictions_involved') or [])}")
                    st.write(f"**Sanctions Screening Required:** {comp.get('sanctions_screening_required','')}")
                    st.write(f"**CFDI UUID:** {comp.get('cfdi_uuid','')}")
                    st.write(f"**CFDI Stamped:** {comp.get('cfdi_stamped','')}")
                    if comp.get("restricted_hs_codes"):
                        st.write(f"**Restricted HS Codes:** {', '.join(comp['restricted_hs_codes'])}")
                    if comp.get("notes"):
                        st.caption(comp["notes"])

                matching_issues = pkg.get("matching_issues") or []
                if matching_issues:
                    with st.expander("Matching Issues"):
                        for issue in matching_issues:
                            st.write(f"• {issue}")

                supported = pkg.get("supporting_docs_matched") or []
                if supported:
                    with st.expander("Supporting Documents"):
                        for s in supported:
                            st.write(
                                f"• **{s.get('filename','')}** ({s.get('doc_type','')}) — "
                                f"matched by `{s.get('matched_by','')}` · "
                                f"confidence {s.get('match_confidence','')}"
                            )

    # ── Documents tab ──────────────────────────────────────────────────────────
    with tabs[1]:
        if not documents:
            st.info("No documents listed.")
        for d in documents:
            needs_rot = d.get("needs_rotation", False)
            rot_icon = "🔄 " if needs_rot else ""
            ocr = d.get("ocr_quality", "")
            ocr_icon = {"HIGH": "✅", "MEDIUM": "⚠️", "LOW": "❌"}.get(ocr, "")
            with st.expander(
                f"{rot_icon}{d.get('filename','')} · {d.get('doc_type','')} · {ocr_icon} {ocr}"
            ):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Language:** {d.get('language','')}")
                    st.write(f"**Languages Detected:** {', '.join(d.get('languages_detected') or [])}")
                    st.write(f"**Page Count:** {d.get('page_count','')}")
                    st.write(f"**Is Scan/Photo:** {d.get('is_scan_or_photo','')}")
                with c2:
                    st.write(f"**Content Orientation:** {d.get('content_orientation','')}")
                    st.write(f"**Metadata Rotation:** {d.get('page_rotation_metadata',0)}°")
                    if needs_rot:
                        st.write(f"**Suggested Rotation:** {d.get('suggested_rotation_degrees','')}°")
                    covers = d.get("covers_receivable_indices") or []
                    if covers:
                        st.write(f"**Covers Receivables:** {', '.join(str(i) for i in covers)}")
                if d.get("doc_notes"):
                    st.caption(d["doc_notes"])

    # ── Unassigned tab ─────────────────────────────────────────────────────────
    with tabs[2]:
        if not unassigned:
            st.success("All documents were matched to receivables.")
        for u in unassigned:
            st.warning(f"**{u.get('filename','')}** — {u.get('reason','')}")

    # ── Raw JSON tab ───────────────────────────────────────────────────────────
    with tabs[3]:
        st.json(report)

    # ── Conversation tab ───────────────────────────────────────────────────────
    with tabs[4]:
        conv = st.session_state.get("conversation", {})
        if not conv:
            st.info("No conversation captured.")
        else:
            st.caption(f"Model: `{conv.get('model','')}`")
            with st.expander("System prompt", expanded=False):
                st.code(conv.get("system", ""), language="markdown")
            with st.expander("User message", expanded=True):
                st.json(conv.get("user_message", []))

    # ── Response tab ───────────────────────────────────────────────────────────
    with tabs[5]:
        conv = st.session_state.get("conversation", {})
        raw_response = conv.get("assistant_response", "")
        if not raw_response:
            st.info("No response captured.")
        else:
            st.caption(f"{len(raw_response):,} characters")
            st.download_button(
                "⬇ Download raw response",
                data=raw_response,
                file_name=f"{st.session_state['submission_id']}_response.json",
                mime="application/json",
            )
            st.code(raw_response, language="json")

elif "conversation" in st.session_state:
    # Parse failed — show what we have so the user can inspect the raw response.
    conv = st.session_state["conversation"]
    elapsed = st.session_state.get("elapsed_seconds")
    if elapsed is not None:
        st.caption(f"⏱ Elapsed: {elapsed:.1f}s")

    tabs = st.tabs(["Conversation", "Response"])
    with tabs[0]:
        st.caption(f"Model: `{conv.get('model','')}`")
        with st.expander("System prompt", expanded=False):
            st.code(conv.get("system", ""), language="markdown")
        with st.expander("User message", expanded=True):
            st.json(conv.get("user_message", []))
    with tabs[1]:
        raw_response = conv.get("assistant_response", "")
        if not raw_response:
            st.warning("Model returned no text.")
        else:
            st.caption(f"{len(raw_response):,} characters")
            st.download_button(
                "⬇ Download raw response",
                data=raw_response,
                file_name=f"{st.session_state.get('submission_id','response')}_response.txt",
                mime="text/plain",
            )
            st.code(raw_response, language="json")

else:
    st.info("Upload documents in the sidebar and click **Analyze** to begin.")
