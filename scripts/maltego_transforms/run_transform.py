#!/usr/bin/env python3
"""
CLI wrapper for the Maltego transform server.
Usage: python run_transform.py <transform_name> <value> [--json]
"""
import sys, asyncio, json, importlib.util

spec = importlib.util.spec_from_file_location("server", "/app/server.py")
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def main():
    if len(sys.argv) < 3:
        print("Usage: run_transform.py <name> <value>")
        print("\nAvailable transforms:")
        for name, t in mod.TRANSFORMS.items():
            print(f"  {name:35s} [{t['input_type']}]  {t['description']}")
        sys.exit(1)

    transform_name = sys.argv[1]
    value = sys.argv[2]
    fmt = "--json" in sys.argv

    t = mod.TRANSFORMS.get(transform_name)
    if not t:
        print(f"Unknown transform: {transform_name}", file=sys.stderr)
        sys.exit(1)

    results = asyncio.run(t["fn"](value, {}))
    if fmt:
        print(json.dumps({"transform": transform_name, "input": value,
                          "count": len(results), "results": results}, indent=2))
    else:
        print(f"Transform: {transform_name}  Input: {value}  Results: {len(results)}")
        for r in results:
            fields_str = "  ".join(f"{k}={v}" for k, v in r.get("fields", {}).items() if v)
            print(f"  [{r['type']}] {r['value']}")
            if fields_str:
                print(f"    {fields_str}")

if __name__ == "__main__":
    main()
