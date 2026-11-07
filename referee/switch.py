"""A minimal async client for the Switch agent protocol.

One SwitchAgent is one registered agent with one live connection: an SSE event
stream down, a heartbeat every 2 seconds, and operations over HTTP.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

import httpx

INTEGRATION_PROFILE = {
    "connection_model": "always_on",
    "message_exchange": True,
    "pre_invocation_mediation": [],
    "post_invocation_mediation": [],
    "event_reporting": [],
}
HEARTBEAT_SECONDS = 2


class OpError(RuntimeError):
    def __init__(self, op, status, body):
        super().__init__(f"{op} failed with HTTP {status}: {body}")
        self.op, self.status, self.body = op, status, body


async def register(base_url, registration_token, name, description):
    """Creates the agent, or replaces one of the same name. Returns {id, api_key, ...}."""
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        response = await client.post(
            "/agents",
            headers={"Authorization": f"Bearer {registration_token}"},
            json={
                "name": name,
                "description": description,
                "connector_type": "custom",
                "integration_profile": INTEGRATION_PROFILE,
                "overwrite": True,
            },
        )
        response.raise_for_status()
        return response.json()


class SwitchAgent:
    def __init__(self, base_url, agent_id, api_key, name):
        self.base_url = base_url
        self.agent_id = agent_id
        self.name = name
        self.connection_id = str(uuid.uuid4())
        self._auth = {"Authorization": f"Bearer {api_key}"}
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30)
        self._connected = asyncio.Event()
        self._tasks = []

    async def op(self, op_name, /, **args):
        """Runs one operation and returns its result, or raises OpError."""
        response = await self._client.post(
            f"/agents/{self.agent_id}/ops/{op_name}",
            json=args,
            headers={**self._auth, "X-Switch-Connection-Id": self.connection_id},
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code != 200:
            raise OpError(op_name, response.status_code, body)
        return body.get("result", body) if isinstance(body, dict) else body

    async def start(self, on_event: Callable[[str, dict], Awaitable[None]]):
        """Opens the stream and heartbeat. Returns once Switch confirms the connection."""
        self._tasks = [
            asyncio.create_task(self._stream(on_event)),
            asyncio.create_task(self._heartbeat()),
        ]
        await asyncio.wait_for(self._connected.wait(), timeout=15)

    async def close(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()

    async def _stream(self, on_event):
        params = {
            "connection_id": self.connection_id,
            "scope": "all",
            "filter": "all",
            "start_from": "head",
        }
        headers = {**self._auth, "Accept": "text/event-stream"}
        timeout = httpx.Timeout(10.0, read=None)
        async with self._client.stream(
            "GET", f"/agents/{self.agent_id}/events", params=params, headers=headers, timeout=timeout
        ) as response:
            response.raise_for_status()
            kind = None
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:])
                    if kind == "connection_state":
                        self._connected.set()
                    await on_event(kind, data)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self._client.post(
                    f"/agents/{self.agent_id}/connection/beat",
                    json={"connection_id": self.connection_id},
                    headers=self._auth,
                    timeout=5,
                )
            except httpx.HTTPError:
                pass
