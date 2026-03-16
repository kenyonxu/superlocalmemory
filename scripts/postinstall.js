#!/usr/bin/env node
/**
 * SuperLocalMemory V3 - NPM Postinstall Script
 *
 * Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
 * Licensed under MIT License
 * Repository: https://github.com/qualixar/superlocalmemory
 */

const { spawnSync } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');

console.log('\n════════════════════════════════════════════════════════════');
console.log('  SuperLocalMemory V3 - Post-Installation');
console.log('  by Varun Pratap Bhardwaj / Qualixar');
console.log('  https://github.com/qualixar/superlocalmemory');
console.log('════════════════════════════════════════════════════════════\n');

// --- Step 1: Create data directory ---
const SLM_HOME = path.join(os.homedir(), '.superlocalmemory');
if (!fs.existsSync(SLM_HOME)) {
    fs.mkdirSync(SLM_HOME, { recursive: true });
    console.log('✓ Created data directory: ' + SLM_HOME);
} else {
    console.log('✓ Data directory exists: ' + SLM_HOME);
}

// --- Step 2: Find Python 3 ---
function findPython() {
    const candidates = ['python3', 'python', '/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3'];
    if (os.platform() === 'win32') candidates.push('py -3');
    for (const cmd of candidates) {
        try {
            const parts = cmd.split(' ');
            const r = spawnSync(parts[0], [...parts.slice(1), '--version'], { stdio: 'pipe', timeout: 5000 });
            if (r.status === 0 && (r.stdout || '').toString().includes('3.')) return cmd;
        } catch (e) { /* next */ }
    }
    return null;
}

const python = findPython();
if (!python) {
    console.log('⚠ Python 3.11+ not found. Install from https://python.org/downloads/');
    console.log('  After installing Python, run: slm setup');
    process.exit(0); // Don't fail npm install
}
console.log('✓ Found Python: ' + python);

// --- Step 3: Install Python dependencies ---
console.log('\nInstalling Python dependencies...');
const PKG_ROOT = path.join(__dirname, '..');
const requirementsFile = path.join(PKG_ROOT, 'src', 'superlocalmemory', 'requirements-install.txt');

// Create a minimal requirements file for core operation
const coreDeps = [
    'numpy>=1.26.0',
    'scipy>=1.12.0',
    'networkx>=3.0',
    'httpx>=0.24.0',
    'python-dateutil>=2.9.0',
    'rank-bm25>=0.2.2',
    'vaderSentiment>=3.3.2',
].join('\n');

// Write temp requirements file
const tmpReq = path.join(SLM_HOME, '.install-requirements.txt');
fs.writeFileSync(tmpReq, coreDeps);

const pythonParts = python.split(' ');
const pipResult = spawnSync(pythonParts[0], [
    ...pythonParts.slice(1), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
    '-r', tmpReq,
], { stdio: 'inherit', timeout: 120000 });

if (pipResult.status === 0) {
    console.log('✓ Core Python dependencies installed');
} else {
    console.log('⚠ Some Python dependencies failed to install.');
    console.log('  Run manually: ' + python + ' -m pip install numpy scipy networkx httpx python-dateutil rank-bm25 vaderSentiment');
}

// Clean up temp file
try { fs.unlinkSync(tmpReq); } catch (e) { /* ok */ }

// --- Step 4: Optional search dependencies (sentence-transformers) ---
console.log('\nChecking optional search dependencies...');
const stCheck = spawnSync(pythonParts[0], [
    ...pythonParts.slice(1), '-c', 'import sentence_transformers; print("ok")',
], { stdio: 'pipe', timeout: 10000 });

if (stCheck.status === 0) {
    console.log('✓ sentence-transformers already installed (semantic search enabled)');
} else {
    console.log('ℹ sentence-transformers not installed (BM25-only search mode)');
    console.log('  For semantic search, run: ' + python + ' -m pip install sentence-transformers');
    console.log('  This downloads ~1.5GB of ML models on first use.');
}

// --- Step 5: Detect V2 installation ---
const V2_HOME = path.join(os.homedir(), '.claude-memory');
if (fs.existsSync(V2_HOME) && fs.existsSync(path.join(V2_HOME, 'memory.db'))) {
    console.log('');
    console.log('╔══════════════════════════════════════════════════════════╗');
    console.log('║  V2 Installation Detected                                ║');
    console.log('╚══════════════════════════════════════════════════════════╝');
    console.log('');
    console.log('  Found V2 data at: ' + V2_HOME);
    console.log('  Your memories are safe and will NOT be deleted.');
    console.log('');
    console.log('  To migrate V2 data to V3, run:');
    console.log('    slm migrate');
    console.log('');
}

// --- Done ---
console.log('════════════════════════════════════════════════════════════');
console.log('  ✓ SuperLocalMemory V3 installed successfully!');
console.log('');
console.log('  Quick start:');
console.log('    slm setup          # First-time configuration');
console.log('    slm status         # Check system status');
console.log('    slm remember "..." # Store a memory');
console.log('    slm recall "..."   # Search memories');
console.log('');
console.log('  Documentation: https://github.com/qualixar/superlocalmemory/wiki');
console.log('════════════════════════════════════════════════════════════\n');
