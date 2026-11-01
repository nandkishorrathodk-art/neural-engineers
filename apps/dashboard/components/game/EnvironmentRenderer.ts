import {
  GRID_SIZE_X,
  GRID_SIZE_Y,
  ROOM_ZONES,
  WORKSTATIONS,
  MEETING_SEATS,
  ELEVATOR_SPAWN,
  RoomZoneDef,
} from './OfficeLayout';
import {
  gridToScreen,
  drawIsometricTile,
  drawIsometricBlock,
  TILE_HALF_W,
  TILE_HALF_H,
} from './IsometricMath';
import { WorkstationDef } from './types';

/**
 * Checks which room zone a continuous grid coordinate belongs to
 */
export function getZoneAt(gx: number, gy: number): RoomZoneDef | null {
  for (const zone of ROOM_ZONES) {
    if (gx >= zone.minX && gx <= zone.maxX && gy >= zone.minY && gy <= zone.maxY) {
      return zone;
    }
  }
  return null;
}

/**
 * Draws the entire base environmental layer:
 * Floor tiles, cyber conduits, zone borders, pathways, and entrance
 */
export function drawFloorAndArchitecture(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number,
  time: number
): void {
  // 1. Draw all isometric floor tiles
  for (let gy = 0; gy < GRID_SIZE_Y; gy++) {
    for (let gx = 0; gx < GRID_SIZE_X; gx++) {
      const zone = getZoneAt(gx, gy);
      const { screenX, screenY } = gridToScreen(gx, gy, 0, originX, originY);

      let floorColor = '#060b17';
      let strokeColor = '#0e1a30';

      if (zone) {
        floorColor = zone.floorColor;
        strokeColor = zone.gridLineColor;
      } else {
        // Hallway / corridor styling with subtle grid
        const isPathway = (gx >= 10 && gx <= 12) || (gy >= 10 && gy <= 12);
        if (isPathway) {
          floorColor = '#070f20';
          strokeColor = '#1e3a8a33';
        }
      }

      drawIsometricTile(ctx, screenX, screenY, floorColor, strokeColor, 1);
    }
  }

  // 2. Animated Cyber Conduits / Floor Lighting Strips
  ctx.save();
  ctx.strokeStyle = '#0284c7';
  ctx.lineWidth = 1.5;
  ctx.shadowColor = '#38bdf8';
  ctx.shadowBlur = 6;
  ctx.setLineDash([8, 12]);
  ctx.lineDashOffset = -time * 25; // Continuous flowing pulse

  // Cross hallways conduits
  const pElevator = gridToScreen(2, 11, 0, originX, originY);
  const pHubCenter = gridToScreen(11, 11, 0, originX, originY);
  const pEastVault = gridToScreen(21, 11, 0, originX, originY);
  const pNorthCEO = gridToScreen(11, 3, 0, originX, originY);
  const pSouthBoard = gridToScreen(11, 20, 0, originX, originY);

  ctx.beginPath();
  // Horizontal corridor
  ctx.moveTo(pElevator.screenX, pElevator.screenY);
  ctx.lineTo(pHubCenter.screenX, pHubCenter.screenY);
  ctx.lineTo(pEastVault.screenX, pEastVault.screenY);
  // Vertical corridor
  ctx.moveTo(pNorthCEO.screenX, pNorthCEO.screenY);
  ctx.lineTo(pHubCenter.screenX, pHubCenter.screenY);
  ctx.lineTo(pSouthBoard.screenX, pSouthBoard.screenY);
  ctx.stroke();

  ctx.restore();

  // 3. Office Entrance / Elevator Portal
  const elevPos = gridToScreen(ELEVATOR_SPAWN.x, ELEVATOR_SPAWN.y, 0, originX, originY);
  ctx.save();
  // Elevator floor indicator pad
  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#38bdf8';
  ctx.shadowBlur = 8;
  ctx.beginPath();
  ctx.ellipse(elevPos.screenX, elevPos.screenY, 26, 13, 0, 0, Math.PI * 2);
  ctx.stroke();

  // Sliding double doors back wall
  drawIsometricBlock(
    ctx,
    elevPos.screenX - 16,
    elevPos.screenY - 14,
    0.6,
    0.6,
    34,
    '#0f172a',
    '#0284c7',
    '#0369a1',
    '#38bdf8'
  );
  ctx.fillStyle = '#38bdf8';
  ctx.font = 'bold 8px monospace';
  ctx.fillText('▲ ELEVATOR', elevPos.screenX - 25, elevPos.screenY - 38);
  ctx.restore();

  // 4. Low Glass Partition Walls with Neon Trims
  drawGlassPartitions(ctx, originX, originY);

  // 5. Floating Neon Room Signboards
  drawNeonSignboards(ctx, originX, originY, time);
}

