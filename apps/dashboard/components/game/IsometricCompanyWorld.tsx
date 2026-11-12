'use client';

import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  fetchEmployees,
  fetchProjects,
  fetchMeetings,
  fetchMessages,
  fetchEmployeeTasks,
  triggerDailyStandup,
  fetchWorkstationVncToken,
} from '@/lib/api';
import {
  GameCharacter,
  WorkstationDef,
  MeetingSeatDef,
  CommsPacket,
  SpeechBubble,
  CameraState,
} from './types';
import {
  WORKSTATIONS,
  MEETING_SEATS,
  GRID_SIZE_X,
  GRID_SIZE_Y,
  ROOM_ZONES,
  RoomZoneDef,
} from './OfficeLayout';
import {
  gridToScreen,
  screenToGrid,
  getRenderDepth,
  gridDistance,
  TILE_WIDTH,
  TILE_HEIGHT,
} from './IsometricMath';
import { toIngressDesktopUrl } from '@/lib/desktopUrl';
import {
  drawFloorAndArchitecture,
  drawCentralAIHub,
  drawWorkstation,
  drawServerRacks,
  drawBoardroomTable,
} from './EnvironmentRenderer';
import { drawCharacter } from './CharacterRenderer';
import {
  updateCharacterMovement,
  findPath,
} from './MovementEngine';
import {
  drawSpeechBubbles,
  drawCommsPackets,
} from './CommunicationVisualizer';
import {
  callCharactersToMeeting,
  returnCharactersFromMeeting,
  MeetingSession,
} from './MeetingVisualizer';
import { syncEmployeesToCharacters } from './EmployeeCharacterManager';
import { BossChatDrawer } from '../boss/BossChatDrawer';

