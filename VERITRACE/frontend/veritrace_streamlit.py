"""Streamlit frontend for the VeriTrace research event API."""

import streamlit as st
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.research_client import ResearchView, research_events, task_details, task_graph


def main():
    st.set_page_config(page_title="VERITRACE", layout="wide")
    st.title("VERITRACE")
    st.session_state.setdefault("research_history", [])
    st.session_state.setdefault("research_view", ResearchView())
    with st.sidebar:
        language = st.selectbox("Language", ["en", "cn"])
        if st.button("New conversation"):
            st.session_state.research_history = []
            st.session_state.research_view = ResearchView()
            st.rerun()
    report, detail = st.columns([2, 1])
    with report:
        for item in st.session_state.research_history:
            with st.chat_message(item["role"]):
                st.markdown(item["content"])
        question = st.chat_input("Ask a question")
        if question:
            st.session_state.research_history.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            view = ResearchView(question=question, status="running")
            st.session_state.research_view = view
            with st.chat_message("assistant"):
                output = st.empty()
                progress = detail.empty()
                try:
                    for event in research_events(question, language=language):
                        view.apply(event)
                        output.markdown(view.article or view.progress or "Researching...")
                        progress.markdown(task_graph(view), unsafe_allow_html=True)
                    if view.status == "running":
                        raise RuntimeError("Research stream closed before completion")
                    if view.error:
                        output.error(view.error)
                except Exception as error:
                    view.error, view.status = str(error), "failed"
                    output.error(view.error)
            st.session_state.research_history.append({"role": "assistant", "content": view.article or view.error or view.progress})
    view = st.session_state.research_view
    with detail:
        st.markdown(task_graph(view), unsafe_allow_html=True)
        if view.tasks:
            task_id = st.selectbox("Search task", list(view.tasks))
            st.markdown(task_details(view.tasks[task_id]))
        if view.cognitive_graph:
            with st.expander("Cognitive graph"):
                st.json(view.cognitive_graph)
        if view.usage:
            with st.expander("Usage"):
                st.json(view.usage)


if __name__ == "__main__":
    main()
