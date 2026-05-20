import json
import time
from datetime import datetime

import streamlit as st

from api_client import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VARIANT,
    MODEL_PRICING,
    PROMPT_VARIANTS,
    analyze_submission,
    parse_report,
)
from excel_exporter import generate_excel, generate_factorsql_csv

UNCIA_LOGO_PATH = "assets/UnciaLogo.png"
UNCIA_PURPLE = "#552E8C"
UNCIA_PURPLE_DARK = "#2E1A51"

st.set_page_config(
    page_title="Uncia Sense — Document Intelligence",
    page_icon=UNCIA_LOGO_PATH,
    layout="wide",
)

st.logo(UNCIA_LOGO_PATH, link="https://uncia.ai", size="large")

st.markdown(
    f"""
    <style>
      /* Hide the main menu (settings → theme picker lives here) and footer */
      #MainMenu {{ visibility: hidden; }}
      header [data-testid="stMainMenu"] {{ display: none !important; }}
      footer {{ visibility: hidden; }}

      /* Collapse the large default top padding above the page title */
      [data-testid="stMainBlockContainer"],
      .main .block-container,
      section.main > div.block-container {{
        padding-top: 1rem !important;
      }}
      [data-testid="stHeader"] {{
        height: 0;
        min-height: 0;
        background: transparent;
      }}

      /* Main-area brand accents */
      h1, h2, h3 {{ color: {UNCIA_PURPLE_DARK}; }}
      .stTabs [aria-selected="true"] {{ color: {UNCIA_PURPLE} !important; }}
      .stButton > button[kind="primary"] {{
        background-color: {UNCIA_PURPLE};
        border-color: {UNCIA_PURPLE};
        color: #FFFFFF;
      }}
      .stButton > button[kind="primary"]:hover {{
        background-color: {UNCIA_PURPLE_DARK};
        border-color: {UNCIA_PURPLE_DARK};
      }}

      /* ── Sidebar: Uncia purple background ─────────────────────────────── */
      [data-testid="stSidebar"] {{
        background-color: {UNCIA_PURPLE};
      }}
      [data-testid="stSidebar"] h1,
      [data-testid="stSidebar"] h2,
      [data-testid="stSidebar"] h3,
      [data-testid="stSidebar"] h4,
      [data-testid="stSidebar"] p,
      [data-testid="stSidebar"] label,
      [data-testid="stSidebar"] .stMarkdown,
      [data-testid="stSidebar"] [data-testid="stWidgetLabel"],
      [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
      [data-testid="stSidebar"] small,
      [data-testid="stSidebar"] svg {{
        color: #FFFFFF !important;
        fill: #FFFFFF;
      }}

      /* Inputs (text, textarea, select): white surface with dark text */
      [data-testid="stSidebar"] input,
      [data-testid="stSidebar"] textarea,
      [data-testid="stSidebar"] [data-baseweb="select"] > div {{
        background-color: #FFFFFF !important;
        color: {UNCIA_PURPLE_DARK} !important;
        border-color: rgba(255, 255, 255, 0.3) !important;
      }}
      [data-testid="stSidebar"] input::placeholder,
      [data-testid="stSidebar"] textarea::placeholder {{
        color: rgba(46, 26, 81, 0.55) !important;
      }}

      /* File uploader dropzone: translucent white so it stands out on purple */
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
        background-color: rgba(255, 255, 255, 0.1);
        border: 1px dashed rgba(255, 255, 255, 0.55);
      }}
      /* Dropzone helper text ("Drag and drop file here", limits) stays white */
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"],
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] * {{
        color: #FFFFFF !important;
      }}
      /* "Browse files" button — scoped to the DROPZONE only, so the help (?)
         icon next to the "Upload documents" label (which is also a <button>
         inside [data-testid="stFileUploader"]) keeps its white SVG. */
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button,
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button * {{
        background-color: #FFFFFF !important;
        color: {UNCIA_PURPLE_DARK} !important;
        border-color: #FFFFFF !important;
        fill: {UNCIA_PURPLE_DARK} !important;
      }}
      [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button:hover {{
        background-color: #F5F2FA !important;
      }}

      /* Uploaded-file rows: light surface with purple text (broad prefix match) */
      [data-testid="stSidebar"] [data-testid^="stFileUploaderFile"] {{
        background-color: #FFFFFF !important;
        border-radius: 4px;
      }}
      [data-testid="stSidebar"] [data-testid^="stFileUploaderFile"],
      [data-testid="stSidebar"] [data-testid^="stFileUploaderFile"] * {{
        color: {UNCIA_PURPLE_DARK} !important;
        fill: {UNCIA_PURPLE_DARK} !important;
      }}

      /* Primary Analyze button on purple → invert to white with purple text.
         Also target descendants (p/span/div) because Streamlit wraps the label
         in a <p>, and the broader sidebar `p {{ color:#fff !important }}` rule
         would otherwise win. */
      [data-testid="stSidebar"] .stButton > button[kind="primary"],
      [data-testid="stSidebar"] .stButton > button[kind="primary"] *,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"],
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"] * {{
        background-color: #FFFFFF !important;
        color: {UNCIA_PURPLE} !important;
        border-color: #FFFFFF !important;
      }}
      [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
      [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover *,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]:hover,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]:hover * {{
        background-color: #F5F2FA !important;
        color: {UNCIA_PURPLE_DARK} !important;
      }}
      [data-testid="stSidebar"] .stButton > button[kind="primary"]:disabled,
      [data-testid="stSidebar"] .stButton > button[kind="primary"]:disabled *,
      [data-testid="stSidebar"] .stButton > button[kind="primary"][disabled],
      [data-testid="stSidebar"] .stButton > button[kind="primary"][disabled] *,
      [data-testid="stSidebar"] .stButton > button[kind="primary"][aria-disabled="true"],
      [data-testid="stSidebar"] .stButton > button[kind="primary"][aria-disabled="true"] *,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]:disabled,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"]:disabled *,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"][disabled],
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"][disabled] *,
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"][aria-disabled="true"],
      [data-testid="stSidebar"] [data-testid*="stBaseButton-primary"][aria-disabled="true"] * {{
        background-color: #E8E0F0 !important;   /* light lavender */
        color: rgba(46, 26, 81, 0.55) !important;
        border-color: #E8E0F0 !important;
        cursor: not-allowed !important;
        opacity: 1 !important;
      }}

      /* Secondary buttons (other than the file uploader Browse button) */
      [data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {{
        background-color: rgba(255, 255, 255, 0.15) !important;
        color: #FFFFFF !important;
        border-color: rgba(255, 255, 255, 0.55) !important;
      }}

      /* Sidebar collapse arrow */
      [data-testid="stSidebarCollapseButton"] svg,
      [data-testid="stSidebarCollapsedControl"] svg {{
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
      }}

      /* Tooltip / help (?) icons next to widget labels — force white wherever
         they appear, including inside the file uploader (whose button rule
         would otherwise turn this icon purple). Placed last so it wins on
         source order against earlier rules of equal specificity. */
      [data-testid="stSidebar"] [data-testid="stTooltipIcon"],
      [data-testid="stSidebar"] [data-testid="stTooltipIcon"] *,
      [data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"],
      [data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] * {{
        background-color: transparent !important;
        border: none !important;
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)

# Debug mode is toggled by adding ?debug=1 to the URL. When off (demo default)
# the UI hides model selection, preprocessing toggle, live response stream,
# cost panel, and the diagnostic tabs (Preprocessed / Raw JSON / Conversation /
# Response).
debug_mode = st.query_params.get("debug", "").lower() in ("1", "true", "yes", "on")

st.title("Uncia Sense — Document Intelligence")
st.caption("v2.2 · debug mode" if debug_mode else "v2.2")

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

    if debug_mode:
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

        preprocess_enabled = st.checkbox(
            "Local preprocessing (faster, fewer tokens)",
            value=True,
            help=(
                "Extract text, tables, and orientation locally with PyMuPDF + Tesseract "
                "before sending to Claude. Original PDF/image is attached as a vision "
                "fallback when extraction confidence is low. Disable to A/B-compare "
                "against the pure-vision pipeline."
            ),
        )

        _variant_keys = list(PROMPT_VARIANTS.keys())
        prompt_variant = st.selectbox(
            "Prompt",
            options=_variant_keys,
            index=_variant_keys.index(DEFAULT_PROMPT_VARIANT),
            format_func=lambda k: PROMPT_VARIANTS[k]["label"],
            help=(
                "Fast: simplified prompt (~3KB) — skips match matrix, terse summaries, "
                "fewer red-flag rules. Significantly faster (~30–60s typical) for demos.\n\n"
                "Full: original prompt (~20KB) — 29 fraud rules, cross-doc match matrix, "
                "detailed line items. Slower (~90–120s+) but most thorough."
            ),
        )
    else:
        # Demo defaults — Opus 4.7 with local preprocessing on, fast prompt
        selected_model = DEFAULT_MODEL
        preprocess_enabled = True
        prompt_variant = DEFAULT_PROMPT_VARIANT

    response_language = st.selectbox(
        "Response language",
        options=["en", "es"],
        format_func=lambda code: {"en": "English", "es": "Spanish (Español)"}[code],
        help=(
            "Language for free-text fields in the report (underwriter summary, "
            "descriptions, notes, reasons). JSON keys and enum values (recommendation, "
            "severity, direction, status) always remain in English. Party names and "
            "product descriptions are preserved in the source document's language."
        ),
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
        f"Analyzing {len(files)} document(s)…", expanded=debug_mode
    ) as status:
        log_lines: list[str] = []

        if debug_mode:
            log_placeholder = st.empty()
            st.markdown("**Live response from Claude**")
            stream_box = st.container(height=400, border=True)
            with stream_box:
                stream_placeholder = st.empty()
                stream_placeholder.caption("_(waiting for first token…)_")
        else:
            log_placeholder = None
            stream_placeholder = None

        STREAM_PREFIX = "Awaiting response"

        # Demo mode: classify each progress message and update the st.status
        # label to one of three high-level stages. Returns the new label or
        # None if the message shouldn't change the label.
        def _demo_label(msg: str) -> "str | None":
            s = msg.strip()
            if s.startswith(STREAM_PREFIX):
                # Preserve the rotating spinner char appended by api_client.
                spinner_char = s[len(STREAM_PREFIX):].strip()
                tail = f" {spinner_char}" if spinner_char else ""
                return f"Awaiting response from Claude…{tail}"
            if s.startswith("Sending"):
                return "Awaiting response from Claude…"
            if s.startswith("Response complete") or s.startswith("Parsing JSON"):
                return "Processing analysis…"
            if (
                s.startswith("[")                  # "[N/M] Processing …"
                or s.startswith("Starting submission")
                or s.startswith("Tesseract OCR")   # banner + per-page lines
                or s.startswith("preprocessing")
                or s.startswith("page ")
                or s.startswith("no OCR")
            ):
                return "Preparing documents…"
            return None

        def on_progress(msg: str) -> None:
            if log_placeholder is None:
                # Demo mode — map to a high-level stage label on the status widget.
                new_label = _demo_label(msg)
                if new_label is not None:
                    status.update(label=new_label)
                return
            # Debug mode — full log behavior.
            if (
                msg.startswith(STREAM_PREFIX)
                and log_lines
                and log_lines[-1].startswith(STREAM_PREFIX)
            ):
                log_lines[-1] = msg
            else:
                log_lines.append(msg)
            log_placeholder.code("\n".join(log_lines), language="log")

        def on_stream(text: str) -> None:
            if stream_placeholder is not None:
                stream_placeholder.code(text, language="json")

        # List the uploaded files up front (debug only)
        if debug_mode:
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
                model=selected_model,
                progress_cb=on_progress,
                preprocess=preprocess_enabled,
                stream_cb=on_stream,
                response_language=response_language,
                prompt_variant=prompt_variant,
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

    # ── Downloads ──────────────────────────────────────────────────────────────
    factorsql_bytes = generate_factorsql_csv(report)
    excel_bytes = generate_excel(report)
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "⬇ FactorSQL Upload (CSV)",
            data=factorsql_bytes,
            file_name=f"{st.session_state['submission_id']}_factorsql.csv",
            mime="text/csv",
            help=(
                "One row per receivable in FactorSQL's expected column order "
                "(ACCT_ID, ACCT_SUB, BAL_ASSIGN, DTR_NAME, DUE_DATE, INV_DATE, "
                "INV_ID, PO_NO, REL_ID). ACCT_ID, ACCT_SUB, and REL_ID are "
                "left blank for FactorSQL to fill in."
            ),
            use_container_width=True,
        )
    with dl2:
        st.download_button(
            "⬇ Analysis Report (Excel)",
            data=excel_bytes,
            file_name=f"{st.session_state['submission_id']}_analysis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Multi-sheet underwriting analysis: summary, documents, packages, discrepancies, red flags, match matrix, missing docs, unassigned.",
            use_container_width=True,
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

    # ── Cost (debug only) ──────────────────────────────────────────────────────
    cost = st.session_state.get("cost")
    model_used = st.session_state.get("model_used", "")
    if debug_mode and cost:
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
    tab_labels = ["Packages", "Documents", "Unassigned"]
    if debug_mode:
        tab_labels += ["Preprocessed", "Raw JSON", "Conversation", "Response"]
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
                    prepay = inv.get("prepayment_amount") or 0
                    if prepay:
                        st.write(f"**Prepayment:** -{prepay}")
                    st.write(f"**Amount Due (Balance):** {inv.get('amount_due', inv.get('total_amount',''))}")
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

                sub1, sub2, sub3, sub4 = st.tabs(
                    ["Discrepancies", "Red Flags", "Missing Docs", "Match Matrix"]
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

    if debug_mode:
        # ── Preprocessed tab ───────────────────────────────────────────────────
        with tabs[3]:
            preprocessed = (st.session_state.get("conversation") or {}).get(
                "preprocessed_documents", []
            )
            st.markdown(
                "**Format:** UTF-8 Markdown (`.md`). Each file starts with a fenced "
                "` ```json ` metadata header (filename, page count, language, OCR "
                "quality, rotation applied, extraction confidence, fallback flag), "
                "followed by a `=== FILE: <name> ===` marker, per-page sections "
                "(`## Page N`), preserved text in reading order, and any detected "
                "tables rendered as GFM pipe tables. XML/CFDI files are pass-through "
                "(not preprocessed) and don't appear here."
            )
            if not preprocessed:
                st.info(
                    "No preprocessed artifacts. Either local preprocessing was "
                    "disabled, the submission contained only XML, or every file "
                    "failed preprocessing and was sent as-is to Claude."
                )
            else:
                import io as _io
                import zipfile as _zipfile

                zip_buf = _io.BytesIO()
                with _zipfile.ZipFile(zip_buf, "w", _zipfile.ZIP_DEFLATED) as zf:
                    for art in preprocessed:
                        stem = art["filename"].rsplit(".", 1)[0]
                        zf.writestr(f"{stem}.md", art["markdown"])
                st.download_button(
                    f"⬇ Download all ({len(preprocessed)} file(s), ZIP)",
                    data=zip_buf.getvalue(),
                    file_name=f"{st.session_state['submission_id']}_preprocessed.zip",
                    mime="application/zip",
                )
                st.divider()

                for art in preprocessed:
                    meta = art.get("metadata") or {}
                    conf = art.get("confidence", 0.0) or 0.0
                    quality = meta.get("ocr_quality", "?")
                    ocr_pages = meta.get("ocr_pages", 0)
                    pages = meta.get("page_count", "?")
                    fb = art.get("fallback_attached", False)
                    fb_label = "📎 fallback attached" if fb else "✓ text-only (Tier A)"
                    header = (
                        f"📄 **{art['filename']}** · {pages} page(s) · "
                        f"OCR'd {ocr_pages} · quality {quality} · "
                        f"conf {conf:.2f} · {fb_label}"
                    )
                    with st.expander(header, expanded=False):
                        langs = ", ".join(meta.get("language_detected") or []) or "—"
                        applied_rot = meta.get("applied_rotation_degrees", 0)
                        md_size = len(art["markdown"].encode("utf-8"))
                        c1, c2 = st.columns(2)
                        with c1:
                            st.write(f"**Languages detected:** {langs}")
                            st.write(f"**OCR used:** {'yes' if meta.get('ocr_used') else 'no'}")
                            st.write(f"**Rotation applied:** {applied_rot}°")
                        with c2:
                            st.write(f"**Confidence:** {conf:.2f}")
                            st.write(f"**Markdown size:** {md_size:,} bytes")
                            st.write(f"**Tier:** {'B/C (fallback attached)' if fb else 'A (text only)'}")

                        stem = art["filename"].rsplit(".", 1)[0]
                        st.download_button(
                            f"⬇ Download {stem}.md",
                            data=art["markdown"],
                            file_name=f"{stem}.md",
                            mime="text/markdown",
                            key=f"dl_md_{stem}",
                        )
                        st.code(art["markdown"], language="markdown")

        # ── Raw JSON tab ───────────────────────────────────────────────────────
        with tabs[4]:
            st.json(report)

        # ── Conversation tab ───────────────────────────────────────────────────
        with tabs[5]:
            conv = st.session_state.get("conversation", {})
            if not conv:
                st.info("No conversation captured.")
            else:
                st.caption(f"Model: `{conv.get('model','')}`")
                with st.expander("System prompt", expanded=False):
                    st.code(conv.get("system", ""), language="markdown")
                with st.expander("User message", expanded=True):
                    st.json(conv.get("user_message", []))

        # ── Response tab ───────────────────────────────────────────────────────
        with tabs[6]:
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
    # Parse failed.
    conv = st.session_state["conversation"]
    elapsed = st.session_state.get("elapsed_seconds")
    if elapsed is not None:
        st.caption(f"⏱ Elapsed: {elapsed:.1f}s")

    if debug_mode:
        # Diagnostic view: show conversation + raw response so we can debug.
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
        st.error(
            "Couldn't parse the underwriting report. Please retry, or open this "
            "page with `?debug=1` in the URL to see diagnostics."
        )

else:
    st.info("Upload documents in the sidebar and click **Analyze** to begin.")