export const IsometricCompanyWorld: React.FC = () => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Boss Executive Chat Drawer state
  const [showBossChat, setShowBossChat] = useState(false);

  // Core World State
  const [workstations, setWorkstations] = useState<WorkstationDef[]>(() =>
    WORKSTATIONS.map((w) => ({ ...w }))
  );
  const [meetingSeats, setMeetingSeats] = useState<MeetingSeatDef[]>(() =>
    MEETING_SEATS.map((s) => ({ ...s }))
  );
  const [characters, setCharacters] = useState<Map<string, GameCharacter>>(new Map());
  const [commsPackets, setCommsPackets] = useState<CommsPacket[]>([]);
  const [activeMeeting, setActiveMeeting] = useState<MeetingSession | null>(null);
  const [backendMeetings, setBackendMeetings] = useState<any[]>([]);

  // Interaction State
  const [selectedCharId, setSelectedCharId] = useState<string | null>(null);
  const [hoveredCharId, setHoveredCharId] = useState<string | null>(null);
  const [selectedWsId, setSelectedWsId] = useState<string | null>(null);
  const [selectedDeptId, setSelectedDeptId] = useState<string | null>(null);
  const [isBoardroomSelected, setIsBoardroomSelected] = useState(false);

  const [camera, setCamera] = useState<CameraState>({ x: 0, y: 0, zoom: 0.9 });
  const fitZoomRef = useRef(0.9);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  // Floating HUD metrics
  // The page already shows company metrics; keep the detailed card collapsed so it does not cover the floor.
  const [showOverviewCard, setShowOverviewCard] = useState(false);
  const [metrics, setMetrics] = useState({
    employeeCount: null as number | null,
    activeProjects: null as number | null,
  });

  const [sseStatus, setSseStatus] = useState<'connecting' | 'connected' | 'reconnecting' | 'unavailable'>('connecting');
  const [lastEventId, setLastEventId] = useState<string | null>(null);
  const [showVncModal, setShowVncModal] = useState(false);
  const [vncViewOnly, setVncViewOnly] = useState(true);
  const [verifiedDeliverable, setVerifiedDeliverable] = useState<{
    taskId: string;
    sha256Digest: string;
    verifiedAt: string;
    receiptSignature?: string;
  } | null>(null);
  const processedEventIdsRef = useRef(new Set<string>());

  const [vncTokenUrl, setVncTokenUrl] = useState<string | null>(null);
  const [vncLoading, setVncLoading] = useState(false);
  const [vncError, setVncError] = useState<string | null>(null);

  const loadVncToken = useCallback(async (employeeId: string, interactive: boolean) => {
    setVncLoading(true);
    setVncError(null);
    try {
      const data = await fetchWorkstationVncToken(employeeId, interactive);
      const raw = interactive ? data.desktop_url : (data.read_only_desktop_url || data.desktop_url);
      setVncTokenUrl(toIngressDesktopUrl(raw));
    } catch {
      setVncTokenUrl(null);
      setVncError('Workstation container offline or token unavailable.');
    } finally {
      setVncLoading(false);
    }
  }, []);

  const isInitialSyncRef = useRef(true);
  const processedMessageIdsRef = useRef(new Set<string>());
  const hasLoadedMessageHistoryRef = useRef(false);
  const [recentMessages, setRecentMessages] = useState<Array<{
    id: string;
    sender: string;
    recipient: string | null;
    content: string;
    createdAt: string;
  }>>([]);

  // Keep references for animation loop without closure staleness
  const stateRef = useRef({
    characters,
    workstations,
    meetingSeats,
    commsPackets,
    activeMeeting,
    camera,
    selectedCharId,
    hoveredCharId,
    selectedWsId,
    selectedDeptId,
    isBoardroomSelected,
  });

  useEffect(() => {
    stateRef.current = {
      characters,
      workstations,
      meetingSeats,
      commsPackets,
      activeMeeting,
      camera,
      selectedCharId,
      hoveredCharId,
      selectedWsId,
      selectedDeptId,
      isBoardroomSelected,
    };
  }, [
    characters,
    workstations,
    meetingSeats,
    commsPackets,
    activeMeeting,
    camera,
    selectedCharId,
    hoveredCharId,
    selectedWsId,
    selectedDeptId,
    isBoardroomSelected,
  ]);

  // ─────────────────────────────────────────────────────────────────────────────
  // 1. BACKEND SYNC (Persisted employees, tasks, projects, meetings, communications)
  // ─────────────────────────────────────────────────────────────────────────────
  const syncBackendData = useCallback(async () => {
    try {
      // 1. Fetch persisted projects.
      const projects = await fetchProjects().catch(() => null);
      if (Array.isArray(projects)) {
        setMetrics((prev) => ({
          ...prev,
          activeProjects: projects.length,
        }));
      }

      // 2. Fetch persisted employee records.
      const employees = await fetchEmployees().catch(() => null);
      if (Array.isArray(employees)) {
        // Real count of recruited employees
        setMetrics((prev) => ({
          ...prev,
          employeeCount: employees.length,
        }));
      }

      const employeesWithTasks = Array.isArray(employees)
        ? await Promise.all(employees.map(async (employee: any) => {
            const tasks = await fetchEmployeeTasks(employee.employee_id).catch(() => []);
            const activeTasks = Array.isArray(tasks)
              ? tasks.filter((task: any) => ['assigned', 'running', 'retrying', 'blocked'].includes(String(task.status).toLowerCase()))
              : [];
            const taskRank = (status: string) => {
              const normalized = status.toLowerCase();
              return normalized === 'running' ? 3 : normalized === 'retrying' ? 2 : normalized === 'assigned' ? 1 : 0;
            };
            activeTasks.sort((a: any, b: any) => taskRank(String(b.status)) - taskRank(String(a.status)));
            const activeTask = activeTasks[0];
            return {
              ...employee,
              current_task: activeTask?.title || null,
              current_task_status: activeTask?.status || null,
            };
          }))
        : null;

      setCharacters((prevChars) => {
        if (Array.isArray(employees)) {
          const isBootstrap = isInitialSyncRef.current;
          isInitialSyncRef.current = false;
          return syncEmployeesToCharacters(employeesWithTasks || [], prevChars, workstations, isBootstrap);
        }
        return prevChars;
      });

      // 4. Fetch persisted communication records; only unseen records animate.
      const msgs = await fetchMessages(20).catch(() => null);
      if (Array.isArray(msgs)) {
        const persisted = msgs.flatMap((message: any) => {
          const content = typeof message.payload?.content === 'string'
            ? message.payload.content
            : typeof message.payload === 'string'
              ? message.payload
              : '';
          if (!message.message_id || !message.sender || !content) return [];
          return [{
            id: message.message_id,
            sender: message.sender,
            recipient: message.recipient || null,
            content,
            createdAt: message.created_at,
          }];
        });

        const unseen = persisted.filter((message) => !processedMessageIdsRef.current.has(message.id));
        for (const message of unseen) processedMessageIdsRef.current.add(message.id);

        if (!hasLoadedMessageHistoryRef.current) {
          hasLoadedMessageHistoryRef.current = true;
          setRecentMessages(persisted.slice(0, 8));
        } else if (unseen.length > 0) {
          setRecentMessages((previous) => [...unseen, ...previous].slice(0, 8));
          const newPackets: CommsPacket[] = unseen.flatMap((message) => {
            const sender = stateRef.current.characters.get(message.sender);
            const recipient = message.recipient ? stateRef.current.characters.get(message.recipient) : null;
            if (!sender || !recipient) return [];
            return [{
              id: `comms-${message.id}`,
              fromCharId: sender.id,
              toCharId: recipient.id,
              fromScreenX: 0,
              fromScreenY: 0,
              toScreenX: 0,
              toScreenY: 0,
              progress: 0,
              speed: 1.0,
              textSnippet: message.content.slice(0, 24),
              color: '#38bdf8',
            }];
          });
          setCharacters((previous) => {
            const next = new Map(previous);
            for (const message of unseen) {
              const sender = next.get(message.sender);
              const recipient = message.recipient ? next.get(message.recipient) : null;
              if (sender) {
                sender.speechText = message.content;
                sender.speechRemainingMs = 6000;
                sender.animState = 'speaking';
              }
              if (recipient) {
                recipient.speechText = message.content;
                recipient.speechRemainingMs = 6000;
                recipient.animState = 'listening';
              }
            }
            return next;
          });
          if (newPackets.length > 0) {
            setCommsPackets((previousPackets) => [...previousPackets, ...newPackets]);
          }
        }
      }

      // 5. Fetch Real Meetings
      const meetings = await fetchMeetings(10).catch(() => null);
      if (Array.isArray(meetings)) {
        setBackendMeetings(meetings);
        const inProgress = meetings.find((m: any) => m.status === 'in_progress');
        if (inProgress) {
          if (!stateRef.current.activeMeeting || stateRef.current.activeMeeting.id !== inProgress.meeting_id) {
            const pIds = Array.isArray(inProgress.participants)
              ? inProgress.participants.filter((id: string) => stateRef.current.characters.has(id))
              : [];

            const session = callCharactersToMeeting(
              inProgress.meeting_id,
              inProgress.title,
              pIds,
              stateRef.current.characters,
              stateRef.current.meetingSeats
            );
            setActiveMeeting(session);
          }
        } else if (stateRef.current.activeMeeting) {
          returnCharactersFromMeeting(
            stateRef.current.activeMeeting,
            stateRef.current.characters,
            workstations,
            meetingSeats
          );
          setActiveMeeting(null);
        }
      }
    } catch (e) {
      // Tolerate periodic network tick failures
    }
  }, [workstations, meetingSeats]);

  // Initial Sync + Periodic 4s polling
  useEffect(() => {
    syncBackendData();
    const interval = setInterval(syncBackendData, 4000);
    return () => clearInterval(interval);
  }, [syncBackendData]);

  // Live SSE stream connection with monotonic replay & Last-Event-ID handling
  useEffect(() => {
    let eventSource: EventSource | null = null;
    let isMounted = true;
    let currentLastEventId: string | null = null;

    const handleIncomingEvent = (eventData: any) => {
      if (!eventData || !eventData.event_id) return;
      if (processedEventIdsRef.current.has(eventData.event_id)) return;
      processedEventIdsRef.current.add(eventData.event_id);
      currentLastEventId = eventData.event_id;
      setLastEventId(eventData.event_id);

      const type = eventData.event_type;
      const data = eventData.data || {};

      if (type === 'employee_recruited') {
        syncBackendData();
      } else if (type === 'task_started' || type === 'task_assigned') {
        const empId = data.employee_id || data.assigned_to;
        if (empId) {
          setCharacters((prev) => {
            const next = new Map(prev);
            const char = next.get(empId);
            if (char) {
              char.currentTask = data.task_title || data.title || char.currentTask;
              char.currentTaskStatus = 'running';
              char.animState = 'working';
            }
            return next;
          });
        }
      } else if (type === 'deliverable_verified' || type === 'task_completed') {
        if (data.sha256_deliverable_digest || data.artifact_hash) {
          setVerifiedDeliverable({
            taskId: data.task_id || 'TASK-DELIVERABLE',
            sha256Digest: data.sha256_deliverable_digest || data.artifact_hash,
            verifiedAt: data.verified_at || new Date().toISOString(),
            receiptSignature: data.receipt_signature || data.signature,
          });
        }
        const empId = data.employee_id || data.assigned_to;
        if (empId) {
          setCharacters((prev) => {
            const next = new Map(prev);
            const char = next.get(empId);
            if (char) {
              char.currentTaskStatus = 'completed';
              char.animState = 'idle';
            }
            return next;
          });
        }
      } else if (type === 'meeting_started' || type === 'meeting_concluded') {
        syncBackendData();
      } else if (type === 'meeting_speaker_changed') {
        const speakerId = data.active_speaker_id || data.speaker_id;
        if (speakerId) {
          setCharacters((prev) => {
            const next = new Map(prev);
            for (const [id, c] of next.entries()) {
              if (id === speakerId) {
                c.animState = 'speaking';
                c.speechText = data.statement || c.speechText;
                c.speechRemainingMs = 4000;
              } else if (c.currentMeetingSeatIndex !== null) {
                c.animState = 'listening';
              }
            }
            return next;
          });
        }
      }
    };

    const connectSSE = () => {
      if (!isMounted) return;
      try {
        const streamUrl = `/api/ancos/api/v1/events/stream${currentLastEventId ? `?last_event_id=${encodeURIComponent(currentLastEventId)}` : ''}`;
        eventSource = new EventSource(streamUrl);

        eventSource.onopen = () => {
          if (!isMounted) return;
          setSseStatus('connected');
        };

        eventSource.onmessage = (event) => {
          if (!isMounted || !event.data) return;
          try {
            if (event.lastEventId) {
              currentLastEventId = event.lastEventId;
              setLastEventId(event.lastEventId);
            }
            const payload = JSON.parse(event.data);
            handleIncomingEvent(payload);
          } catch {
            // Keepalive comment / parse skip
          }
        };

        const eventNames = [
          'employee_recruited',
          'task_assigned',
          'task_started',
          'task_completed',
          'deliverable_verified',
          'meeting_started',
          'meeting_speaker_changed',
          'meeting_concluded',
          'workstation_status',
          'system_status',
        ];

        eventNames.forEach((name) => {
          eventSource?.addEventListener(name, (e: any) => {
            if (!isMounted || !e.data) return;
            try {
              if (e.lastEventId) {
                currentLastEventId = e.lastEventId;
                setLastEventId(e.lastEventId);
              }
              const payload = JSON.parse(e.data);
              handleIncomingEvent(payload);
            } catch {
              // Parse error skip
            }
          });
        });

        eventSource.onerror = () => {
          if (!isMounted) return;
          setSseStatus('reconnecting');
          eventSource?.close();
          setTimeout(connectSSE, 4000);
        };
      } catch {
        setSseStatus('unavailable');
      }
    };

    connectSSE();

    return () => {
      isMounted = false;
      eventSource?.close();
    };
  }, [syncBackendData]);

  // ─────────────────────────────────────────────────────────────────────────────
  // 3. MASTER CANVAS RENDER LOOP (Layered top-down office world)
  // ─────────────────────────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animId: number;
    let lastTime = performance.now();

    const resize = () => {
      if (!canvas.parentElement) return;
      canvas.width = canvas.parentElement.clientWidth;
      canvas.height = canvas.parentElement.clientHeight || 680;
      fitZoomRef.current = Math.min(
        1.05,
        (canvas.width - 48) / (GRID_SIZE_X * TILE_WIDTH),
        (canvas.height - 92) / (GRID_SIZE_Y * TILE_HEIGHT)
      );
      setCamera((current) => ({ ...current, x: 0, y: 0, zoom: fitZoomRef.current }));
    };
    resize();
    window.addEventListener('resize', resize);

    const render = (now: number) => {
      const dt = Math.min((now - lastTime) / 1000, 0.1);
      lastTime = now;
      const time = now / 1000;

      const current = stateRef.current;

      // Update character movements & animations
      let stateChanged = false;
      for (const char of current.characters.values()) {
        const prevAnim = char.animState;
        updateCharacterMovement(char, dt);
        if (char.animState !== prevAnim && char.id === current.selectedCharId) {
          stateChanged = true;
        }
      }
      if (stateChanged) {
        setCharacters(new Map(current.characters));
      }

      // Update communication packets progress
      if (current.commsPackets.length > 0) {
        setCommsPackets((prev) =>
          prev
            .map((p) => ({ ...p, progress: p.progress + dt * p.speed }))
            .filter((p) => p.progress <= 1.0)
        );
      }

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      ctx.save();
      // Apply Camera Transform
      const originX = canvas.width / 2 - ((GRID_SIZE_X - 1) * TILE_WIDTH) / 2 + current.camera.x;
      const originY = canvas.height / 2 - ((GRID_SIZE_Y - 1) * TILE_HEIGHT) / 2 + current.camera.y;

      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.scale(current.camera.zoom, current.camera.zoom);
      ctx.translate(-canvas.width / 2, -canvas.height / 2);

      // ─── LAYER 1: FLOOR & ROOM ARCHITECTURE ───
      drawFloorAndArchitecture(ctx, originX, originY, time);

      // Highlight selected department zone if active
      if (current.selectedDeptId) {
        const zone = ROOM_ZONES.find((z) => z.id === current.selectedDeptId);
        if (zone) {
          ctx.save();
          ctx.strokeStyle = zone.neonColor;
          ctx.lineWidth = 2.5;
          ctx.shadowColor = zone.neonColor;
          ctx.shadowBlur = 14;
          const p1 = gridToScreen(zone.minX, zone.minY, 0, originX, originY);
          const p2 = gridToScreen(zone.maxX, zone.minY, 0, originX, originY);
          const p3 = gridToScreen(zone.maxX, zone.maxY, 0, originX, originY);
          const p4 = gridToScreen(zone.minX, zone.maxY, 0, originX, originY);
          ctx.beginPath();
          ctx.moveTo(p1.screenX, p1.screenY);
          ctx.lineTo(p2.screenX, p2.screenY);
          ctx.lineTo(p3.screenX, p3.screenY);
          ctx.lineTo(p4.screenX, p4.screenY);
          ctx.closePath();
          ctx.stroke();
          ctx.restore();
        }
      }

      // ─── LAYER 2: CENTRAL AI HUB & PEDESTAL ───
      drawCentralAIHub(ctx, originX, originY, time);

      // ─── LAYER 3: SERVER RACKS ───
      drawServerRacks(ctx, originX, originY, time);

      // ─── LAYER 4: BOARDROOM CONFERENCE TABLE & HOLOGRAM ───
      drawBoardroomTable(
        ctx,
        originX,
        originY,
        time,
        current.activeMeeting !== null,
        current.activeMeeting?.title ?? ''
      );

      // Highlight boardroom when clicked
      if (current.isBoardroomSelected) {
        const p = gridToScreen(12, 5, 0, originX, originY);
        ctx.save();
        ctx.strokeStyle = '#e879f9';
        ctx.lineWidth = 2.5;
        ctx.shadowColor = '#e879f9';
        ctx.shadowBlur = 14;
        ctx.beginPath();
        ctx.ellipse(p.screenX, p.screenY - 4, 60, 30, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      // ─── LAYER 5: WORKSTATIONS (Desks, Monitors, Chairs) ───
      for (const ws of current.workstations) {
        drawWorkstation(ctx, ws, originX, originY, time);
      }

      // Highlight selected workstation
      if (current.selectedWsId) {
        const ws = current.workstations.find((w) => w.id === current.selectedWsId);
        if (ws) {
          const p = gridToScreen(ws.deskGridX, ws.deskGridY, 0, originX, originY);
          ctx.save();
          ctx.strokeStyle = '#38bdf8';
          ctx.lineWidth = 2;
          ctx.shadowColor = '#38bdf8';
          ctx.shadowBlur = 10;
          ctx.beginPath();
          ctx.ellipse(p.screenX, p.screenY - 2, 28, 14, 0, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }
      }

      // ─── LAYER 6: INDEPENDENT ANIMATED CHARACTERS (Isometric Depth Sorted) ───
      const sortedCharacters = Array.from(current.characters.values()).sort(
        (a, b) => getRenderDepth(a.gridX, a.gridY) - getRenderDepth(b.gridX, b.gridY)
      );

      for (const char of sortedCharacters) {
        drawCharacter(
          ctx,
          char,
          originX,
          originY,
          current.selectedCharId === char.id,
          current.hoveredCharId === char.id
        );
      }

      // ─── LAYER 7: DIGITAL COMMS DATA PACKETS ───
      drawCommsPackets(
        ctx,
        current.commsPackets,
        current.characters,
        originX,
        originY
      );

      // ─── LAYER 8: DYNAMIC SPEECH BUBBLES ───
      const activeBubbles: SpeechBubble[] = [];
      for (const char of current.characters.values()) {
        if (char.speechText && char.speechRemainingMs > 0) {
          activeBubbles.push({
            id: `bubble-${char.id}`,
            charId: char.id,
            text: char.speechText,
            color: char.theme.accentColor,
            createdAt: now,
            durationMs: char.speechRemainingMs,
          });
        }
      }
      drawSpeechBubbles(
        ctx,
        activeBubbles,
        current.characters,
        originX,
        originY
      );

      ctx.restore();
      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);
    return () => {
      window.removeEventListener('resize', resize);
      cancelAnimationFrame(animId);
    };
  }, []);

  // ─────────────────────────────────────────────────────────────────────────────
  // 4. INTERACTION MANAGERS: HIT-TESTING, DRAGGING, ZOOMING (Requirement #7)
  // ─────────────────────────────────────────────────────────────────────────────
  const getCanvasMousePos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const mouseX = (e.clientX - rect.left - cx) / camera.zoom + cx;
    const mouseY = (e.clientY - rect.top - cy) / camera.zoom + cy;
    return { x: mouseX, y: mouseY };
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    setIsDragging(true);
    setDragStart({ x: e.clientX - camera.x, y: e.clientY - camera.y });
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isDragging) {
      setCamera((prev) => ({
        ...prev,
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      }));
      return;
    }

    const canvas = canvasRef.current;
    if (!canvas) return;
    const { x: mouseX, y: mouseY } = getCanvasMousePos(e);
    const originX = canvas.width / 2 - ((GRID_SIZE_X - 1) * TILE_WIDTH) / 2 + camera.x;
    const originY = canvas.height / 2 - ((GRID_SIZE_Y - 1) * TILE_HEIGHT) / 2 + camera.y;

    // Hit test characters for hover rings
    let hitChar: string | null = null;
    for (const char of characters.values()) {
      const p = gridToScreen(char.gridX, char.gridY, 0, originX, originY);
      const dist = Math.hypot(mouseX - p.screenX, mouseY - (p.screenY - 38));
      if (dist < 36) {
        hitChar = char.id;
        break;
      }
    }
    setHoveredCharId(hitChar);
  };

  const handleMouseUp = () => setIsDragging(false);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { x: mouseX, y: mouseY } = getCanvasMousePos(e);
    const originX = canvas.width / 2 - ((GRID_SIZE_X - 1) * TILE_WIDTH) / 2 + camera.x;
    const originY = canvas.height / 2 - ((GRID_SIZE_Y - 1) * TILE_HEIGHT) / 2 + camera.y;

    // 1. Check Character Click (highest priority)
    for (const char of characters.values()) {
      const p = gridToScreen(char.gridX, char.gridY, 0, originX, originY);
      const dist = Math.hypot(mouseX - p.screenX, mouseY - (p.screenY - 38));
      if (dist < 38) {
        setSelectedCharId(char.id);
        setSelectedWsId(null);
        setSelectedDeptId(null);
        setIsBoardroomSelected(false);
        return;
      }
    }

    // 2. Check Workstation Click (second priority)
    for (const ws of workstations) {
      const p = gridToScreen(ws.deskGridX, ws.deskGridY, 0, originX, originY);
      const dist = Math.hypot(mouseX - p.screenX, mouseY - (p.screenY - 10));
      if (dist < 32) {
        setSelectedWsId(ws.id);
        setSelectedCharId(null); // Show workstation inspector
        setSelectedDeptId(null);
        setIsBoardroomSelected(false);
        return;
      }
    }

    // 3. Check Boardroom Table Click (third priority)
    const boardroomScreen = gridToScreen(12, 5, 0, originX, originY);
    if (Math.hypot(mouseX - boardroomScreen.screenX, mouseY - boardroomScreen.screenY) < 65) {
      setIsBoardroomSelected(true);
      setSelectedCharId(null);
      setSelectedWsId(null);
      setSelectedDeptId(null);
      return;
    }

    // 4. Check Room / Department Zone Click by continuous grid
    const gridPos = screenToGrid(mouseX, mouseY, originX, originY);
    for (const zone of ROOM_ZONES) {
      if (
        gridPos.x >= zone.minX &&
        gridPos.x <= zone.maxX &&
        gridPos.y >= zone.minY &&
        gridPos.y <= zone.maxY
      ) {
        if (zone.id === 'boardroom') {
          setIsBoardroomSelected(true);
          setSelectedCharId(null);
          setSelectedWsId(null);
          setSelectedDeptId(null);
        } else {
          setSelectedDeptId(zone.id);
          setIsBoardroomSelected(false);
          setSelectedCharId(null);
          setSelectedWsId(null);
        }
        return;
      }
    }

    // Deselect if clicking on open corridor
    setSelectedCharId(null);
    setSelectedWsId(null);
    setSelectedDeptId(null);
    setIsBoardroomSelected(false);
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.08 : 0.92;
    setCamera((prev) => ({
      ...prev,
      zoom: Math.min(Math.max(prev.zoom * factor, 0.45), 2.2),
    }));
  };

  // Resolve target objects for inspector
  const selectedCharacter = characters.get(selectedCharId || '');
  const selectedWorkstation = workstations.find((w) => w.id === selectedWsId);
  const selectedWorkstationOccupant = selectedWorkstation?.occupantId
    ? characters.get(selectedWorkstation.occupantId)
    : undefined;
  const selectedDept = ROOM_ZONES.find((z) => z.id === selectedDeptId);
  const deptWorkstations = selectedDept ? workstations.filter((w) => w.dept === selectedDept.dept) : [];
  const deptWorkstationOccupants = new Set(deptWorkstations.map((ws) => ws.occupantId).filter(Boolean));
  const deptEmployees = selectedDept
    ? Array.from(characters.values()).filter((character) => deptWorkstationOccupants.has(character.id))
    : [];

  return (
    <div
      ref={containerRef}
      className="relative h-[620px] w-full bg-[radial-gradient(ellipse_at_50%_45%,#f2ede2_0%,#ddd8cc_58%,#c7c6bf_100%)] sm:h-[680px] lg:h-[700px] overflow-hidden select-none"
    >
      {/* ─────────────────────────────────────────────────────────────────────────────
          1. TOP-LEFT OVERLAY: COMPANY OVERVIEW, SSE LIVE STATUS & VERIFIED RECEIPTS
      ───────────────────────────────────────────────────────────────────────────── */}
      <div className="absolute top-4 left-4 z-20 flex flex-col gap-2 pointer-events-auto">
        <div className="flex items-center space-x-2 px-3 py-1.5 rounded-xl bg-[#091122]/90 border border-[#162540] text-xs font-mono shadow-lg">
          <span className={`w-2 h-2 rounded-full ${sseStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : sseStatus === 'reconnecting' ? 'bg-amber-400 animate-ping' : 'bg-rose-400'}`} />
          <span className="text-slate-300 uppercase">{sseStatus === 'connected' ? 'SSE LIVE' : sseStatus === 'reconnecting' ? 'RECONNECTING' : 'OFFLINE'}</span>
          {lastEventId && <span className="text-slate-500 text-[10px]">({lastEventId})</span>}
        </div>
        {verifiedDeliverable && (
          <div className="flex items-center space-x-2 px-3 py-1.5 rounded-xl bg-emerald-950/90 border border-emerald-500/40 text-xs font-mono shadow-lg text-emerald-200">
            <span className="text-emerald-400 font-bold">✓ VERIFIED:</span>
            <span>{verifiedDeliverable.taskId}</span>
            <span className="text-emerald-300 text-[10px]">[{verifiedDeliverable.sha256Digest.slice(0, 10)}...]</span>
          </div>
        )}
      </div>

      {showOverviewCard && (
        <div className="absolute left-4 top-4 z-20 w-56 pointer-events-auto">
          {/* Company Overview Card */}
          <div className="bg-[#091122]/90 backdrop-blur-md border border-[#162540] rounded-2xl p-4 shadow-2xl text-slate-200">
            <div className="flex items-center justify-between border-b border-[#132038] pb-2 mb-3">
              <span className="text-xs font-bold text-white tracking-wide">Company Overview</span>
              <button
                onClick={() => setShowOverviewCard(false)}
                className="text-slate-400 hover:text-white text-xs px-1"
                title="Minimize Overview"
              >
                ✕
              </button>
            </div>

            <div className="space-y-2.5 text-xs font-medium">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-slate-300">
                  <span className="w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 flex items-center justify-center text-[10px]">👤</span>
                  <span>Employees</span>
                </div>
                <span className="font-mono font-bold text-white text-sm">
                  {metrics.employeeCount ?? '—'}
                </span>
              </div>

              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-slate-300">
                  <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[10px]">📁</span>
                  <span>Projects</span>
                </div>
                <span className="font-mono font-bold text-white text-sm">
                  {metrics.activeProjects ?? '—'}
                </span>
              </div>

            </div>
            <div className="mt-4 border-t border-[#132038] pt-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-300">Persisted messages</span>
                <span className="font-mono text-[10px] text-slate-500">{recentMessages.length}</span>
              </div>
              {recentMessages.length ? (
                <div className="max-h-36 space-y-2 overflow-y-auto pr-1">
                  {recentMessages.slice(0, 4).map((message) => (
                    <div key={message.id} className="rounded-lg border border-[#162747] bg-[#0c1629] p-2">
                      <div className="mb-1 flex items-center gap-1 text-[9px] text-cyan-300">
                        <span className="truncate">{characters.get(message.sender)?.name || message.sender}</span>
                        {message.recipient && <><span className="text-slate-500">→</span><span className="truncate">{characters.get(message.recipient)?.name || message.recipient}</span></>}
                      </div>
                      <p className="break-words text-[10px] leading-4 text-slate-200">{message.content}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-slate-700 px-2 py-3 text-center text-[10px] text-slate-500">No persisted messages.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────────────────
          2. TOP-RIGHT OVERLAY: CONTEXTUAL INSPECTION DECK (Requirement #7)
          Supports inspecting: Employees, Workstations, Boardroom, and Departments
      ───────────────────────────────────────────────────────────────────────────── */}
      {/* 2A. CHARACTER INSPECTOR */}
      {selectedCharacter && (
        <div className="absolute top-4 right-4 z-20 w-80 bg-[#091122]/95 backdrop-blur-md border border-sky-500/40 rounded-2xl p-4 shadow-2xl text-slate-200 animate-in fade-in duration-200 pointer-events-auto">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-bold text-white tracking-wide truncate pr-2">
              {selectedCharacter.name}
            </h3>
            <div className="flex items-center space-x-2">
              <span
                className={`flex items-center space-x-1 border text-[10px] font-mono font-bold px-2 py-0.5 rounded-full flex-shrink-0 ${
                  selectedCharacter.animState === 'warning'
                    ? 'bg-amber-950/80 border-amber-500/40 text-amber-400'
                    : selectedCharacter.animState === 'celebrating'
                    ? 'bg-fuchsia-950/80 border-fuchsia-500/40 text-fuchsia-300'
                    : selectedCharacter.animState === 'meeting'
                    ? 'bg-purple-950/80 border-purple-500/40 text-purple-300'
                    : selectedCharacter.animState === 'speaking' || selectedCharacter.animState === 'listening'
                    ? 'bg-sky-950/80 border-sky-500/40 text-sky-400'
                    : 'bg-emerald-950/80 border-emerald-500/40 text-emerald-400'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    selectedCharacter.animState === 'warning'
                      ? 'bg-amber-400 animate-ping'
                      : selectedCharacter.animState === 'celebrating'
                      ? 'bg-fuchsia-400 animate-bounce'
                      : 'bg-emerald-400'
                  }`}
                ></span>
                <span>{selectedCharacter.animState.toUpperCase()}</span>
              </span>
              <button
                onClick={() => setSelectedCharId(null)}
                className="text-slate-400 hover:text-white text-xs px-1"
                title="Deselect"
              >
                ✕
              </button>
            </div>
          </div>

          <div className="text-xs text-sky-400 font-semibold mb-3 flex items-center space-x-1.5">
            <span>{selectedCharacter.title}</span>
            <span className="text-slate-500">•</span>
            <span className="text-slate-400 font-mono text-[11px]">{selectedCharacter.department}</span>
          </div>

          {/* Avatar Preview */}
          <div className="w-full h-24 rounded-xl bg-gradient-to-b from-[#0e1c36] to-[#070d1a] border border-[#1b2b48] mb-3 flex items-center justify-center relative overflow-hidden">
            <div
              className="w-12 h-12 rounded-2xl flex items-center justify-center text-lg font-black text-white shadow-xl border-2"
              style={{
                backgroundColor: `${selectedCharacter.theme.accentColor}33`,
                borderColor: selectedCharacter.theme.accentColor,
              }}
            >
              {selectedCharacter.avatarInitials}
            </div>
            <div className="absolute bottom-2 left-2 text-[10px] font-mono text-slate-400 bg-slate-900/80 px-2 py-0.5 rounded border border-slate-700">
              ID: {selectedCharacter.id}
            </div>
            {selectedCharacter.assignedDeskId && (
              <div className="absolute bottom-2 right-2 text-[10px] font-mono text-sky-400 bg-sky-950/80 px-2 py-0.5 rounded border border-sky-800">
                Desk: {selectedCharacter.assignedDeskId}
              </div>
            )}
          </div>

          <div className="mb-3 bg-[#0c1629] border border-[#162747] p-2.5 rounded-xl">
            <div className="text-[10px] text-slate-400 font-mono font-bold uppercase mb-0.5">Current Task:</div>
            <div className="text-xs text-slate-200">{selectedCharacter.currentTask}</div>
          </div>

            <div className="mb-3">
            <div className="text-[11px] font-bold text-slate-300 mb-1.5">Skills on record:</div>
            <div className="grid grid-cols-2 gap-1.5">
              {selectedCharacter.skills.slice(0, 4).map((skill, idx) => (
                <div
                  key={idx}
                  className="flex items-center space-x-1.5 text-[11px] font-medium text-slate-200 bg-[#0c1629] border border-[#162747] px-2 py-1 rounded-lg truncate"
                >
                  <span className="text-slate-500 text-xs">•</span>
                  <span className="truncate">{skill}</span>
                </div>
              ))}
            </div>
            {selectedCharacter.skills.length === 0 && <p className="text-[10px] text-slate-500">No skills recorded.</p>}
          </div>

          <button
            onClick={() => {
              setShowVncModal(true);
              setVncViewOnly(true);
              loadVncToken(selectedCharacter.id, false);
            }}
            className="w-full py-2 bg-gradient-to-r from-blue-600 to-sky-600 hover:from-blue-500 hover:to-sky-500 text-white font-bold rounded-xl text-xs flex items-center justify-center space-x-1.5 shadow-[0_0_15px_rgba(14,165,233,0.35)] transition-all cursor-pointer"
          >
            <span>{selectedCharacter.desktopUrl ? 'Launch Workstation Stream' : 'Workstation Status'}</span>
            <span>📺</span>
          </button>
        </div>
      )}

      {/* 2B. WORKSTATION INSPECTOR (When desk is clicked) */}
      {!selectedCharacter && selectedWorkstation && (
        <div className="absolute top-4 right-4 z-20 w-80 bg-[#091122]/95 backdrop-blur-md border border-cyan-500/40 rounded-2xl p-4 shadow-2xl text-slate-200 animate-in fade-in duration-200 pointer-events-auto">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-bold text-white tracking-wide truncate pr-2">
              {selectedWorkstation.name}
            </h3>
            <button
              onClick={() => setSelectedWsId(null)}
              className="text-slate-400 hover:text-white text-xs px-1"
            >
              ✕
            </button>
          </div>

          <div className="text-xs text-cyan-400 font-semibold mb-3 flex items-center space-x-1.5">
            <span>{selectedWorkstation.id}</span>
            <span className="text-slate-500">•</span>
            <span className="text-slate-400 font-mono text-[11px] uppercase">{selectedWorkstation.dept}</span>
          </div>

          <div className="mb-3 flex items-center justify-between bg-[#0c1629] border border-[#162747] p-2.5 rounded-xl text-xs">
            <span className="text-slate-300 font-semibold">Recorded employee:</span>
            {selectedWorkstationOccupant ? (
              <span className="font-mono font-bold text-emerald-300">{selectedWorkstationOccupant.status}</span>
            ) : (
              <span className="font-mono font-bold text-slate-400">Vacant</span>
            )}
          </div>

          <div className="space-y-2 mb-3 text-xs bg-[#0c1629] border border-[#162747] p-3 rounded-xl font-mono text-[11px]">
            <div className="flex justify-between">
              <span className="text-slate-400">Desk Architecture:</span>
              <span className="text-white capitalize">{selectedWorkstation.deskType}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Display Setup:</span>
              <span className="text-white">{selectedWorkstation.monitorCount} displays</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Current Occupant:</span>
              <span className="text-cyan-300">{selectedWorkstation.occupantId || 'Unassigned'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Character state:</span>
              <span className="text-white">{selectedWorkstationOccupant?.animState || '—'}</span>
            </div>
          </div>

          {selectedWorkstation.id === 'WS-BOSS' && (
            <button
              onClick={() => setShowBossChat(true)}
              className="w-full py-2.5 mb-2.5 bg-gradient-to-r from-amber-600 via-indigo-600 to-sky-600 hover:from-amber-500 hover:to-sky-500 text-white font-bold rounded-xl text-xs shadow-[0_0_15px_rgba(245,158,11,0.3)] flex items-center justify-center space-x-2 transition-all"
            >
              <span>👑</span>
              <span>Chat with Boss AI (CEO)</span>
            </button>
          )}

          {selectedWorkstation.occupantId ? (
            <button
              onClick={() => setSelectedCharId(selectedWorkstation.occupantId)}
              className="w-full py-2 bg-[#0e1c36] hover:bg-sky-950 border border-sky-500/40 text-sky-200 font-bold rounded-xl text-xs"
            >
              Inspect Occupant ({selectedWorkstation.occupantId})
            </button>
          ) : (
            <div className="text-[11px] text-center text-slate-400 bg-slate-900/60 py-2 rounded-xl border border-slate-800">
              No persisted employee is assigned here
            </div>
          )}
        </div>
      )}

      {/* 2C. BOARDROOM / MEETING INSPECTOR */}
      {!selectedCharacter && !selectedWorkstation && isBoardroomSelected && (
        <div className="absolute top-4 right-4 z-20 w-80 bg-[#091122]/95 backdrop-blur-md border border-fuchsia-500/40 rounded-2xl p-4 shadow-2xl text-slate-200 animate-in fade-in duration-200 pointer-events-auto">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-bold text-white tracking-wide">
              Executive Boardroom
            </h3>
            <button
              onClick={() => setIsBoardroomSelected(false)}
              className="text-slate-400 hover:text-white text-xs px-1"
            >
              ✕
            </button>
          </div>

          <div className="text-xs text-fuchsia-400 font-semibold mb-3 flex items-center space-x-1.5">
            <span>Conference Suite</span>
            <span className="text-slate-500">•</span>
            <span className="text-slate-400 font-mono text-[11px]">
              {activeMeeting ? `● ${activeMeeting.title}` : '○ AVAILABLE'}
            </span>
          </div>

          <div className="mb-3 bg-[#0c1629] border border-[#162747] p-2.5 rounded-xl space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-400 font-mono text-[11px]">Conference Seats:</span>
              <span className="text-white font-mono font-bold">6 Seats</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400 font-mono text-[11px]">3D Hologram:</span>
              <span className="text-fuchsia-300 font-mono">{activeMeeting?.title || 'Meeting table'}</span>
            </div>
          </div>

          <div className="mb-3">
            <div className="text-[11px] font-bold text-slate-300 mb-1.5">Meetings & Standups:</div>
            {backendMeetings.length > 0 ? (
              <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                {backendMeetings.slice(0, 3).map((m: any, idx: number) => (
                  <div
                    key={idx}
                    className="p-2 rounded-lg bg-[#0c1629] border border-[#162747] text-[11px]"
                  >
                    <div className="font-semibold text-white truncate">{m.title}</div>
                    <div className="flex justify-between text-slate-400 text-[10px] mt-0.5">
                      <span className="capitalize">{m.meeting_type}</span>
                      <span className="font-mono text-fuchsia-400">{m.status}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-400 bg-[#0c1629] p-3 rounded-xl border border-[#162747] text-center">
                No meetings currently active
              </div>
            )}
          </div>

          <button
            onClick={async () => {
              try {
                await triggerDailyStandup();
                await syncBackendData();
              } catch (e) {
                // Meeting visualization only follows backend meeting records.
              }
            }}
            className="w-full py-2 bg-gradient-to-r from-purple-600 to-fuchsia-600 hover:from-purple-500 hover:to-fuchsia-500 text-white font-bold rounded-xl text-xs flex items-center justify-center space-x-1.5 shadow-[0_0_15px_rgba(192,132,252,0.35)] transition-all cursor-pointer"
          >
            <span>Trigger Daily Standup</span>
            <span>🤝</span>
          </button>
        </div>
      )}

      {/* 2D. DEPARTMENT INSPECTOR */}
      {!selectedCharacter && !selectedWorkstation && !isBoardroomSelected && selectedDept && (
        <div className="absolute top-4 right-4 z-20 w-80 bg-[#091122]/95 backdrop-blur-md border border-sky-500/30 rounded-2xl p-4 shadow-2xl text-slate-200 animate-in fade-in duration-200 pointer-events-auto">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-bold text-white tracking-wide">
              {selectedDept.name}
            </h3>
            <button
              onClick={() => setSelectedDeptId(null)}
              className="text-slate-400 hover:text-white text-xs px-1"
            >
              ✕
            </button>
          </div>

          <div
            className="text-xs font-semibold mb-3 flex items-center space-x-1.5 font-mono"
            style={{ color: selectedDept.neonColor }}
          >
            <span>{selectedDept.label}</span>
            <span className="text-slate-500">•</span>
            <span className="text-slate-400 text-[11px] uppercase">{selectedDept.dept}</span>
          </div>

          <div className="mb-3 bg-[#0c1629] border border-[#162747] p-2.5 rounded-xl space-y-2 text-xs">
            <div className="flex justify-between font-mono text-[11px]">
              <span className="text-slate-400">Workstation Pods:</span>
              <span className="text-white font-bold">{deptWorkstations.length} Desks</span>
            </div>
            <div className="flex justify-between font-mono text-[11px]">
              <span className="text-slate-400">Occupancy:</span>
              <span className="text-emerald-400 font-bold">
                {deptWorkstations.filter((w) => w.occupantId !== null).length} / {deptWorkstations.length}
              </span>
            </div>
          </div>

          <div className="mb-2">
            <div className="text-[11px] font-bold text-slate-300 mb-1.5">Department Employees:</div>
            {deptEmployees.length > 0 ? (
              <div className="space-y-1.5">
                {deptEmployees.map((e) => (
                  <div
                    key={e.id}
                    onClick={() => setSelectedCharId(e.id)}
                    className="p-2 rounded-lg bg-[#0c1629] hover:bg-[#10203a] border border-[#162747] flex items-center justify-between cursor-pointer text-xs"
                  >
                    <div>
                      <div className="font-semibold text-white">{e.name}</div>
                      <div className="text-[10px] text-slate-400">{e.title}</div>
                    </div>
                    <span className="text-emerald-400 font-mono text-[10px]">{e.animState}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-400 bg-[#0c1629] p-3 rounded-xl border border-[#162747] text-center">
                No active workers assigned to this zone
              </div>
            )}
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────────────────────────
          3. MAIN TOP-DOWN CANVAS (Layered living office world)
      ───────────────────────────────────────────────────────────────────────────── */}
      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onClick={handleCanvasClick}
        onWheel={handleWheel}
        className="w-full h-full cursor-grab active:cursor-grabbing"
      />

      {/* ─────────────────────────────────────────────────────────────────────────────
          4. BOTTOM WORLD CONTROLS
      ───────────────────────────────────────────────────────────────────────────── */}
      <div className="absolute bottom-14 left-4 z-20 flex items-center space-x-2 pointer-events-auto">
        {!showOverviewCard && (
          <button
            onClick={() => setShowOverviewCard(true)}
            className="px-3 py-1.5 rounded-xl bg-[#091122]/80 border border-[#162540] text-slate-300 hover:text-white text-xs font-semibold"
          >
            Show Overview
          </button>
        )}
      </div>

      <div className="absolute bottom-14 right-4 z-20 flex items-center space-x-2 bg-[#f8f4ea]/95 backdrop-blur border border-[#bdb19a] p-1.5 rounded-xl text-[#46524b] shadow-lg pointer-events-auto">
        <button
          onClick={() => setCamera((prev) => ({ ...prev, zoom: Math.min(prev.zoom + 0.15, 2.0) }))}
          className="w-7 h-7 rounded-lg hover:bg-[#e9e0cf] flex items-center justify-center font-bold text-xs"
          title="Zoom In"
        >
          ➕
        </button>
        <button
          onClick={() => setCamera((prev) => ({ ...prev, zoom: Math.max(prev.zoom - 0.15, 0.45) }))}
          className="w-7 h-7 rounded-lg hover:bg-[#e9e0cf] flex items-center justify-center font-bold text-xs"
          title="Zoom Out"
        >
          ➖
        </button>
        <button
          onClick={() => setCamera({ x: 0, y: 0, zoom: fitZoomRef.current })}
          className="px-2.5 py-1 rounded-lg hover:bg-[#e9e0cf] text-[11px] font-mono font-semibold"
          title="Reset View"
        >
          Center ⌖
        </button>
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex h-11 items-center justify-between border-t border-[#b8ac95] bg-[#eee6d7]/95 px-4 text-[10px] font-mono uppercase tracking-[0.12em] text-[#59645d] shadow-[0_-4px_18px_rgba(57,51,43,0.08)] sm:px-6 sm:text-[11px]">
        <div className="flex items-center gap-3 sm:gap-6">
          <span className="font-bold text-[#2f433a]">ANCOS HQ</span>
          <span className="hidden text-[#657168] sm:inline">Team <b className="text-[#31443b]">{metrics.employeeCount ?? '—'}</b></span>
          <span className="hidden text-[#657168] sm:inline">Projects <b className="text-[#31443b]">{metrics.activeProjects ?? '—'}</b></span>
        </div>
        <div className="flex items-center gap-2 text-[#c2b49b]">
          <span className={`h-2 w-2 ${activeMeeting ? 'bg-amber-300' : 'bg-emerald-300'}`} />
          <span className="text-[#5e685f]">{activeMeeting ? 'Meeting in progress' : 'Company floor'}</span>
        </div>
      </div>

      {/* ─────────────────────────────────────────────────────────────────────────────
          5. EMBEDDED TOKENIZED noVNC WORKSTATION DESKTOP STREAM MODAL
      ───────────────────────────────────────────────────────────────────────────── */}
      {showVncModal && selectedCharacter && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 pointer-events-auto">
          <div className="bg-[#090f1d] border border-sky-500/40 rounded-2xl w-full max-w-5xl h-[85vh] flex flex-col shadow-[0_0_40px_rgba(56,189,248,0.2)] overflow-hidden">
            <div className="bg-[#0c1527] border-b border-[#1b2b4a] px-5 py-3.5 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <span className={`w-3 h-3 rounded-full ${vncTokenUrl ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`} />
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center space-x-2">
                    <span>{selectedCharacter.name}&apos;s Workstation</span>
                    <span className="text-[10px] font-mono text-sky-400 bg-sky-950/80 px-2 py-0.5 rounded border border-sky-800">
                      Xfce4 + TigerVNC + noVNC
                    </span>
                  </h3>
                  <p className="text-[10px] text-slate-400 font-mono">
                    ID: {selectedCharacter.id} • Desk: {selectedCharacter.assignedDeskId || 'WS-UNASSIGNED'}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-3">
                <button
                  disabled={vncLoading}
                  onClick={() => {
                    const nextInteractive = vncViewOnly;
                    setVncViewOnly(!vncViewOnly);
                    loadVncToken(selectedCharacter.id, nextInteractive);
                  }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium border transition-colors ${
                    vncViewOnly
                      ? 'bg-amber-950/80 border-amber-600 text-amber-300'
                      : 'bg-emerald-950/80 border-emerald-600 text-emerald-300'
                  } ${vncLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  Mode: {vncViewOnly ? 'Read-Only' : 'Interactive'}
                </button>
                {vncTokenUrl && (
                  <a
                    href={vncTokenUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="px-3 py-1.5 rounded-lg bg-sky-600/20 text-sky-400 border border-sky-500/40 hover:bg-sky-600/30 text-xs font-medium flex items-center space-x-1.5"
                  >
                    <span>↗</span>
                    <span>Popout</span>
                  </a>
                )}
                <button
                  onClick={() => setShowVncModal(false)}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white text-sm"
                >
                  ✕
                </button>
              </div>
            </div>
            <div className="flex-1 bg-black relative">
              {vncLoading ? (
                <div className="flex flex-col items-center justify-center h-full text-slate-400 font-mono text-sm space-y-2">
                  <span className="text-sky-400 animate-pulse font-bold">ACQUIRING WORKSTATION TOKEN...</span>
                  <span className="text-xs text-slate-500">Negotiating authenticated WebSocket ticket via Broker.</span>
                </div>
              ) : vncTokenUrl ? (
                <iframe
                  src={vncTokenUrl}
                  title={`${selectedCharacter.name} Workstation`}
                  className="w-full h-full border-0"
                  allow="clipboard-read; clipboard-write; fullscreen"
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-400 font-mono text-sm space-y-2">
                  <span className="text-rose-400 font-bold">WORKSTATION UNAVAILABLE</span>
                  <span className="text-xs text-slate-500">{vncError || 'Container offline or unprovisioned. Visual telemetry suppressed.'}</span>
                </div>
              )}
            </div>
            <div className="bg-[#080e1c] border-t border-[#141f36] px-5 py-2.5 flex items-center justify-between text-xs font-mono text-slate-400">
              <span>Display: <strong className="text-slate-200">:1 (1280x800)</strong></span>
              <span>Mode: <strong className={vncViewOnly ? 'text-amber-400' : 'text-emerald-400'}>{vncViewOnly ? 'Read-Only (View Only)' : 'Interactive (Gated Input)'}</strong></span>
              <span>Security: <strong className="text-emerald-400">Tokenized Least-Privilege</strong></span>
              <span>Audit: <strong className="text-sky-300">Active Logging</strong></span>
            </div>
          </div>
        </div>
      )}

      {/* Floating Executive Chat with Boss AI Button */}
      <button
        onClick={() => setShowBossChat(true)}
        aria-label="Open Boss AI Executive Chat"
        className="absolute bottom-6 right-6 z-20 px-4 py-2.5 bg-gradient-to-r from-amber-500 via-indigo-600 to-blue-700 hover:from-amber-400 hover:to-blue-600 text-white font-bold text-xs rounded-2xl shadow-[0_0_25px_rgba(245,158,11,0.4)] flex items-center space-x-2 transition-transform hover:scale-105 active:scale-95 border border-amber-300/40 pointer-events-auto"
      >
        <span className="text-base">👑</span>
        <span className="tracking-wide">Chat with Boss AI</span>
        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
      </button>

      {/* Slide-In Boss AI Chat Drawer */}
      <BossChatDrawer
        isOpen={showBossChat}
        onClose={() => setShowBossChat(false)}
        onMissionLaunched={() => {
          syncBackendData();
        }}
      />

    </div>
  );
};
