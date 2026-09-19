"""Streamlit chat UI: founder asks a question, live monday.com data backs the answer."""
import streamlit as st

from bi_agent import analytics, data_source
from bi_agent.agent import Agent
from bi_agent.cleaning import clean_deals, clean_work_orders
from bi_agent.config import load_settings
from bi_agent.monday_client import MondayError

st.set_page_config(page_title="Skylark BI Agent", page_icon="📊", layout="centered")
st.title("📊 Skylark BI Agent")
st.caption("Ask about the deal pipeline, revenue, billing or data quality — answered live from monday.com.")

settings = load_settings()
missing = settings.missing_for_agent()
if missing:
    st.error(f"Missing configuration: {', '.join(missing)}. Add it to .env locally or to Streamlit secrets.")
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_agent() -> Agent:
    return Agent(api_key=settings.gemini_key, model=settings.gemini_model)


def _load_and_clean(force: bool = False):
    loaded = data_source.load_all(force_refresh=force, settings=settings)
    wo_df = loaded["work_orders"].board.to_dataframe()
    deals_df = loaded["deals"].board.to_dataframe()
    wo_clean, wo_report = clean_work_orders(wo_df)
    deals_clean, deal_report = clean_deals(deals_df)
    analytics.set_data(wo_clean, deals_clean, wo_report.notes, deal_report.notes)
    return loaded, wo_report, deal_report


with st.sidebar:
    st.subheader("Data status")
    refresh = st.button("🔄 Refresh from monday.com")
    try:
        loaded, wo_report, deal_report = _load_and_clean(force=refresh)
        for kind, label in (("work_orders", "Work Orders"), ("deals", "Deals")):
            info = loaded[kind]
            tag = "🟡 stale" if info.stale else "🟢 live"
            st.write(f"{tag} **{label}**: {len(info.board.rows)} items")
            if info.warning:
                st.warning(info.warning)
        with st.expander("Data-quality notes"):
            st.write("**Work Orders**")
            for n in wo_report.notes:
                st.markdown(f"- {n}")
            st.write("**Deals**")
            for n in deal_report.notes:
                st.markdown(f"- {n}")
    except MondayError as exc:
        st.error(f"Could not load data: {exc}")
        st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("e.g. How's the energy-sector pipeline this quarter?")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("model"):
        with st.spinner("Checking live data..."):
            agent = _get_agent()
            history = [{"role": "user" if m["role"] == "user" else "model", "content": m["content"]}
                       for m in st.session_state.messages]
            reply = agent.answer(history)
        st.markdown(reply)
    st.session_state.messages.append({"role": "model", "content": reply})