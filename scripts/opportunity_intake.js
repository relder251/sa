#!/usr/bin/env node
/**
 * Parse an opportunity JSON file, create a project-specific output directory,
 * and move the file from pending/ to running/.
 *
 * Opportunity file format:
 *   { "name": "my-project", "prompt": "Build a ..." }
 *
 * Usage: node opportunity_intake.js <file_path>
 * Output: JSON to stdout with all paths the workflow needs
 */
const fs   = require('fs');
const path = require('path');

const filePath = process.argv[2];
if (!filePath) {
  console.error('Usage: opportunity_intake.js <file_path>');
  process.exit(1);
}

// Guard: file may have already been claimed by a concurrent execution
if (!fs.existsSync(filePath)) {
  console.log(JSON.stringify({ skip: true, reason: 'File already claimed by another execution' }));
  process.exit(0);
}

let opportunity;
try {
  opportunity = JSON.parse(fs.readFileSync(filePath, 'utf8'));
} catch (e) {
  console.error(`Invalid JSON in ${filePath}: ${e.message}`);
  process.exit(1);
}

const { name, prompt } = opportunity;
if (!name || !prompt) {
  console.error('Opportunity file must have "name" and "prompt" fields');
  process.exit(1);
}

// Sanitise name for use as a directory name
const safeName  = name.toLowerCase().replace(/[^a-z0-9-_]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
const projectBase      = `/data/output/projects/${safeName}`;
const planPath         = `${projectBase}/project_plan.md`;
const codeOutputPath   = `${projectBase}/execution_output.md`;
const projectDir       = `${projectBase}/project`;
const reportPath       = `${projectBase}/phase4_report.md`;

// Create output directories
fs.mkdirSync(projectDir, { recursive: true });

// Move opportunity file from pending/ to running/
// Works for both /data/opportunities/pending/ and /data/output/opportunities/pending/
const runningDir  = path.join(path.dirname(path.dirname(filePath)), 'running');
fs.mkdirSync(runningDir, { recursive: true });
const runningPath = path.join(runningDir, path.basename(filePath));

// Write a fresh file (owned by this process) then delete the pending original.
// This avoids EACCES when the pending file was created by a different user (e.g. root).
fs.writeFileSync(runningPath, JSON.stringify(
  { ...opportunity, status: 'running', startedAt: new Date().toISOString() },
  null, 2
));
fs.unlinkSync(filePath);

console.log(JSON.stringify({
  name: safeName,
  prompt,
  projectBase,
  planPath,
  codeOutputPath,
  projectDir,
  reportPath,
  runningPath,
}));
