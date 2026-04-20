#!/usr/bin/env node
/**
 * SuperLocalMemory v3.4.21 — slm-hook binary fetcher.
 *
 * LLD reference: .backup/active-brain/lld/LLD-06-windows-binary-and-legacy-migration.md §6.2
 *
 * Fails open: any network / SHA / unpack failure logs a warning and
 * exits 0. The dispatcher (bin/slm) falls back to the Python path so
 * the hook still works — just slower on Windows.
 *
 * Stdlib-only (node:https, node:crypto, node:fs, node:path, node:os).
 */
'use strict';

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const https = require('node:https');

const SLM_HOME = path.join(os.homedir(), '.superlocalmemory');
const BIN_DIR = path.join(SLM_HOME, 'bin');
const MANIFEST_BASE_URL = process.env.SLM_MANIFEST_BASE_URL ||
    'https://github.com/qualixar/superlocalmemory/releases/download';

// Map Node os.platform() / os.arch() -> canonical manifest tuple.
const PLATFORM_MAP = {
    'darwin': 'macos',
    'linux': 'linux',
    'win32': 'windows',
};
const ARCH_MAP = {
    'arm64': 'arm64',
    'x64': 'x86_64',
    'x86_64': 'x86_64',
};

function canonicalPlatformArch(platform, arch) {
    const p = PLATFORM_MAP[platform] || null;
    const a = ARCH_MAP[arch] || null;
    return (p && a) ? { platform: p, arch: a } : null;
}

function logInfo(msg) { console.log(`[slm-hook] ${msg}`); }
function logWarn(msg) { console.warn(`[slm-hook] ${msg}`); }

// S8-SEC-03: redirect host allow-list. Previously any 3xx ``Location``
// header would be followed blindly, so a compromised upstream (DNS
// hijack, typosquat in the package registry) could redirect the postinstall
// download to an attacker-controlled host and substitute the hook binary.
// We only follow redirects back to GitHub's own release CDN.
const ALLOWED_REDIRECT_HOSTS = new Set([
    'github.com',
    'api.github.com',
    'codeload.github.com',
    'objects.githubusercontent.com',
    'raw.githubusercontent.com',
    'release-assets.githubusercontent.com',
]);

function redirectIsAllowed(rawLocation) {
    if (typeof rawLocation !== 'string' || rawLocation.length === 0) return false;
    try {
        const parsed = new URL(rawLocation);
        if (parsed.protocol !== 'https:') return false;
        return ALLOWED_REDIRECT_HOSTS.has(parsed.hostname);
    } catch (_err) {
        return false;
    }
}

function fetchJson(url) {
    return new Promise((resolve, reject) => {
        const req = https.get(url, (res) => {
            if (res.statusCode && res.statusCode >= 300 &&
                res.statusCode < 400 && res.headers.location) {
                if (!redirectIsAllowed(res.headers.location)) {
                    reject(new Error('redirect host not allow-listed'));
                    return;
                }
                // Single redirect hop — GitHub releases use redirects.
                fetchJson(res.headers.location).then(resolve, reject);
                return;
            }
            if (res.statusCode !== 200) {
                reject(new Error(`HTTP ${res.statusCode} for ${url}`));
                return;
            }
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => {
                try {
                    resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
                } catch (err) {
                    reject(err);
                }
            });
        });
        req.on('error', reject);
        req.setTimeout(15000, () => {
            req.destroy(new Error('timeout fetching manifest'));
        });
    });
}

function downloadFile(url, destPath) {
    return new Promise((resolve, reject) => {
        const out = fs.createWriteStream(destPath);
        const req = https.get(url, (res) => {
            if (res.statusCode && res.statusCode >= 300 &&
                res.statusCode < 400 && res.headers.location) {
                if (!redirectIsAllowed(res.headers.location)) {
                    out.close();
                    fs.unlink(destPath, () => {});
                    reject(new Error('redirect host not allow-listed'));
                    return;
                }
                out.close();
                fs.unlink(destPath, () => {
                    downloadFile(res.headers.location, destPath)
                        .then(resolve, reject);
                });
                return;
            }
            if (res.statusCode !== 200) {
                out.close();
                fs.unlink(destPath, () => {});
                reject(new Error(`HTTP ${res.statusCode} for ${url}`));
                return;
            }
            res.pipe(out);
            out.on('finish', () => out.close(resolve));
        });
        req.on('error', (err) => {
            out.close();
            fs.unlink(destPath, () => {});
            reject(err);
        });
        req.setTimeout(60000, () => {
            req.destroy(new Error('timeout downloading asset'));
        });
    });
}

async function sha256File(filePath) {
    const h = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    return new Promise((resolve, reject) => {
        stream.on('data', (c) => h.update(c));
        stream.on('end', () => resolve(h.digest('hex')));
        stream.on('error', reject);
    });
}

