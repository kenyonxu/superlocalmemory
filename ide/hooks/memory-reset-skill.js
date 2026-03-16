#!/usr/bin/env node
/**
 * Memory Reset CLI Skill (V3)
 * Provides /memory-reset command with safety warnings
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

async function memoryResetSkill() {
  const args = process.argv.slice(2);

  // Show help if no args
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log(`
SuperLocalMemory V3 - Reset Commands

Usage: /memory-reset <command> [options]

Commands:
  status              Show current memory system status (SAFE)
  soft                Clear all memories, keep schema (DESTRUCTIVE)
  hard --confirm      Delete everything, reinitialize (NUCLEAR)

Examples:
  /memory-reset status
  /memory-reset soft
  /memory-reset hard --confirm

WARNING: soft/hard operations create automatic backups
         but will delete data. Always check status first!
`);
    rl.close();
    return;
  }

  const command = args[0];

  // STATUS command (safe, no warnings)
  if (command === 'status') {
    try {
      const { stdout } = await execFileAsync('python3', [
        '-m', 'superlocalmemory.cli.main', 'status'
      ]);
      console.log(stdout);
    } catch (error) {
      console.error('Error:', error.message);
    }
    rl.close();
    return;
  }

  // SOFT RESET command (destructive, show warning)
  if (command === 'soft') {
    console.log(`
WARNING: SOFT RESET

This will:
  - Delete ALL memories from current profile
  - Clear graph data (nodes, edges, clusters)
  - Clear learned patterns
  - Create automatic backup before deletion
  - Keep V3 schema structure intact

Backup location: ~/.superlocalmemory/backups/pre-reset-[timestamp].db
`);

    const answer = await question('Proceed with soft reset? (yes/no): ');

    if (answer.toLowerCase() === 'yes') {
      try {
        const { stdout } = await execFileAsync('python3', [
          '-m', 'superlocalmemory.cli.main', 'reset', 'soft'
        ]);
        console.log(stdout);
      } catch (error) {
        console.error('Error:', error.message);
      }
    } else {
      console.log('\nCancelled. No changes made.');
    }

    rl.close();
    return;
  }

  // HARD RESET command (nuclear, extra warnings)
  if (command === 'hard') {
    if (!args.includes('--confirm')) {
      console.log(`
Error: Hard reset requires --confirm flag

HARD RESET is DESTRUCTIVE and will:
  - Delete the ENTIRE database file
  - Remove ALL memories permanently
  - Remove ALL graph data permanently
  - Remove ALL learned patterns permanently

To proceed, use:
  /memory-reset hard --confirm
`);
      rl.close();
      return;
    }

    console.log(`
DANGER: HARD RESET

This will:
  - DELETE the entire database file (memory.db)
  - DESTROY all memories (cannot undo within system)
  - ERASE all graph relationships
  - REMOVE all learned patterns
  - Reinitialize fresh V3 schema

Backup location: ~/.superlocalmemory/backups/pre-reset-[timestamp].db
`);

    const answer = await question('Type "DELETE EVERYTHING" to confirm: ');

    if (answer === 'DELETE EVERYTHING') {
      try {
        const { stdout } = await execFileAsync('python3', [
          '-m', 'superlocalmemory.cli.main', 'reset', 'hard', '--confirm'
        ]);
        console.log(stdout);
      } catch (error) {
        console.error('Error:', error.message);
      }
    } else {
      console.log('\nCancelled. No changes made.');
      console.log('(You must type exactly "DELETE EVERYTHING" to confirm)');
    }

    rl.close();
    return;
  }

  // Unknown command
  console.log(`
Unknown command: ${command}

Valid commands: status, soft, hard

Use: /memory-reset --help for more information
`);
  rl.close();
}

memoryResetSkill();
