'use client';

import { FormEvent, useState } from 'react';

export default function OwnerLoginPage() {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setSubmitting(true);

    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        setError(result.detail || 'Sign-in failed. Check the owner token and dashboard configuration.');
        return;
      }

      const requestedPath = new URLSearchParams(window.location.search).get('returnTo');
      let returnTo = '/';
      if (requestedPath?.startsWith('/') && !requestedPath.startsWith('//')) {
        const destination = new URL(requestedPath, window.location.origin);
        if (destination.origin === window.location.origin) {
          returnTo = `${destination.pathname}${destination.search}${destination.hash}`;
        }
      }
      window.location.replace(returnTo);
    } catch {
      setError('Dashboard authentication service is unavailable.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#060a12] text-slate-100 flex items-center justify-center px-6">
      <section className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-950/80 p-8 shadow-2xl">
        <div className="mb-8">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-cyan-400">ANCOS owner access</p>
          <h1 className="mt-3 text-2xl font-semibold">Sign in to the company dashboard</h1>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Enter the owner API token configured for this ANCOS deployment.
          </p>
        </div>

        <form onSubmit={signIn} className="space-y-5">
          <label className="block text-sm font-medium text-slate-200" htmlFor="owner-token">
            Owner token
          </label>
          <input
            id="owner-token"
            type="password"
            name="owner-token"
            autoComplete="current-password"
            autoCapitalize="none"
            spellCheck={false}
            required
            minLength={32}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/20"
          />
          {error && <p role="alert" className="text-sm text-rose-400">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-cyan-500 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-wait disabled:opacity-60"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  );
}
