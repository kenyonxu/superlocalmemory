#!/usr/bin/env node
/**
 * Memory List CLI Skill (V3)
 * List recent memories with sorting and filtering
 */

const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

async function memoryListSkill() {
  const args = process.argv.slice(2);

  // Show help if --help
  if (args.includes('--help') || args.includes('-h')) {
    console.log(`
SuperLocalMemory V3 - List Recent Memories

Display recent memories with optional sorting and limits.
Quick overview of what's stored in your memory database.

Usage: memory-list [options]

Options:
  --limit <n>            Number of memories to show (default: 20)
  --sort <field>         Sort by: recent, accessed, importance
  --full                 Show complete content without truncation

Examples:
  memory-list
  memory-list --limit 50
  memory-list --sort importance
  memory-list --limit 10 --sort accessed --full
`);
    return;
  }

  // Build CLI args for V3
  const cliArgs = ['-m', 'superlocalmemory.cli.main', 'list'];

  for (let i = 0; i < args.length; i++) {
    cliArgs.push(args[i]);
  }

  try {
    const { stdout, stderr } = await execFileAsync('python3', cliArgs);

    if (stderr) {
      console.error('Warning:', stderr);
    }

    console.log(stdout);

    // Show helpful next steps
    console.log(`
Next steps:
  - Use \`slm recall <query>\` to search memories
  - Use \`slm remember <content>\` to add new memories
  - Use \`slm list --sort <field>\` to change sort order
`);

  } catch (error) {
    console.error('Error listing memories:', error.message);
    if (error.stdout) console.log(error.stdout);
    if (error.stderr) console.error(error.stderr);
  }
}

memoryListSkill();
