"""One Switch agent in its own OS process, so a test can really kill it.

Holds an event stream and a 2s heartbeat. Reads operation requests as JSON lines
on stdin and writes replies and stream events as JSON lines on stdout.
"""

import asyncio
import json
import os
import sys
import time
import uuid

import httpx

BASE, AGENT_ID, API_KEY = sys.argv[1:4]
CONNECTION_ID = str(uuid.uuid4())
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def emit(**fields):
    print(json.dumps({"t": time.time(), **fields}), flush=True)


async def stream(client):
    params = {
        "connection_id": CONNECTION_ID,
        "scope": "all",
        "filter": "all",
        "start_from": "head",
    }
    url = f"{BASE}/agents/{AGENT_ID}/events"
    headers = {**AUTH, "Accept": "text/event-stream"}
    timeout = httpx.Timeout(10.0, read=None)
    async with client.stream(
        "GET", url, params=params, headers=headers, timeout=timeout
    ) as response:
        kind = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                emit(type="event", kind=kind, data=json.loads(line[5:]))


async def heartbeat(client):
    url = f"{BASE}/agents/{AGENT_ID}/connection/beat"
    while True:
        await asyncio.sleep(2)
        try:
            await client.post(
                url, json={"connection_id": CONNECTION_ID}, headers=AUTH, timeout=5
            )
        except httpx.HTTPError as error:
            emit(type="beat_error", error=str(error))


async def serve(client):
    loop = asyncio.get_running_loop()
    headers = {**AUTH, "X-Switch-Connection-Id": CONNECTION_ID}
    while line := await loop.run_in_executor(None, sys.stdin.readline):
        request = json.loads(line)
        response = await client.post(
            f"{BASE}/agents/{AGENT_ID}/ops/{request['op']}",
            json=request.get("args", {}),
            headers=headers,
            timeout=30,
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        emit(type="reply", id=request["id"], status=response.status_code, body=body)


async def main():
    emit(type="started", pid=os.getpid())
    async with httpx.AsyncClient() as client:
        await asyncio.gather(stream(client), heartbeat(client), serve(client))


asyncio.run(main())
