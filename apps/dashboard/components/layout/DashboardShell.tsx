'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [timeStr, setTimeStr] = useState('16:45');
  const [dateStr, setDateStr] = useState('Oct 24, 2026');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      const hours = String(now.getHours()).padStart(2, '0');
      const mins = String(now.getMinutes()).padStart(2, '0');
      setTimeStr(`${hours}:${mins}`);
      
      const options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', year: 'numeric' };
      setDateStr(now.toLocaleDateString('en-US', options));
    };
    updateTime();
    const interval = setInterval(updateTime, 10000);
    return () => clearInterval(interval);
  }, []);

  const navItems = [
    { label: 'Company', href: '/', icon: '🏢' },
    { label: 'Employees', href: '/employees', icon: '👥' },
    { label: 'Projects', href: '#projects', icon: '📁' },
    { label: 'Meetings', href: '/meetings', icon: '💬' },
    { label: 'Evolution', href: '#evolution', icon: '🧬' },
    { label: 'Analytics', href: '#analytics', icon: '📊' },
    { label: 'Security', href: '#security', icon: '🛡️' },
    { label: 'Settings', href: '#settings', icon: '⚙️' },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-[#050912] text-slate-100 font-sans selection:bg-sky-500 selection:text-white">
      {/* ─────────────────────────────────────────────────────────────────────────────
          TOP NAVIGATION BAR (Exact Reference Styling)
      ───────────────────────────────────────────────────────────────────────────── */}
      <header className="border-b border-[#132038] bg-[#070e1c]/95 backdrop-blur-md px-6 py-2.5 flex items-center justify-between sticky top-0 z-50 shadow-lg">
        {/* Left: ANCOS Branding */}
        <div className="flex items-center space-x-3.5">
          {/* Glowing Geometric Logo */}
          <Link href="/" className="flex items-center space-x-3 group">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-900 via-sky-900 to-indigo-950 border border-sky-400/50 flex items-center justify-center shadow-[0_0_20px_rgba(56,189,248,0.45)] group-hover:shadow-[0_0_25px_rgba(56,189,248,0.7)] transition-all">
              <svg className="w-6 h-6 text-sky-300 drop-shadow-[0_0_8px_rgba(56,189,248,0.8)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2" />
                <path d="M12 22V12" />
                <path d="M12 12L22 8.5" />
                <path d="M12 12L2 8.5" />
              </svg>
            </div>
            <div>
              <div className="text-xl font-black tracking-wider text-white flex items-center space-x-1.5 font-mono">
                <span className="bg-gradient-to-r from-sky-400 via-cyan-200 to-white bg-clip-text text-transparent">ANCOS</span>
              </div>
              <div className="text-[10px] text-sky-400/80 font-medium tracking-wide">
                Autonomous AI Company
              </div>
            </div>
          </Link>
        </div>

        {/* Center: Main Navigation Tabs */}
        <nav className="flex items-center space-x-1 bg-[#0b1426] border border-[#182847] p-1 rounded-xl shadow-inner">
          {navItems.map((item) => {
            const isActive = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
            return (
              <Link
                key={item.label}
                href={item.href}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 transition-all ${
                  isActive
                    ? 'bg-gradient-to-r from-blue-600 to-sky-600 text-white shadow-[0_0_15px_rgba(14,165,233,0.45)] border border-sky-400/40'
                    : 'text-slate-300 hover:text-white hover:bg-slate-800/60'
                }`}
              >
                <span className="text-sm">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {/* Right: System Status & Live Clock */}
        <div className="flex items-center space-x-5">
          {/* System Online Badge */}
          <div className="flex items-center space-x-2 bg-[#091e1d] border border-emerald-500/40 px-3 py-1 rounded-full shadow-[0_0_12px_rgba(16,185,129,0.2)]">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span className="text-xs font-mono font-bold text-emerald-400">System Online</span>
          </div>

          {/* Time & Date */}
          <div className="text-right">
            <div className="text-sm font-bold font-mono text-white leading-none tracking-tight">
              {timeStr}
            </div>
            <div className="text-[10px] text-slate-400 font-mono mt-0.5 leading-none">
              {dateStr}
            </div>
          </div>
        </div>
      </header>

      {/* Main Page Content */}
      <main className="flex-1 w-full p-4 lg:p-6 max-w-[1920px] mx-auto">
        {children}
      </main>
    </div>
  );
}
