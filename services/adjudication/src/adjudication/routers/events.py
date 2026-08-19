"""GET /cases/{id}/events (the stored audit log, replayed from `case_events`) and GET
/cases/{id}/stream (the live SSE view of the same events).

Both render `repositories.case_events.to_wire`'s output, and the stream's publish call
lives inside `repositories.case_events.append` itself -- see that module's `bind`
docstring for why that is what keeps the two from ever rendering a different sequence
(task-8 brief: "the SSE stream and the stored log must render the same sequence")."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from adjudication.repositories import case_events as case_events_repo
from adjudication.repositories import cases as cases_repo

router = APIRouter()


@router.get("/cases/{case_id}/events")
async def list_events(case_id: str, request: Request) -> list[dict]:
    pool = request.app.state.pool
    if await cases_repo.get(pool, case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    events = await case_events_repo.list_for_case(pool, case_id)
    return [case_events_repo.to_wire(event) for event in events]


@router.get("/cases/{case_id}/stream")
async def stream_events(case_id: str, request: Request) -> StreamingResponse:
    pool = request.app.state.pool
    if await cases_repo.get(pool, case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    pubsub = request.app.state.redis.pubsub()
    # Subscribed here, inside the handler and before StreamingResponse below ever
    # starts sending -- the response headers reaching the caller are therefore the
    # caller's own proof the subscription is already live, so no event published from
    # this point on can be missed. See tests/test_routes.py's seq-parity test, which
    # depends on exactly this ordering to be deterministic rather than racy.
    await pubsub.subscribe(case_events_repo.channel(case_id))

    async def event_source():
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    # The subscribe confirmation Redis sends on `subscribe()` itself,
                    # not an event -- forwarding it would hand the client a line that
                    # isn't valid JSON.
                    continue
                yield f"data: {message['data']}\n\n"
        finally:
            await pubsub.aclose()

    return StreamingResponse(event_source(), media_type="text/event-stream")
