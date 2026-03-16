#!/usr/bin/env node
/**
 * Memory Profile CLI Skill (V3)
 * Provides memory-profile commands for managing multiple memory contexts
 */

const { execFile } = require('child_process');
const { promisify } = require('util');
const readline = require('readline');

const execFileAsync = promisify(execFile);

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout
});

const question = (query) => new Promise((resolve) => rl.question(query, resolve));

async function memoryProfileSkill() {
  const args = process.argv.slice(2);

  // Show help if no args
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log(`
SuperLocalMemory V3 - Profile Management

Profiles let you maintain separate memory contexts in ONE database:
  - Work vs Personal projects
  - Different clients or teams
  - Experimentation vs Production

All profiles share one database. Switching is instant and safe.
No data copying, no data loss risk.

Usage: memory-profile <command> [options]

Commands:
  list                List all profiles and show active one
  current             Show current active profile
  create <name>       Create a new empty profile
  switch <name>       Switch to a different profile
  delete <name>       Delete a profile (with confirmation)

Examples:
  memory-profile list
  memory-profile current
  memory-profile create work
  memory-profile switch work
  memory-profile delete old-project
`);
    rl.close();
    return;
  }

  const command = args[0];
  const cliArgs = ['-m', 'superlocalmemory.cli.main', 'profile', ...args];

  // For destructive operations, add confirmation prompts
  if (command === 'delete' && args.length >= 2) {
    const profileName = args[1];

    if (profileName === 'default') {
      console.log('Error: Cannot delete the default profile.');
      rl.close();
      return;
    }

    const answer = await question(`Type the profile name "${profileName}" to confirm deletion: `);

    if (answer !== profileName) {
      console.log('\nCancelled. No changes made.');
      rl.close();
      return;
    }
  }

  if (command === 'switch' && args.length >= 2) {
    const profileName = args[1];
    const answer = await question(`Switch to profile "${profileName}"? (yes/no): `);

    if (answer.toLowerCase() !== 'yes') {
      console.log('\nCancelled. No changes made.');
      rl.close();
      return;
    }
  }

  try {
    const { stdout, stderr } = await execFileAsync('python3', cliArgs);
    if (stdout) console.log(stdout);
    if (stderr) console.error(stderr);
  } catch (error) {
    console.error('Error:', error.message);
    if (error.stdout) console.log(error.stdout);
  }

  rl.close();
}

memoryProfileSkill();
