'use strict';
let csrf = '';
const state = document.getElementById('state');
const logout = document.getElementById('logout');
async function session() {
  const response = await fetch('/v1/session', { cache: 'no-store' });
  if (response.ok) {
    const body = await response.json();
    csrf = body.csrf;
    state.textContent = `Paired as ${body.name}. Permissions: ${body.scopes.join(', ')}. Session expires ${new Date(body.expires_at).toLocaleString()}, or after 24 hours without use. Re-pair on the Mac when it expires.`;
    logout.hidden = false;
  } else if (response.status === 401) {
    csrf = ''; logout.hidden = true;
    state.textContent = 'No active session. If your session expired or was revoked, ask the operator for a new pairing code.';
  }
}
document.getElementById('pair').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.getElementById('code');
  const code = input.value;
  input.value = '';
  try {
    const response = await fetch('/v1/pair', { method: 'POST', headers: { Authorization: `Pairing ${code}` } });
    if (response.status === 429) throw new Error(`Too many pairing attempts. Try again in ${response.headers.get('Retry-After') || '60'} seconds.`);
    if (!response.ok) throw new Error('Pairing code is invalid or expired. Ask for a new code.');
    await session();
  } catch (error) { state.textContent = error.message; }
});
logout.addEventListener('click', async () => {
  try {
    const response = await fetch('/v1/logout', { method: 'POST', headers: { 'X-Nuru-CSRF': csrf } });
    if (!response.ok) throw new Error('Sign out failed. Try again.');
    csrf = ''; logout.hidden = true; state.textContent = 'Signed out.';
  } catch (error) { state.textContent = error.message; }
});
session().catch(() => { state.textContent = 'Nuru is unavailable. Try again.'; });
