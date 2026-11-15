// Rebuild with the pinned SDK and esbuild versions documented in README.md.
import { createClient } from '@supabase/supabase-js';
let clientPromise;
export async function authClient() {
  if (!clientPromise) {
    clientPromise = (async () => {
      const response = await fetch('/api/auth/config');
      if (!response.ok) throw new Error('GitHub sign-in is not configured.');
      const config = await response.json();
      return createClient(config.url, config.publishable_key, {
        auth: { flowType: 'pkce', storage: window.sessionStorage, persistSession: true,
                autoRefreshToken: true, detectSessionInUrl: true }
      });
    })().catch(error => { clientPromise = null; throw error; });
  }
  return clientPromise;
}
