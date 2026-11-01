import { DepartmentId, HeadingDirection, MeetingSeatDef, Point2D, WaypointNode, WorkstationDef } from './types';

export const GRID_SIZE_X = 24;
export const GRID_SIZE_Y = 24;

export interface RoomZoneDef {
  id: string;
  name: string;
  dept: DepartmentId;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  floorColor: string;
  gridLineColor: string;
  neonColor: string;
  label: string;
  labelPos: Point2D;
}

export const ROOM_ZONES: RoomZoneDef[] = [
  {
    id: 'ceo_suite',
    name: 'Executive Office',
    dept: 'executive',
    minX: 2,
    maxX: 7,
    minY: 2,
    maxY: 7,
    floorColor: '#0c1b33',
    gridLineColor: '#1d4ed833',
    neonColor: '#38bdf8',
    label: 'CEO OFFICE',
    labelPos: { x: 4.5, y: 2.5 },
  },
  {
    id: 'engineering_bay',
    name: 'Engineering Lab',
    dept: 'engineering',
    minX: 14,
    maxX: 21,
    minY: 2,
    maxY: 8,
    floorColor: '#0a162e',
    gridLineColor: '#2563eb33',
    neonColor: '#60a5fa',
    label: 'ENGINEERING LAB',
    labelPos: { x: 17.5, y: 2.5 },
  },
  {
    id: 'ai_hub',
    name: 'ANCOS AI Core',
    dept: 'hub',
    minX: 8,
    maxX: 14,
    minY: 8,
    maxY: 14,
    floorColor: '#061726',
    gridLineColor: '#05966933',
    neonColor: '#10b981',
    label: 'ANCOS AI HUB',
    labelPos: { x: 11, y: 8.5 },
  },
  {
    id: 'creative_studio',
    name: 'Creative Studio',
    dept: 'creative',
    minX: 2,
    maxX: 8,
    minY: 14,
    maxY: 20,
    floorColor: '#1c102a',
    gridLineColor: '#9333ea33',
    neonColor: '#c084fc',
    label: 'CREATIVE STUDIO',
    labelPos: { x: 5, y: 14.5 },
  },
  {
    id: 'boardroom',
    name: 'Executive Boardroom',
    dept: 'executive',
    minX: 9,
    maxX: 13,
    minY: 15,
    maxY: 20,
    floorColor: '#1a1829',
    gridLineColor: '#a855f733',
    neonColor: '#e879f9',
    label: 'BOARDROOM',
    labelPos: { x: 11, y: 15.5 },
  },
  {
    id: 'research_lab',
    name: 'Research & ML Lab',
    dept: 'research',
    minX: 14,
    maxX: 21,
    minY: 14,
    maxY: 20,
    floorColor: '#1e1124',
    gridLineColor: '#db277733',
    neonColor: '#f472b6',
    label: 'RESEARCH LAB',
    labelPos: { x: 17.5, y: 14.5 },
  },
  {
    id: 'server_vault',
    name: 'Infrastructure & DevOps',
    dept: 'devops',
    minX: 18,
    maxX: 22,
    minY: 9,
    maxY: 13,
    floorColor: '#081c24',
    gridLineColor: '#0891b233',
    neonColor: '#06b6d4',
    label: 'SERVER VAULT',
    labelPos: { x: 20, y: 9.5 },
  },
];