function pickAsset(manifest, platform, arch) {
    if (!manifest || !Array.isArray(manifest.assets)) return null;
    // Prefer setup.exe on Windows (Inno Setup) when present, fall back
    // to plain archive.
    const matches = manifest.assets.filter(a =>
        a.platform === platform && a.arch === arch);
    if (matches.length === 0) return null;
    // Deterministic ordering: setup.exe > .zip > .tar.gz
    matches.sort((a, b) => {
        const rank = (n) => n.endsWith('setup.exe') ? 0 :
            n.endsWith('.zip') ? 1 : 2;
        return rank(a.name) - rank(b.name);
    });
    return matches[0];
}

async function main() {
    try {
        const pkgJson = require(path.join(__dirname, '..', 'package.json'));
        const version = pkgJson.version;
        const url = `${MANIFEST_BASE_URL}/v${version}/manifest.json`;

        const pa = canonicalPlatformArch(os.platform(), os.arch());
        if (!pa) {
            logWarn(`unsupported platform ${os.platform()}/${os.arch()}; ` +
                    'Python fallback');
            return 0;
        }

        let manifest;
        try {
            manifest = await fetchJson(url);
        } catch (err) {
            logWarn(`manifest fetch failed: ${err.message}; Python fallback`);
            return 0;
        }

        const asset = pickAsset(manifest, pa.platform, pa.arch);
        if (!asset) {
            logWarn(`no prebuilt binary for ${pa.platform}/${pa.arch}; ` +
                    'Python fallback');
            return 0;
        }

        // S8-SEC-01 fix: the manifest is remote-attacker-controllable (even
        // when minisigned, the signer is a release process we don't fully
        // trust at the per-asset level). Reject any ``asset.name`` that
        // contains a path separator, traversal segments, or is empty —
        // and re-derive the safe filename from basename before joining.
        // Without this, a poisoned manifest can write arbitrary paths
        // (e.g., "../../.ssh/authorized_keys") even though SHA-256 validates
        // the bytes, because SHA only binds the content to the claimed name.
        const rawName = typeof asset.name === 'string' ? asset.name : '';
        if (!rawName
            || rawName.includes('/')
            || rawName.includes('\\')
            || rawName.includes('\0')
            || rawName === '.'
            || rawName === '..'
            || rawName.startsWith('.')) {
            logWarn(`rejected asset name (path-traversal guard): ` +
                    `${JSON.stringify(rawName)}; Python fallback`);
            return 0;
        }
        const safeName = path.basename(rawName);
        if (safeName !== rawName) {
            logWarn(`asset name changed under basename sanitisation: ` +
                    `${JSON.stringify(rawName)} -> ${JSON.stringify(safeName)}; ` +
                    'Python fallback');
            return 0;
        }

        await fsp.mkdir(BIN_DIR, { recursive: true });
        const tmpName = `${safeName}.part`;
        const tmpPath = path.join(BIN_DIR, tmpName);
        try {
            await downloadFile(asset.url, tmpPath);
        } catch (err) {
            logWarn(`download failed: ${err.message}; Python fallback`);
            await fsp.unlink(tmpPath).catch(() => {});
            return 0;
        }

        const actual = await sha256File(tmpPath);
        if (actual !== asset.sha256) {
            await fsp.unlink(tmpPath).catch(() => {});
            logWarn(`SHA-256 mismatch for ${asset.name}: expected ` +
                    `${asset.sha256}, got ${actual}; Python fallback`);
            return 0;
        }

        const finalPath = path.join(BIN_DIR, safeName);
        // Belt-and-suspenders: after resolving the final path, assert it
        // sits under BIN_DIR. ``path.resolve`` normalises any '..' that
        // could have slipped through; if the result escapes BIN_DIR
        // (which it shouldn't after basename sanitisation, but it's
        // cheap insurance), refuse to install.
        const resolvedFinal = path.resolve(finalPath);
        const resolvedBin = path.resolve(BIN_DIR);
        if (!resolvedFinal.startsWith(resolvedBin + path.sep)
            && resolvedFinal !== resolvedBin) {
            await fsp.unlink(tmpPath).catch(() => {});
            logWarn(`path escape detected: ${resolvedFinal} not under ` +
                    `${resolvedBin}; Python fallback`);
            return 0;
        }
        await fsp.rename(tmpPath, finalPath);
        logInfo(`✓ slm-hook asset fetched: ${asset.name} ` +
                `(${asset.signing})`);
        logInfo(`  path: ${finalPath}`);
        logInfo('  Note: unpack step is platform-specific; ' +
                'the installer runs it.');
        return 0;
    } catch (err) {
        logWarn(`unexpected error: ${err.message}; Python fallback`);
        return 0;
    }
}

// Exports for testing; direct execution runs main().
module.exports = {
    canonicalPlatformArch,
    pickAsset,
    sha256File,
    MANIFEST_BASE_URL,
    main,
};

if (require.main === module) {
    main().then((code) => process.exit(code || 0),
                () => process.exit(0));
}
