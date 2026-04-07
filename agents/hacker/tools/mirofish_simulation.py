import asyncio
import json
from python.helpers.tool import Tool, Response
import httpx

MIROFISH_URL = "http://mirofish:5001"


class MirofishSimulation(Tool):
    async def execute(self, action="", actors=None, simulation_id="", **kwargs):
        if action == "launch":
            return await self._launch(actors or [])
        elif action == "status":
            return await self._status(simulation_id)
        elif action == "results":
            return await self._results(simulation_id)
        else:
            return Response(
                message="action must be one of: launch, status, results", break_loop=False
            )

    async def _launch(self, actors: list) -> Response:
        if not actors:
            return Response(message="actors list is required for launch", break_loop=False)

        # Convert actors JSON to markdown document for MiroFish file upload
        doc_lines = ["# Simulation Actors\n"]
        for a in actors:
            doc_lines.append(f"## {a.get('name', 'Actor')}")
            doc_lines.append(f"**Role:** {a.get('role', 'unknown')}")
            doc_lines.append(f"**Goal:** {a.get('goal', '')}")
            if a.get("attributes"):
                doc_lines.append(f"**Attributes:** {json.dumps(a['attributes'])}")
            doc_lines.append("")
        doc_text = "\n".join(doc_lines)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Upload actor document
                upload_resp = await client.post(
                    f"{MIROFISH_URL}/upload",
                    files={"file": ("actors.md", doc_text.encode(), "text/markdown")},
                )
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
                file_id = upload_data.get("file_id") or upload_data.get("id")

                # Generate ontology
                await self.agent.handle_intervention(
                    self.agent.set_progress("Generating ontology...")
                )
                onto_resp = await client.post(
                    f"{MIROFISH_URL}/ontology/generate",
                    json={"file_id": file_id},
                )
                onto_resp.raise_for_status()
                onto_data = onto_resp.json()
                onto_id = onto_data.get("ontology_id") or onto_data.get("id")

                # Create simulation
                await self.agent.handle_intervention(
                    self.agent.set_progress("Creating simulation...")
                )
                sim_resp = await client.post(
                    f"{MIROFISH_URL}/simulation/create",
                    json={"ontology_id": onto_id, "actors": actors},
                )
                sim_resp.raise_for_status()
                sim_data = sim_resp.json()
                sim_id = sim_data.get("simulation_id") or sim_data.get("id")

                # Start simulation
                run_resp = await client.post(f"{MIROFISH_URL}/simulation/{sim_id}/run")
                run_resp.raise_for_status()

        except httpx.HTTPStatusError as e:
            return Response(
                message=f"MiroFish error {e.response.status_code}: {e.response.text}",
                break_loop=False,
            )
        except Exception as e:
            return Response(message=f"MiroFish request failed: {e}", break_loop=False)

        return Response(
            message=f"Simulation launched. simulation_id={sim_id}\nUse action='status' to poll progress.",
            break_loop=False,
        )

    async def _status(self, simulation_id: str) -> Response:
        if not simulation_id:
            return Response(message="simulation_id is required for status", break_loop=False)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{MIROFISH_URL}/simulation/{simulation_id}/status")
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return Response(message=f"MiroFish status error: {e}", break_loop=False)

        status = data.get("status", "unknown")
        progress = data.get("progress", "")
        return Response(
            message=f"Simulation {simulation_id}: {status}\n{progress}",
            break_loop=False,
        )

    async def _results(self, simulation_id: str) -> Response:
        if not simulation_id:
            return Response(message="simulation_id is required for results", break_loop=False)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{MIROFISH_URL}/simulation/{simulation_id}/results")
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return Response(message=f"MiroFish results error: {e}", break_loop=False)

        timeline = data.get("timeline", [])
        if not timeline:
            return Response(
                message=f"No results yet for simulation {simulation_id}", break_loop=False
            )

        lines = [f"## Simulation Results: {simulation_id}"]
        for step in timeline:
            t = step.get("time", "")
            actor = step.get("actor", "")
            action = step.get("action", "")
            outcome = step.get("outcome", "")
            lines.append(f"[{t}] {actor}: {action} → {outcome}")

        return Response(message="\n".join(lines), break_loop=False)
