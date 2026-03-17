#!/usr/bin/env node
/**
 * Validate AI executor output before file extraction.
 * Checks for structural issues that produce broken or incomplete code.
 *
 * Always exits 0. Outputs compact JSON to stdout:
 *   {"valid":true,"issues":[],"file_count":N}
 *   {"valid":false,"issues":["..."],"file_count":N}
 *
 * Usage: node validate_format.js <input_md>
 */
const fs = require('fs');

const inputPath = process.argv[2] || '/data/output/execution_output.md';

let content;
try {
  content = fs.readFileSync(inputPath, 'utf8');
} catch (e) {
  console.log(JSON.stringify({ valid: false, issues: [`Could not read file: ${e.message}`], file_count: 0 }));
  process.exit(0);
}

const issues = [];

// Check 1: Must have at least one FILE block
const fileBlocks = content.match(/===FILE:\s*.+?===/g);
const fileCount = fileBlocks ? fileBlocks.length : 0;
if (fileCount === 0) {
  issues.push('No ===FILE:=== blocks found — AI did not use the required output format');
}

// Check 2: Diff / merge conflict markers
const diffMarkers = ['<<<<<<< SEARCH', '<<<<<<< HEAD', '>>>>>>> REPLACE', '>>>>>>> '];
const hasDiff = diffMarkers.some(m => content.includes(m));
if (hasDiff) {
  issues.push('Diff/merge conflict markers detected — AI used patch format instead of complete files');
}

// Check 3: Truncation placeholders
const truncationPatterns = [
  /# rest of (the )?(implementation|code|file)/i,
  /\/\/ rest of (the )?(implementation|code|file)/i,
  /# \.\.\..*rest/i,
  /# TODO: implement/i,
  /\[rest of (the )?(implementation|code)\]/i,
  /\.\.\. (existing|rest of|more) (code|implementation)/i,
];
const hasTruncation = truncationPatterns.some(p => p.test(content));
if (hasTruncation) {
  issues.push('Truncated content detected — AI used placeholder comments instead of full implementation');
}

console.log(JSON.stringify({ valid: issues.length === 0, issues, file_count: fileCount }));
