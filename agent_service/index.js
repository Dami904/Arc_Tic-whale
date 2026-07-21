import { config } from 'dotenv';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
const __dirname = dirname(fileURLToPath(import.meta.url));
// Load from root .env (one level up from agent_service/)
config({ path: resolve(__dirname, '../.env') });
import express from 'express';
import cors from 'cors';
import { v4 as uuidv4 } from 'uuid';
import { initiateDeveloperControlledWalletsClient } from '@circle-fin/developer-controlled-wallets';

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.AGENT_SERVICE_PORT || 3001;
const DRY_RUN = process.env.TRADE_DRY_RUN === 'true';
const AGENT_SERVICE_SECRET = process.env.AGENT_SERVICE_SECRET || '';
const CIRCLE_SELLER_ADDRESS = process.env.CIRCLE_SELLER_ADDRESS || '';
const X402_FACILITATOR_URL = process.env.X402_FACILITATOR_URL || 'https://x402.org/facilitator';
const PYTHON_BACKEND_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8765';

function requireSecret(req, res, next) {
  if (!AGENT_SERVICE_SECRET) return next();
  if (req.headers['x-agent-secret'] !== AGENT_SERVICE_SECRET) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

// x402 payment middleware - only active when CIRCLE_SELLER_ADDRESS is configured
if (CIRCLE_SELLER_ADDRESS) {
  import('@circle-fin/x402-batching').then(({ paymentMiddleware }) => {
    app.use(paymentMiddleware(
      CIRCLE_SELLER_ADDRESS,
      {
        'POST /wallets':            { price: '$0.10', network: 'base-sepolia' },
        'POST /signals':            { price: '$0.05', network: 'base-sepolia' },
        'GET /wallets/:id/balance': { price: '$0.01', network: 'base-sepolia' },
      },
      { url: X402_FACILITATOR_URL }
    ));
    console.log(`[circle-agent] x402 payment middleware active - seller: ${CIRCLE_SELLER_ADDRESS}`);
  }).catch(err => {
    console.warn(`[circle-agent] x402-batching unavailable - payment middleware disabled: ${err.message}`);
  });
}

app.use('/wallets', requireSecret);

// Initialise Circle client once at startup
let circleClient = null;
try {
  if (process.env.CIRCLE_API_KEY && process.env.CIRCLE_ENTITY_SECRET) {
    circleClient = initiateDeveloperControlledWalletsClient({
      apiKey: process.env.CIRCLE_API_KEY,
      entitySecret: process.env.CIRCLE_ENTITY_SECRET,
    });
    console.log('[circle-agent] Circle client initialised');
  } else {
    console.warn('[circle-agent] CIRCLE_API_KEY or CIRCLE_ENTITY_SECRET missing - running unconfigured');
  }
} catch (err) {
  console.error('[circle-agent] Circle client init failed:', err.message);
}

// ── GET /health ──────────────────────────────────────────────────────────────
app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    circle_configured: !!circleClient,
    dry_run: DRY_RUN,
    version: '1.0.0',
  });
});

// ── POST /wallets ─────────────────────────────────────────────────────────────
// Body: { name, blockchain?, policy?: { dailyLimit, maxPerTx, monthlyLimit } }
// Returns: { wallet_id, address, policy_attached }
app.post('/wallets', async (req, res) => {
  const { name, blockchain = 'ARC-TESTNET', policy } = req.body;

  if (!name) return res.status(400).json({ error: 'name is required' });

  if (DRY_RUN) {
    return res.json({
      wallet_id: `dryrun-agent-${uuidv4()}`,
      address: '0x0000000000000000000000000000000000000000',
      policy_attached: false,
      dry_run: true,
    });
  }

  if (!circleClient) {
    return res.status(503).json({ error: 'Circle client not configured' });
  }

  try {
    // 1. Create wallet set
    const setRes = await circleClient.createWalletSet({
      name: `${name}_WalletSet`,
      idempotencyKey: uuidv4(),
    });
    const walletSetId = setRes.data?.walletSet?.id;
    if (!walletSetId) throw new Error('Wallet set creation failed - no ID returned');

    // 2. Create wallet inside the set
    const walletRes = await circleClient.createWallets({
      idempotencyKey: uuidv4(),
      walletSetId,
      blockchains: [blockchain],
      count: 1,
      accountType: 'SCA',
    });
    const wallet = walletRes.data?.wallets?.[0];
    if (!wallet) throw new Error('Wallet creation failed - no wallet returned');

    // 3. Apply spending policy if requested
    let policyAttached = false;
    if (policy && wallet.id) {
      try {
        const dailyLimit = String(policy.dailyLimit || '50.00');
        const maxPerTx = String(policy.maxPerTx || '2.00');
        const monthlyLimit = String(policy.monthlyLimit || '500.00');

        await circleClient.updateWallet({
          id: wallet.id,
          name: name,
          // spending controls supported on DCW wallets
          spendingLimits: [
            { limits: [{ amount: maxPerTx, currency: 'USD' }], timeFrame: 'TRANSACTION' },
            { limits: [{ amount: dailyLimit, currency: 'USD' }], timeFrame: 'DAILY' },
            { limits: [{ amount: monthlyLimit, currency: 'USD' }], timeFrame: 'MONTHLY' },
          ],
        });
        policyAttached = true;
        console.log(`[circle-agent] Spending policy applied to wallet ${wallet.id}`);
      } catch (policyErr) {
        // Policy attachment is best-effort - wallet is still usable without it
        console.warn(`[circle-agent] Spending policy failed (non-fatal): ${policyErr.message}`);
      }
    }

    return res.json({
      wallet_id: wallet.id,
      address: wallet.address,
      policy_attached: policyAttached,
    });
  } catch (err) {
    console.error('[circle-agent] Wallet creation error:', err.message);
    return res.status(500).json({ error: err.message });
  }
});

