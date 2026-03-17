#!/usr/bin/env python3
"""
Parse structured AI executor output and write individual files to disk.

Expected format in the AI response:
    ===FILE: path/to/file.py===
    ... file content ...
    ===END FILE===

Usage:
    python3 extract_files.py <input_md> <output_dir>
"""
import re, os, sys


def extract_and_write(input_path: str, output_dir: str) -> int:
    with open(input_path) as f:
        content = f.read()

    pattern = re.compile(r'===FILE:\s*(.+?)===\n(.*?)===END FILE===', re.DOTALL)
    matches = pattern.findall(content)

    if not matches:
        print("No structured file blocks found — check that the AI used the ===FILE:=== format.")
        return 0

    os.makedirs(output_dir, exist_ok=True)
    written = []

    for raw_path, file_content in matches:
        rel_path = raw_path.strip().lstrip('/')
        abs_path = os.path.join(output_dir, rel_path)

        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(abs_path, 'w') as f:
            f.write(file_content)

        written.append(rel_path)
        print(f"  wrote: {abs_path}")

    manifest_path = os.path.join(output_dir, 'MANIFEST.txt')
    with open(manifest_path, 'w') as f:
        f.write('\n'.join(written) + '\n')
    print(f"  wrote: {manifest_path}")

    return len(written)


if __name__ == '__main__':
    input_md  = sys.argv[1] if len(sys.argv) > 1 else '/data/output/execution_output.md'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else '/data/output/project'
    count = extract_and_write(input_md, output_dir)
    print(f"\nExtracted {count} file(s) to {output_dir}")
    sys.exit(0 if count > 0 else 1)
