# app.py
# ─────────────────────────────────────────────────────────────
#  ARYA Session Evaluator — Streamlit Web Interface
#  Run with:  streamlit run app.py
# ─────────────────────────────────────────────────────────────

import os
import io
import time
import logging
import tempfile
import threading

import streamlit as st
from dotenv import load_dotenv

from excel_processor   import load_excel, group_sessions, write_results_to_excel
from gemini_evaluator  import configure_gemini, evaluate_all_sessions

# ── Load .env (API key) ────────────────────────────────────────
load_dotenv()

# ── Logging setup ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  PAGE CONFIG
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="ARYA Session Evaluator",
    page_icon="🤖",
    layout="wide",
)

# ── Custom CSS for cleaner look ────────────────────────────────
st.markdown("""
<style>
    .stProgress > div > div > div > div { background-color: #4CAF50; }
    .metric-card {
        background: #f0f2f6;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
    .log-box {
        background: #1e1e1e;
        color: #d4d4d4;
        font-family: monospace;
        font-size: 12px;
        padding: 12px;
        border-radius: 8px;
        height: 300px;
        overflow-y: auto;
    }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  SESSION STATE INITIALISATION
# ══════════════════════════════════════════════════════════════
def init_state():
    defaults = {
        "api_key":        os.getenv("GEMINI_API_KEY", ""),
        "df":             None,
        "sessions":       None,
        "results":        None,
        "output_bytes":   None,
        "log_lines":      [],
        "is_running":     False,
        "total_sessions": 0,
        "done_sessions":  0,
        "error_sessions": [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_state()


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
def add_log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    st.session_state.log_lines.append(f"[{timestamp}] {msg}")
    # Keep only last 200 log lines (memory safety)
    if len(st.session_state.log_lines) > 200:
        st.session_state.log_lines = st.session_state.log_lines[-200:]


def reset_run_state():
    st.session_state.results        = None
    st.session_state.output_bytes   = None
    st.session_state.log_lines      = []
    st.session_state.is_running     = False
    st.session_state.done_sessions  = 0
    st.session_state.error_sessions = []


# ══════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════
st.title("🤖 ARYA Session Evaluator")
st.markdown(
    "Upload your session Excel file, configure your Gemini API key, "
    "and let the system automatically evaluate every session."
)
st.divider()


# ══════════════════════════════════════════════════════════════
#  SIDEBAR — CONFIGURATION
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ Configuration")

    # API Key input
    api_key_input = st.text_input(
        "Gemini API Key",
        value=st.session_state.api_key,
        type="password",
        help="Get your key from https://aistudio.google.com/app/apikey",
    )
    if api_key_input:
        st.session_state.api_key = api_key_input

    st.divider()
    st.subheader("📋 Expected Excel Columns")
    st.code(
        "SESSION_ID\nQUERY\nRESPONSE\nLLM GCR\nLLM GCR yes/no",
        language=None,
    )
    st.caption(
        "Rows sharing the same SESSION_ID are treated as one session. "
        "Results will be written back into 'LLM GCR' and 'LLM GCR yes/no'."
    )

    st.divider()
    st.subheader("ℹ️ About")
    st.markdown(
        "- Uses **Gemini 1.5 Flash** for evaluation\n"
        "- Each session is isolated (no context bleed)\n"
        "- Handles rate limits with automatic retry\n"
        "- Progress is shown in real-time\n"
        "- Download output when done"
    )


# ══════════════════════════════════════════════════════════════
#  STEP 1 — UPLOAD EXCEL
# ══════════════════════════════════════════════════════════════
st.subheader("📁 Step 1: Upload Excel File")

uploaded_file = st.file_uploader(
    "Choose your Excel file (.xlsx)",
    type=["xlsx"],
    help="The file must contain columns: SESSION_ID, QUERY, RESPONSE",
)

if uploaded_file:
    if st.session_state.df is None or uploaded_file.name not in str(st.session_state.get("_last_file", "")):
        # New file uploaded — reload
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(uploaded_file.getbuffer())
                tmp_path = tmp.name

            df       = load_excel(tmp_path)
            sessions = group_sessions(df)

            st.session_state.df             = df
            st.session_state.sessions       = sessions
            st.session_state.total_sessions = len(sessions)
            st.session_state._last_file     = uploaded_file.name
            st.session_state._tmp_path      = tmp_path
            reset_run_state()

            st.success(f"✅ File loaded: **{uploaded_file.name}**")

        except Exception as e:
            st.error(f"❌ Failed to load file: {e}")
            logger.exception("File load error")

    # ── Preview ────────────────────────────────────────────────
    if st.session_state.df is not None:
        df       = st.session_state.df
        sessions = st.session_state.sessions

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Rows",     len(df))
        col2.metric("Unique Sessions", len(sessions))
        col3.metric("Avg Turns/Session", f"{len(df)/max(len(sessions),1):.1f}")
        col4.metric(
            "Max Turns in a Session",
            max(len(v) for v in sessions.values()) if sessions else 0
        )

        with st.expander("👀 Preview Data (first 20 rows)"):
            st.dataframe(df.head(20), use_container_width=True)

        with st.expander("📊 Session Summary"):
            session_sizes = {sid: len(turns) for sid, turns in sessions.items()}
            import pandas as pd
            size_df = pd.DataFrame(
                [(sid[:30]+"…" if len(sid)>30 else sid, n) for sid, n in session_sizes.items()],
                columns=["Session ID", "Number of Turns"]
            )
            st.dataframe(size_df, use_container_width=True, height=250)


# ══════════════════════════════════════════════════════════════
#  STEP 2 — RUN EVALUATION
# ══════════════════════════════════════════════════════════════
st.divider()
st.subheader("🚀 Step 2: Run Evaluation")

can_run = (
    st.session_state.df is not None
    and st.session_state.sessions is not None
    and st.session_state.api_key.strip() != ""
    and not st.session_state.is_running
)

if not st.session_state.api_key.strip():
    st.warning("⚠️ Please enter your Gemini API key in the sidebar to continue.")

if st.session_state.df is None:
    st.info("👆 Upload an Excel file above to get started.")

# ── Run button ─────────────────────────────────────────────────
if st.button(
    "▶️  Start Evaluation",
    disabled=not can_run,
    use_container_width=True,
    type="primary",
):
    reset_run_state()
    st.session_state.is_running = True
    st.rerun()


# ══════════════════════════════════════════════════════════════
#  EVALUATION LOOP  (runs only when is_running = True)
# ══════════════════════════════════════════════════════════════
if st.session_state.is_running and st.session_state.results is None:

    st.info("⏳ Evaluation in progress — do not close this tab.")

    progress_bar    = st.progress(0, text="Starting…")
    status_text     = st.empty()
    log_placeholder = st.empty()

    # ── Configure Gemini once ─────────────────────────────────
    try:
        configure_gemini(st.session_state.api_key)
    except Exception as e:
        st.error(f"❌ Gemini configuration failed: {e}")
        st.session_state.is_running = False
        st.stop()

    sessions       = st.session_state.sessions
    total          = len(sessions)
    results        = {}
    error_sessions = []

    # ── Process session by session ────────────────────────────
    for n, (sid, turns_data) in enumerate(sessions.items(), start=1):

        pct  = int((n - 1) / total * 100)
        msg  = f"Session {n}/{total}: {sid[:25]}…"
        progress_bar.progress(pct, text=msg)
        status_text.markdown(f"**Processing:** `{sid}`")

        add_log(f"▶ Session {n}/{total} | ID: {sid[:30]}")

        row_indices = [t["row_index"] for t in turns_data]
        turns       = [{"query": t["query"], "response": t["response"]} for t in turns_data]

        # -- Import here to avoid circular at module level
        from gemini_evaluator import evaluate_session

        def _cb(msg):
            add_log(msg)
            log_placeholder.markdown(
                "<div class='log-box'>" +
                "<br>".join(st.session_state.log_lines[-20:]) +
                "</div>",
                unsafe_allow_html=True,
            )

        eval_result = evaluate_session(
            session_id=sid,
            turns=turns,
            progress_callback=_cb,
        )

        if eval_result.get("error"):
            error_sessions.append(sid)

        llm_gcr_text = (
            f"Goal Completed: {eval_result['goal_completed']}\n"
            f"Confidence: {eval_result['confidence']}\n"
            f"Reasoning: {eval_result['reasoning']}\n\n"
            f"--- Full Gemini Output ---\n{eval_result['raw']}"
        )

        results[sid] = {
            "llm_gcr":     llm_gcr_text,
            "yes_no":      eval_result["yes_no"],
            "row_indices": row_indices,
        }

        # Inter-session delay (rate limit courtesy)
        if n < total:
            time.sleep(3)

    progress_bar.progress(100, text="✅ All sessions evaluated!")
    status_text.markdown("**Evaluation complete!**")

    # ── Write results to Excel (in memory) ────────────────────
    try:
        tmp_path = st.session_state._tmp_path
        out_path = tmp_path.replace(".xlsx", "_evaluated.xlsx")

        write_results_to_excel(
            source_path=tmp_path,
            output_path=out_path,
            results=results,
        )

        with open(out_path, "rb") as f:
            st.session_state.output_bytes = f.read()

        add_log("📝 Results written to Excel successfully.")

    except Exception as e:
        st.error(f"❌ Failed to write Excel: {e}")
        logger.exception("Excel write error")
        add_log(f"ERROR writing Excel: {e}")

    st.session_state.results        = results
    st.session_state.error_sessions = error_sessions
    st.session_state.is_running     = False
    st.rerun()


# ══════════════════════════════════════════════════════════════
#  STEP 3 — RESULTS & DOWNLOAD
# ══════════════════════════════════════════════════════════════
if st.session_state.results is not None:
    st.divider()
    st.subheader("📊 Step 3: Results")

    results = st.session_state.results
    total   = len(results)
    yes_ct  = sum(1 for r in results.values() if r["yes_no"] == "Yes")
    no_ct   = sum(1 for r in results.values() if r["yes_no"] == "No")
    err_ct  = sum(1 for r in results.values() if r["yes_no"] == "ERROR")

    # ── Summary metrics ────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Sessions",   total)
    c2.metric("✅ Goal Met (Yes)", yes_ct)
    c3.metric("❌ Goal Not Met",   no_ct)
    c4.metric("⚠️ Errors",        err_ct)

    if err_ct > 0:
        with st.expander(f"⚠️ {err_ct} session(s) had errors — click to view"):
            for sid in st.session_state.error_sessions:
                st.code(sid)

    # ── Results table ──────────────────────────────────────────
    import pandas as pd
    summary_rows = []
    for sid, data in results.items():
        summary_rows.append({
            "Session ID":     sid,
            "Turns":          len(data["row_indices"]),
            "Goal Completed": data["yes_no"],
            "Reasoning (preview)": data["llm_gcr"][:120] + "…",
        })

    summary_df = pd.DataFrame(summary_rows)

    def _color_row(val):
        if val == "Yes":    return "background-color: #C6EFCE"
        if val == "No":     return "background-color: #FFC7CE"
        if val == "ERROR":  return "background-color: #FFEB9C"
        return ""

    st.dataframe(
        summary_df.style.applymap(_color_row, subset=["Goal Completed"]),
        use_container_width=True,
        height=350,
    )

    # ── Download button ────────────────────────────────────────
    st.divider()
    st.subheader("⬇️ Download Evaluated Excel")

    if st.session_state.output_bytes:
        fname = (st.session_state.get("_last_file") or "output").replace(".xlsx", "")
        st.download_button(
            label="📥  Download Evaluated Excel File",
            data=st.session_state.output_bytes,
            file_name=f"{fname}_evaluated.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )
        st.success(
            "Your Excel file is ready! "
            "'LLM GCR' column contains the full evaluation. "
            "'LLM GCR yes/no' contains Yes/No with colour coding."
        )
    else:
        st.error("Output file could not be generated. Check the error log.")

    # ── Log viewer ─────────────────────────────────────────────
    with st.expander("🔍 View Full Log"):
        st.markdown(
            "<div class='log-box'>" +
            "<br>".join(st.session_state.log_lines) +
            "</div>",
            unsafe_allow_html=True,
        )

    # ── Re-run button ──────────────────────────────────────────
    if st.button("🔄 Evaluate Again (re-upload or retry)", use_container_width=True):
        reset_run_state()
        st.session_state.df       = None
        st.session_state.sessions = None
        st.rerun()