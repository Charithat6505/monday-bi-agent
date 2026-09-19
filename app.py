"""Streamlit chat UI: founder asks a question, live monday.com data backs the answer."""

import streamlit as st

from bi_agent import analytics, data_source
from bi_agent.agent import Agent
from bi_agent.cleaning import clean_deals, clean_work_orders
from bi_agent.config import load_settings
from bi_agent.monday_client import MondayError


# --------------------------------------------------
# Page Configuration
# --------------------------------------------------

st.set_page_config(
    page_title="Skylark BI Agent",
    page_icon="📊",
    layout="wide",
)


# --------------------------------------------------
# Custom UI Styling
# --------------------------------------------------

st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.3rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .subtitle {
        color: #6b7280;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }

    .status-card {
        padding: 1rem;
        border-radius: 10px;
        background-color: #f5f7fa;
        margin-bottom: 1rem;
    }

    .stChatMessage {
        border-radius: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------
# Header
# --------------------------------------------------

st.markdown(
    '<div class="main-title">📊 Skylark BI Agent</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="subtitle">'
    "Ask questions about your deal pipeline, revenue, billing, "
    "and data quality using live monday.com data."
    "</div>",
    unsafe_allow_html=True,
)


# --------------------------------------------------
# Load Settings
# --------------------------------------------------

settings = load_settings()

missing = settings.missing_for_agent()

if missing:
    st.error(
        f"Missing configuration: {', '.join(missing)}. "
        "Add it to .env locally or Streamlit Secrets."
    )
    st.stop()


# --------------------------------------------------
# Initialize Groq Agent
# --------------------------------------------------

@st.cache_resource(show_spinner=False)
def _get_agent() -> Agent:
    return Agent(
        api_key=settings.groq_key,
        model=settings.groq_model,
    )


# --------------------------------------------------
# Load and Clean monday.com Data
# --------------------------------------------------

def _load_and_clean(force: bool = False):
    loaded = data_source.load_all(
        force_refresh=force,
        settings=settings,
    )

    wo_df = loaded["work_orders"].board.to_dataframe()
    deals_df = loaded["deals"].board.to_dataframe()

    wo_clean, wo_report = clean_work_orders(wo_df)
    deals_clean, deal_report = clean_deals(deals_df)

    analytics.set_data(
        wo_clean,
        deals_clean,
        wo_report.notes,
        deal_report.notes,
    )

    return loaded, wo_report, deal_report


# --------------------------------------------------
# Sidebar: Data Status
# --------------------------------------------------

with st.sidebar:
    st.header("⚙️ Dashboard Settings")

    st.subheader("📡 Data Status")

    refresh = st.button(
        "🔄 Refresh from monday.com",
        use_container_width=True,
    )

    try:
        loaded, wo_report, deal_report = _load_and_clean(
            force=refresh
        )

        for kind, label in (
            ("work_orders", "Work Orders"),
            ("deals", "Deals"),
        ):
            info = loaded[kind]

            tag = "🟡 Stale" if info.stale else "🟢 Live"

            st.markdown(
                f"**{label}**  \n"
                f"{tag} · {len(info.board.rows)} items"
            )

            if info.warning:
                st.warning(info.warning)

        with st.expander("📋 Data-quality notes"):
            st.write("**Work Orders**")

            for note in wo_report.notes:
                st.markdown(f"- {note}")

            st.write("**Deals**")

            for note in deal_report.notes:
                st.markdown(f"- {note}")

    except MondayError as exc:
        st.error(f"Could not load data: {exc}")
        st.stop()

    st.divider()

    st.caption("Powered by monday.com + Groq")


# --------------------------------------------------
# Chat History
# --------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []


for msg in st.session_state.messages:
    display_role = (
        "assistant"
        if msg["role"] == "model"
        else msg["role"]
    )

    with st.chat_message(display_role):
        st.markdown(msg["content"])


# --------------------------------------------------
# Chat Input
# --------------------------------------------------

prompt = st.chat_input(
    "Ask about pipeline, revenue, billing, or data quality..."
)


if prompt:
    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
        }
    )

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("🔍 Checking live data..."):
            agent = _get_agent()

            history = [
                {
                    "role": (
                        "user"
                        if message["role"] == "user"
                        else "model"
                    ),
                    "content": message["content"],
                }
                for message in st.session_state.messages
            ]

            reply = agent.answer(history)

        st.markdown(reply)

    st.session_state.messages.append(
        {
            "role": "model",
            "content": reply,
        }
    )