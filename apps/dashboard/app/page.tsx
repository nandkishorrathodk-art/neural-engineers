'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  CircleAlert,
  Cpu,
  FolderKanban,
  Gauge,
  HardDrive,
  MemoryStick,
  MessageSquareText,
  Radio,
  Users,
  X,
} from 'lucide-react';
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

type ProjectSummary = { name: string; status: string };
type ActivityItem = { text: string; time: string; kind: string; timestamp: number };
type OverviewApiStatus = 'loading' | 'available' | 'unavailable';

function formatTime(value?: string) {
  if (!value) return 'Recently';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? 'Recently'
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatBytes(value?: number) {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function MetricCard({ label, value, detail, icon: Icon, tint }: {
  label: string;
  value: string | number;
  detail: string;
  icon: React.ElementType;
  tint: 'cyan' | 'violet' | 'blue' | 'emerald';
}) {
  const tones = {
    cyan: 'border-cyan-300/10 bg-cyan-300/[0.07] text-cyan-200',
    violet: 'border-violet-300/10 bg-violet-300/[0.07] text-violet-200',
    blue: 'border-blue-300/10 bg-blue-300/[0.07] text-blue-200',
    emerald: 'border-emerald-300/10 bg-emerald-300/[0.07] text-emerald-200',
  };
  return (
    <article className="rounded-2xl border border-white/[0.07] bg-[#0b1727] p-4 shadow-[0_10px_34px_rgba(0,0,0,0.12)] sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-medium text-slate-500">{label}</div>
          <div className="mt-2 truncate text-[25px] font-semibold leading-none tracking-tight text-white">{value}</div>
          <div className="mt-2 truncate text-[11px] text-slate-500">{detail}</div>
        </div>
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${tones[tint]}`}>
          <Icon size={17} strokeWidth={1.8} />
        </div>
      </div>
    </article>
  );
}

export default function DashboardOverview() {
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [showKillModal, setShowKillModal] = useState(false);
  const [safetyBusy, setSafetyBusy] = useState(false);
  const [safetyError, setSafetyError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [employeeCount, setEmployeeCount] = useState<number | null>(null);
  const [meetingCount, setMeetingCount] = useState<number | null>(null);
  const [overviewApiStatus, setOverviewApiStatus] = useState<OverviewApiStatus>('loading');
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [bossStatus, setBossStatus] = useState<any>(null);
  const [telemetry, setTelemetry] = useState<any>(null);

  useEffect(() => {
    let mounted = true;
    const loadOverviewData = async () => {
      const [projs, boss, mtgs, msgs, emps, telem] = await Promise.allSettled([
        fetchProjects(),
        fetchBossStatus(),
        fetchMeetings(5),
        fetchMessages(5),
        fetchEmployees(),
        fetchTelemetry(),
      ]);
      if (!mounted) return;

      const results = [projs, boss, mtgs, msgs, emps, telem];
      setOverviewApiStatus(results.some((result) => result.status === 'fulfilled') ? 'available' : 'unavailable');

      if (telem.status === 'fulfilled' && telem.value) {
        setTelemetry(telem.value);
        if (telem.value.kill_switch?.active !== undefined) setKillSwitchActive(telem.value.kill_switch.active);
      } else {
        setTelemetry(null);
      }
      if (projs.status === 'fulfilled' && Array.isArray(projs.value)) {
        setProjects(projs.value.map((project: any) => ({
          name: project.name || project.title || 'Untitled project',
          status: String(project.status || 'unknown').replaceAll('_', ' '),
        })));
      } else {
        setProjects(null);
      }
      setBossStatus(boss.status === 'fulfilled' ? boss.value : null);
      setEmployeeCount(emps.status === 'fulfilled' && Array.isArray(emps.value) ? emps.value.length : null);
      setMeetingCount(mtgs.status === 'fulfilled' && Array.isArray(mtgs.value) ? mtgs.value.length : null);

      const nextActivities: ActivityItem[] = [];
      const addActivity = (text: string, kind: string, timestamp?: string) => {
        const parsed = timestamp ? new Date(timestamp).getTime() : 0;
        nextActivities.push({ text, kind, timestamp: Number.isFinite(parsed) ? parsed : 0, time: formatTime(timestamp) });
      };
      if (projs.status === 'fulfilled' && Array.isArray(projs.value)) {
        for (const project of projs.value.slice(0, 3)) {
          addActivity(`Project ${project.name || project.title || 'Untitled'} · ${String(project.status || 'updated').replaceAll('_', ' ')}`, 'Project', project.updated_at || project.created_at);
        }
      }
      if (mtgs.status === 'fulfilled' && Array.isArray(mtgs.value)) {
        for (const meeting of mtgs.value.slice(0, 3)) {
          addActivity(`Meeting ${meeting.title || 'Boardroom session'} · ${String(meeting.status || 'scheduled').replaceAll('_', ' ')}`, 'Meeting', meeting.updated_at || meeting.created_at || meeting.scheduled_at);
        }
      }
      if (msgs.status === 'fulfilled' && Array.isArray(msgs.value)) {
        for (const message of msgs.value.slice(0, 3)) {
          const preview = message.payload?.content || (typeof message.payload === 'string' ? message.payload : '');
          addActivity(`${message.sender || 'ANCOS'} → ${message.recipient || 'All'}${preview ? ` · ${preview.slice(0, 52)}` : ''}`, 'Message', message.created_at);
        }
      }
      if (emps.status === 'fulfilled' && Array.isArray(emps.value)) {
        for (const employee of emps.value.slice(0, 2)) {
          addActivity(`${employee.name || 'Employee'} joined ${employee.department || 'the workforce'}`, 'Workforce', employee.created_at);
        }
      }
      setActivities(nextActivities.sort((a, b) => b.timestamp - a.timestamp).slice(0, 5));
    };

    loadOverviewData();
    const interval = setInterval(loadOverviewData, 5000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const metricPercent = (metric: any) => (
    metric?.available === true
    && typeof metric.percent === 'number'
    && Number.isFinite(metric.percent)
      ? Math.round(metric.percent)
      : null
  );
  const cpuVal = metricPercent(telemetry?.cpu);
  const memVal = metricPercent(telemetry?.memory);
  const diskVal = metricPercent(telemetry?.disk);
  const networkReceived = telemetry?.network?.available === true ? telemetry.network.bytes_recv : null;
  const telemetryAvailable = telemetry?.available === true;
  const bossLabel = bossStatus?.ceo_status || bossStatus?.status || (bossStatus ? 'Ready' : '—');
  const statusLabel = killSwitchActive
    ? 'Operations paused'
    : overviewApiStatus === 'loading'
      ? 'Connecting to ANCOS'
      : telemetryAvailable
        ? 'Telemetry connected'
        : 'Telemetry unavailable';
  const statusTone = killSwitchActive
    ? 'border-rose-400/20 bg-rose-400/[0.07] text-rose-200'
    : overviewApiStatus === 'loading'
      ? 'border-amber-400/15 bg-amber-400/[0.06] text-amber-200'
      : telemetryAvailable
        ? 'border-emerald-400/15 bg-emerald-400/[0.06] text-emerald-200'
        : 'border-rose-400/15 bg-rose-400/[0.06] text-rose-200';

  const resources = [
    { label: 'CPU', value: cpuVal, icon: Cpu, color: 'bg-cyan-400' },
    { label: 'Memory', value: memVal, icon: MemoryStick, color: 'bg-violet-400' },
    { label: 'Disk', value: diskVal, icon: HardDrive, color: 'bg-amber-400' },
  ];

  const handleResume = async () => {
    setSafetyBusy(true);
    setSafetyError(null);
    try {
      await resumeSystem();
      setKillSwitchActive(false);
    } catch {
      setSafetyError('Could not resume ANCOS. Check the connection and try again.');
    } finally {
      setSafetyBusy(false);
    }
  };

  const handleEmergencyStop = async () => {
    setSafetyBusy(true);
    setSafetyError(null);
    try {
      await triggerEmergencyStop('Manual halt triggered from Control Room Dashboard');
      setKillSwitchActive(true);
      setShowKillModal(false);
    } catch {
      setSafetyError('Emergency stop request failed. ANCOS did not confirm the pause.');
    } finally {
      setSafetyBusy(false);
    }
  };

  return (
    <DashboardShell>
      <div className="space-y-6 text-slate-200">
        <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-300/80">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-300" /> Operations overview
            </div>
            <h1 className="text-[26px] font-semibold tracking-tight text-white sm:text-[30px]">Company at a glance</h1>
            <p className="mt-1.5 text-[13px] text-slate-500">Live view of your AI workforce, projects, and system health.</p>
          </div>
          <div className={`inline-flex w-fit items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-medium ${statusTone}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${killSwitchActive || (overviewApiStatus !== 'loading' && !telemetryAvailable) ? 'bg-rose-300' : telemetryAvailable ? 'bg-emerald-300' : 'bg-amber-300'}`} />
            {statusLabel}
          </div>
        </section>

        <section aria-label="Company summary" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <MetricCard label="Employees" value={employeeCount ?? '—'} detail="AI workforce members" icon={Users} tint="cyan" />
          <MetricCard label="Projects" value={projects?.length ?? '—'} detail="Across the company" icon={FolderKanban} tint="violet" />
          <MetricCard label="Boardroom" value={meetingCount ?? '—'} detail="Recent sessions" icon={MessageSquareText} tint="blue" />
          <MetricCard label="Boss AI" value={bossLabel} detail="Executive orchestration" icon={BriefcaseBusiness} tint="emerald" />
        </section>

        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.75fr)_minmax(310px,0.75fr)]">
          <section className="overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0a1524] shadow-[0_18px_55px_rgba(0,0,0,0.2)]">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-4 sm:px-5">
              <div>
                <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.15em] text-slate-500">
                  <Radio size={13} className="text-cyan-300" /> Live simulation
                </div>
                <h2 className="mt-1 text-[15px] font-semibold text-white">Company floor</h2>
              </div>
              <Link href="/employees" className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.09] bg-white/[0.025] px-3 py-2 text-[11px] font-medium text-slate-300 transition-colors hover:border-cyan-300/20 hover:text-cyan-100">
                View workforce <ArrowRight size={13} />
              </Link>
            </div>
            <IsometricCompanyWorld />
          </section>

          <aside className="space-y-5">
            <section id="projects" className="rounded-2xl border border-white/[0.07] bg-[#0b1727] p-4 sm:p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Work in progress</div>
                  <h2 className="mt-1 text-[14px] font-semibold text-white">Projects</h2>
                </div>
                <span className="rounded-lg border border-white/[0.07] bg-white/[0.03] px-2.5 py-1 text-[11px] tabular-nums text-slate-400">{projects ? `${projects.length} total` : 'Unavailable'}</span>
              </div>
              {projects && projects.length > 0 ? (
                <div className="space-y-2">
                  {projects.slice(0, 4).map((project, index) => {
                    const isComplete = project.status.toLowerCase().includes('complete');
                    return (
                      <div key={`${project.name}-${index}`} className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-[#081321] px-3 py-3">
                        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${isComplete ? 'bg-emerald-400/10 text-emerald-300' : 'bg-cyan-400/10 text-cyan-200'}`}>
                          {isComplete ? <CheckCircle2 size={15} /> : <FolderKanban size={15} />}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] font-medium text-slate-200">{project.name}</div>
                          <div className="mt-1 text-[10px] capitalize text-slate-500">{project.status}</div>
                        </div>
                        <ArrowDownRight size={14} className="shrink-0 text-slate-600" />
                      </div>
                    );
                  })}
                </div>
              ) : projects ? (
                <div className="rounded-xl border border-dashed border-white/[0.1] px-3 py-6 text-center">
                  <FolderKanban size={19} className="mx-auto text-slate-600" />
                  <p className="mt-2 text-[11px] text-slate-400">Projects will appear here when available.</p>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-rose-300/10 bg-rose-300/[0.025] px-3 py-6 text-center">
                  <CircleAlert size={18} className="mx-auto text-rose-300/70" />
                  <p className="mt-2 text-[11px] text-slate-400">ANCOS API is unavailable. Project data could not be loaded.</p>
                </div>
              )}
            </section>

            <section className="rounded-2xl border border-white/[0.07] bg-[#0b1727] p-4 sm:p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Infrastructure</div>
                  <h2 className="mt-1 text-[14px] font-semibold text-white">System resources</h2>
                </div>
                <Gauge size={17} className="text-slate-500" />
              </div>
              <div className="space-y-4">
                {resources.map(({ label, value, icon: Icon, color }) => (
                  <div key={label}>
                    <div className="mb-2 flex items-center justify-between text-[11px]">
                      <span className="flex items-center gap-2 text-slate-400"><Icon size={14} className="text-slate-500" />{label}</span>
                      <span className="font-medium tabular-nums text-slate-200">{value === null ? '—' : `${value}%`}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                      <div className={`h-full rounded-full ${color} transition-[width] duration-700`} style={{ width: `${value === null ? 0 : Math.min(100, Math.max(0, value))}%` }} />
                    </div>
                  </div>
                ))}
                <div className="flex items-center justify-between border-t border-white/[0.07] pt-3 text-[11px]">
                  <span className="flex items-center gap-2 text-slate-400"><Activity size={14} className="text-slate-500" />Network received</span>
                  <span className="font-medium tabular-nums text-slate-200">{formatBytes(networkReceived)}</span>
                </div>
              </div>
            </section>

            <section id="activity" className="rounded-2xl border border-white/[0.07] bg-[#0b1727] p-4 sm:p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Company feed</div>
                  <h2 className="mt-1 text-[14px] font-semibold text-white">Recent activity</h2>
                </div>
                <Link href="/meetings" aria-label="Open boardroom" className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-cyan-200"><ArrowRight size={15} /></Link>
              </div>
              {activities.length ? (
                <div className="space-y-3">
                  {activities.map((activity, index) => (
                    <div key={`${activity.kind}-${activity.timestamp}-${index}`} className="flex gap-3">
                      <div className="relative flex w-3 shrink-0 justify-center">
                        {index < activities.length - 1 && <span className="absolute bottom-[-13px] top-3 w-px bg-white/[0.08]" />}
                        <span className={`relative mt-1 h-2 w-2 rounded-full ring-4 ring-[#0b1727] ${activity.kind === 'Project' ? 'bg-violet-300' : activity.kind === 'Meeting' ? 'bg-blue-300' : activity.kind === 'Message' ? 'bg-cyan-300' : 'bg-emerald-300'}`} />
                      </div>
                      <div className="min-w-0 flex-1 pb-1">
                        <div className="line-clamp-2 text-[11px] leading-4 text-slate-300">{activity.text}</div>
                        <div className="mt-1 text-[10px] text-slate-600">{activity.kind} <span className="px-1">·</span> {activity.time}</div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/[0.1] px-3 py-5 text-center text-[11px] text-slate-500">
                  {overviewApiStatus === 'unavailable' ? 'ANCOS API is unavailable. Activity could not be loaded.' : 'No recent activity received yet.'}
                </div>
              )}
            </section>
          </aside>
        </div>

        <section className={`flex flex-col gap-4 rounded-2xl border p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5 ${killSwitchActive ? 'border-rose-400/20 bg-rose-500/[0.06]' : 'border-white/[0.07] bg-[#0b1727]'}`}>
          <div className="flex items-start gap-3">
            <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${killSwitchActive ? 'bg-rose-400/10 text-rose-200' : 'bg-slate-700/40 text-slate-300'}`}>
              {killSwitchActive ? <CircleAlert size={17} /> : <Activity size={17} />}
            </div>
            <div>
              <div className="text-[13px] font-semibold text-white">Emergency controls</div>
              <p className="mt-1 text-[11px] text-slate-500">{killSwitchActive ? 'ANCOS reports that operations are paused.' : 'Pause autonomous task execution if an incident requires it.'}</p>
              {safetyError && <p role="alert" className="mt-2 text-[11px] text-rose-300">{safetyError}</p>}
            </div>
          </div>
          <button
            onClick={killSwitchActive ? handleResume : () => { setSafetyError(null); setShowKillModal(true); }}
            disabled={safetyBusy}
            className={`w-full rounded-xl px-4 py-2.5 text-[11px] font-semibold transition-colors disabled:cursor-wait disabled:opacity-60 sm:w-auto ${killSwitchActive ? 'border border-emerald-300/15 bg-emerald-400/10 text-emerald-100 hover:bg-emerald-400/15' : 'border border-rose-300/15 bg-rose-400/10 text-rose-100 hover:bg-rose-400/15'}`}
          >
            {safetyBusy ? 'Working…' : killSwitchActive ? 'Resume operations' : 'Emergency stop'}
          </button>
        </section>
      </div>

      {showKillModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#020711]/80 p-4 backdrop-blur-sm" role="presentation">
          <div role="dialog" aria-modal="true" aria-labelledby="shutdown-title" className="w-full max-w-md rounded-2xl border border-rose-300/15 bg-[#0b1421] p-5 shadow-2xl shadow-black/40 sm:p-6">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-400/10 text-rose-200"><CircleAlert size={20} /></div>
                <div>
                  <h2 id="shutdown-title" className="text-[16px] font-semibold text-white">Pause all operations?</h2>
                  <p className="mt-1 text-[11px] text-slate-500">Emergency stop confirmation</p>
                </div>
              </div>
              <button onClick={() => setShowKillModal(false)} aria-label="Close confirmation" className="rounded-lg p-1.5 text-slate-500 hover:bg-white/5 hover:text-white"><X size={16} /></button>
            </div>
            <p className="mt-5 text-[12px] leading-5 text-slate-300">This will pause autonomous task execution, worker loops, and neural scheduling across the company.</p>
            {safetyError && <p role="alert" className="mt-3 rounded-lg border border-rose-300/10 bg-rose-300/[0.05] p-2.5 text-[11px] text-rose-200">{safetyError}</p>}
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button onClick={() => setShowKillModal(false)} disabled={safetyBusy} className="rounded-xl border border-white/[0.08] px-4 py-2.5 text-[11px] font-medium text-slate-300 hover:bg-white/[0.04] disabled:opacity-50">Cancel</button>
              <button onClick={handleEmergencyStop} disabled={safetyBusy} className="rounded-xl bg-rose-500 px-4 py-2.5 text-[11px] font-semibold text-white hover:bg-rose-400 disabled:cursor-wait disabled:opacity-60">{safetyBusy ? 'Sending request…' : 'Confirm emergency stop'}</button>
            </div>
          </div>
        </div>
      )}
    </DashboardShell>
  );
}
