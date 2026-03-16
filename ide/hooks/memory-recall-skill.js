#!/usr/bin/env node
/**
 * Memory Recall CLI Skill (V3)
 * Search and retrieve memories with advanced filtering
 */

const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

async function memoryRecallSkill() {
  const args = process.argv.slice(2);

  // Show help if no args or --help
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log(`
SuperLocalMemory V3 - Recall (Search Memories)

Search your memory store using hybrid retrieval (semantic, BM25, graph, temporal).

Usage: memory-recall <query> [options]

Arguments:
  <query>                 Search query (required)

Options:
  --limit <n>            Maximum results to return (default: 10)
  --full                 Show complete content without truncation

Examples:
  memory-recall "authentication bug"
  memory-recall "API configuration" --limit 5
  memory-recall "security best practices" --full
`);
    return;
  }

  // Build V3 CLI args
  const cliArgs = ['-m', 'superlocalmemory.cli.main', 'recall'];

  for (let i = 0; i < args.length; i++) {
    cliArgs.push(args[i]);
  }

  try {
    const { stdout, stderr } = await execFileAsync('python3', cliArgs);

    if (stderr) {
      console.error('Warning:', stderr);
    }

    console.log(stdout);

  } catch (error) {
    console.error('Error searching memories:', error.message);
    if (error.stdout) console.log(error.stdout);
    if (error.stderr) console.error(error.stderr);
  }
}

memoryRecallSkill();
