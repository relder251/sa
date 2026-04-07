from python.helpers.tool import Tool, Response
import httpx

MALTEGO_URL = "http://maltego_transforms:8080"

TRANSFORMS = [
    "DomainToIPAddress",
    "DomainToDNSRecords",
    "DomainToSubdomains",
    "DomainToWhois",
    "IPToGeolocation",
    "IPToASN",
    "IPToShodan",
    "EmailToDomain",
    "EmailToBreaches",
    "DomainToCompanyProfile",
]


class MaltegoOsint(Tool):
    async def execute(self, transform="", value="", **kwargs):
        if not transform or not value:
            return Response(
                message="Both 'transform' and 'value' are required.", break_loop=False
            )
        if transform not in TRANSFORMS:
            return Response(
                message=f"Unknown transform '{transform}'. Available: {', '.join(TRANSFORMS)}",
                break_loop=False,
            )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{MALTEGO_URL}/api/{transform}",
                    json={"value": value},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return Response(message=f"Maltego error {e.response.status_code}: {e.response.text}", break_loop=False)
        except Exception as e:
            return Response(message=f"Maltego request failed: {e}", break_loop=False)

        entities = data.get("entities", [])
        if not entities:
            return Response(message=f"No results from {transform}({value})", break_loop=False)

        lines = [f"### {transform}({value}) — {len(entities)} result(s)"]
        for ent in entities:
            label = ent.get("value") or ent.get("label") or str(ent)
            props = ent.get("properties", {})
            prop_str = "  " + ", ".join(f"{k}: {v}" for k, v in props.items()) if props else ""
            lines.append(f"- {label}{prop_str}")

        return Response(message="\n".join(lines), break_loop=False)
