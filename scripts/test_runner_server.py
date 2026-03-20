#!/usr/bin/env python3
"""
Phase 3: Test & Fix Feedback Loop Server

POST /run    → installs deps, runs pytest, loops LLM fix attempts, writes report
GET  /health → {"status": "ok"}

The full loop runs inside this service so n8n just makes one long-lived HTTP call.
"""
import hashlib, os, re, subprocess, sys, venv
from pathlib import Path
from flask import Flask, request, jsonify
import requests as http

app = Flask(__name__)

LITELLM_URL  = os.environ.get('LITELLM_BASE_URL', 'http://litellm:4000')
LITELLM_KEY  = os.environ.get('LITELLM_API_KEY',  'sk-vibe-coding-key-123')
MAX_ATTEMPTS = int(os.environ.get('PHASE3_MAX_ATTEMPTS', '3'))
FIX_MODEL    = 'free/code'


# ── venv management ───────────────────────────────────────────────────────────

def ensure_venv(project_dir: Path):
    """Create/update a .venv in the project dir, caching on requirements.txt hash."""
    req_file  = project_dir / 'requirements.txt'
    venv_dir  = project_dir / '.venv'
    hash_file = venv_dir / '.req_hash'

    if not req_file.exists():
        # No requirements — create minimal venv with just pytest
        if not (venv_dir / 'bin' / 'pip').exists():
            venv.create(str(venv_dir), with_pip=True)
            _pip(venv_dir, ['install', 'pytest', '-q'])
        return True, venv_dir, 'No requirements.txt — using minimal venv'

    current_hash = hashlib.md5(req_file.read_bytes()).hexdigest()

    if (hash_file.exists()
            and hash_file.read_text().strip() == current_hash
            and (venv_dir / 'bin' / 'pytest').exists()):
        return True, venv_dir, 'Deps unchanged — skipped install'

    if not (venv_dir / 'bin' / 'pip').exists():
        venv.create(str(venv_dir), with_pip=True)

    ok, out = _pip(venv_dir, ['install', '-r', str(req_file), 'pytest', '-q'])
    if ok:
        hash_file.write_text(current_hash)
    return ok, venv_dir, out


def _pip(venv_dir: Path, args: list):
    pip = venv_dir / 'bin' / 'pip'
    env = os.environ.copy()
    env['HOME'] = '/tmp'
    r = subprocess.run([str(pip)] + args, capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


# ── test execution ────────────────────────────────────────────────────────────

def run_pytest(project_dir: Path, venv_dir: Path):
    """
    Returns: (passed, output, failures)
      passed=True  → all tests green
      passed=False → test failures (fixable)
      passed=None  → structural error exit≥2 (not fixable by patching source)
    """
    tests_dir = project_dir / 'tests'
    if not tests_dir.exists():
        return True, 'No tests/ directory — skipped', []

    pytest_bin = venv_dir / 'bin' / 'pytest'
    if not pytest_bin.exists():
        return False, 'pytest binary missing — pip install likely failed', ['pip install failed: pytest not installed']

    env = os.environ.copy()
    env['PYTHONPATH'] = str(project_dir)
    env['HOME'] = '/tmp'

    r = subprocess.run(
        [str(pytest_bin), 'tests/', '-v', '--tb=short', '--no-header'],
        capture_output=True, text=True, cwd=str(project_dir), env=env,
    )
    output   = r.stdout + r.stderr
    failures = [l for l in output.splitlines()
                if 'FAILED' in l or ('ERROR' in l and 'test_' in l)]

    if r.returncode >= 3:
        # exit code ≥ 3 = pytest internal error (not fixable by source changes)
        return None, output, []
    # exit code 2 = collection errors (ImportError, SyntaxError) — LLM can fix these
    if r.returncode == 2:
        # Extract error lines from collection output for the fix prompt
        failures = [l for l in output.splitlines() if 'ERROR' in l or 'ImportError' in l or 'SyntaxError' in l]
    return r.returncode == 0, output, failures


# ── LLM integration ───────────────────────────────────────────────────────────

def read_source_files(project_dir: Path):
    """Collect all non-venv, non-cache project files."""
    skip_dirs = {'.venv', '__pycache__', '.pytest_cache', '.git', 'node_modules'}
    files = {}

    for f in sorted(project_dir.rglob('*')):
        if f.is_dir():
            continue
        parts = set(f.relative_to(project_dir).parts)
        if parts & skip_dirs:
            continue
        if f.suffix in ('.pyc', '.pyo', '.egg-info'):
            continue
        rel = str(f.relative_to(project_dir))
        try:
            files[rel] = f.read_text(errors='replace')
        except Exception:
            pass

    return files


def call_llm_fix(test_output: str, source_files: dict, attempt: int):
    file_blocks = '\n\n'.join(
        f'===FILE: {path}===\n{content}\n===END FILE==='
        for path, content in source_files.items()
    )
    user_msg = (
        f'FAILING TEST OUTPUT (attempt {attempt} of {MAX_ATTEMPTS}):\n'
        f'{test_output}\n\n'
        f'CURRENT PROJECT FILES:\n{file_blocks}'
    )
    payload = {
        'model':      FIX_MODEL,
        'max_tokens': 8192,
        'timeout':    120,
        'messages': [
            {
                'role':    'system',
                'content': (
                    'You are a debugging assistant. Python tests are failing. '
                    'Fix the source files so all tests pass.\n\n'
                    'Output ONLY files that need changes using EXACTLY this format:\n'
                    '===FILE: path/to/file.py===\n[complete corrected content]\n===END FILE===\n\n'
                    'RULES:\n'
                    '- Include ONLY changed files — omit files that need no changes\n'
                    '- Output COMPLETE file contents — never diffs, never truncated\n'
                    '- Fix the root cause shown in the traceback\n'
                    '- If a missing package causes an ImportError, also fix requirements.txt'
                ),
            },
            {'role': 'user', 'content': user_msg},
        ],
    }
    def _call(model):
        p = dict(payload, model=model)
        return http.post(
            f'{LITELLM_URL}/v1/chat/completions',
            headers={'Authorization': f'Bearer {LITELLM_KEY}', 'Content-Type': 'application/json'},
            json=p, timeout=300,
        )

    resp = _call(FIX_MODEL)
    if resp.status_code == 429:
        print(f'  [{FIX_MODEL}] rate-limited, falling back to {CODE_FALLBACK}', flush=True)
        resp = _call(CODE_FALLBACK)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def parse_and_apply_fixes(project_dir: Path, llm_response: str):
    """Parse ===FILE:=== blocks and write changed files (merge — never full replace)."""
    pattern = re.compile(r'===FILE:\s*(.+?)===\n([\s\S]*?)(?====FILE:|===END FILE===|$)')
    applied = []
    for m in pattern.finditer(llm_response):
        rel_path = m.group(1).strip().lstrip('/')
        content  = m.group(2)
        content  = re.sub(r'^```[^\n]*\n', '', content)
        content  = re.sub(r'\n```\s*$', '', content)
        if not content.strip():
            continue
        abs_path = project_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)
        applied.append(rel_path)
    return applied


