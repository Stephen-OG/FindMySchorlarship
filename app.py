import asyncio
import os

import gradio as gr
from agents import gen_trace_id, trace
from dotenv import load_dotenv

from scholarship_agents.schorlarship_agent import chat_stream

load_dotenv(override=True)


with gr.Blocks() as demo:
    gr.Markdown("# 🎓 FindMyScholarship AI")

    chatbot = gr.Chatbot(label="📋 Funding Search Results", height=500)
    query = gr.Textbox(
        label="🔍 What funding are you looking for?",
        placeholder="e.g., PhD funding in machine learning at University of Exeter or UK universities",
    )
    search_btn = gr.Button("🚀 Search Funding")

    # Prevent concurrent double-submits from triggering repeated crawls.
    run_lock = asyncio.Lock()

    async def run_query(message, history):
        if not message or not message.strip():
            yield history, message
            return

        async with run_lock:
            trace_id = gen_trace_id()
            history = list(history or [])
            # Add the user message immediately so the bubble appears at once.
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": ""})

            prior_history = history[:-2]

            with trace("Scholarship Search", trace_id=trace_id):
                async for partial in chat_stream(message, prior_history):
                    # Replace the last assistant bubble with the latest partial
                    history[-1] = {"role": "assistant", "content": partial}
                    yield history, ""

    search_btn.click(run_query, [query, chatbot], [chatbot, query])
    query.submit(run_query, [query, chatbot], [chatbot, query])

demo.launch(
    server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
    server_port=int(os.getenv("PORT", os.getenv("GRADIO_SERVER_PORT", "7860"))),
    # theme=gr.themes.Soft(),
)
