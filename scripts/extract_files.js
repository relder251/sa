#!/usr/bin/env node
/**
 * Parse structured AI executor output and write individual files to disk.
 *
 * Accepts two formats:
 *   1. With END marker:
 *        ===FILE: path/to/file.py===
 *        content
 *        ===END FILE===
 *
 *   2. Without END marker (next ===FILE: or end-of-string terminates):
 *        ===FILE: path/to/file.py===
 *        content
 *        ===FILE: next/file.py===
 *        ...
 *
 * Also strips markdown code fences (```lang ... ```) from file content.
 *
 * Usage:
 *   node extract_files.js <input_md> <output_dir>
 */
const fs   = require('fs');
const path = require('path');

const inputPath = process.argv[2] || '/data/output/execution_output.md';
const outputDir = process.argv[3] || '/data/output/project';

const content = fs.readFileSync(inputPath, 'utf8');

// Match ===FILE: path=== followed by content until next ===FILE:, ===END FILE===, or end
const pattern = /===FILE:\s*(.+?)===\n([\s\S]*?)(?===FILE:|===END FILE===|$)/g;

let match;
const written = [];

while ((match = pattern.exec(content)) !== null) {
  const relPath = match[1].trim().replace(/^\//, '');
  let fileContent = match[2];

  // Strip markdown code fences (```lang\n ... \n```)
  fileContent = fileContent.replace(/^```[^\n]*\n/, '').replace(/\n```\s*$/, '');

  // Skip empty files
  if (!fileContent.trim()) continue;

  const absPath = path.join(outputDir, relPath);
  const parentDir = path.dirname(absPath);

  if (!fs.existsSync(parentDir)) {
    fs.mkdirSync(parentDir, { recursive: true });
  }

  fs.writeFileSync(absPath, fileContent);
  written.push(relPath);
  console.log(`  wrote: ${absPath}`);
}

if (written.length === 0) {
  console.error('No ===FILE:=== blocks found — check AI output format.');
  process.exit(1);
}

const manifestPath = path.join(outputDir, 'MANIFEST.txt');
fs.writeFileSync(manifestPath, written.join('\n') + '\n');
console.log(`  wrote: ${manifestPath}`);
console.log(`\nExtracted ${written.length} file(s) to ${outputDir}`);
