'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { sendBossChatMessage, submitObjective } from '@/lib/api';
import { BossChatMessage, BossPlanProposal } from '@/types';

interface BossChatInterfaceProps {
  projectId?: string;
  onMissionLaunched?: (projectName: string) => void;
  className?: string;
  isDrawer?: boolean;
  onClose?: () => void;
}

const QUICK_PROMPTS = [
  { label: '📊 Company Health', prompt: 'Give me an executive briefing on company health and active operations.' },
  { label: '🚧 Active Blockers', prompt: 'Are there any active blockers or stalled issues right now?' },
  { label: '👥 Workforce Status', prompt: 'What is the current status of the workforce and verified capabilities?' },
  { label: '💰 Budget & Quota', prompt: 'What is our current monthly budget burn and available quota?' },
  { label: '🚀 Plan Mission', prompt: 'Plan a high-throughput event processing pipeline with automated verification.' },
];

/**
 * Lightweight accessible markdown formatter for Boss AI responses.
 * Formats headers, bold text, bullet lists, inline code, and code blocks
 * without bulky external dependencies.
 */
function MarkdownRenderer({ content }: { content: string }) {
  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeBlockLines: string[] = [];
  let codeBlockLang = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block toggle
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <div key={`code-${i}`} className="my-2 rounded-xl bg-[#050a14] border border-[#1b2b4a] overflow-hidden">
            {codeBlockLang && (
              <div className="px-3 py-1 bg-[#091122] border-b border-[#1b2b4a] text-[10px] font-mono text-slate-400 uppercase tracking-wider">
                {codeBlockLang}
              </div>
            )}
            <div className="p-3 font-mono text-[11px] text-sky-300 overflow-x-auto">
              <pre><code>{codeBlockLines.join('\n')}</code></pre>
            </div>
          </div>
        );
        codeBlockLines = [];
        inCodeBlock = false;
        codeBlockLang = '';
      } else {
        inCodeBlock = true;
        codeBlockLang = line.trim().slice(3);
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockLines.push(line);
      continue;
    }

    // Headers
    if (line.startsWith('### ')) {
      elements.push(
        <h3 key={i} className="text-xs font-bold text-sky-300 uppercase tracking-wider mt-3 mb-1.5 flex items-center space-x-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-sky-400"></span>
          <span>{line.slice(4)}</span>
        </h3>
      );
      continue;
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h2 key={i} className="text-sm font-bold text-white mt-3.5 mb-2">
          {line.slice(3)}
        </h2>
      );
      continue;
    }

    // Bullet points
    if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
      const bulletText = line.trim().slice(2);
      elements.push(
        <li key={i} className="ml-4 list-disc text-slate-300 text-xs leading-relaxed my-0.5 marker:text-sky-400">
          {renderFormattedInline(bulletText)}
        </li>
      );
      continue;
    }

    // Empty lines
    if (!line.trim()) {
      elements.push(<div key={i} className="h-1.5" />);
      continue;
    }

    // Normal paragraph
    elements.push(
      <p key={i} className="text-xs text-slate-200 leading-relaxed my-1">
        {renderFormattedInline(line)}
      </p>
    );
  }

  // Flush dangling code block if any
  if (inCodeBlock && codeBlockLines.length > 0) {
    elements.push(
      <div key="dangling-code" className="my-2 rounded-xl bg-[#050a14] border border-[#1b2b4a] overflow-hidden">
        {codeBlockLang && (
          <div className="px-3 py-1 bg-[#091122] border-b border-[#1b2b4a] text-[10px] font-mono text-slate-400 uppercase tracking-wider">
            {codeBlockLang}
          </div>
        )}
        <div className="p-3 font-mono text-[11px] text-sky-300 overflow-x-auto">
          <pre><code>{codeBlockLines.join('\n')}</code></pre>
        </div>
      </div>
    );
  }

  return <div className="space-y-0.5">{elements}</div>;
}

