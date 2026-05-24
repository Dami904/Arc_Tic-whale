import fs from 'node:fs/promises';
import path from 'node:path';

const outFile = path.resolve('static/runtime-config.js');

const runtimeConfig = {
  API_BASE: process.env.API_BASE || '',
  PRIVY_APP_ID: process.env.PRIVY_APP_ID || '',
  PRIVY_CLIENT_ID: process.env.PRIVY_CLIENT_ID || '',
  PRIVY_AUTH_ORIGIN: process.env.PRIVY_AUTH_ORIGIN || '',
  WC_PROJECT_ID: process.env.WC_PROJECT_ID || '',
};

const contents = `window.API_BASE = ${JSON.stringify(runtimeConfig.API_BASE)};
window.PRIVY_APP_ID = ${JSON.stringify(runtimeConfig.PRIVY_APP_ID)};
window.PRIVY_CLIENT_ID = ${JSON.stringify(runtimeConfig.PRIVY_CLIENT_ID)};
window.PRIVY_AUTH_ORIGIN = ${JSON.stringify(runtimeConfig.PRIVY_AUTH_ORIGIN)};
window.WC_PROJECT_ID = ${JSON.stringify(runtimeConfig.WC_PROJECT_ID)};
`;

await fs.writeFile(outFile, contents, 'utf8');
