#!/usr/bin/env node
/**
 * Dashboard Build Script
 * Uses esbuild for bundling and minification
 *
 * Usage:
 *   node build.js           # Production build (minified)
 *   node build.js --dev     # Development build (no minification, sourcemaps)
 *   node build.js --watch   # Watch mode for development
 */

const esbuild = require('esbuild');
const path = require('path');
const fs = require('fs');

// Parse command line arguments
const args = process.argv.slice(2);
const isDev = args.includes('--dev');
const isWatch = args.includes('--watch');

// Paths
const srcDir = path.join(__dirname, 'src');
const distDir = path.join(__dirname, 'dist');
const cssDir = path.join(__dirname, 'css');

// Clean and ensure dist directory exists
// This prevents old chunks from accumulating
if (fs.existsSync(distDir)) {
    // Remove old JS files (keep structure for future)
    const oldFiles = fs.readdirSync(distDir);
    for (const file of oldFiles) {
        if (file.endsWith('.js') || file.endsWith('.css') || file === 'manifest.json') {
            fs.unlinkSync(path.join(distDir, file));
        }
    }
    // Clean chunks directory
    const chunksDir = path.join(distDir, 'chunks');
    if (fs.existsSync(chunksDir)) {
        fs.rmSync(chunksDir, { recursive: true });
    }
} else {
    fs.mkdirSync(distDir, { recursive: true });
}

// =============================================================================
// JAVASCRIPT BUILD
// =============================================================================

const jsBuildConfig = {
    entryPoints: [path.join(srcDir, 'main.js')],
    bundle: true,
    outdir: distDir,
    entryNames: isDev ? 'bundle' : 'bundle.min',
    format: 'esm',
    target: ['es2020', 'chrome90', 'firefox88', 'safari14'],
    platform: 'browser',
    minify: !isDev,
    sourcemap: isDev ? 'inline' : false,
    treeShaking: true,
    // Code splitting for lazy-loaded modules
    splitting: true,
    chunkNames: 'chunks/[name]-[hash]',
    metafile: true,
    // External dependencies (loaded from CDN)
    external: [],
    // Strip console.* calls in production builds
    drop: isDev ? [] : ['console'],
    define: {
        'process.env.NODE_ENV': isDev ? '"development"' : '"production"'
    },
    banner: {
        js: `/* RDE Dashboard - Built ${new Date().toISOString()} */`
    }
};

// =============================================================================
// CSS BUILD
// =============================================================================

async function buildCSS() {
    const cssPath = path.join(cssDir, 'main.css');
    const outPath = isDev ? path.join(distDir, 'bundle.css') : path.join(distDir, 'bundle.min.css');

    if (!fs.existsSync(cssPath)) {
        console.log('CSS file not found, skipping CSS build');
        return;
    }

    try {
        const result = await esbuild.build({
            entryPoints: [cssPath],
            bundle: true,
            outfile: outPath,
            minify: !isDev,
            sourcemap: isDev ? 'inline' : false,
            loader: { '.css': 'css' }
        });
        console.log(`CSS built: ${outPath}`);
        return result;
    } catch (err) {
        console.error('CSS build failed:', err);
        throw err;
    }
}

// =============================================================================
// BUILD FUNCTION
// =============================================================================

async function build() {
    const startTime = Date.now();
    console.log(`\nBuilding dashboard (${isDev ? 'development' : 'production'})...\n`);

    try {
        // Build JavaScript
        const jsResult = await esbuild.build(jsBuildConfig);
        console.log(`JS built: ${jsBuildConfig.outdir}`);

        // Log bundle size
        if (jsResult.metafile) {
            const outputs = jsResult.metafile.outputs;
            for (const [file, info] of Object.entries(outputs)) {
                const size = (info.bytes / 1024).toFixed(1);
                console.log(`  ${path.basename(file)}: ${size} KB`);
            }
        }

        // Build CSS
        await buildCSS();

        // Generate manifest for cache busting
        const manifest = {
            buildTime: new Date().toISOString(),
            files: {
                js: isDev ? 'bundle.js' : 'bundle.min.js',
                css: isDev ? 'bundle.css' : 'bundle.min.css'
            }
        };
        fs.writeFileSync(
            path.join(distDir, 'manifest.json'),
            JSON.stringify(manifest, null, 2)
        );

        const elapsed = Date.now() - startTime;
        console.log(`\nBuild completed in ${elapsed}ms\n`);

        return jsResult;
    } catch (err) {
        console.error('Build failed:', err);
        process.exit(1);
    }
}

// =============================================================================
// WATCH MODE
// =============================================================================

async function watch() {
    console.log('Starting watch mode...\n');

    // Create context for watching
    const ctx = await esbuild.context({
        ...jsBuildConfig,
        entryNames: 'bundle', // Use non-minified for dev
        minify: false,
        sourcemap: 'inline'
    });

    // Start watching
    await ctx.watch();
    console.log('Watching for changes...');

    // Handle Ctrl+C
    process.on('SIGINT', async () => {
        console.log('\nStopping watch mode...');
        await ctx.dispose();
        process.exit(0);
    });
}

// =============================================================================
// MAIN
// =============================================================================

if (isWatch) {
    watch();
} else {
    build();
}
