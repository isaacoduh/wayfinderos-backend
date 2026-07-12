import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Wayfinder OS")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

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

@app.get("/health")
def health():
    return {"status": "ok", "version": "-0.000001"}

@app.post("/travel-query")
def travel_query(body: TravelQuery):
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body.query},
        ],
        max_output_tokens=1600,
    )

    return {"answer": response.output_text.strip()}