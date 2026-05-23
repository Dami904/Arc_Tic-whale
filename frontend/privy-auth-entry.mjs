/**
 * Privy core SDK wrapper for vanilla HTML (bundled to /static/privy-bundle.mjs).
 */
import Privy, { LocalStorage } from '@privy-io/js-sdk-core';

let privy = null;
let initPromise = null;

function requirePrivy() {
  if (!privy) throw new Error('Privy is not initialized');
  return privy;
}

export function isPrivyConfigured(appId, clientId) {
  return Boolean(appId && clientId);
}

export async function initPrivy(appId, clientId) {
  if (!isPrivyConfigured(appId, clientId)) {
    throw new Error('PRIVY_APP_ID and PRIVY_CLIENT_ID are required');
  }
  if (initPromise) return initPromise;
  initPromise = (async () => {
    privy = new Privy({
      appId,
      clientId,
      storage: new LocalStorage(),
    });
    await privy.initialize();
    return privy;
  })();
  return initPromise;
}

export async function getExistingSession() {
  const client = requirePrivy();
  const { user } = await client.user.get();
  if (!user) return null;
  const token = await client.getAccessToken();
  const email = _extractEmail(user);
  return { userId: user.id, token, email };
}

function _extractEmail(user) {
  const accounts = user.linked_accounts || user.linkedAccounts || [];
  for (const a of accounts) {
    if (a.type === 'email' && a.address) return a.address;
  }
  return '';
}

export async function sendEmailCode(email) {
  const client = requirePrivy();
  await client.auth.email.sendCode(email);
}

export async function loginWithEmailCode(email, code) {
  const client = requirePrivy();
  const session = await client.auth.email.loginWithCode(email, code);
  const user = session.user;
  const token = await client.getAccessToken();
  return {
    userId: user.id,
    token,
    email: _extractEmail(user) || email,
  };
}

export async function startOAuth(provider) {
  const client = requirePrivy();
  const authOrigin = (window.PRIVY_AUTH_ORIGIN || window.location.origin || '').replace(/\/+$/, '');
  const redirectURI = `${authOrigin}/auth/callback`;
  const oauthURL = await client.auth.oauth.generateURL(provider, redirectURI);
  const url = typeof oauthURL === 'string' ? oauthURL : oauthURL?.url || oauthURL;
  if (!url) throw new Error('Privy did not return an OAuth URL');
  return url;
}

export async function completeOAuthFromCallback() {
  const client = requirePrivy();
  const params = new URLSearchParams(window.location.search);
  const code = params.get('privy_oauth_code');
  const state = params.get('privy_oauth_state');
  if (!code || !state) {
    throw new Error('Missing OAuth callback parameters');
  }
  const session = await client.auth.oauth.loginWithCode(code, state);
  const user = session.user;
  const token = await client.getAccessToken();
  return {
    userId: user.id,
    token,
    email: _extractEmail(user),
  };
}

export async function logout() {
  if (!privy) return;
  try {
    const { user } = await privy.user.get();
    if (user) await privy.auth.logout({ userId: user.id });
  } catch (_) {
    /* ignore */
  }
  privy = null;
  initPromise = null;
}
