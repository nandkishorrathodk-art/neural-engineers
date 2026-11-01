'use client';

import React, { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { IsometricCompanyWorld } from '@/components/game/IsometricCompanyWorld';
import {
  fetchProjects,
  fetchMeetings,
  fetchMessages,
  fetchEmployees,
  fetchBossStatus,
  fetchTelemetry,
  triggerEmergencyStop,
  resumeSystem,
} from '@/lib/api';

export default function DashboardOverview() {
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [showKillModal, setShowKillModal] = useState(false);
  const [projects, setProjects] = useState<Array<{ name: string; status: string }>>([]);
  const [activities, setActivities] = useState<Array<{ text: string; time: string }>>([]);
  const [bossStatus, setBossStatus] = useState<any>(null);
  const [telemetry, setTelemetry] = useState<any>(null);


  useEffect(() => {
    const loadOverviewData = async () => {
      try {
        const [projs, boss, mtgs, msgs, emps, telem] = await Promise.allSettled([
          fetchProjects(),
          fetchBossStatus(),
          fetchMeetings(5),
          fetchMessages(5),
          fetchEmployees(),
          fetchTelemetry(),
        ]);

        if (telem.status === 'fulfilled' && telem.value) {
          setTelemetry(telem.value);
          if (telem.value.kill_switch?.active !== undefined) {
            setKillSwitchActive(telem.value.kill_switch.active);
          }
        }


        if (projs.status === 'fulfilled' && Array.isArray(projs.value)) {
          setProjects(
            projs.value.map((p: any) => ({
              name: p.name || p.title || 'Untitled project',
              status: String(p.status || 'unknown').replaceAll('_', ' '),
            }))
          );
        }

        if (boss.status === 'fulfilled') {
          setBossStatus(boss.value);
        }

        const activityList: Array<{ text: string; time: string }> = [];
        if (projs.status === 'fulfilled' && Array.isArray(projs.value)) {
          for (const p of projs.value.slice(0, 2)) {
            activityList.push({
              text: `Project "${p.name}" status: ${p.status}`,
              time: p.status || 'Project',
            });
          }
        }
        if (mtgs.status === 'fulfilled' && Array.isArray(mtgs.value)) {
          for (const m of mtgs.value.slice(0, 2)) {
            activityList.push({
              text: `Meeting "${m.title}" (${m.status})`,
              time: m.status || 'Meeting',
            });
          }
        }
        if (msgs.status === 'fulfilled' && Array.isArray(msgs.value)) {
          for (const msg of msgs.value.slice(0, 2)) {
            const preview = msg.payload?.content || (typeof msg.payload === 'string' ? msg.payload : '') || '';
            activityList.push({
              text: `Comms: ${msg.sender} → ${msg.recipient || 'All'}: ${preview.slice(0, 25)}`,
              time: msg.created_at ? new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Message',
            });
          }
        }
        if (emps.status === 'fulfilled' && Array.isArray(emps.value)) {
          for (const emp of emps.value.slice(0, 2)) {
            activityList.push({
              text: `Workforce: ${emp.name} (${emp.title}) assigned to ${emp.department}`,
              time: 'Employee',
            });
          }
        }
        setActivities(activityList.slice(0, 4));
      } catch (e) {
        // Fallback
      }
    };

    loadOverviewData();
    const interval = setInterval(loadOverviewData, 5000);
    return () => clearInterval(interval);
  }, []);

  const cpuVal = telemetry?.cpu?.percent == null ? null : Math.round(telemetry.cpu.percent);
  const memVal = telemetry?.memory?.percent == null ? null : Math.round(telemetry.memory.percent);
  const diskVal = telemetry?.disk?.percent == null ? null : Math.round(telemetry.disk.percent);
  const netVal = telemetry?.network?.bytes_recv == null ? null : Math.round(telemetry.network.bytes_recv / 1000);

  const gauges = [
    { label: 'CPU', val: cpuVal, color: '#38bdf8', stroke: 'stroke-sky-400' },
    { label: 'Memory', val: memVal, color: '#10b981', stroke: 'stroke-emerald-400' },
    { label: 'Disk', val: diskVal, color: '#f59e0b', stroke: 'stroke-amber-500' },
    { label: 'Network', val: netVal, color: '#c084fc', stroke: 'stroke-purple-400' },
  ];


  return (
    <DashboardShell>
      <div className="space-y-5 text-slate-200">
        {/* ─────────────────────────────────────────────────────────────────────────────
            1. LIVING 2D ISOMETRIC ANIME COMPANY SIMULATION (Exact Reference Match)
        ───────────────────────────────────────────────────────────────────────────── */}
        <section className="overflow-hidden rounded-[26px] border border-slate-800/90 bg-[#080f1d] shadow-[0_24px_80px_rgba(0,0,0,0.28)]">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800/80 px-5 py-4 sm:px-6">
            <div>
              <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-sky-300/80">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan-300 shadow-[0_0_10px_#67e8f9]" />
                Operations floor
              </div>
              <h1 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">Company world</h1>
              <p className="mt-1 text-xs text-slate-400 sm:text-sm">Live view of your ANCOS workspace</p>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-slate-700/80 bg-slate-900/70 px-3 py-2 text-xs text-slate-300">
              <span className={`h-2 w-2 rounded-full ${telemetry ? 'bg-emerald-400 shadow-[0_0_9px_#34d399]' : 'bg-amber-400'}`} />
              {telemetry ? 'Telemetry connected' : 'Waiting for telemetry'}
            </div>
          </div>
          <div className="w-full">
          <IsometricCompanyWorld />
          </div>
        </section>

        {/* ─────────────────────────────────────────────────────────────────────────────
            2. BOTTOM MANAGEMENT DECK (Exact 4 Cards from Reference Image)
        ───────────────────────────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          
          {/* Card 1: Active Projects */}
          <div className="min-h-44 rounded-2xl border border-slate-800 bg-gradient-to-br from-[#0d1729] to-[#080f1c] p-5 shadow-lg shadow-black/10">
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-white tracking-wide">Projects</h3>
                <a href="#projects" className="text-xs text-sky-400 hover:text-sky-300 font-medium">
                  View All →
                </a>
              </div>

              <div className="space-y-3">
                {projects.length > 0 ? (
                  projects.map((proj, idx) => (
                    <div key={`${proj.name}-${idx}`} className="flex items-center justify-between gap-3 rounded-xl border border-slate-800/80 bg-slate-950/40 px-3 py-2.5">
                      <div className="flex items-center justify-between text-xs font-medium">
                        <div className="flex items-center space-x-2">
                          <span className={`h-2 w-2 rounded-full ${proj.status.toLowerCase().includes('complete') ? 'bg-emerald-400' : 'bg-sky-400'}`}></span>
                          <span className="max-w-44 truncate text-slate-200">{proj.name}</span>
                        </div>
                      </div>
                      <span className="shrink-0 rounded-full bg-slate-800 px-2 py-1 text-[10px] capitalize text-slate-300">{proj.status}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-4 text-xs text-slate-400">
                    No projects returned by ANCOS.
                    <br />
                    <span className="text-[10px] text-slate-500">Projects will appear here when available.</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Card 2: System Resources (4 Circular SVG Gauges) */}
          <div className="min-h-44 rounded-2xl border border-slate-800 bg-gradient-to-br from-[#0d1729] to-[#080f1c] p-5 shadow-lg shadow-black/10">
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-white tracking-wide">System Resources</h3>
              </div>

              <div className="grid grid-cols-4 gap-2 text-center my-1">
                {gauges.map((g, idx) => {
                  const radius = 22;
                  const circ = 2 * Math.PI * radius;
                  const normalizedVal = g.val === null ? 0 : Math.min(100, Math.max(0, g.val));
                  const strokeDashoffset = circ - (normalizedVal / 100) * circ;

                  return (
                    <div key={idx} className="flex flex-col items-center">
                      <div className="relative w-14 h-14 flex items-center justify-center">
                        <svg className="w-14 h-14 transform -rotate-90" viewBox="0 0 54 54">
                          {/* Background Track */}
                          <circle
                            cx="27"
                            cy="27"
                            r={radius}
                            className="stroke-[#0e1c36]"
                            strokeWidth="4.5"
                            fill="transparent"
                          />
                          {/* Progress Circle */}
                          <circle
                            cx="27"
                            cy="27"
                            r={radius}
                            stroke={g.color}
                            strokeWidth="4.5"
                            strokeDasharray={circ}
                            strokeDashoffset={strokeDashoffset}
                            strokeLinecap="round"
                            fill="transparent"
                            style={{ filter: `drop-shadow(0 0 3px ${g.color}88)` }}
                          />
                        </svg>
                        <span className="absolute font-mono font-bold text-xs text-white">
                          {g.val === null ? '—' : `${g.val}${g.label === 'Network' ? 'k' : '%'}`}
                        </span>
                      </div>
                      <span className="text-[11px] text-slate-400 mt-1 font-semibold">{g.label}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Card 3: Recent Activity */}
          <div className="min-h-44 rounded-2xl border border-slate-800 bg-gradient-to-br from-[#0d1729] to-[#080f1c] p-5 shadow-lg shadow-black/10">
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-white tracking-wide">Recent Activity</h3>
                <a href="#activity" className="text-xs text-sky-400 hover:text-sky-300 font-medium">
                  →
                </a>
              </div>

              <div className="space-y-2.5">
                {activities.length > 0 ? activities.map((act, idx) => (
                  <div key={idx} className="flex items-center justify-between text-xs">
                    <div className="flex items-center space-x-2 truncate pr-2">
                      <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0"></span>
                      <span className="text-slate-300 truncate">{act.text}</span>
                    </div>
                    <span className="text-slate-400 font-mono text-[11px] flex-shrink-0">
                      {act.time}
                    </span>
                  </div>
                )) : <div className="rounded-xl border border-dashed border-slate-800 px-3 py-5 text-center text-xs text-slate-500">No recent activity received from ANCOS yet.</div>}
              </div>
            </div>
          </div>

          {/* Card 4: Emergency Kill Switch */}
          <div className="flex min-h-44 flex-col justify-between rounded-2xl border border-rose-900/70 bg-gradient-to-br from-[#210d17] to-[#100910] p-5 shadow-lg shadow-rose-950/20">
            <div>
              <div className="flex items-center space-x-2 mb-1">
                <span className="text-rose-400 text-base">⚠️</span>
                <h3 className="text-sm font-bold text-rose-300 tracking-wide">Emergency Kill Switch</h3>
              </div>
              <p className="text-[11px] text-rose-400/80 leading-snug mb-4">
                Stop all operations immediately
              </p>
            </div>

            <button
              onClick={async () => {
                if (killSwitchActive) {
                  try {
                    await resumeSystem();
                  } catch (e) {}
                  setKillSwitchActive(false);
                } else {
                  setShowKillModal(true);
                }
              }}
              className="w-full py-2 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-bold text-xs rounded-xl shadow-[0_0_15px_rgba(225,29,72,0.4)] transition-all cursor-pointer"
            >
              {killSwitchActive ? 'Resume System' : 'Activate'}
            </button>
          </div>

        </div>
      </div>

      {/* Kill Switch Confirmation Modal */}
      {showKillModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#100609] border border-rose-600/60 rounded-2xl p-6 max-w-md w-full shadow-[0_0_30px_rgba(225,29,72,0.5)] text-center space-y-4">
            <div className="text-4xl">⚠️</div>
            <h2 className="text-lg font-bold text-rose-300">Confirm Emergency Shutdown</h2>
            <p className="text-xs text-rose-200/80 leading-relaxed">
              This will immediately pause all autonomous task execution DAGs, worker agent loops, and neural scheduling across all company departments.
            </p>
            <div className="flex items-center justify-center space-x-3 pt-2">
              <button
                onClick={() => setShowKillModal(false)}
                className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  try {
                    await triggerEmergencyStop('Manual halt triggered from Control Room Dashboard');
                  } catch (e) {}
                  setKillSwitchActive(true);
                  setShowKillModal(false);
                }}
                className="px-5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-xs font-bold text-white shadow-lg shadow-rose-600/40"
              >
                Confirm Shutdown
              </button>
            </div>
          </div>
        </div>
      )}

    </DashboardShell>
  );
}
