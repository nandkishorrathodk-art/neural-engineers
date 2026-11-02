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
    <div className="min-h-screen flex flex-col bg-[#171c25] text-slate-100 font-sans selection:bg-emerald-700 selection:text-white">
      {/* ─────────────────────────────────────────────────────────────────────────────
          TOP NAVIGATION BAR (Exact Reference Styling)
      ───────────────────────────────────────────────────────────────────────────── */}
      <header className="border-b-2 border-[#716958] bg-[#252a30]/95 px-4 py-2.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 sticky top-0 z-50 shadow-lg shadow-black/20 sm:px-6">
        {/* Left: ANCOS Branding */}
        <div className="flex items-center space-x-3.5">
          {/* Glowing Geometric Logo */}
          <Link href="/" className="flex items-center space-x-3 group">
            <div className="w-10 h-10 rounded-md bg-[#3e5b59] border-2 border-[#a8c8b3] flex items-center justify-center shadow-[3px_3px_0_#171c25] group-hover:bg-[#4d706c] transition-colors">
              <svg className="w-6 h-6 text-[#e6d8b8]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" strokeLinejoin="miter">
                <polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2" />
                <path d="M12 22V12" />
                <path d="M12 12L22 8.5" />
                <path d="M12 12L2 8.5" />
              </svg>
            </div>
            <div>
              <div className="text-xl font-black tracking-wider text-[#f1e4c8] flex items-center space-x-1.5 font-mono">
                <span>ANCOS</span>
              </div>
              <div className="text-[10px] text-[#c2b49b] font-medium tracking-wide">
                Autonomous AI Company
              </div>
            </div>
          </Link>
        </div>

        {/* Center: Main Navigation Tabs */}
        <nav className="order-3 flex w-full max-w-full items-center space-x-1 overflow-x-auto bg-[#34383b] border-2 border-[#716958] p-1 rounded-md shadow-inner md:order-none md:w-auto md:max-w-[62vw]">
          {navItems.map((item) => {
            const isActive = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
            return (
              <Link
                key={item.label}
                href={item.href}
                className={`shrink-0 px-3.5 py-1.5 rounded-md text-xs font-semibold flex items-center space-x-2 transition-all ${
                  isActive
                    ? 'bg-[#60796b] text-white shadow-[2px_2px_0_#22272a] border border-[#aec5ae]'
                    : 'text-[#d4cbb8] hover:text-white hover:bg-[#484d4e]'
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
          <div className="flex items-center space-x-2 bg-[#34383b] border border-[#716958] px-3 py-1 rounded-md">
            <span className="w-2 h-2 bg-[#e0bd72]"></span>
            <span className="text-xs font-mono font-bold text-[#e0d4bc]">ANCOS OS</span>
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
