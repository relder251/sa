# Tool: mirofish_simulation

Run social simulations via MiroFish to model actor behavior and predict outcomes.

## Usage

Three-stage workflow: launch → status (poll) → results

### 1. Launch a simulation

```json
{
  "tool_name": "mirofish_simulation",
  "tool_args": {
    "action": "launch",
    "actors": [
      {
        "name": "Actor Name",
        "role": "their role",
        "goal": "what they are trying to achieve",
        "attributes": {"key": "value"}
      }
    ]
  }
}
```

### 2. Poll status

```json
{
  "tool_name": "mirofish_simulation",
  "tool_args": {
    "action": "status",
    "simulation_id": "<id from launch>"
  }
}
```

### 3. Get results

```json
{
  "tool_name": "mirofish_simulation",
  "tool_args": {
    "action": "results",
    "simulation_id": "<id from launch>"
  }
}
```

## Notes
- Always poll status until complete before fetching results
- Include at least 2 actors for meaningful simulations
- Specify concrete goals to get actionable timeline output
