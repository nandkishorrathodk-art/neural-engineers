'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { fetchEmployee, fetchEmployeeTasks, fetchWorkstationVncToken } from '@/lib/api';
import { toIngressDesktopUrl } from '@/lib/desktopUrl';
import { Employee, Task } from '@/types';

const STATUS_STYLES: Record<string, string> = {
  completed: 'border-emerald-600 bg-emerald-950/70 text-emerald-300',
  running: 'border-sky-600 bg-sky-950/70 text-sky-300',
  assigned: 'border-sky-600 bg-sky-950/70 text-sky-300',
  pending: 'border-slate-600 bg-slate-900 text-slate-300',
  failed: 'border-rose-600 bg-rose-950/70 text-rose-300',
  cancelled: 'border-slate-700 bg-slate-900 text-slate-400',
};

function TaskEvidencePanel({ employeeId }: { employeeId: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!employeeId) return;
    fetchEmployeeTasks(employeeId)
      .then(setTasks)
      .catch(() => setError('Task records could not be loaded.'));
  }, [employeeId]);

  return (
    <div className="rounded-2xl border border-[#16233d] bg-[#0a1120] p-5">
      <h2 className="text-sm font-bold text-white">Task results &amp; verification evidence</h2>
      <p className="mt-0.5 text-[11px] text-slate-400">
        Ground-truth receipts recorded for this employee&apos;s work. A task only counts as
        done when an independent verifier confirms it.
      </p>
      {error && <p className="mt-3 font-mono text-xs text-rose-400">{error}</p>}
      {!error && tasks.length === 0 && (
        <p className="mt-3 font-mono text-xs text-slate-500">No tasks assigned yet.</p>
      )}
      <ul className="mt-4 space-y-3">
        {tasks.map((task) => {
          const verification = task.verification_result || null;
          const passed = verification?.passed === true;
          return (
            <li
              key={task.task_id}
              className="rounded-xl border border-[#16233d] bg-[#070d18] p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-white">{task.title}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                    {task.task_id} • {task.project_id}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase ${
                      STATUS_STYLES[String(task.status).toLowerCase()] ||
                      'border-slate-600 bg-slate-900 text-slate-300'
                    }`}
                  >
                    {String(task.status)}
                  </span>
                  {verification && (
                    <span
                      className={`rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase ${
                        passed
                          ? 'border-emerald-600 bg-emerald-950/70 text-emerald-300'
                          : 'border-rose-600 bg-rose-950/70 text-rose-300'
                      }`}
                    >
                      {passed ? 'verified' : 'not verified'}
                    </span>
                  )}
                </div>
              </div>

              {verification?.notes && (
                <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
                  {verification.notes}
                </p>
              )}

              {Array.isArray(verification?.checks) && verification!.checks!.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {verification!.checks!.map((check) => (
                    <li
                      key={check.name}
                      className="flex items-start gap-2 font-mono text-[11px] text-slate-400"
                    >
                      <span className={check.passed ? 'text-emerald-400' : 'text-rose-400'}>
                        {check.passed ? '✓' : '✗'}
                      </span>
                      <span>{check.name}</span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

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

        {employeeId && <TaskEvidencePanel employeeId={employeeId} />}
      </div>
    </DashboardShell>
  );
}
