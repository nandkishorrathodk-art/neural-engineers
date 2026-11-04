'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, Building2, CalendarDays, ChevronRight, CircleHelp, Command, Users } from 'lucide-react';

const navItems = [
  { label: 'Overview', href: '/', icon: Building2 },
  { label: 'Employees', href: '/employees', icon: Users },
  { label: 'Boardroom', href: '/meetings', icon: CalendarDays },
];

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [timeStr, setTimeStr] = useState('--:--');
  const [dateStr, setDateStr] = useState('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      setDateStr(now.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }));
    };
    updateTime();
    const interval = setInterval(updateTime, 10000);
    return () => clearInterval(interval);
  }, []);

  const currentPage = navItems.find((item) => item.href === pathname)?.label || 'ANCOS';

  return (
    <div className="min-h-screen bg-[#07111f] text-slate-100 selection:bg-cyan-500/30 selection:text-white lg:flex">
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-white/[0.07] bg-[#091321] px-4 py-5 lg:flex">
        <Link href="/" className="mb-9 flex items-center gap-3 rounded-xl px-2 py-1.5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-cyan-300/20 bg-cyan-400/10 text-cyan-200 shadow-[0_0_28px_rgba(34,211,238,0.08)]">
            <Command size={21} strokeWidth={1.8} />
          </div>
          <div>
            <div className="text-[15px] font-semibold tracking-[0.13em] text-white">ANCOS</div>
            <div className="mt-0.5 text-[10px] font-medium tracking-[0.08em] text-slate-500">COMPANY OS</div>
          </div>
        </Link>

        <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">Workspace</div>
        <nav aria-label="Main navigation" className="space-y-1">
          {navItems.map(({ label, href, icon: Icon }) => {
            const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
            return (
              <Link
                key={label}
                href={href}
                aria-current={active ? 'page' : undefined}
                className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-colors ${
                  active
                    ? 'border border-cyan-300/10 bg-cyan-400/[0.09] text-cyan-100'
                    : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-100'
                }`}
              >
                <Icon size={17} strokeWidth={1.8} className={active ? 'text-cyan-300' : 'text-slate-500 group-hover:text-slate-300'} />
                <span className="flex-1">{label}</span>
                {active && <ChevronRight size={14} className="text-cyan-300/70" />}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto space-y-4">
          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
            <div className="flex items-center gap-2 text-[11px] font-medium text-slate-300">
              <Command size={13} className="text-cyan-300" />
              Operations workspace
            </div>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">Company operations and workforce at a glance.</p>
          </div>
          <div className="flex items-center justify-between px-2 text-[11px] text-slate-500">
            <span className="flex items-center gap-2"><CircleHelp size={14} /> ANCOS workspace</span>
            <span>v0.1</span>
          </div>
        </div>
      </aside>

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 border-b border-white/[0.07] bg-[#07111f]/90 backdrop-blur-xl">
          <div className="flex min-h-[68px] items-center justify-between gap-4 px-4 sm:px-6 xl:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <Link href="/" className="flex items-center gap-2 lg:hidden">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-200"><Command size={17} /></div>
                <span className="text-xs font-semibold tracking-[0.12em] text-white">ANCOS</span>
              </Link>
              <div className="hidden h-5 w-px bg-white/10 lg:block" />
              <div className="flex items-center gap-2 text-[12px] text-slate-500">
                <span className="hidden sm:inline">Workspace</span>
                <ChevronRight size={13} className="hidden sm:block" />
                <span className="truncate font-medium text-slate-200">{currentPage}</span>
              </div>
            </div>

            <nav aria-label="Mobile navigation" className="flex items-center gap-1 lg:hidden">
              {navItems.map(({ label, href, icon: Icon }) => {
                const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
                return (
                  <Link key={label} href={href} aria-label={label} aria-current={active ? 'page' : undefined}
                    className={`flex h-9 w-9 items-center justify-center rounded-lg transition-colors ${active ? 'bg-cyan-400/10 text-cyan-200' : 'text-slate-500 hover:bg-white/5 hover:text-white'}`}>
                    <Icon size={17} />
                  </Link>
                );
              })}
            </nav>

            <div className="flex shrink-0 items-center gap-3 sm:gap-5">
              <div className="hidden items-center gap-2 rounded-full border border-cyan-400/15 bg-cyan-400/[0.06] px-3 py-1.5 sm:flex">
                <Activity size={13} className="text-cyan-300" />
                <span className="text-[11px] font-medium text-cyan-100">ANCOS workspace</span>
              </div>
              <div className="text-right leading-tight">
                <div className="text-[13px] font-semibold tabular-nums text-slate-200">{timeStr}</div>
                <div className="mt-1 text-[10px] text-slate-500">{dateStr}</div>
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1800px] flex-1 px-4 py-5 sm:px-6 sm:py-6 xl:px-8 xl:py-7">
          {children}
        </main>
      </div>
    </div>
  );
}
