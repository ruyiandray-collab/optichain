import { build, preview } from 'vite';
// Rollup watch avoids dependency prebundling's parent-directory probes in this sandbox.
// All generated files stay in web/dist. Refresh the browser after a rebuild.
const watcher = await build({ configFile: false, build: { watch: {} } });
let server;
watcher.on('event', async event => {
  if (event.code === 'END' && !server) {
    server = await preview({ configFile: false, preview: { host: '127.0.0.1', port: 5173 } });
    server.printUrls();
  }
});
async function stop() { await watcher.close(); server?.httpServer.close(); process.exit(); }
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