function renderFormattedInline(text: string): React.ReactNode {
  // Split on bold (**text**), inline code (`code`), and markdown links [text](url)
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*.*?\*\*|`.*?`|\[.*?\]\(.*?\))/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={match.index} className="font-semibold text-white">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className="px-1.5 py-0.5 mx-0.5 rounded bg-[#0b1528] border border-[#1b2b4a] text-sky-300 font-mono text-[11px]">
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('[') && token.includes('](') && token.endsWith(')')) {
      const linkMatch = token.match(/\[(.*?)\]\((.*?)\)/);
      if (linkMatch) {
        parts.push(
          <a
            key={match.index}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sky-400 underline hover:text-sky-300"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        parts.push(token);
      }
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}

export function BossChatInterface({
  projectId,
  onMissionLaunched,
  className = '',
  isDrawer = false,
  onClose,
}: BossChatInterfaceProps) {
  const [messages, setMessages] = useState<BossChatMessage[]>([
    {
      role: 'assistant',
      content:
        'Greetings Operator. I am **Boss AI**, Chief Executive Officer of ANCOS. ' +
        'I maintain real-time strategic oversight of company operations, workforce allocation, and task execution.\n\n' +
        'How can I assist you today? You can ask about **company health**, investigate **blockers**, ' +
        'inspect **workforce skills**, or ask me to **plan and launch a new mission**.',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [launchingIndex, setLaunchingIndex] = useState<number | null>(null);
  const [launchedProjects, setLaunchedProjects] = useState<Record<number, string>>({});
  const [launchError, setLaunchError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to latest message
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, sending, scrollToBottom]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputValue).trim();
    if (!text || sending) return;

    const userMessage: BossChatMessage = {
      role: 'user',
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setSending(true);
    setLaunchError(null);

    try {
      const response = await sendBossChatMessage(text, messages, projectId);
      if (response.conversation_history && response.conversation_history.length > 0) {
        setMessages(
          response.conversation_history.map((msg, idx) => ({
            ...msg,
            timestamp: msg.timestamp
              ? new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
              : messages[idx]?.timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          }))
        );
      } else {
        const assistantMessage: BossChatMessage = {
          role: 'assistant',
          content: response.reply,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          plan_proposal: response.plan_proposal,
          action: response.action,
        };
        setMessages((prev) => [...prev, assistantMessage]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `⚠️ **Communication Error**: ${err.message || 'Boss AI could not be reached.'}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // IME composition safety check per modern web guidance (ime-safe-enter-submit)
      if (e.nativeEvent.isComposing || (e as any).keyCode === 229) {
        return;
      }
      handleSendMessage();
    }
  };

  const handleLaunchPlan = async (plan: BossPlanProposal, messageIndex: number) => {
    try {
      setLaunchingIndex(messageIndex);
      setLaunchError(null);
      const project = await submitObjective(
        plan.objective,
        plan.title,
        undefined,
        plan.estimated_cost_usd,
        plan.deadline_days
      );
      setLaunchedProjects((prev) => ({
        ...prev,
        [messageIndex]: project.name || plan.title,
      }));
      if (onMissionLaunched) {
        onMissionLaunched(project.name || plan.title);
      }
    } catch (err: any) {
      setLaunchError(err.message || 'Failed to launch project DAG');
    } finally {
      setLaunchingIndex(null);
    }
  };

  const handleClearHistory = () => {
    setMessages([
      {
        role: 'assistant',
        content: 'Conversation history reset. Operator, what strategic objective shall we address?',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      },
    ]);
  };

  return (
    <div
      role="region"
      aria-label="Boss AI Executive Chat"
      className={`flex flex-col bg-[#070d18] border border-[#16233d] rounded-2xl shadow-2xl overflow-hidden ${className}`}
    >
      {/* ─────────────────────────────────────────────────────────────────────────────
          1. HEADER
      ───────────────────────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3.5 bg-gradient-to-r from-[#0a1222] via-[#0d172e] to-[#0a1222] border-b border-[#16233d]">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-500 via-indigo-600 to-blue-700 p-0.5 shadow-[0_0_15px_rgba(245,158,11,0.25)] flex items-center justify-center">
            <div className="w-full h-full bg-[#090f1d] rounded-[10px] flex items-center justify-center text-lg">
              👑
            </div>
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold text-white tracking-wide">Boss AI (CEO & Chief Architect)</span>
              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[9px] font-mono font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                <span>Active</span>
              </span>
            </div>
            <p className="text-[10px] text-slate-400">Autonomous Neural Company Operating System</p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={handleClearHistory}
            title="Reset Conversation"
            className="px-2.5 py-1 text-[10px] rounded-lg bg-[#0e172a] border border-[#1e2d4a] hover:bg-[#15233e] text-slate-400 hover:text-slate-200 transition-colors"
          >
            🧹 Reset
          </button>
          {isDrawer && onClose && (
            <button
              onClick={onClose}
              aria-label="Close Chat Drawer"
              className="w-7 h-7 rounded-lg bg-[#0e172a] border border-[#1e2d4a] hover:bg-rose-950/40 hover:border-rose-800/60 text-slate-400 hover:text-rose-400 flex items-center justify-center text-xs transition-colors"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          2. MESSAGE THREAD
      ───────────────────────────────────────────────────────────────────────────── */}
      <div
        className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4 max-h-[520px] min-h-[320px] scroll-smooth"
        role="log"
        aria-live="polite"
      >
        {messages.map((msg, idx) => {
          const isUser = msg.role === 'user';
          return (
            <div
              key={idx}
              className={`flex items-start space-x-3 ${isUser ? 'flex-row-reverse space-x-reverse' : 'flex-row'}`}
            >
              {/* Avatar */}
              <div
                className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs shrink-0 select-none shadow-md ${
                  isUser
                    ? 'bg-gradient-to-br from-sky-600 to-blue-700 text-white border border-sky-400/40'
                    : 'bg-gradient-to-br from-amber-500 to-indigo-800 text-white border border-amber-400/40'
                }`}
              >
                {isUser ? '👤' : '👑'}
              </div>

              {/* Message Bubble */}
              <div className={`max-w-[85%] sm:max-w-[78%] space-y-2 ${isUser ? 'items-end' : 'items-start'}`}>
                <div
                  className={`rounded-2xl px-4 py-3 text-xs shadow-lg transition-all ${
                    isUser
                      ? 'bg-gradient-to-r from-sky-900/40 to-blue-900/40 border border-sky-700/50 text-white rounded-tr-none'
                      : 'bg-[#0b1325] border border-[#192642] text-slate-200 rounded-tl-none'
                  }`}
                >
                  {isUser ? (
                    <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                  ) : (
                    <MarkdownRenderer content={msg.content} />
                  )}
                </div>

                {/* Plan Proposal Action Card */}
                {msg.plan_proposal && (
                  <div className="mt-2.5 p-4 rounded-xl bg-gradient-to-br from-[#0c162b] to-[#07101f] border border-cyan-500/30 shadow-[0_0_20px_rgba(6,182,212,0.15)] space-y-3">
                    <div className="flex items-center justify-between border-b border-[#18294b] pb-2">
                      <div className="flex items-center space-x-2">
                        <span className="text-base">🚀</span>
                        <h4 className="text-xs font-bold text-cyan-300 tracking-wide uppercase">
                          Mission DAG Blueprint
                        </h4>
                      </div>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-cyan-950/60 text-cyan-300 border border-cyan-700/50">
                        ${msg.plan_proposal.estimated_cost_usd?.toFixed(2) || '15.00'} USD
                      </span>
                    </div>

                    <div>
                      <h5 className="text-xs font-bold text-white mb-0.5">{msg.plan_proposal.title}</h5>
                      <p className="text-[11px] text-slate-400 line-clamp-2">{msg.plan_proposal.objective}</p>
                    </div>

                    {/* Phases Breakdown */}
                    {msg.plan_proposal.phases && msg.plan_proposal.phases.length > 0 && (
                      <div className="space-y-1.5 pt-1">
                        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block">
                          Phases ({msg.plan_proposal.phases.length})
                        </span>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                          {msg.plan_proposal.phases.map((phase, pIdx) => (
                            <div
                              key={pIdx}
                              className="px-2.5 py-1.5 rounded-lg bg-[#070e1b] border border-[#182845] text-[11px]"
                            >
                              <div className="font-semibold text-sky-300 truncate">{phase.name}</div>
                              <div className="text-[10px] text-slate-400 truncate">
                                {phase.tasks?.length || 0} task(s)
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Required Skills Chips */}
                    {msg.plan_proposal.required_skills && msg.plan_proposal.required_skills.length > 0 && (
                      <div className="flex flex-wrap gap-1 pt-1">
                        {msg.plan_proposal.required_skills.map((skill, sIdx) => (
                          <span
                            key={sIdx}
                            className="px-2 py-0.5 rounded-md bg-[#0a1529] border border-[#1c335a] text-[10px] font-mono text-cyan-300"
                          >
                            {skill}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Launch Button / Confirmation */}
                    {launchedProjects[idx] ? (
                      <div className="p-2.5 rounded-lg bg-emerald-950/40 border border-emerald-700/50 text-emerald-400 text-xs font-semibold flex items-center space-x-2">
                        <span>✓</span>
                        <span>Project DAG Launched: {launchedProjects[idx]}</span>
                      </div>
                    ) : (
                      <button
                        onClick={() => handleLaunchPlan(msg.plan_proposal!, idx)}
                        disabled={launchingIndex === idx}
                        className="w-full mt-1 px-4 py-2 bg-gradient-to-r from-cyan-600 via-sky-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 disabled:opacity-50 text-white font-bold text-xs rounded-xl shadow-[0_0_15px_rgba(6,182,212,0.3)] transition-all flex items-center justify-center space-x-2"
                      >
                        {launchingIndex === idx ? (
                          <>
                            <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                            <span>Decomposing & Persisting DAG...</span>
                          </>
                        ) : (
                          <>
                            <span>🚀</span>
                            <span>Launch Project DAG</span>
                          </>
                        )}
                      </button>
                    )}
                  </div>
                )}

                {/* Timestamp */}
                <div className={`text-[10px] text-slate-500 px-1 ${isUser ? 'text-right' : 'text-left'}`}>
                  {msg.timestamp}
                </div>
              </div>
            </div>
          );
        })}

        {/* Thinking Indicator */}
        {sending && (
          <div className="flex items-start space-x-3">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber-500 to-indigo-800 text-white border border-amber-400/40 flex items-center justify-center text-xs shrink-0 select-none shadow-md">
              👑
            </div>
            <div className="bg-[#0b1325] border border-[#192642] rounded-2xl rounded-tl-none px-4 py-3 text-xs text-slate-300 flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-sky-400 animate-ping"></span>
              <span className="text-[11px] text-slate-400">Boss AI is analyzing live telemetry and formulating strategy...</span>
            </div>
          </div>
        )}

        {launchError && (
          <div className="p-2.5 rounded-lg bg-rose-950/40 border border-rose-800/60 text-rose-400 text-xs">
            ⚠️ {launchError}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          3. QUICK PROMPT SUGGESTION PILLS
      ───────────────────────────────────────────────────────────────────────────── */}
      <div className="px-4 py-2 border-t border-[#141f36] bg-[#070d18] flex items-center space-x-1.5 overflow-x-auto scrollbar-none">
        <span className="text-[10px] font-semibold text-slate-500 shrink-0 uppercase tracking-wider">Quick Prompts:</span>
        {QUICK_PROMPTS.map((qp, qIdx) => (
          <button
            key={qIdx}
            disabled={sending}
            onClick={() => handleSendMessage(qp.prompt)}
            className="px-2.5 py-1 rounded-lg bg-[#0b1424] border border-[#18294a] hover:border-sky-500/50 hover:bg-[#101e38] text-[10px] text-slate-300 hover:text-white transition-all whitespace-nowrap shrink-0 disabled:opacity-50"
          >
            {qp.label}
          </button>
        ))}
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          4. INPUT AREA
      ───────────────────────────────────────────────────────────────────────────── */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSendMessage();
        }}
        className="p-3 bg-[#0a1120] border-t border-[#16233d]"
      >
        <label htmlFor="boss-chat-input" className="sr-only">
          Message to Boss AI
        </label>
        <div className="relative flex items-center">
          <textarea
            id="boss-chat-input"
            ref={inputRef}
            rows={1}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="Ask Boss AI about strategy, blockers, or type: 'Plan a mission to...'"
            placeholder="Ask Boss AI about strategy, blockers, or type: 'Plan a mission to...'"
            disabled={sending}
            className="w-full bg-[#050912] border border-[#1b2b4a] focus:border-sky-500 focus:ring-1 focus:ring-sky-500 rounded-xl pl-4 pr-24 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none transition-all resize-none min-h-[42px] max-h-[120px]"
          />
          <button
            type="submit"
            disabled={sending || !inputValue.trim()}
            className="absolute right-2 px-3 py-1.5 bg-gradient-to-r from-sky-600 to-blue-600 hover:from-sky-500 hover:to-blue-500 disabled:opacity-40 text-white font-bold text-xs rounded-lg transition-all flex items-center space-x-1"
          >
            {sending ? (
              <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            ) : (
              <>
                <span>Send</span>
                <span className="text-[11px]">↵</span>
              </>
            )}
          </button>
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1.5 px-1">
          <span>Enter to send • Shift+Enter for new line</span>
          <span className="font-mono text-slate-400">Grounded in Live System Truth</span>
        </div>
      </form>
    </div>
  );
}

