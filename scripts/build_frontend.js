const path = require('path');
const nodeModulesPath = path.resolve(__dirname, '..', 'shopsphere', 'frontend', 'node_modules');
module.paths.push(nodeModulesPath);

const esbuild = require('esbuild');

async function build() {
  console.log('Building React frontend bundle with esbuild (resolved via local node_modules)...');
  try {
    const result = await esbuild.build({
      entryPoints: ['app/static/src/index.jsx'],
      bundle: true,
      outfile: 'app/static/app.js',
      format: 'iife',
      globalName: 'VerityQAApp',
      nodePaths: [nodeModulesPath],
      loader: {
        '.js': 'jsx',
        '.jsx': 'jsx',
      },
      minify: false,
      sourcemap: false,
    });
    console.log('Build completed successfully: app/static/app.js');
  } catch (err) {
    console.error('Build failed:', err);
    process.exit(1);
  }
}

build();
