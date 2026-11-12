'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { fetchEmployee, fetchWorkstationVncToken } from '@/lib/api';
import { toIngressDesktopUrl } from '@/lib/desktopUrl';
import { Employee } from '@/types';

export default function DedicatedWorkstationPage() {
  const params = useParams<{ employeeId: string }>();
  const employeeId = params?.employeeId;

  const [employee, setEmployee] = useState<Employee | null>(null);
  const [desktopUrl, setDesktopUrl] = useState<string | null>(null);
  const [viewOnly, setViewOnly] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadDesktop = useCallback(
    async (interactive: boolean) => {
      if (!employeeId) return;
      try {
        setLoading(true);
        setError(null);
        const data = await fetchWorkstationVncToken(employeeId, interactive);
        const url = toIngressDesktopUrl(
          interactive ? data.desktop_url : data.read_only_desktop_url || data.desktop_url
        );
        if (!url) throw new Error('Workstation token unavailable');
        setDesktopUrl(url);
        setViewOnly(!interactive);
      } catch (err: any) {
        setDesktopUrl(null);
        setError(err.message || 'Workstation container offline or token unavailable.');
      } finally {
        setLoading(false);
      }
    },
    [employeeId]
  );

  useEffect(() => {
    if (!employeeId) return;
    fetchEmployee(employeeId)
      .then(setEmployee)
      .catch(() => setError('Employee record could not be loaded.'));
    loadDesktop(false);
  }, [employeeId, loadDesktop]);

  return (
    <DashboardShell>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#16233d] bg-[#0a1120] p-5">
          <div>
            <h1 className="text-base font-bold text-white">
              {employee?.name || employeeId}&apos;s Workstation
            </h1>
            <p className="mt-0.5 font-mono text-[11px] text-slate-400">
              {employee?.title || 'Employee'} • {employeeId} •{' '}
              {viewOnly ? 'Read-Only' : 'Interactive'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => loadDesktop(viewOnly)}
              disabled={loading}
              className={`rounded-lg border px-3 py-1.5 font-mono text-xs font-medium transition-colors ${
                viewOnly
                  ? 'border-amber-600 bg-amber-950/80 text-amber-300'
                  : 'border-emerald-600 bg-emerald-950/80 text-emerald-300'
              } ${loading ? 'cursor-not-allowed opacity-50' : ''}`}
            >
              Mode: {viewOnly ? 'Read-Only' : 'Interactive'}
            </button>
            {desktopUrl && (
              <a
                href={desktopUrl}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border border-sky-500/40 bg-sky-600/20 px-3 py-1.5 text-xs font-medium text-sky-400 transition-colors hover:bg-sky-600/30"
              >
                ↗ Popout
              </a>
            )}
            <Link
              href="/employees"
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-700"
            >
              ← Back
            </Link>
          </div>
        </div>

        <div className="relative h-[78vh] overflow-hidden rounded-2xl border border-sky-500/40 bg-black">
          {loading ? (
            <div className="flex h-full flex-col items-center justify-center space-y-2 font-mono text-sm text-slate-400">
              <span className="animate-pulse font-bold text-sky-400">
                ACQUIRING WORKSTATION TOKEN...
              </span>
              <span className="text-xs text-slate-500">
                Negotiating authenticated WebSocket ticket via Broker.
              </span>
            </div>
          ) : desktopUrl ? (
            <iframe
              src={desktopUrl}
              title={`${employee?.name || employeeId} Workstation`}
              className="h-full w-full border-0"
              allow="clipboard-read; clipboard-write; fullscreen"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center space-y-2 font-mono text-sm text-slate-400">
              <span className="font-bold text-rose-400">WORKSTATION UNAVAILABLE</span>
              <span className="text-xs text-slate-500">{error}</span>
            </div>
          )}
        </div>
      </div>
    </DashboardShell>
  );
}
