export interface Point2D {
  x: number;
  y: number;
}

export interface ScreenPoint {
  screenX: number;
  screenY: number;
}

export type DepartmentId = 
  | 'executive' 
  | 'engineering' 
  | 'research' 
  | 'creative' 
  | 'qa' 
  | 'devops' 
  | 'hub';

export type CharacterRole = 
  | 'boss' 
  | 'swe' 
  | 'frontend' 
  | 'qa' 
  | 'devops' 
  | 'research' 
  | 'creative' 
  | 'general';

export type CharacterAnimationState = 
  | 'idle' 
  | 'walking' 
  | 'sitting' 
  | 'working' 
  | 'thinking' 
  | 'speaking' 
  | 'listening' 
  | 'meeting' 
  | 'celebrating' 
  | 'warning';

export type HeadingDirection = 'left' | 'right' | 'up' | 'down';

export interface WorkstationDef {
  id: string;
  name: string;
  dept: DepartmentId;
  deskGridX: number;
  deskGridY: number;
  seatGridX: number;
  seatGridY: number;
  approachGridX: number;
  approachGridY: number;
  facing: HeadingDirection;
  deskType: 'single' | 'executive' | 'pod';
  monitorCount: number;
  occupantId: string | null;
}

export interface MeetingSeatDef {
  seatIndex: number;
  gridX: number;
  gridY: number;
  approachGridX: number;
  approachGridY: number;
  facing: HeadingDirection;
  occupantId: string | null;
}

export interface WaypointNode {
  id: string;
  gridX: number;
  gridY: number;
  neighbors: string[];
  description?: string;
}

export interface CharacterVisualTheme {
  hairColor: string;
  suitColor: string;
  accentColor: string;
  skinTone: string;
  hairStyle: 'spiky' | 'bob' | 'long' | 'ponytail' | 'cyber';
  hasVisor: boolean;
  hasHeadphones: boolean;
  hasGlasses: boolean;
}

export interface GameCharacter {
  id: string;
  name: string;
  title: string;
  department: string;
  role: CharacterRole;
  status: 'online' | 'busy' | 'working' | 'offline' | 'idle' | 'suspended' | 'terminated' | 'unknown';
  skills: string[];
  currentTask: string;
  vncPort: number;
  desktopUrl?: string | null;
  avatarInitials: string;

  // Spatial & Movement
  gridX: number;
  gridY: number;
  targetGridX: number;
  targetGridY: number;
  path: Point2D[];
  speed: number; // grid units / second
  heading: HeadingDirection;

  // Animation State
  animState: CharacterAnimationState;
  animTime: number; // accumulated time for continuous oscillation
  blinkTimer: number;
  isBlinking: boolean;

  // Placement assignments
  assignedDeskId: string | null;
  currentMeetingSeatIndex: number | null;

  // Visual Theme
  theme: CharacterVisualTheme;

  // Live Speech
  speechText: string | null;
  speechRemainingMs: number;

  // Metadata
  isTestCharacter?: boolean;
}

export interface CommsPacket {
  id: string;
  fromCharId: string;
  toCharId: string;
  fromScreenX: number;
  fromScreenY: number;
  toScreenX: number;
  toScreenY: number;
  progress: number; // 0..1
  speed: number;
  textSnippet: string;
  color: string;
}

export interface SpeechBubble {
  id: string;
  charId: string;
  text: string;
  color?: string;
  createdAt: number;
  durationMs: number;
}

export interface CameraState {
  x: number;
  y: number;
  zoom: number;
}

export type InspectionTarget = 
  | { type: 'employee'; employee: GameCharacter }
  | { type: 'workstation'; workstation: WorkstationDef; occupant?: GameCharacter }
  | { type: 'meeting'; activeMeeting: any; attendees: GameCharacter[] }
  | { type: 'department'; deptId: DepartmentId; employees: GameCharacter[] }
  | null;