/**
 * Draws low isometric translucent glass partitions around zones
 */
function drawGlassPartitions(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number
): void {
  ctx.save();

  // Partitions for CEO Suite, Engineering, Research, Creative
  const partitions = [
    // CEO Suite bottom wall with doorway gap
    { start: { x: 2, y: 7 }, end: { x: 4, y: 7 }, color: '#38bdf8' },
    { start: { x: 5.5, y: 7 }, end: { x: 7, y: 7 }, color: '#38bdf8' },
    // Engineering Bay bottom wall with doorway gap
    { start: { x: 14, y: 8 }, end: { x: 16.5, y: 8 }, color: '#60a5fa' },
    { start: { x: 18, y: 8 }, end: { x: 21, y: 8 }, color: '#60a5fa' },
    // Creative Studio top wall with doorway gap
    { start: { x: 2, y: 14 }, end: { x: 4.5, y: 14 }, color: '#c084fc' },
    { start: { x: 6, y: 14 }, end: { x: 8, y: 14 }, color: '#c084fc' },
    // Research Lab top wall with doorway gap
    { start: { x: 14, y: 14 }, end: { x: 16.5, y: 14 }, color: '#f472b6' },
    { start: { x: 18, y: 14 }, end: { x: 21, y: 14 }, color: '#f472b6' },
  ];

  for (const part of partitions) {
    const p1 = gridToScreen(part.start.x, part.start.y, 0, originX, originY);
    const p2 = gridToScreen(part.end.x, part.end.y, 0, originX, originY);

    const heightPx = 16;
    ctx.fillStyle = 'rgba(14, 165, 233, 0.12)';
    ctx.strokeStyle = part.color;
    ctx.lineWidth = 1.2;

    ctx.beginPath();
    ctx.moveTo(p1.screenX, p1.screenY);
    ctx.lineTo(p2.screenX, p2.screenY);
    ctx.lineTo(p2.screenX, p2.screenY - heightPx);
    ctx.lineTo(p1.screenX, p1.screenY - heightPx);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Glowing top neon rail
    ctx.strokeStyle = part.color;
    ctx.shadowColor = part.color;
    ctx.shadowBlur = 4;
    ctx.beginPath();
    ctx.moveTo(p1.screenX, p1.screenY - heightPx);
    ctx.lineTo(p2.screenX, p2.screenY - heightPx);
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  ctx.restore();
}

/**
 * Draws floating cyber neon signboards above room entrances
 */
function drawNeonSignboards(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number,
  time: number
): void {
  ctx.save();
  for (const zone of ROOM_ZONES) {
    const { screenX, screenY } = gridToScreen(zone.labelPos.x, zone.labelPos.y, 1.2, originX, originY);
    const floatBob = Math.sin(time * 2 + zone.minX) * 2;

    const posY = screenY + floatBob;

    // Glowing signboard badge
    ctx.save();
    ctx.fillStyle = 'rgba(7, 14, 28, 0.88)';
    ctx.strokeStyle = zone.neonColor;
    ctx.lineWidth = 1.2;
    ctx.shadowColor = zone.neonColor;
    ctx.shadowBlur = 8;

    const textWidth = ctx.measureText(zone.label).width + 24;
    const boxW = Math.max(textWidth, 80);
    const boxH = 18;

    ctx.beginPath();
    ctx.roundRect(screenX - boxW / 2, posY - boxH / 2, boxW, boxH, 4);
    ctx.fill();
    ctx.stroke();

    // Neon typography
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 9px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.shadowColor = zone.neonColor;
    ctx.shadowBlur = 4;
    ctx.fillText(zone.label, screenX, posY);
    ctx.restore();
  }
  ctx.restore();
}

/**
 * Draws the central ANCOS AI Hub Holographic Pedestal & pulsating core
 */
export function drawCentralAIHub(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number,
  time: number
): void {
  const { screenX, screenY } = gridToScreen(11, 11, 0, originX, originY);

  ctx.save();

  // 1. Multi-tier concentric holographic base rings on floor
  const pulse = Math.sin(time * 3) * 3;
  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#10b981';
  ctx.shadowBlur = 12;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 48 + pulse, 24 + pulse * 0.5, 0, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 32 - pulse * 0.5, 16 - pulse * 0.25, 0, 0, Math.PI * 2);
  ctx.stroke();

  // 2. Central Metallic Pedestal Column
  drawIsometricBlock(
    ctx,
    screenX,
    screenY - 14,
    0.7,
    0.7,
    18,
    '#0f2233',
    '#051524',
    '#081e30',
    '#10b981'
  );

  // 3. Floating 3D Holographic Neural Core Sphere
  const coreY = screenY - 34 + Math.sin(time * 2.5) * 4;
  ctx.fillStyle = '#38bdf8';
  ctx.shadowColor = '#38bdf8';
  ctx.shadowBlur = 16;
  ctx.beginPath();
  ctx.arc(screenX, coreY, 9, 0, Math.PI * 2);
  ctx.fill();

  // Rotating holographic wireframe ring
  ctx.save();
  ctx.strokeStyle = '#34d399';
  ctx.lineWidth = 1.5;
  const rotAngle = time * 2;
  ctx.translate(screenX, coreY);
  ctx.rotate(rotAngle);
  ctx.beginPath();
  ctx.ellipse(0, 0, 18, 6, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  // Floating data particles rising upwards
  for (let i = 0; i < 6; i++) {
    const pOffset = ((time * 30 + i * 15) % 40);
    const px = screenX + Math.sin(i * 1.5 + time) * 14;
    const py = coreY + 12 - pOffset;
    ctx.fillStyle = i % 2 === 0 ? '#38bdf8' : '#10b981';
    ctx.fillRect(px, py, 2, 2);
  }

  ctx.restore();
}

/**
 * Draws all workstations (desks, computers, monitors, ergonomic chairs)
 */
export function drawWorkstation(
  ctx: CanvasRenderingContext2D,
  ws: WorkstationDef,
  originX: number,
  originY: number,
  time: number
): void {
  const { screenX, screenY } = gridToScreen(ws.deskGridX, ws.deskGridY, 0, originX, originY);
  const isOccupied = ws.occupantId !== null;

  ctx.save();

  // 1. Workstation Desk
  const isExec = ws.deskType === 'executive';
  const deskWidthRatio = isExec ? 1.4 : 1.1;
  const deskDepthRatio = 0.8;
  const deskHeight = 14;

  drawIsometricBlock(
    ctx,
    screenX,
    screenY - 10,
    deskWidthRatio,
    deskDepthRatio,
    deskHeight,
    isExec ? '#162238' : '#0e1726',
    isExec ? '#0b1320' : '#080d16',
    isExec ? '#111c2e' : '#0b121e',
    isOccupied ? '#38bdf866' : '#1e293b'
  );

  // 2. Monitors & Screen Displays
  const monitorY = screenY - 18;

  if (ws.monitorCount >= 2) {
    // Dual monitor setup
    // Left Monitor
    drawMonitor(ctx, screenX - 10, monitorY - 4, isOccupied, time, '#38bdf8', 12, 10);
    // Right Monitor
    drawMonitor(ctx, screenX + 10, monitorY - 4, isOccupied, time, '#10b981', 12, 10);
  } else {
    // Single wide curved monitor
    drawMonitor(ctx, screenX, monitorY - 4, isOccupied, time, '#38bdf8', 18, 11);
  }

  // 3. Ergonomic Office Swivel Chair
  const chairPos = gridToScreen(ws.seatGridX, ws.seatGridY, 0, originX, originY);
  ctx.save();
  // Chair base caster shadow
  ctx.fillStyle = '#00000044';
  ctx.beginPath();
  ctx.ellipse(chairPos.screenX, chairPos.screenY + 2, 8, 4, 0, 0, Math.PI * 2);
  ctx.fill();

  // Chair seat cushion
  ctx.fillStyle = '#1e293b';
  ctx.strokeStyle = isOccupied ? '#38bdf8' : '#334155';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.ellipse(chairPos.screenX, chairPos.screenY - 4, 6, 4, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  // Chair backrest
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(chairPos.screenX - 5, chairPos.screenY - 12, 10, 8);
  ctx.strokeRect(chairPos.screenX - 5, chairPos.screenY - 12, 10, 8);
  ctx.restore();

  ctx.restore();
}

/**
 * Draws an isometric monitor screen with dynamic glowing code or standby status
 */
function drawMonitor(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  isOccupied: boolean,
  time: number,
  activeColor: string,
  w: number,
  h: number
): void {
  ctx.save();
  // Monitor stand
  ctx.fillStyle = '#334155';
  ctx.fillRect(x - 1.5, y + h / 2, 3, 5);
  ctx.fillRect(x - 4, y + h / 2 + 5, 8, 2);

  // Monitor frame
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(x - w / 2, y - h / 2, w, h);
  ctx.strokeStyle = isOccupied ? activeColor : '#334155';
  ctx.lineWidth = 1;
  ctx.strokeRect(x - w / 2, y - h / 2, w, h);

  // Monitor Screen
  if (isOccupied) {
    ctx.fillStyle = '#071224';
    ctx.fillRect(x - w / 2 + 1, y - h / 2 + 1, w - 2, h - 2);

    // Glowing animated syntax code lines or graphs
    ctx.fillStyle = activeColor;
    const scanLine = (Math.sin(time * 8) + 1) * 2;
    for (let l = 0; l < 3; l++) {
      const lineLen = ((l * 4 + time * 10) % (w - 4)) + 2;
      ctx.fillRect(x - w / 2 + 2, y - h / 2 + 2 + l * 2.2, lineLen, 1.2);
    }
  } else {
    // Standby: dark screen with subtle amber/green standby power LED
    ctx.fillStyle = '#050811';
    ctx.fillRect(x - w / 2 + 1, y - h / 2 + 1, w - 2, h - 2);

    const ledBlink = Math.sin(time * 2) > 0;
    ctx.fillStyle = ledBlink ? '#10b981' : '#064e3b';
    ctx.fillRect(x + w / 2 - 3, y + h / 2 - 3, 1.5, 1.5);
  }

  ctx.restore();
}

/**
 * Draws server racks with multi-bay animated status LEDs in the server vault
 */
export function drawServerRacks(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number,
  time: number
): void {
  ctx.save();
  const rackLocations = [
    { gx: 21, gy: 9.5 },
    { gx: 21, gy: 11.5 },
    { gx: 21, gy: 13.5 },
  ];

  for (const loc of rackLocations) {
    const { screenX, screenY } = gridToScreen(loc.gx, loc.gy, 0, originX, originY);
    // Draw tall server rack cabinet
    drawIsometricBlock(ctx, screenX, screenY - 14, 0.9, 0.9, 28, '#0b1626', '#060d17', '#08111e', '#0284c7');

    // Draw multi-bay LED banks
    for (let r = 0; r < 4; r++) {
      const ledY = screenY - 26 + r * 6;
      for (let c = 0; c < 3; c++) {
        const ledX = screenX - 6 + c * 5;
        const blinkVal = Math.sin(time * 6 + loc.gy * 3 + r * 2 + c);
        let ledColor = '#10b981'; // normal green
        if (blinkVal > 0.6) ledColor = '#38bdf8'; // blue data traffic
        else if (blinkVal < -0.7) ledColor = '#f59e0b'; // amber cache activity

        ctx.fillStyle = ledColor;
        ctx.fillRect(ledX, ledY, 2, 2);
      }
    }
  }

  ctx.restore();
}

/**
 * Draws the Boardroom conference table & central hologram projector
 */
export function drawBoardroomTable(
  ctx: CanvasRenderingContext2D,
  originX: number,
  originY: number,
  time: number,
  meetingActive: boolean
): void {
  const { screenX, screenY } = gridToScreen(11, 17, 0, originX, originY);

  ctx.save();

  // Conference Table (Hexagonal/Oval Isometric shape)
  drawIsometricBlock(
    ctx,
    screenX,
    screenY - 8,
    2.2,
    1.4,
    12,
    '#131126',
    '#0c0b1a',
    '#100d21',
    '#a855f7'
  );

  // Central Hologram Tabletop Projector
  const projY = screenY - 14;
  ctx.fillStyle = '#a855f7';
  ctx.shadowColor = '#a855f7';
  ctx.shadowBlur = meetingActive ? 14 : 4;
  ctx.beginPath();
  ctx.ellipse(screenX, projY, 14, 7, 0, 0, Math.PI * 2);
  ctx.fill();

  if (meetingActive) {
    // Spinning 3D Milestone Hologram
    const holoY = projY - 18 + Math.sin(time * 3) * 2;
    ctx.strokeStyle = '#e879f9';
    ctx.lineWidth = 1.5;
    ctx.shadowBlur = 12;

    ctx.save();
    ctx.translate(screenX, holoY);
    ctx.rotate(time * 1.5);
    ctx.strokeRect(-8, -8, 16, 16);
    ctx.restore();

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 8px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('STANDUP SYNC', screenX, holoY - 14);
  }

  // Draw Conference Chairs around table
  for (const seat of MEETING_SEATS) {
    const chairPos = gridToScreen(seat.gridX, seat.gridY, 0, originX, originY);
    ctx.fillStyle = '#1e1b38';
    ctx.strokeStyle = seat.occupantId ? '#c084fc' : '#475569';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.ellipse(chairPos.screenX, chairPos.screenY - 3, 5, 3.5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  ctx.restore();
}

