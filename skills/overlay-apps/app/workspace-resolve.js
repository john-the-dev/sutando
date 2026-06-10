'use strict';

// Canonical workspace-directory resolution for the overlay-apps Electron skill.
// CommonJS wrapper — mirrors workspace_default.ts contract but can be require()'d
// by the CJS Electron main and control-server processes.

const os = require('os');
const path = require('path');

function resolveWorkspace() {
  const env = process.env.SUTANDO_WORKSPACE;
  if (env) {
    return path.resolve(env.replace(/^~(?=$|\/)/, os.homedir()));
  }
  return path.join(os.homedir(), '.sutando', 'workspace');
}

module.exports = { resolveWorkspace };
