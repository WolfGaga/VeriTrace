"""Gradio frontend for the VeriTrace research event API."""

import gradio as gr
from copy import deepcopy
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.research_client import ResearchView, research_events, task_details, task_graph


def _frame(history, view, views):
    choices = list(view.tasks)
    selected = choices[-1] if choices else None
    rounds = [(f"{index + 1}. {item.question[:60]}", str(index)) for index, item in enumerate(views)]
    return (history, task_graph(view), gr.update(choices=choices, value=selected),
            task_details(view.tasks.get(selected)), view.usage, view, views,
            gr.update(choices=rounds, value=str(len(views) - 1) if views else None))


def select_research_round(index, views):
    view = views[int(index)] if index is not None and 0 <= int(index) < len(views) else ResearchView()
    frame = _frame([], view, views)
    return frame[1:6]


def stop_research(history, views):
    history, views = list(history or []), list(views or [])
    view = deepcopy(views[-1]) if views else ResearchView()
    if view.status == "running":
        view.status = "cancelled"
        for task in view.tasks.values():
            if task.get("status") in ("pending", "running"):
                task["status"] = "cancelled"
        if history and history[-1].get("role") == "assistant":
            history[-1] = {**history[-1], "content": view.article or "Research cancelled."}
        views[-1] = view
    return _frame(history, view, views)


def run_research(question, history, language, views=None):
    history = list(history or [])
    history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": "Researching..."}])
    view = ResearchView(question=question, status="running")
    views = [*(views or []), view]
    try:
        for event in research_events(question, language=language):
            view.apply(event)
            history[-1]["content"] = view.article or view.progress or "Researching..."
            yield _frame(history, view, views)
        if view.status == "running":
            raise RuntimeError("Research stream closed before completion")
        if view.error:
            history[-1]["content"] = view.error
            yield _frame(history, view, views)
    except Exception as error:
        view.status, view.error = "failed", str(error)
        history[-1]["content"] = str(error)
        yield _frame(history, view, views)


def build_app():
    with gr.Blocks(title="VERITRACE") as demo:
        gr.Markdown("# VERITRACE")
        state = gr.State(ResearchView())
        views = gr.State([])
        with gr.Row():
            with gr.Column(scale=2):
                history = gr.Chatbot(type="messages", label="Research", height=500)
                question = gr.Textbox(label="Question")
                with gr.Row():
                    language = gr.Dropdown(["en", "cn"], value="en", label="Language")
                with gr.Row():
                    submit = gr.Button("Research", variant="primary")
                    stop = gr.Button("Stop")
                    clear = gr.Button("Clear")
                gr.Examples([
                    ["Find legal precedents in contract law."],
                    ["What are the top 10 e-commerce websites?"],
                    ["Generate a report on global climate change."],
                ], inputs=question, label="Try these examples:")
            with gr.Column():
                research_round = gr.Dropdown([], label="Research round")
                graph = gr.HTML(label="Task progress")
                task = gr.Dropdown([], label="Search task")
                details = gr.Markdown(task_details(None))
                usage = gr.JSON(label="Usage")
        inputs = [question, history, language, views]
        outputs = [history, graph, task, details, usage, state, views, research_round]
        run = submit.click(run_research, inputs=inputs, outputs=outputs)
        enter = question.submit(run_research, inputs=inputs, outputs=outputs)
        stop.click(stop_research, inputs=[history, views], outputs=outputs, cancels=[run, enter], queue=False)
        clear.click(lambda: (*_frame([], ResearchView(), []), ""),
                    outputs=[*outputs, question], cancels=[run, enter], queue=False)
        research_round.input(select_research_round, inputs=[research_round, views], outputs=[graph, task, details, usage, state])
        task.input(lambda task_id, view: task_details(view.tasks.get(task_id)), inputs=[task, state], outputs=details)
    return demo


if __name__ == "__main__":
    build_app().queue().launch(server_name="127.0.0.1", server_port=7883, inbrowser=True, share=False)
