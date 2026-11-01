import { Employee } from '@/types';
import { GameCharacter, CharacterRole, CharacterVisualTheme, WorkstationDef } from './types';
import { ELEVATOR_SPAWN } from './OfficeLayout';
import { findPath } from './MovementEngine';

/**
 * Derives a deterministic visual theme from role and employee details
 */
export function generateCharacterTheme(role: CharacterRole, seed: string): CharacterVisualTheme {
  const hash = seed.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);

  const skinTones = ['#f8fafc', '#fed7aa', '#fde68a', '#fbcfe8', '#f5d0fe'];
  const skinTone = skinTones[hash % skinTones.length];

  switch (role) {
    case 'boss':
      return {
        hairColor: '#38bdf8',
        suitColor: '#0b1329',
        accentColor: '#38bdf8',
        skinTone: '#f8fafc',
        hairStyle: 'spiky',
        hasVisor: true,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'swe':
      return {
        hairColor: (hash % 2 === 0) ? '#3b82f6' : '#2563eb',
        suitColor: '#1e293b',
        accentColor: '#60a5fa',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    case 'frontend':
      return {
        hairColor: '#8b5cf6',
        suitColor: '#1e1b4b',
        accentColor: '#a78bfa',
        skinTone,
        hairStyle: 'bob',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    case 'research':
      return {
        hairColor: '#ec4899',
        suitColor: '#371435',
        accentColor: '#f472b6',
        skinTone,
        hairStyle: 'long',
        hasVisor: false,
        hasHeadphones: false,
        hasGlasses: true,
      };
    case 'creative':
      return {
        hairColor: '#d946ef',
        suitColor: '#2e1065',
        accentColor: '#e879f9',
        skinTone,
        hairStyle: 'bob',
        hasVisor: false,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'qa':
      return {
        hairColor: '#eab308',
        suitColor: '#3b2505',
        accentColor: '#fbbf24',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: true,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'devops':
      return {
        hairColor: '#06b6d4',
        suitColor: '#083344',
        accentColor: '#22d3ee',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    default:
      return {
        hairColor: '#38bdf8',
        suitColor: '#0f172a',
        accentColor: '#38bdf8',
        skinTone,
        hairStyle: 'bob',
        hasVisor: false,
        hasHeadphones: false,
        hasGlasses: false,
      };
  }
}

/**
 * Maps backend department and title to internal role identifier
 */
export function inferRole(dept: string = '', title: string = ''): CharacterRole {
  const d = dept.toLowerCase();
  const t = title.toLowerCase();

  if (d.includes('exec') || t.includes('ceo') || t.includes('boss')) return 'boss';
  if (d.includes('research') || t.includes('research') || t.includes('ml')) return 'research';
  if (d.includes('creative') || d.includes('design') || t.includes('design') || t.includes('ux')) return 'creative';
  if (d.includes('qa') || d.includes('reliability') || t.includes('qa') || t.includes('test')) return 'qa';
  if (d.includes('infra') || d.includes('devops') || t.includes('devops') || t.includes('cloud')) return 'devops';
  if (t.includes('frontend') || t.includes('ui')) return 'frontend';
  return 'swe';
}

/**
 * Finds the most suitable vacant workstation for an employee
 */
export function allocateWorkstation(
  empRole: CharacterRole,
  workstations: WorkstationDef[]
): WorkstationDef | null {
  // First match preferred department
  const preferredDeptMap: Record<CharacterRole, string> = {
    boss: 'executive',
    swe: 'engineering',
    frontend: 'engineering',
    qa: 'qa',
    devops: 'devops',
    research: 'research',
    creative: 'creative',
    general: 'engineering',
  };

  const prefDept = preferredDeptMap[empRole];

  // Try finding an empty workstation in the preferred department
  let candidate = workstations.find((w) => w.dept === prefDept && w.occupantId === null);
  if (candidate) return candidate;

  // Otherwise, find any vacant non-executive workstation
  candidate = workstations.find((w) => w.dept !== 'executive' && w.occupantId === null);
  return candidate || null;
}

/**
 * Synchronizes backend employee list with living game character entities
 */
export function syncEmployeesToCharacters(
  backendEmployees: Employee[],
  existingCharacters: Map<string, GameCharacter>,
  workstations: WorkstationDef[],
  isInitialBootstrap: boolean = false
): Map<string, GameCharacter> {
  const updatedMap = new Map<string, GameCharacter>(existingCharacters);
  const activeIds = new Set<string>();

  for (const emp of backendEmployees) {
    activeIds.add(emp.employee_id);
    const existing = updatedMap.get(emp.employee_id);

    if (existing) {
      // Update existing character state without resetting position or path
      existing.name = emp.name;
      existing.title = emp.title;
      existing.department = emp.department;
      existing.status = (emp.status as any) || existing.status;
      existing.skills = Array.isArray(emp.skills)
        ? emp.skills.map((s) => (typeof s === 'string' ? s : s.name))
        : existing.skills;
      existing.desktopUrl = emp.desktop_url;
      // If status says 'working' and not currently walking, set state to working
      if (emp.status === 'working' && existing.path.length === 0 && existing.assignedDeskId) {
        if (
          existing.currentMeetingSeatIndex === null &&
          existing.animState !== 'celebrating' &&
          existing.animState !== 'warning' &&
          existing.animState !== 'speaking' &&
          existing.animState !== 'listening'
        ) {
          existing.animState = 'working';
        }
      }
    } else {
      // Recruited employee!
      const role = inferRole(emp.department, emp.title);
      const theme = generateCharacterTheme(role, emp.employee_id + emp.name);

      const ws = allocateWorkstation(role, workstations);
      const assignedDeskId = ws ? ws.id : null;
      if (ws) {
        ws.occupantId = emp.employee_id;
      }

      const spawnPoint = ELEVATOR_SPAWN;
      const initialSkills = Array.isArray(emp.skills)
        ? emp.skills.map((s) => (typeof s === 'string' ? s : s.name))
        : ['Full-Stack', 'Async Python', 'Docker'];

      // If initial bootstrap, place directly at workstation in working state.
      // Otherwise, spawn at elevator and walk to workstation.
      const startAtDesk = isInitialBootstrap && ws;
      const startX = startAtDesk ? ws.seatGridX : spawnPoint.x;
      const startY = startAtDesk ? ws.seatGridY : spawnPoint.y;

      const newChar: GameCharacter = {
        id: emp.employee_id,
        name: emp.name,
        title: emp.title,
        department: emp.department,
        role,
        status: (emp.status as any) || 'online',
        skills: initialSkills,
        currentTask: startAtDesk ? 'Active task execution' : 'Initial workstation provisioning & setup',
        vncPort: 6080 + updatedMap.size,
        desktopUrl: emp.desktop_url,
        avatarInitials: emp.name
          .split(' ')
          .map((n) => n[0])
          .join('')
          .toUpperCase()
          .slice(0, 2),
        gridX: startX,
        gridY: startY,
        targetGridX: ws ? ws.seatGridX : startX,
        targetGridY: ws ? ws.seatGridY : startY,
        path: startAtDesk
          ? []
          : (ws ? findPath(spawnPoint, { x: ws.seatGridX, y: ws.seatGridY }) : []),
        speed: 2.2, // grid units / sec
        heading: startAtDesk ? 'down' : 'right',
        animState: startAtDesk ? 'working' : (ws ? 'walking' : 'idle'),
        animTime: Math.random() * 5,
        blinkTimer: 2.0,
        isBlinking: false,
        assignedDeskId,
        currentMeetingSeatIndex: null,
        theme,
        speechText: startAtDesk ? null : `Hello ANCOS! Excited to join ${emp.department}.`,
        speechRemainingMs: startAtDesk ? 0 : 5000,
      };

      updatedMap.set(emp.employee_id, newChar);
    }
  }

  // Remove employees that no longer exist in backend (preserve test characters and Boss AI)
  for (const [id, char] of updatedMap.entries()) {
    if (
      !activeIds.has(id) &&
      !char.isTestCharacter &&
      char.id !== 'ANC-BOSS' &&
      char.role !== 'boss'
    ) {
      if (char.assignedDeskId) {
        const ws = workstations.find((w) => w.id === char.assignedDeskId);
        if (ws) ws.occupantId = null;
      }
      updatedMap.delete(id);
    }
  }

  return updatedMap;
}