// ── GET /wallets/:id ──────────────────────────────────────────────────────────
app.get('/wallets/:id', async (req, res) => {
  if (DRY_RUN) return res.json({ wallet_id: req.params.id, dry_run: true });
  if (!circleClient) return res.status(503).json({ error: 'Circle client not configured' });

  try {
    const result = await circleClient.getWallet({ id: req.params.id });
    const wallet = result.data?.wallet;
    return res.json({
      wallet_id: wallet?.id,
      address: wallet?.address,
      state: wallet?.state,
      blockchain: wallet?.blockchain,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// ── GET /wallets/:id/balance ──────────────────────────────────────────────────
app.get('/wallets/:id/balance', async (req, res) => {
  if (DRY_RUN) return res.json({ wallet_id: req.params.id, balances: [], dry_run: true });
  if (!circleClient) return res.status(503).json({ error: 'Circle client not configured' });

  try {
    const result = await circleClient.listWalletBalance({ id: req.params.id });
    const tokenBalances = result.data?.tokenBalances || [];
    return res.json({
      wallet_id: req.params.id,
      balances: tokenBalances.map(tb => ({
        symbol: tb.token?.symbol,
        amount: tb.amount,
        decimals: tb.token?.decimals,
      })),
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// ── PUT /wallets/:id/policy ───────────────────────────────────────────────────
// Body: { dailyLimit, maxPerTx, monthlyLimit }
app.put('/wallets/:id/policy', async (req, res) => {
  if (DRY_RUN) return res.json({ wallet_id: req.params.id, updated: false, dry_run: true });
  if (!circleClient) return res.status(503).json({ error: 'Circle client not configured' });

  const { dailyLimit = '50.00', maxPerTx = '2.00', monthlyLimit = '500.00' } = req.body;

  try {
    await circleClient.updateWallet({
      id: req.params.id,
      spendingLimits: [
        { limits: [{ amount: String(maxPerTx), currency: 'USD' }], timeFrame: 'TRANSACTION' },
        { limits: [{ amount: String(dailyLimit), currency: 'USD' }], timeFrame: 'DAILY' },
        { limits: [{ amount: String(monthlyLimit), currency: 'USD' }], timeFrame: 'MONTHLY' },
      ],
    });
    return res.json({ wallet_id: req.params.id, updated: true });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// ── POST /signals ─────────────────────────────────────────────────────────────
// Paid endpoint: returns an AI trading signal without executing a trade.
// External agents on the Circle Agents marketplace pay $0.05 USDC per call.
// Body: { agent_id? }  - defaults to Conservative_Whale
app.post('/signals', async (req, res) => {
  const { agent_id = 'Conservative_Whale' } = req.body || {};

  if (DRY_RUN) {
    return res.json({
      action: 'HOLD',
      asset: null,
      agent: agent_id,
      reason: 'Dry-run mode - no real signal generated',
      paid: true,
      dry_run: true,
    });
  }

  try {
    const response = await fetch(`${PYTHON_BACKEND_URL}/trigger-trade`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${process.env.API_AUTH_TOKEN || ''}`,
      },
      body: JSON.stringify({ agent_id, dry_run: true }),
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    return res.json({
      action: data.action ?? 'HOLD',
      asset: data.asset ?? null,
      agent: agent_id,
      reason: data.reason ?? null,
      paid: true,
    });
  } catch (err) {
    console.error('[circle-agent] /signals error:', err.message);
    return res.status(500).json({ error: 'Signal generation failed', detail: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`[circle-agent] Circle Agent Service running on port ${PORT} (dry_run=${DRY_RUN})`);
});
