#!/usr/bin/env python3
"""
Phase 3: Test & Fix Feedback Loop Server

POST /run    → installs deps, runs pytest, loops LLM fix attempts, writes report
GET  /health → {"status": "ok"}

The full loop runs inside this service so n8n just makes one long-lived HTTP call.
"""
import os
import re
import sys
from pathlib import Path
from flask import Flask, request, jsonify
import requests as http

# ── Shared utilities ──────────────────────────────────────────────────────────
# scripts/ dir must be on the path so shared_utils is importable
_SCRIPTS_SEARCH = [
    "/data/scripts",
    str(Path(__file__).parent),  # repo root scripts/ when running locally
]
for _sp in _SCRIPTS_SEARCH:
    if Path(_sp).exists() and _sp not in sys.path:
        sys.path.insert(0, _sp)

from shared_utils import (  # noqa: E402
    ensure_venv,
    run_pytest,
    read_source_files,
    call_llm_fix,
    parse_and_apply_fixes,
    run_postprocess,
)

app = Flask(__name__)

LITELLM_URL  = os.environ.get('LITELLM_BASE_URL', 'http://litellm:4000')
LITELLM_KEY  = os.environ.get('LITELLM_API_KEY',  'sk-vibe-coding-key-123')
MAX_ATTEMPTS = int(os.environ.get('PHASE3_MAX_ATTEMPTS', '3'))
FIX_MODEL    = 'free/code'


# ── main route ────────────────────────────────────────────────────────────────

# ── Phase 4: full opportunity pipeline ───────────────────────────────────────

PLAN_MODEL  = 'hybrid/chat'
CODE_MODEL  = 'free/code'         # cloud-first (Groq/Gemini) — faster for large outputs
CODE_FALLBACK = 'hybrid/code'     # local Ollama fallback if free tier is rate-limited

def _llm(model, system, user, max_tokens=8192, timeout=300, fallback=None):
    """Call LiteLLM; on 429 retry with `fallback` model if provided."""
    def _call(m):
        resp = http.post(
            f'{LITELLM_URL}/v1/chat/completions',
            headers={'Authorization': f'Bearer {LITELLM_KEY}', 'Content-Type': 'application/json'},
            json={'model': m, 'max_tokens': max_tokens, 'timeout': max(timeout - 30, 60),
                  'messages': [{'role': 'system', 'content': system},
                               {'role': 'user',   'content': user}]},
            timeout=timeout,
        )
        return resp

    resp = _call(model)
    if resp.status_code == 429 and fallback:
        print(f'  [{model}] rate-limited, falling back to {fallback}', flush=True)
        resp = _call(fallback)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def validate_format_inline(content):
    """Mirror of validate_format.js — returns {valid, issues, file_count}."""
    issues = []
    blocks = re.findall(r'===FILE:\s*.+?===', content)
    if not blocks:
        issues.append('No ===FILE:=== blocks found')
    diff_markers = ['<<<<<<< SEARCH', '<<<<<<< HEAD', '>>>>>>> REPLACE']
    if any(m in content for m in diff_markers):
        issues.append('Diff/merge conflict markers detected')
    trunc = [r'# rest of (the )?(implementation|code|file)',
             r'# TODO: implement', r'\.\.\. (existing|rest of) (code|implementation)']
    if any(re.search(p, content, re.I) for p in trunc):
        issues.append('Truncated content detected')
    return {'valid': len(issues) == 0, 'issues': issues, 'file_count': len(blocks)}


def extract_files(content, project_dir: Path):
    """Parse ===FILE:=== blocks and write to project_dir."""
    pattern = re.compile(r'===FILE:\s*(.+?)===\n([\s\S]*?)(?====FILE:|===END FILE===|$)')
    written = []
    for m in pattern.finditer(content):
        rel  = m.group(1).strip().lstrip('/')
        body = re.sub(r'^```[^\n]*\n', '', m.group(2))
        body = re.sub(r'\n```\s*$', '', body)
        if not body.strip():
            continue
        dest = project_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)
        written.append(rel)
    return written