export const WORKSTATIONS: WorkstationDef[] = [
  // CEO Executive Desk
  {
    id: 'WS-BOSS',
    name: 'CEO Command Workstation',
    dept: 'executive',
    deskGridX: 4,
    deskGridY: 4,
    seatGridX: 4,
    seatGridY: 3.5,
    approachGridX: 4,
    approachGridY: 5,
    facing: 'down',
    deskType: 'executive',
    monitorCount: 3,
    occupantId: null,
  },
  // Engineering Desks
  {
    id: 'WS-ENG-01',
    name: 'Systems Engineering Pod #1',
    dept: 'engineering',
    deskGridX: 16,
    deskGridY: 4,
    seatGridX: 16,
    seatGridY: 3.5,
    approachGridX: 16,
    approachGridY: 5,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-ENG-02',
    name: 'Backend & Agent Pod #2',
    dept: 'engineering',
    deskGridX: 19,
    deskGridY: 4,
    seatGridX: 19,
    seatGridY: 3.5,
    approachGridX: 19,
    approachGridY: 5,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-ENG-03',
    name: 'Frontend & UI Pod #3',
    dept: 'engineering',
    deskGridX: 16,
    deskGridY: 7,
    seatGridX: 16,
    seatGridY: 6.5,
    approachGridX: 16,
    approachGridY: 8,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-ENG-04',
    name: 'Full-Stack Pod #4',
    dept: 'engineering',
    deskGridX: 19,
    deskGridY: 7,
    seatGridX: 19,
    seatGridY: 6.5,
    approachGridX: 19,
    approachGridY: 8,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  // Research Desks
  {
    id: 'WS-RES-01',
    name: 'Neural Model Lab #1',
    dept: 'research',
    deskGridX: 16,
    deskGridY: 16,
    seatGridX: 16,
    seatGridY: 15.5,
    approachGridX: 16,
    approachGridY: 17,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-RES-02',
    name: 'Cognitive Research Pod #2',
    dept: 'research',
    deskGridX: 19,
    deskGridY: 16,
    seatGridX: 19,
    seatGridY: 15.5,
    approachGridX: 19,
    approachGridY: 17,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-RES-03',
    name: 'Distillation Pod #3',
    dept: 'research',
    deskGridX: 17,
    deskGridY: 19,
    seatGridX: 17,
    seatGridY: 18.5,
    approachGridX: 17,
    approachGridY: 20,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  // Creative Desks
  {
    id: 'WS-CRE-01',
    name: 'Design System Station #1',
    dept: 'creative',
    deskGridX: 4,
    deskGridY: 16,
    seatGridX: 4,
    seatGridY: 15.5,
    approachGridX: 4,
    approachGridY: 17,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  {
    id: 'WS-CRE-02',
    name: 'Interactive Motion Pod #2',
    dept: 'creative',
    deskGridX: 7,
    deskGridY: 16,
    seatGridX: 7,
    seatGridY: 15.5,
    approachGridX: 7,
    approachGridY: 17,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
  // DevOps / Infrastructure Desk
  {
    id: 'WS-OPS-01',
    name: 'Cloud & Sandbox Console #1',
    dept: 'devops',
    deskGridX: 20,
    deskGridY: 11,
    seatGridX: 20,
    seatGridY: 10.5,
    approachGridX: 20,
    approachGridY: 12,
    facing: 'down',
    deskType: 'single',
    monitorCount: 3,
    occupantId: null,
  },
  // QA Verification Desk
  {
    id: 'WS-QA-01',
    name: 'Autonomous Verification Deck #1',
    dept: 'qa',
    deskGridX: 5,
    deskGridY: 19,
    seatGridX: 5,
    seatGridY: 18.5,
    approachGridX: 5,
    approachGridY: 20,
    facing: 'down',
    deskType: 'single',
    monitorCount: 2,
    occupantId: null,
  },
];

export const MEETING_SEATS: MeetingSeatDef[] = [
  { seatIndex: 0, gridX: 9.5, gridY: 17, approachGridX: 9, approachGridY: 17, facing: 'right', occupantId: null },
  { seatIndex: 1, gridX: 12.5, gridY: 17, approachGridX: 13, approachGridY: 17, facing: 'left', occupantId: null },
  { seatIndex: 2, gridX: 10.5, gridY: 16, approachGridX: 10.5, approachGridY: 15, facing: 'down', occupantId: null },
  { seatIndex: 3, gridX: 11.5, gridY: 16, approachGridX: 11.5, approachGridY: 15, facing: 'down', occupantId: null },
  { seatIndex: 4, gridX: 10.5, gridY: 18.5, approachGridX: 10.5, approachGridY: 19.5, facing: 'up', occupantId: null },
  { seatIndex: 5, gridX: 11.5, gridY: 18.5, approachGridX: 11.5, approachGridY: 19.5, facing: 'up', occupantId: null },
];

export const ELEVATOR_SPAWN: Point2D = { x: 2, y: 11 };

export const WAYPOINTS: Record<string, WaypointNode> = {
  // Elevators & Entrance
  'wp-elevator': { id: 'wp-elevator', gridX: 2, gridY: 11, neighbors: ['wp-hall-west'] },
  'wp-hall-west': { id: 'wp-hall-west', gridX: 5, gridY: 11, neighbors: ['wp-elevator', 'wp-hub-west', 'wp-ceo-appr', 'wp-cre-appr-main'] },
  
  // AI Hub Center & Ring
  'wp-hub-west': { id: 'wp-hub-west', gridX: 8, gridY: 11, neighbors: ['wp-hall-west', 'wp-hub-center', 'wp-hub-north', 'wp-hub-south'] },
  'wp-hub-center': { id: 'wp-hub-center', gridX: 11, gridY: 11, neighbors: ['wp-hub-west', 'wp-hub-east', 'wp-hub-north', 'wp-hub-south'] },
  'wp-hub-east': { id: 'wp-hub-east', gridX: 14, gridY: 11, neighbors: ['wp-hub-center', 'wp-hall-east', 'wp-hub-north', 'wp-hub-south'] },
  'wp-hub-north': { id: 'wp-hub-north', gridX: 11, gridY: 8, neighbors: ['wp-hub-center', 'wp-hall-north', 'wp-hub-west', 'wp-hub-east'] },
  'wp-hub-south': { id: 'wp-hub-south', gridX: 11, gridY: 14, neighbors: ['wp-hub-center', 'wp-boardroom-enter', 'wp-hub-west', 'wp-hub-east'] },

  // North Hallway & CEO / Engineering
  'wp-hall-north': { id: 'wp-hall-north', gridX: 11, gridY: 5, neighbors: ['wp-hub-north', 'wp-ceo-appr', 'wp-eng-hall-1'] },
  'wp-ceo-appr': { id: 'wp-ceo-appr', gridX: 4, gridY: 5, neighbors: ['wp-hall-north', 'wp-hall-west'] },

  // Engineering Corridor
  'wp-eng-hall-1': { id: 'wp-eng-hall-1', gridX: 16, gridY: 5, neighbors: ['wp-hall-north', 'wp-eng-hall-2', 'wp-eng-hall-3'] },
  'wp-eng-hall-2': { id: 'wp-eng-hall-2', gridX: 19, gridY: 5, neighbors: ['wp-eng-hall-1', 'wp-eng-hall-4'] },
  'wp-eng-hall-3': { id: 'wp-eng-hall-3', gridX: 16, gridY: 8, neighbors: ['wp-eng-hall-1', 'wp-eng-hall-4', 'wp-hub-east'] },
  'wp-eng-hall-4': { id: 'wp-eng-hall-4', gridX: 19, gridY: 8, neighbors: ['wp-eng-hall-2', 'wp-eng-hall-3', 'wp-hall-east'] },

  // East Hallway & Server Vault
  'wp-hall-east': { id: 'wp-hall-east', gridX: 17, gridY: 11, neighbors: ['wp-hub-east', 'wp-eng-hall-4', 'wp-server-appr', 'wp-res-hall-1'] },
  'wp-server-appr': { id: 'wp-server-appr', gridX: 20, gridY: 12, neighbors: ['wp-hall-east'] },

  // Research Lab Corridor
  'wp-res-hall-1': { id: 'wp-res-hall-1', gridX: 16, gridY: 17, neighbors: ['wp-hall-east', 'wp-res-hall-2', 'wp-res-hall-3'] },
  'wp-res-hall-2': { id: 'wp-res-hall-2', gridX: 19, gridY: 17, neighbors: ['wp-res-hall-1'] },
  'wp-res-hall-3': { id: 'wp-res-hall-3', gridX: 17, gridY: 20, neighbors: ['wp-res-hall-1'] },

  // Creative Studio Corridor
  'wp-cre-appr-main': { id: 'wp-cre-appr-main', gridX: 5, gridY: 14, neighbors: ['wp-hall-west', 'wp-cre-hall-1', 'wp-boardroom-enter'] },
  'wp-cre-hall-1': { id: 'wp-cre-hall-1', gridX: 4, gridY: 17, neighbors: ['wp-cre-appr-main', 'wp-cre-hall-2', 'wp-qa-appr'] },
  'wp-cre-hall-2': { id: 'wp-cre-hall-2', gridX: 7, gridY: 17, neighbors: ['wp-cre-hall-1', 'wp-boardroom-west'] },
  'wp-qa-appr': { id: 'wp-qa-appr', gridX: 5, gridY: 20, neighbors: ['wp-cre-hall-1'] },

  // Boardroom
  'wp-boardroom-enter': { id: 'wp-boardroom-enter', gridX: 11, gridY: 15, neighbors: ['wp-hub-south', 'wp-cre-appr-main', 'wp-boardroom-table'] },
  'wp-boardroom-table': { id: 'wp-boardroom-table', gridX: 11, gridY: 17, neighbors: ['wp-boardroom-enter', 'wp-boardroom-west', 'wp-boardroom-east', 'wp-boardroom-south'] },
  'wp-boardroom-west': { id: 'wp-boardroom-west', gridX: 9, gridY: 17, neighbors: ['wp-boardroom-table', 'wp-cre-hall-2'] },
  'wp-boardroom-east': { id: 'wp-boardroom-east', gridX: 13, gridY: 17, neighbors: ['wp-boardroom-table'] },
  'wp-boardroom-south': { id: 'wp-boardroom-south', gridX: 11, gridY: 19.5, neighbors: ['wp-boardroom-table'] },
};

