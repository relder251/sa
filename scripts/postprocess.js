#!/usr/bin/env node
/**
 * Post-extraction auto-fixes for common AI generation issues.
 *
 * Fixes:
 *   1. requirements.txt — removes Python stdlib modules (not installable via pip)
 *   2. tests/conftest.py — creates it if test files import from the parent package
 *
 * Usage: node postprocess.js <project_dir>
 * Exit: always 0
 */
const fs   = require('fs');
const path = require('path');

const projectDir = process.argv[2] || '/data/output/project';
const fixes = [];

// ── Python stdlib modules that have no PyPI distribution ─────────────────────
const STDLIB = new Set([
  'unittest','os','sys','json','re','math','io','abc','ast','builtins',
  'collections','datetime','functools','hashlib','http','itertools',
  'logging','pathlib','random','shutil','socket','sqlite3','string',
  'subprocess','tempfile','threading','time','traceback','typing',
  'urllib','uuid','warnings','weakref','csv','copy','enum','dataclasses',
  'contextlib','base64','struct','queue','signal','argparse','configparser',
  'pickle','pprint','textwrap','glob','fnmatch','heapq','bisect',
  'statistics','decimal','fractions','operator','inspect','types',
  'gc','platform','locale','codecs','html','xml','email','ftplib',
  'imaplib','smtplib','telnetlib','xmlrpc','zipfile','tarfile','gzip',
  'bz2','lzma','zlib','atexit','cProfile','profile','timeit','trace',
  'dis','tokenize','token','keyword','difflib','fileinput','filecmp',
  'tempfile','calendar','array','mmap','ctypes','select','selectors',
  'asyncio','concurrent','multiprocessing','pty','tty','termios','fcntl',
  'readline','rlcompleter','curses',
]);

// ── Fix 1: requirements.txt ───────────────────────────────────────────────────
const reqPath = path.join(projectDir, 'requirements.txt');
if (fs.existsSync(reqPath)) {
  const original = fs.readFileSync(reqPath, 'utf8');
  const lines = original.split('\n');
  const filtered = lines.filter(line => {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith('#')) return true; // keep blanks/comments
    // Extract base package name (before ==, >=, <=, !=, ~=, [, ;)
    const pkg = stripped.split(/[=><!\[;~]/)[0].trim().toLowerCase().replace(/-/g, '_');
    if (STDLIB.has(pkg)) {
      fixes.push(`requirements.txt: removed stdlib entry "${stripped}"`);
      return false;
    }
    return true;
  });
  if (filtered.length !== lines.length) {
    fs.writeFileSync(reqPath, filtered.join('\n'));
  }
}

// ── Fix 2: tests/conftest.py ──────────────────────────────────────────────────
const testsDir  = path.join(projectDir, 'tests');
const conftest  = path.join(testsDir, 'conftest.py');

if (fs.existsSync(testsDir) && !fs.existsSync(conftest)) {
  const testFiles = fs.readdirSync(testsDir).filter(f => f.endsWith('.py'));
  const needsConftest = testFiles.some(file => {
    const src = fs.readFileSync(path.join(testsDir, file), 'utf8');
    // Bare (non-relative, non-tests-prefixed) import → likely from project root
    return /^from (?!tests\.|\.)\w+ import|^import (?!tests\.)[\w]+/m.test(src);
  });
  if (needsConftest) {
    fs.writeFileSync(
      conftest,
      'import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
    );
    fixes.push('tests/conftest.py: created (added project root to sys.path)');
  }
}

// ── Report ────────────────────────────────────────────────────────────────────
if (fixes.length === 0) {
  console.log('Post-process: no fixes needed.');
} else {
  console.log(`Post-process applied ${fixes.length} fix(es):`);
  fixes.forEach(f => console.log(`  - ${f}`));
}