@app.route('/run-opportunity', methods=['POST'])
def run_opportunity():
    """Full Phase 4 pipeline: plan → generate → validate → extract → test/fix → report."""
    data         = request.json or {}
    name         = data.get('name', 'unnamed')
    prompt       = data.get('prompt', '')
    project_base = Path(data.get('project_base', f'/data/output/projects/{name}'))
    project_dir  = Path(data.get('project_dir',  str(project_base / 'project')))
    plan_path    = Path(data.get('plan_path',     str(project_base / 'project_plan.md')))
    code_path    = Path(data.get('code_output_path', str(project_base / 'execution_output.md')))
    report_path  = Path(data.get('report_path',   str(project_base / 'phase4_report.md')))

    project_dir.mkdir(parents=True, exist_ok=True)

    log = []
    def L(msg): log.append(msg); print(msg, flush=True)

    L(f'=== Phase 4 — {name} ===')

    # ── 1. Plan ───────────────────────────────────────────────────────────────
    L('Step 1: Generating project plan ...')
    plan = _llm(
        PLAN_MODEL,
        ('You are an expert software architect. Create a detailed project plan.\n\n'
         'Include: 1) Project overview, 2) Technical stack, 3) File structure, '
         '4) Core components, 5) Implementation steps, 6) Testing approach.\n'
         'Be specific and actionable.'),
        prompt, max_tokens=4096,
    )
    plan_path.write_text(plan)
    L(f'  Plan saved ({len(plan)} chars)')

    # ── 2. Generate code ──────────────────────────────────────────────────────
    L('Step 2: Generating code ...')
    code_system = (
        'You are the Lead Executor of an automated solution builder. '
        'Take the provided project plan and generate a complete, production-ready codebase.\n\n'
        'Output every file using EXACTLY this format:\n'
        '===FILE: path/to/filename.ext===\n[full file content]\n===END FILE===\n\n'
        'REQUIRED: main app files, tests/ directory, requirements.txt, Dockerfile, .gitignore, README.md\n\n'
        'CRITICAL PROHIBITIONS:\n'
        '- NEVER use diff/patch/merge-conflict markers\n'
        '- NEVER truncate or use placeholder comments\n'
        '- Output COMPLETE files only, every line top to bottom'
    )
    code_output = _llm(CODE_MODEL, code_system, plan, fallback=CODE_FALLBACK, timeout=600)
    code_path.write_text(code_output)
    L(f'  Code output saved ({len(code_output)} chars)')

    # ── 3. Validate & fix format ──────────────────────────────────────────────
    L('Step 3: Validating format ...')
    val = validate_format_inline(code_output)
    L(f'  valid={val["valid"]} files={val["file_count"]} issues={val["issues"]}')

    if not val['valid']:
        L('  Format invalid — requesting LLM fix ...')
        fix_prompt = (f'VALIDATION ISSUES:\n{val["issues"]}\n\n'
                      f'ORIGINAL OUTPUT:\n{code_output}')
        code_output = _llm(
            FIX_MODEL,
            ('Fix the output so every file uses ===FILE: path===\\n[content]\\n===END FILE=== format. '
             'Output COMPLETE corrected files only.'),
            fix_prompt,
            fallback=CODE_FALLBACK,
        )
        code_path.write_text(code_output)
        val2 = validate_format_inline(code_output)
        L(f'  After fix: valid={val2["valid"]} files={val2["file_count"]}')

    # ── 4. Extract files ──────────────────────────────────────────────────────
    L('Step 4: Extracting files ...')
    written = extract_files(code_output, project_dir)
    L(f'  Wrote {len(written)} files: {written[:6]}')

    # ── 5. Postprocess ────────────────────────────────────────────────────────
    L('Step 5: Running postprocess ...')
    for fix in run_postprocess(project_dir):
        L(f'  {fix}')

    # ── 6. Test & fix loop ────────────────────────────────────────────────────
    L('Step 6: Running test & fix loop ...')
    ok, venv_dir, pip_msg = ensure_venv(project_dir)
    L(f'  venv: {pip_msg or "ready"}')

    iterations = []
    for attempt in range(1, MAX_ATTEMPTS + 2):
        passed, test_output, failures = run_pytest(project_dir, venv_dir)
        iter_entry = {'attempt': attempt, 'passed': passed, 'output': test_output}
        iterations.append(iter_entry)
        if passed is None:
            L(f'  Structural pytest error — stopping'); break
        if passed:
            L(f'  ✅ All tests passed on attempt {attempt}!'); break
        L(f'  ❌ {len(failures)} failure(s) on attempt {attempt}')
        if attempt > MAX_ATTEMPTS:
            L('  Max attempts reached'); break
        try:
            src   = read_source_files(project_dir)
            fixed = call_llm_fix(test_output, src, attempt, MAX_ATTEMPTS, FIX_MODEL, CODE_FALLBACK)
            applied = parse_and_apply_fixes(project_dir, fixed)
            L(f'  Applied fixes: {applied}')
            if not applied: break
            for fix in run_postprocess(project_dir):
                L(f'  postprocess: {fix}')
            if 'requirements.txt' in applied:
                h = venv_dir / '.req_hash'
                if h.exists(): h.unlink()
                ok, venv_dir, pip_msg = ensure_venv(project_dir)
        except Exception as e:
            L(f'  LLM fix failed: {e}'); break

    # ── 7. Write report ───────────────────────────────────────────────────────
    final_passed = bool(iterations and iterations[-1].get('passed'))
    icon = '✅ PASSED' if final_passed else '❌ FAILED'
    lines = [f'# Phase 4 Report — {icon}\n',
             f'**Project**: `{name}`  \n**Result**: {icon}  \n**Iterations**: {len(iterations)}\n']
    for it in iterations:
        ic = '✅ PASS' if it.get('passed') else '❌ FAIL'
        lines += [f'## Attempt {it["attempt"]} — {ic}', f'```\n{it["output"][:2000]}\n```\n']
    report_path.write_text('\n'.join(lines))
    L(f'\nReport → {report_path}')

    return jsonify({'passed': final_passed, 'iterations': len(iterations),
                    'project_dir': str(project_dir), 'log': log})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/run', methods=['POST'])
