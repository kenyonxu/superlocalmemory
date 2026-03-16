#!/usr/bin/env node
/**
 * Memory Remember CLI Skill (V3)
 * Save memories with tags, project context, and importance levels
 */

const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

async function memoryRememberSkill() {
  const args = process.argv.slice(2);

  // Show help if no args or --help
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log(`
SuperLocalMemory V3 - Remember (Save Memory)

Save important information to your persistent memory store.
Memories are indexed, searchable, and integrated with the knowledge graph.

Usage: memory-remember <content> [options]

Arguments:
  <content>               The memory content to save (required)

Options:
  --tags <tag1,tag2>      Comma-separated tags for categorization
  --project <path>        Project path context

Examples:
  memory-remember "API key stored in .env file" --tags security,config
  memory-remember "User prefers tabs over spaces"
  memory-remember "Bug in auth.js line 42" --project ~/work/app --tags bug
`);
    return;
  }

  // Build V3 CLI args
  const cliArgs = ['-m', 'superlocalmemory.cli.main', 'remember'];

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
    console.log('Next steps:');
    console.log('  - Use `slm recall <query>` to search this memory');
    console.log('  - Use `slm list` to see recent memories');

  } catch (error) {
    console.error('Error saving memory:', error.message);
    if (error.stdout) console.log(error.stdout);
    if (error.stderr) console.error(error.stderr);
  }
}

memoryRememberSkill();
