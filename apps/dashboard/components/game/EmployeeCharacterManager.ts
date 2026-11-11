import { Employee } from '@/types';
import { GameCharacter, CharacterRole, CharacterVisualTheme, WorkstationDef } from './types';
import { ELEVATOR_SPAWN } from './OfficeLayout';
import { findPath } from './MovementEngine';

/**
 * Derives a deterministic visual theme from role and employee details
 */
export function generateCharacterTheme(role: CharacterRole, seed: string): CharacterVisualTheme {
  const hash = seed.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);

  const skinTones = ['#f1c79f', '#c98f6d', '#e8b995', '#a9684b', '#f2d5b9'];
  const skinTone = skinTones[hash % skinTones.length];

  switch (role) {
    case 'boss':
      return {
        hairColor: '#4d5c70',
        suitColor: '#394e47',
        accentColor: '#efcc87',
        skinTone: '#e8b995',
        hairStyle: 'spiky',
        hasVisor: true,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'swe':
      return {
        hairColor: (hash % 2 === 0) ? '#72533f' : '#46566c',
        suitColor: '#384d4a',
        accentColor: '#d8b974',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    case 'frontend':
      return {
        hairColor: '#6c4e41',
        suitColor: '#465b65',
        accentColor: '#e1b273',
        skinTone,
        hairStyle: 'bob',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    case 'research':
      return {
        hairColor: '#55443b',
        suitColor: '#63576a',
        accentColor: '#c4a5d5',
        skinTone,
        hairStyle: 'long',
        hasVisor: false,
        hasHeadphones: false,
        hasGlasses: true,
      };
    case 'creative':
      return {
        hairColor: '#80543c',
        suitColor: '#76604e',
        accentColor: '#e1b273',
        skinTone,
        hairStyle: 'bob',
        hasVisor: false,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'qa':
      return {
        hairColor: '#544134',
        suitColor: '#4d515b',
        accentColor: '#e4bc70',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: true,
        hasHeadphones: false,
        hasGlasses: false,
      };
    case 'devops':
      return {
        hairColor: '#3c4c50',
        suitColor: '#39545d',
        accentColor: '#8bc5c3',
        skinTone,
        hairStyle: 'spiky',
        hasVisor: false,
        hasHeadphones: true,
        hasGlasses: false,
      };
    default:
      return {
        hairColor: '#5d493c',
        suitColor: '#435358',
        accentColor: '#d7b57b',
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
      if ((emp as any).avatar_config || (emp as any).avatarConfig) {
        existing.theme = (emp as any).avatar_config || (emp as any).avatarConfig;
      }
      existing.name = emp.name;
      existing.title = emp.title;
      existing.department = emp.department;
      existing.status = (emp.status as GameCharacter['status']) || 'unknown';
      const taskStatus = String((emp as any).current_task_status || '').toLowerCase();
      existing.currentTaskStatus = taskStatus || null;
      existing.currentTask = typeof (emp as any).current_task === 'string'
        ? (emp as any).current_task
        : 'No active task';
      existing.skills = Array.isArray(emp.skills)
        ? emp.skills.map((s) => (typeof s === 'string' ? s : s.name))
        : existing.skills;
      existing.desktopUrl = emp.desktop_url;
      if (existing.path.length === 0 && existing.currentMeetingSeatIndex === null) {
        if (taskStatus === 'running') existing.animState = 'working';
        else if (taskStatus === 'assigned' || taskStatus === 'retrying') existing.animState = 'sitting';
        else if (taskStatus === 'blocked') existing.animState = 'warning';
        else if (emp.status === 'working') existing.animState = 'working';
        else if (emp.status === 'idle' || emp.status === 'suspended' || emp.status === 'terminated') {
          existing.animState = 'idle';
        }
      }
      if (emp.desk_id && existing.assignedDeskId !== emp.desk_id) {
        const oldWs = workstations.find((w) => w.id === existing.assignedDeskId);
        if (oldWs && oldWs.occupantId === emp.employee_id) {
          oldWs.occupantId = null;
        }
        const newWs = workstations.find((w) => w.id === emp.desk_id);
        if (newWs) {
          newWs.occupantId = emp.employee_id;
          existing.assignedDeskId = newWs.id;
          existing.targetGridX = newWs.seatGridX;
          existing.targetGridY = newWs.seatGridY;
        } else {
          existing.assignedDeskId = emp.desk_id;
        }
      }
    } else {
      // Recruited employee!
      const role = inferRole(emp.department, emp.title);
      const theme = (emp as any).avatar_config || (emp as any).avatarConfig || generateCharacterTheme(role, emp.employee_id + emp.name);

      let ws: WorkstationDef | null = null;
      if (emp.desk_id) {
        ws = workstations.find((w) => w.id === emp.desk_id) || null;
      }
      if (!ws) {
        ws = allocateWorkstation(role, workstations);
      }
      const assignedDeskId = ws ? ws.id : (emp.desk_id || null);
      if (ws) {
        ws.occupantId = emp.employee_id;
      }

      const spawnPoint = ELEVATOR_SPAWN;
      const initialSkills = Array.isArray(emp.skills)
        ? emp.skills.map((s) => (typeof s === 'string' ? s : s.name))
        : [];
      const employeeStatus = (emp.status as GameCharacter['status']) || 'unknown';
      const taskStatus = String((emp as any).current_task_status || '').toLowerCase();
      const currentTask = typeof (emp as any).current_task === 'string'
        ? (emp as any).current_task
        : 'No active task';

      // Bootstrap at the assigned desk; later recruits walk in from the entry.
      const startAtDesk = Boolean(isInitialBootstrap && ws);
      const startX = (isInitialBootstrap && ws) ? ws.seatGridX : spawnPoint.x;
      const startY = (isInitialBootstrap && ws) ? ws.seatGridY : spawnPoint.y;

      const newChar: GameCharacter = {
        id: emp.employee_id,
        name: emp.name,
        title: emp.title,
        department: emp.department,
        role,
        status: employeeStatus,
        skills: initialSkills,
        currentTask,
        currentTaskStatus: taskStatus || null,
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
        animState: startAtDesk
          ? (taskStatus === 'running' || employeeStatus === 'working'
            ? 'working'
            : taskStatus === 'assigned' || taskStatus === 'retrying'
              ? 'sitting'
              : taskStatus === 'blocked'
                ? 'warning'
                : 'idle')
          : (ws ? 'walking' : 'idle'),
        animTime: Math.random() * 5,
        blinkTimer: 2.0,
        isBlinking: false,
        assignedDeskId,
        currentMeetingSeatIndex: null,
        theme,
        speechText: null,
        speechRemainingMs: 0,
      };

      updatedMap.set(emp.employee_id, newChar);
    }
  }

  // Keep the rendered workforce identical to persisted backend employee records.
  for (const [id, char] of updatedMap.entries()) {
    if (!activeIds.has(id)) {
      if (char.assignedDeskId) {
        const ws = workstations.find((w) => w.id === char.assignedDeskId);
        if (ws) ws.occupantId = null;
      }
      updatedMap.delete(id);
    }
  }

  return updatedMap;
}