# ── post-process (inline mirror of scripts/postprocess.js) ────────────────────

_STDLIB = {
    'unittest','os','sys','json','re','math','io','abc','ast','builtins',
    'collections','datetime','functools','hashlib','http','itertools',
    'logging','pathlib','random','shutil','socket','sqlite3','string',
    'subprocess','tempfile','threading','time','traceback','typing',
    'urllib','uuid','warnings','csv','copy','enum','dataclasses',
    'contextlib','base64','struct','queue','signal','argparse','configparser',
    'pickle','pprint','textwrap','glob','fnmatch','heapq','bisect',
    'statistics','decimal','fractions','operator','inspect','types',
    'gc','platform','locale','codecs','html','xml','email','zipfile',
    'tarfile','gzip','bz2','lzma','zlib','atexit','dis','tokenize',
    'calendar','array','mmap','ctypes','select','selectors','asyncio',
    'concurrent','multiprocessing',
}

def run_postprocess(project_dir: Path):
    fixes = []

    # Fix requirements.txt
    req_file = project_dir / 'requirements.txt'
    if req_file.exists():
        lines    = req_file.read_text().split('\n')
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                filtered.append(line)
                continue
            pkg = re.split(r'[=><!\[;~]', stripped)[0].strip().lower().replace('-', '_')
            if pkg in _STDLIB:
                fixes.append(f'requirements.txt: removed stdlib "{stripped}"')
            else:
                filtered.append(line)
        if len(filtered) != len(lines):
            req_file.write_text('\n'.join(filtered))

    # Loosen exact patch pins (pkg==X.Y.Z → pkg>=X.Y) to avoid pip failures when
    # LLM hallucinates non-existent patch versions (e.g. fastapi==2.0.6)
    if req_file.exists():
        lines = req_file.read_text().split('\n')
        new_lines = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                new_lines.append(line)
                continue
            m = re.match(r'^([A-Za-z0-9_\-\.]+)==(\d+\.\d+)(\.\d+.*)?$', stripped)
            if m and m.group(3):  # only loosen if there's a patch component
                pkg_name, major_minor = m.group(1), m.group(2)
                new_lines.append(f'{pkg_name}>={major_minor}')
                fixes.append(f'requirements.txt: loosened {stripped} → {pkg_name}>={major_minor}')
                changed = True
            else:
                new_lines.append(line)
        if changed:
            req_file.write_text('\n'.join(new_lines))

    # Pin flask>=3.0 if flask is present without a version pin >= 3
    # Prevents ImportError from werkzeug.urls.url_quote removal in werkzeug 3.0
    if req_file.exists():
        content = req_file.read_text()
        lines   = content.split('\n')
        new_lines = []
        changed = False
        for line in lines:
            stripped = line.strip()
            pkg = re.split(r'[=><!\[;~]', stripped)[0].strip().lower().replace('-', '_') if stripped else ''
            if pkg == 'flask' and not re.search(r'flask[>=!~]', stripped, re.I):
                new_lines.append('flask>=3.0')
                fixes.append('requirements.txt: pinned flask>=3.0 (werkzeug 3.x compat)')
                changed = True
            else:
                new_lines.append(line)
        if changed:
            req_file.write_text('\n'.join(new_lines))

    # Add conftest.py if tests import from parent
    tests_dir = project_dir / 'tests'
    conftest  = tests_dir / 'conftest.py'
    if tests_dir.exists() and not conftest.exists():
        needs = any(
            re.search(r'^from (?!tests\.|\.)\w+ import|^import (?!tests\.)\w+', f.read_text(), re.M)
            for f in tests_dir.glob('*.py')
        )
        if needs:
            conftest.write_text(
                'import sys, os\n'
                'sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
            )
            fixes.append('tests/conftest.py: created')

    return fixes


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
    report_path  = Path(data.get('report_path',   str(project_base / 'phase3_report.md')))

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
            fixed = call_llm_fix(test_output, src, attempt)
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
    report_path = Path(request.json.get('report_path',  '/data/output/phase3_report.md'))

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
            llm_response = call_llm_fix(test_output, source_files, attempt)
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