def run():
    project_dir = Path(request.json.get('project_dir', '/data/output/project'))
    report_path = Path(request.json.get('report_path',  '/data/output/phase4_report.md'))

    log        = []
    iterations = []

    def L(msg):
        log.append(msg)
        print(msg, flush=True)

    L(f'=== Phase 3 starting — {project_dir} ===')

    # ── pre-flight postprocess ────────────────────────────────────────────────
    for fix in run_postprocess(project_dir):
        L(f'  postprocess: {fix}')

    # ── install deps ──────────────────────────────────────────────────────────
    ok, venv_dir, pip_msg = ensure_venv(project_dir)
    L(f'  venv: {pip_msg or "ready"}')
    if not ok:
        report = f'# Phase 3 Report\n\n**FAILED** — pip install error:\n```\n{pip_msg}\n```\n'
        report_path.write_text(report)
        return jsonify({'passed': False, 'error': 'pip install failed', 'log': log})

    # ── test → fix loop ───────────────────────────────────────────────────────
    for attempt in range(1, MAX_ATTEMPTS + 2):  # +1 final check after last fix
        L(f'\n── Test run {attempt} {"(final check)" if attempt == MAX_ATTEMPTS + 1 else ""} ──')
        passed, test_output, failures = run_pytest(project_dir, venv_dir)

        iter_entry = {'attempt': attempt, 'passed': passed, 'output': test_output}
        iterations.append(iter_entry)

        if passed is None:
            L('  pytest structural error (exit ≥2) — cannot fix by patching source')
            break

        if passed:
            L('  ✅ All tests passed!')
            break

        L(f'  ❌ {len(failures)} failure(s)')
        for f in failures[:5]:
            L(f'     {f}')

        if attempt > MAX_ATTEMPTS:
            L(f'  Max attempts ({MAX_ATTEMPTS}) reached')
            break

        # ── LLM fix ───────────────────────────────────────────────────────────
        L(f'  Requesting LLM fix ({FIX_MODEL}) ...')
        try:
            source_files = read_source_files(project_dir)
            llm_response = call_llm_fix(test_output, source_files, attempt, MAX_ATTEMPTS, FIX_MODEL, CODE_FALLBACK)
            applied      = parse_and_apply_fixes(project_dir, llm_response)
            L(f'  Fixed files: {applied or "(none)"}')

            if not applied:
                L('  LLM returned no file changes — stopping')
                break

            # Re-run postprocess (LLM may reintroduce stdlib deps)
            for fix in run_postprocess(project_dir):
                L(f'  postprocess: {fix}')

            # Invalidate venv cache if requirements.txt changed
            if 'requirements.txt' in applied:
                hash_file = venv_dir / '.req_hash'
                if hash_file.exists():
                    hash_file.unlink()
                ok, venv_dir, pip_msg = ensure_venv(project_dir)
                L(f'  deps refreshed: {pip_msg or "ready"}')

        except Exception as e:
            L(f'  LLM call failed: {e}')
            break

    # ── write report ──────────────────────────────────────────────────────────
    final_passed = bool(iterations and iterations[-1].get('passed'))
    status_icon  = '✅ PASSED' if final_passed else '❌ FAILED'

    lines = [
        f'# Phase 3 Report — {status_icon}\n',
        f'**Project**: `{project_dir}`  ',
        f'**Iterations**: {len(iterations)}  ',
        f'**Result**: {status_icon}\n',
    ]
    for it in iterations:
        icon = '✅ PASS' if it.get('passed') else ('⚠️ ERROR' if it.get('passed') is None else '❌ FAIL')
        lines.append(f'## Attempt {it["attempt"]} — {icon}')
        lines.append(f'```\n{it["output"][:3000]}\n```\n')

    report_path.write_text('\n'.join(lines))
    L(f'\nReport written → {report_path}')

    return jsonify({
        'passed':     final_passed,
        'iterations': len(iterations),
        'log':        log,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
