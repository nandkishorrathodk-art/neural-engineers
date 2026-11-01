'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { DashboardShell } from '@/components/layout/DashboardShell';
import {
  fetchMeetings,
  fetchActionItems,
  fetchExecutiveBriefing,
  triggerDailyStandup,
  updateActionItem,
  scheduleMeeting,
} from '@/lib/api';
import { Meeting, ActionItem, ExecutiveBriefing } from '@/types';

export default function BoardroomPage() {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [actionItems, setActionItems] = useState<ActionItem[]>([]);
  const [briefing, setBriefing] = useState<ExecutiveBriefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'all' | 'scheduled' | 'in_progress' | 'concluded'>('all');
  const [showBriefingModal, setShowBriefingModal] = useState(false);
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newType, setNewType] = useState('daily_standup');
  const [newAgenda, setNewAgenda] = useState('1. Progress & Receipts\n2. Blockers\n3. Action Items');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadData = async () => {
    try {
      const [mtgs, items, brf] = await Promise.allSettled([
        fetchMeetings(50),
        fetchActionItems(),
        fetchExecutiveBriefing(),
      ]);
      if (mtgs.status === 'fulfilled') setMeetings(mtgs.value);
      if (items.status === 'fulfilled') setActionItems(items.value);
      if (brf.status === 'fulfilled') setBriefing(brf.value);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleTriggerStandup = async () => {
    setIsSubmitting(true);
    try {
      await triggerDailyStandup();
      await loadData();
    } catch (err) {
      alert('Failed to trigger daily standup');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleScheduleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    setIsSubmitting(true);
    try {
      const agendaList = newAgenda.split('\n').map((s) => s.trim()).filter(Boolean);
      await scheduleMeeting({
        title: newTitle,
        meeting_type: newType,
        agenda: agendaList,
        participants: ['BossAI'],
      });
      setShowScheduleModal(false);
      setNewTitle('');
      await loadData();
    } catch (err) {
      alert('Failed to schedule meeting');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResolveActionItem = async (itemId: string) => {
    try {
      await updateActionItem(itemId, { status: 'resolved' });
      await loadData();
    } catch (err) {
      alert('Failed to update action item');
    }
  };

  const filteredMeetings = meetings.filter((m) => {
    if (activeTab === 'all') return true;
    return m.status === activeTab;
  });

  const openActionItems = actionItems.filter((i) => i.status === 'open' || i.status === 'in_progress');
  const overdueActionItems = actionItems.filter((i) => i.is_overdue);

  return (
    <DashboardShell>
      <div className="space-y-8">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-card border border-border p-6 rounded-xl shadow-lg">
        <div>
          <div className="flex items-center space-x-3">
            <h1 className="text-2xl font-bold text-white tracking-wide">Digital Boardroom & Org Governance</h1>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded bg-purple-950 border border-purple-800 text-purple-300">
              PHASE 3 ACTIVE
            </span>
          </div>
          <p className="text-sm text-slate-400 mt-1">
            Autonomous daily standups, incident post-mortems, verified progress reports, and persistent action item carry-over.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={() => setShowBriefingModal(true)}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium rounded-lg text-sm transition-colors shadow flex items-center space-x-2"
          >
            <span>📄</span>
            <span>Executive Briefing</span>
          </button>
          <button
            onClick={() => setShowScheduleModal(true)}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium rounded-lg text-sm transition-colors shadow flex items-center space-x-2"
          >
            <span>+ Schedule Meeting</span>
          </button>
          <button
            onClick={handleTriggerStandup}
            disabled={isSubmitting}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-medium rounded-lg text-sm transition-colors shadow flex items-center space-x-2"
          >
            <span>⚡</span>
            <span>Trigger Standup</span>
          </button>
        </div>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-card border border-border p-4 rounded-xl">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">Total Meetings</div>
          <div className="text-2xl font-bold text-white mt-1">{meetings.length}</div>
          <div className="text-xs text-slate-500 mt-1">
            {meetings.filter((m) => m.status === 'concluded').length} concluded
          </div>
        </div>
        <div className="bg-card border border-border p-4 rounded-xl">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">Open Action Items</div>
          <div className="text-2xl font-bold text-amber-400 mt-1">{openActionItems.length}</div>
          <div className="text-xs text-amber-500/80 mt-1">Carried across standups</div>
        </div>
        <div className="bg-card border border-border p-4 rounded-xl">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">Overdue Items</div>
          <div className="text-2xl font-bold text-rose-400 mt-1">{overdueActionItems.length}</div>
          <div className="text-xs text-rose-500/80 mt-1">Requiring immediate attention</div>
        </div>
        <div className="bg-card border border-border p-4 rounded-xl">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">Total Compute Spend</div>
          <div className="text-2xl font-bold text-emerald-400 mt-1">
            ${briefing?.financial_costs?.total_usd?.toFixed(4) || '0.0000'}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {briefing?.financial_costs?.total_tokens?.toLocaleString() || 0} tokens
          </div>
        </div>
      </div>

      {/* Main Grid: Meetings & Action Items */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Meetings List */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between border-b border-border pb-3">
            <h2 className="text-lg font-semibold text-white">Boardroom Sessions</h2>
            <div className="flex space-x-1 bg-slate-900/60 p-1 rounded-lg border border-slate-800 text-xs">
              {(['all', 'scheduled', 'in_progress', 'concluded'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-3 py-1 rounded capitalize transition-colors ${
                    activeTab === tab
                      ? 'bg-purple-600 text-white font-medium'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tab.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>

          {loading ? (
            <div className="text-sm text-slate-500 py-8 text-center">Loading meetings...</div>
          ) : filteredMeetings.length === 0 ? (
            <div className="bg-card border border-border rounded-xl p-8 text-center text-slate-500 text-sm">
              No meetings found in this view. Use "Trigger Standup" or "Schedule Meeting" to convene a session.
            </div>
          ) : (
            <div className="space-y-3">
              {filteredMeetings.map((m) => (
                <Link
                  key={m.id}
                  href={`/meetings/${m.meeting_id}`}
                  className="block bg-card hover:bg-slate-900/80 border border-border hover:border-purple-800/60 rounded-xl p-5 transition-all shadow group"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold text-white group-hover:text-purple-300 transition-colors">
                          {m.title}
                        </span>
                        <span className="text-xs font-mono text-slate-500">({m.meeting_id})</span>
                      </div>
                      <div className="flex items-center space-x-3 text-xs text-slate-400 mt-1">
                        <span className="capitalize">{m.meeting_type.replace('_', ' ')}</span>
                        <span>•</span>
                        <span>{new Date(m.scheduled_at).toLocaleDateString()}</span>
                        <span>•</span>
                        <span>{m.participants.length} attendee(s)</span>
                      </div>
                    </div>
                    <span
                      className={`text-xs px-2.5 py-1 rounded-full font-mono uppercase font-semibold ${
                        m.status === 'concluded'
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : m.status === 'in_progress'
                          ? 'bg-blue-950 text-blue-400 border border-blue-800 animate-pulse'
                          : 'bg-slate-800 text-slate-300 border border-slate-700'
                      }`}
                    >
                      {m.status.replace('_', ' ')}
                    </span>
                  </div>

                  {m.minutes?.summary && (
                    <p className="text-xs text-slate-400 mt-3 line-clamp-2 bg-slate-950/40 p-2.5 rounded border border-slate-800/60">
                      {m.minutes.summary}
                    </p>
                  )}

                  <div className="mt-3 flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-slate-800/40">
                    <span>Agenda items: {m.agenda.length}</span>
                    <span className="text-purple-400 group-hover:translate-x-1 transition-transform inline-flex items-center">
                      View Room & Minutes &rarr;
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Right Column: Action Items Kanban */}
        <div className="space-y-4">
          <div className="border-b border-border pb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Tracked Action Items</h2>
            <span className="text-xs px-2 py-0.5 bg-slate-800 text-slate-300 rounded font-mono">
              {actionItems.length} total
            </span>
          </div>

          {actionItems.length === 0 ? (
            <div className="bg-card border border-border rounded-xl p-6 text-center text-slate-500 text-sm">
              No action items tracked. Action items created during standups appear here and carry forward until resolved.
            </div>
          ) : (
            <div className="space-y-3 max-h-[700px] overflow-y-auto pr-1">
              {actionItems.map((item) => (
                <div
                  key={item.id}
                  className={`p-4 rounded-xl border transition-all ${
                    item.status === 'resolved'
                      ? 'bg-slate-950/40 border-slate-800/60 opacity-60'
                      : item.is_overdue
                      ? 'bg-rose-950/20 border-rose-800/60'
                      : 'bg-card border-border'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-medium text-slate-200">{item.description}</p>
                    {item.is_overdue && (
                      <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-rose-950 border border-rose-800 text-rose-300 shrink-0">
                        Overdue
                      </span>
                    )}
                  </div>

                  <div className="flex items-center justify-between text-xs text-slate-400 mt-3 pt-2 border-t border-slate-800/60">
                    <div>
                      <span className="text-slate-500">Assigned: </span>
                      <span className="text-blue-400 font-mono">{item.assigned_to}</span>
                    </div>

                    {item.status !== 'resolved' ? (
                      <button
                        onClick={() => handleResolveActionItem(item.id)}
                        className="px-2.5 py-1 text-xs bg-emerald-950/80 hover:bg-emerald-900 text-emerald-300 border border-emerald-700 rounded transition-colors"
                      >
                        ✓ Resolve
                      </button>
                    ) : (
                      <span className="text-emerald-400 font-mono text-[11px]">✓ Resolved</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Executive Briefing Modal */}
      {showBriefingModal && briefing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-4xl w-full max-h-[85vh] flex flex-col shadow-2xl overflow-hidden">
            <div className="p-6 border-b border-slate-800 flex items-center justify-between">
              <div>
                <h3 className="text-xl font-bold text-white flex items-center space-x-2">
                  <span>🏢 ANCOS Executive Briefing</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-900/60 text-blue-300 border border-blue-700">
                    HUMAN OWNER VIEW
                  </span>
                </h3>
                <p className="text-xs text-slate-400 mt-1">Generated: {briefing.generated_at}</p>
              </div>
              <button
                onClick={() => setShowBriefingModal(false)}
                className="text-slate-400 hover:text-white text-xl font-bold p-1"
              >
                ✕
              </button>
            </div>

            <div className="p-6 overflow-y-auto space-y-6 text-sm">
              {/* Executive Summary */}
              <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 text-slate-300 leading-relaxed">
                <div className="text-xs font-bold uppercase text-purple-400 mb-1">Executive Summary</div>
                {briefing.executive_summary}
              </div>

              {/* Milestones */}
              <div>
                <h4 className="text-sm font-semibold text-white mb-2">Project Milestones & Delivery</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {briefing.milestones.map((m) => (
                    <div key={m.project_id} className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                      <div className="flex justify-between items-center text-sm font-medium text-white">
                        <span>{m.project_name}</span>
                        <span className="text-xs text-emerald-400 font-mono">{m.completion_percentage}%</span>
                      </div>
                      <div className="text-xs text-slate-400 mt-1">
                        Tasks: {m.completed_tasks}/{m.total_tasks} completed • {m.blocked_tasks} blocked
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Workforce Health */}
              <div>
                <h4 className="text-sm font-semibold text-white mb-2">AI Workforce & Skills Health</h4>
                <div className="grid grid-cols-3 gap-3 text-xs">
                  <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                    <div className="text-slate-400">Headcount</div>
                    <div className="text-lg font-bold text-white mt-1">
                      {briefing.active_workforce.total_employees}
                    </div>
                    <div className="text-slate-500 mt-0.5">
                      {briefing.active_workforce.working} working, {briefing.active_workforce.idle} idle
                    </div>
                  </div>
                  <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                    <div className="text-slate-400">Verified Skills</div>
                    <div className="text-lg font-bold text-blue-400 mt-1">
                      {briefing.active_workforce.skills.total_verified}
                    </div>
                    <div className="text-slate-500 mt-0.5">
                      of {briefing.active_workforce.skills.total_declared} total skills
                    </div>
                  </div>
                  <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                    <div className="text-slate-400">Total Compute Cost</div>
                    <div className="text-lg font-bold text-emerald-400 mt-1">
                      ${briefing.financial_costs.total_usd.toFixed(4)}
                    </div>
                    <div className="text-slate-500 mt-0.5">
                      {briefing.financial_costs.total_tokens.toLocaleString()} tokens
                    </div>
                  </div>
                </div>
              </div>

              {/* Critical Blockers */}
              <div>
                <h4 className="text-sm font-semibold text-white mb-2">Critical Blockers</h4>
                {briefing.blockers.length === 0 ? (
                  <div className="text-xs text-emerald-400 bg-emerald-950/30 border border-emerald-900/60 p-3 rounded-lg">
                    ✅ Zero active blockers. Systems executing smoothly.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {briefing.blockers.map((b) => (
                      <div key={b.task_id} className="text-xs p-3 bg-rose-950/30 border border-rose-900/60 rounded-lg text-rose-300">
                        ⚠️ <span className="font-semibold">{b.title}</span> ({b.task_id}) in {b.project_name} — Priority: {b.priority}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="p-4 bg-slate-950 border-t border-slate-800 flex justify-end">
              <button
                onClick={() => setShowBriefingModal(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-sm font-medium rounded-lg transition-colors"
              >
                Close Briefing
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Schedule Modal */}
      {showScheduleModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-lg w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-4">Schedule Boardroom Session</h3>
            <form onSubmit={handleScheduleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Meeting Title</label>
                <input
                  type="text"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Architecture Sync: Microservices Integration"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-white focus:outline-none focus:border-purple-500"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Meeting Type</label>
                <select
                  value={newType}
                  onChange={(e) => setNewType(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-white focus:outline-none focus:border-purple-500"
                >
                  <option value="daily_standup">Daily Standup</option>
                  <option value="project_review">Project Review</option>
                  <option value="architecture_review">Architecture Review</option>
                  <option value="incident_review">Incident Review</option>
                  <option value="executive_briefing">Executive Briefing</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Agenda Items (One per line)</label>
                <textarea
                  value={newAgenda}
                  onChange={(e) => setNewAgenda(e.target.value)}
                  rows={4}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-white focus:outline-none focus:border-purple-500"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowScheduleModal(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-medium rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg"
                >
                  Schedule Session
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  </DashboardShell>
);
}
