import json
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

app = FastAPI(title="Wayfinder OS")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class TravelQuery(BaseModel):
    query: str


SYSTEM_PROMPT = """
You are Wayfinder OS, a travel planning assistant.
Only answer travel-related questions.

Travel-related topics include destinations, itineraries, budgets, places to visit,
food, hotels, transport, flights, packing, safety, visas, seasons, and trip planning.

If the user query is not travel-related, reply exactly:
I can help with travel planning. Please send a question about destinations, itineraries, budgets, places to visit, food, hotels, transport, or trip planning.

If it is travel-related, provide a helpful travel guide with:
- a short summary
- a practical day-by-day plan when relevant
- budget notes
- transport tips
- 3 to 5 concrete recommendations

Keep the answer complete but concise.
"""


def ndjson(event: dict) -> str:
    return json.dumps(event) + "\n"


def get_event_error_message(event) -> str:
    error = getattr(event, "error", None)
    if error and getattr(error, "message", None):
        return error.message

    message = getattr(event, "message", None)
    if message:
        return message

    return "Wayfinder could not complete this request. Please try again."


@app.get("/health")
def health():
    return {"status": "ok", "version": "v0.1"}

@app.post("/travel-query")
def travel_query(body: TravelQuery):
    def stream_events():
        if not body.query.strip():
            yield ndjson({"type": "error", "message": "Please enter a travel planning question."})
            return

        if not os.getenv("OPENAI_API_KEY"):
            yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
            return

        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            stream = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": body.query},
                ],
                max_output_tokens=1600,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type in ("response.output_text.delta", "response.refusal.delta"):
                    text = getattr(event, "delta", "")
                    if text:
                        yield ndjson({"type": "delta", "text": text})

                elif event_type in ("response.failed", "response.incomplete", "error"):
                    yield ndjson({"type": "error", "message": get_event_error_message(event)})
                    return

            yield ndjson({"type": "done"})

        except Exception:
            yield ndjson({
                "type": "error",
                "message": "Wayfinder could not complete this request. Please try again.",
            })

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
