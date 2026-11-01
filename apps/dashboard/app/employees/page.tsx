'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { DashboardShell } from '@/components/layout/DashboardShell';
import {
  fetchBossStatus,
  fetchEmployees,
  submitObjective,
  provisionWorkstation,
  terminateWorkstation,
} from '@/lib/api';
import { Employee, Workstation } from '@/types';

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [bossStatus, setBossStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Objective submission state
  const [objectiveInput, setObjectiveInput] = useState('');
  const [submittingObjective, setSubmittingObjective] = useState(false);
  const [objectiveSuccess, setObjectiveSuccess] = useState<string | null>(null);

  // Active desktop stream modal
  const [selectedWorkstation, setSelectedWorkstation] = useState<{
    employee: Employee;
    desktopUrl: string;
    vncPort?: number;
    novncPort?: number;
    dockerId?: string;
  } | null>(null);

  // Action loading state
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [boss, emps] = await Promise.all([
        fetchBossStatus().catch(() => ({ ceo_status: 'operational', active_mode: 'autonomous_orchestration', active_missions: 1 })),
        fetchEmployees().catch(() => []),
      ]);
      setBossStatus(boss);
      setEmployees(emps);
    } catch (err: any) {
      setError(err.message || 'Failed to load employee roster');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleLaunchObjective = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!objectiveInput.trim()) return;

    try {
      setSubmittingObjective(true);
      setError(null);
      const project = await submitObjective(objectiveInput.trim());
      setObjectiveSuccess(`Mission planned: ${project.name} (${project.execution_plan?.phases?.length || 0} phases)`);
      setObjectiveInput('');
      await loadData();
    } catch (err: any) {
      setError(err.message || 'Failed to submit objective to Boss AI');
    } finally {
      setSubmittingObjective(false);
    }
  };

  const handleProvisionWorkstation = async (employeeId: string) => {
    try {
      setActionLoadingId(employeeId);
      setError(null);
      const ws = await provisionWorkstation(employeeId);
      await loadData();
      // Auto-open live stream preview
      const emp = employees.find((e) => e.employee_id === employeeId);
      if (emp && ws.desktop_url) {
        setSelectedWorkstation({
          employee: emp,
          desktopUrl: ws.desktop_url,
          vncPort: ws.vnc_port,
          novncPort: ws.novnc_port,
          dockerId: ws.docker_id,
        });
      }
    } catch (err: any) {
      setError(err.message || `Failed to provision workstation for ${employeeId}`);
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleTerminateWorkstation = async (employeeId: string) => {
    try {
      setActionLoadingId(employeeId);
      setError(null);
      await terminateWorkstation(employeeId);
      if (selectedWorkstation?.employee.employee_id === employeeId) {
        setSelectedWorkstation(null);
      }
      await loadData();
    } catch (err: any) {
      setError(err.message || `Failed to terminate workstation for ${employeeId}`);
    } finally {
      setActionLoadingId(null);
    }
  };

  return (
    <DashboardShell>
      <div className="space-y-6 text-slate-200">
      {/* ─────────────────────────────────────────────────────────────────────────────
          1. CEO STATUS CARD & MISSION KICKOFF BAR
      ───────────────────────────────────────────────────────────────────────────── */}
      <div className="bg-[#0a1120] border border-[#16233d] rounded-2xl p-6 shadow-xl">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-5 border-b border-[#141f36]">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-900 border border-blue-400/40 flex items-center justify-center text-2xl shadow-[0_0_20px_rgba(56,189,248,0.3)]">
              👑
            </div>
            <div>
              <div className="flex items-center space-x-2.5">
                <h1 className="text-lg font-bold text-white tracking-tight">Boss AI Control Center</h1>
                <span className="inline-flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                  <span>{bossStatus?.ceo_status || 'Operational'}</span>
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Mode: <span className="text-sky-300 font-mono">{bossStatus?.active_mode || 'autonomous_orchestration'}</span> • Active Missions: <span className="text-emerald-400 font-mono font-bold">{bossStatus?.active_missions ?? 1}</span>
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={loadData}
              disabled={loading}
              className="px-3.5 py-2 rounded-xl bg-[#0e172a] border border-[#1e2d4a] hover:bg-[#15233e] text-xs font-medium text-slate-300 hover:text-white transition-colors"
            >
              🔄 Refresh Roster
            </button>
          </div>
        </div>

        {/* Objective Submission Form */}
        <form onSubmit={handleLaunchObjective} className="mt-5 space-y-2">
          <label className="block text-xs font-semibold text-slate-300">
            Submit Objective to CEO Brain (Auto-Decompose & Auto-Assign to Workforce)
          </label>
          <div className="flex flex-col sm:flex-row gap-3">
            <input
              type="text"
              value={objectiveInput}
              onChange={(e) => setObjectiveInput(e.target.value)}
              placeholder="e.g. Build an API backend with authentication and automated test suite"
              disabled={submittingObjective}
              className="flex-1 bg-[#060b14] border border-[#1b2b4a] focus:border-sky-500 rounded-xl px-4 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none transition-colors"
            />
            <button
              type="submit"
              disabled={submittingObjective || !objectiveInput.trim()}
              className="px-5 py-2.5 bg-gradient-to-r from-sky-600 to-blue-600 hover:from-sky-500 hover:to-blue-500 disabled:opacity-50 text-white font-bold text-xs rounded-xl shadow-[0_0_15px_rgba(56,189,248,0.25)] transition-all flex items-center justify-center space-x-2 whitespace-nowrap"
            >
              {submittingObjective ? (
                <span>Planning DAG...</span>
              ) : (
                <>
                  <span>🚀</span>
                  <span>Launch Objective</span>
                </>
              )}
            </button>
          </div>
          {objectiveSuccess && (
            <div className="text-xs text-emerald-400 bg-emerald-950/40 border border-emerald-800/60 rounded-lg p-2.5 mt-2">
              ✓ {objectiveSuccess}
            </div>
          )}
          {error && (
            <div className="text-xs text-rose-400 bg-rose-950/40 border border-rose-800/60 rounded-lg p-2.5 mt-2">
              ⚠️ {error}
            </div>
          )}
        </form>
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          2. WORKFORCE ROSTER GRID
      ───────────────────────────────────────────────────────────────────────────── */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-base font-bold text-white flex items-center space-x-2">
              <span>Dynamic Workforce Roster</span>
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-sky-500/20 text-sky-400 border border-sky-500/30">
                {employees.length} Active Agents
              </span>
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Isolated Docker Desktop Workstations with live TigerVNC / noVNC remote viewing.
            </p>
          </div>
        </div>

        {loading && employees.length === 0 ? (
          <div className="bg-[#0a1120] border border-[#16233d] rounded-2xl p-12 text-center text-slate-400 text-xs">
            <span className="inline-block animate-spin mr-2">⏳</span> Loading company employee roster...
          </div>
        ) : employees.length === 0 ? (
          <div className="bg-[#0a1120] border border-[#16233d] rounded-2xl p-12 text-center text-slate-400 text-xs">
            No employees recruited yet. Submit an objective above to automatically bootstrap the initial engineering workforce!
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {employees.map((emp) => {
              const hasWorkstation = Boolean(emp.desktop_url);
              const isLoadingAction = actionLoadingId === emp.employee_id;

              return (
                <div
                  key={emp.employee_id}
                  className="bg-[#0a1120] border border-[#16233d] hover:border-[#22355b] rounded-2xl p-5 shadow-xl flex flex-col justify-between transition-all"
                >
                  <div>
                    {/* Header: Name, Title & Status */}
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center space-x-3">
                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-slate-800 to-slate-900 border border-slate-700 flex items-center justify-center text-lg font-bold text-sky-300">
                          {emp.name.split(' ').map((n) => n[0]).join('')}
                        </div>
                        <div>
                          <h3 className="text-sm font-bold text-white leading-tight">{emp.name}</h3>
                          <p className="text-[11px] text-slate-400 font-medium">{emp.title}</p>
                          <span className="text-[10px] text-sky-400/80 font-mono">{emp.department}</span>
                        </div>
                      </div>

                      <span
                        className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold uppercase ${
                          emp.status === 'working'
                            ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                            : 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                        }`}
                      >
                        {emp.status}
                      </span>
                    </div>

                    <div className="text-[10px] text-slate-500 font-mono mb-3">
                      ID: {emp.employee_id}
                    </div>

                    {/* Skills Section */}
                    <div className="mb-4">
                      <div className="text-[11px] font-semibold text-slate-400 mb-1.5 flex items-center justify-between">
                        <span>Skills & Capabilities:</span>
                        <span className="text-[10px] text-slate-500 font-mono">
                          {emp.skills.filter((s) => s.verified).length}/{emp.skills.length} Verified
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {emp.skills.map((skill, sIdx) => (
                          <span
                            key={sIdx}
                            className={`text-[10px] px-2 py-0.5 rounded-md font-mono flex items-center space-x-1 ${
                              skill.verified
                                ? 'bg-emerald-950/60 text-emerald-300 border border-emerald-700/60'
                                : 'bg-[#0f192c] text-slate-300 border border-[#1b2b4a]'
                            }`}
                          >
                            <span>{skill.verified ? '✓' : '○'}</span>
                            <span>{skill.name}</span>
                            <span className="text-[8px] text-slate-500 capitalize">({skill.level})</span>
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Workstation Status Banner */}
                    <div className="bg-[#060b14] border border-[#141f36] rounded-xl p-3 mb-4 space-y-1.5 text-xs font-mono">
                      <div className="flex items-center justify-between">
                        <span className="text-slate-400 text-[11px]">Workstation:</span>
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded font-bold ${
                            hasWorkstation
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                              : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {hasWorkstation ? 'ONLINE' : 'NOT RUNNING'}
                        </span>
                      </div>

                      {hasWorkstation ? (
                        <>
                          <div className="flex justify-between text-[11px] text-slate-300">
                            <span>Remote GUI:</span>
                            <span className="text-sky-300 font-bold">TigerVNC + noVNC</span>
                          </div>
                          <div className="flex justify-between text-[10px] text-slate-400">
                            <span>Desktop URL:</span>
                            <a
                              href={emp.desktop_url!}
                              target="_blank"
                              rel="noreferrer"
                              className="text-sky-400 hover:underline truncate max-w-[150px]"
                            >
                              {emp.desktop_url}
                            </a>
                          </div>
                        </>
                      ) : (
                        <div className="text-[10px] text-slate-500 italic">
                          Click provision below to boot an isolated Docker desktop container.
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Workstation Actions */}
                  <div className="space-y-2 pt-2 border-t border-[#141f36]">
                    {hasWorkstation ? (
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          onClick={() =>
                            setSelectedWorkstation({
                              employee: emp,
                              desktopUrl: emp.desktop_url!,
                            })
                          }
                          className="w-full py-2 bg-gradient-to-r from-sky-600 to-blue-600 hover:from-sky-500 hover:to-blue-500 text-white font-bold text-xs rounded-xl shadow-[0_0_10px_rgba(56,189,248,0.25)] transition-all flex items-center justify-center space-x-1"
                        >
                          <span>🖥️</span>
                          <span>Open Desktop</span>
                        </button>
                        <button
                          onClick={() => handleTerminateWorkstation(emp.employee_id)}
                          disabled={isLoadingAction}
                          className="w-full py-2 bg-[#1a0f18] hover:bg-[#2b1424] text-rose-300 border border-rose-800/40 font-bold text-xs rounded-xl transition-colors disabled:opacity-50"
                        >
                          {isLoadingAction ? 'Terminating...' : 'Stop'}
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => handleProvisionWorkstation(emp.employee_id)}
                        disabled={isLoadingAction}
                        className="w-full py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 disabled:opacity-50 text-white font-bold text-xs rounded-xl shadow-[0_0_12px_rgba(16,185,129,0.25)] transition-all flex items-center justify-center space-x-2"
                      >
                        {isLoadingAction ? (
                          <span>Booting Workstation...</span>
                        ) : (
                          <>
                            <span>🚀</span>
                            <span>Provision Workstation</span>
                          </>
                        )}
                      </button>
                    )}

                    <Link
                      href={`/employees/${emp.employee_id}/desktop`}
                      className="block text-center text-[10px] text-slate-400 hover:text-sky-300 font-mono transition-colors"
                    >
                      View Dedicated Workstation Page →
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          3. EMBEDDED LIVE noVNC DESKTOP STREAM MODAL (FR-4.4 / FR-10.2)
      ───────────────────────────────────────────────────────────────────────────── */}
      {selectedWorkstation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-[#090f1d] border border-sky-500/40 rounded-2xl w-full max-w-5xl h-[85vh] flex flex-col shadow-[0_0_40px_rgba(56,189,248,0.2)] overflow-hidden">
            {/* Modal Header */}
            <div className="bg-[#0c1527] border-b border-[#1b2b4a] px-5 py-3.5 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <span className="w-3 h-3 rounded-full bg-emerald-400 animate-pulse"></span>
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center space-x-2">
                    <span>{selectedWorkstation.employee.name}&apos;s Workstation</span>
                    <span className="text-[10px] font-mono text-sky-400 bg-sky-950/80 px-2 py-0.5 rounded border border-sky-800">
                      Xfce4 + TigerVNC + noVNC
                    </span>
                  </h3>
                  <p className="text-[10px] text-slate-400 font-mono">
                    {selectedWorkstation.employee.title} • Password: <strong className="text-amber-300">password123</strong>
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-3">
                <a
                  href={selectedWorkstation.desktopUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 rounded-lg bg-sky-600/20 text-sky-400 border border-sky-500/40 hover:bg-sky-600/30 text-xs font-medium flex items-center space-x-1.5 transition-colors"
                >
                  <span>↗</span>
                  <span>Popout Tab</span>
                </a>
                <button
                  onClick={() => setSelectedWorkstation(null)}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors text-sm"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Embedded Live Desktop noVNC iframe */}
            <div className="flex-1 bg-black relative">
              <iframe
                src={selectedWorkstation.desktopUrl}
                title={`${selectedWorkstation.employee.name} Workstation`}
                className="w-full h-full border-0"
                allow="clipboard-read; clipboard-write; fullscreen"
              />
            </div>

            {/* Modal Footer Controls */}
            <div className="bg-[#080e1c] border-t border-[#141f36] px-5 py-2.5 flex items-center justify-between text-xs font-mono text-slate-400">
              <div className="flex items-center space-x-4">
                <span>Display: <strong className="text-slate-200">:1 (1280x800)</strong></span>
                <span>User: <strong className="text-emerald-400">employee (uid 1000)</strong></span>
                <span>URL: <strong className="text-sky-300">{selectedWorkstation.desktopUrl}</strong></span>
              </div>
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => setSelectedWorkstation(null)}
                  className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg transition-colors"
                >
                  Close Viewer
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  </DashboardShell>
);
}
