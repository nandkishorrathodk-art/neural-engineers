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
  drawPixelFloorTile,
  drawPixelFurnitureBlock,
  TILE_WIDTH,
  TILE_HEIGHT,
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
  // 1. Draw the orthogonal office floor as independent pixel tiles.
  for (let gy = 0; gy < GRID_SIZE_Y; gy++) {
    for (let gx = 0; gx < GRID_SIZE_X; gx++) {
      const zone = getZoneAt(gx, gy);
      const { screenX, screenY } = gridToScreen(gx, gy, 0, originX, originY);

      let floorColor = '#55575b';
      let strokeColor = '#66686b';

      if (zone) {
        floorColor = zone.floorColor;
        strokeColor = zone.gridLineColor;
      } else {
        // Hallway / corridor styling with subtle grid
        const isPathway = (gx >= 10 && gx <= 12) || (gy >= 10 && gy <= 12);
        if (isPathway) {
          floorColor = '#62605c';
          strokeColor = '#77736b';
        }
      }

      drawPixelFloorTile(ctx, screenX, screenY, floorColor, strokeColor, 1);

      if (!zone && (gx * 7 + gy * 11) % 5 === 0) {
        ctx.fillStyle = 'rgba(235, 220, 190, 0.14)';
        ctx.fillRect(screenX - 12, screenY - 7, 3, 2);
        ctx.fillStyle = 'rgba(24, 29, 32, 0.13)';
        ctx.fillRect(screenX + 9, screenY + 5, 4, 2);
      }
    }
  }

  // Framed room edge keeps the whole floor readable as one connected place.
  ctx.save();
  ctx.strokeStyle = '#303337';
  ctx.lineWidth = 10;
  ctx.strokeRect(
    originX - TILE_HALF_W,
    originY - TILE_HALF_H,
    GRID_SIZE_X * TILE_WIDTH,
    GRID_SIZE_Y * TILE_HEIGHT
  );
  ctx.strokeStyle = '#b7a889';
  ctx.lineWidth = 2;
  ctx.strokeRect(
    originX - TILE_HALF_W + 5,
    originY - TILE_HALF_H + 5,
    GRID_SIZE_X * TILE_WIDTH - 10,
    GRID_SIZE_Y * TILE_HEIGHT - 10
  );
  ctx.restore();

  // 2. Pixel entrance pad and sliding doors
  const elevPos = gridToScreen(ELEVATOR_SPAWN.x, ELEVATOR_SPAWN.y, 0, originX, originY);
  ctx.save();
  // Elevator floor indicator pad
  ctx.fillStyle = '#ad956c';
  ctx.fillRect(elevPos.screenX - 27, elevPos.screenY - 12, 54, 24);
  ctx.strokeStyle = '#594f41';
  ctx.lineWidth = 3;
  ctx.strokeRect(elevPos.screenX - 27, elevPos.screenY - 12, 54, 24);

  // Sliding double doors back wall
  drawPixelFurnitureBlock(
    ctx,
    elevPos.screenX - 16,
    elevPos.screenY - 14,
    0.6,
    0.6,
    34,
    '#88775c',
    '#a98f65',
    '#8b7250',
    '#e2c78e'
  );
  ctx.fillStyle = '#f1e4c8';
  ctx.font = 'bold 9px monospace';
  ctx.textAlign = 'center';
  ctx.fillText('HQ ENTRANCE', elevPos.screenX, elevPos.screenY - 24);
  ctx.restore();

  // 3. Room walls and door openings
  drawGlassPartitions(ctx, originX, originY);

  // 4. Room labels and independent office props
  drawNeonSignboards(ctx, originX, originY, time);
  drawOfficeProps(ctx, originX, originY, time);
}

function drawOfficeProps(ctx: CanvasRenderingContext2D, originX: number, originY: number, time: number): void {
  const plants = [
    [2.7, 3.4], [8.2, 3.5], [17, 3.5], [21.2, 7.2],
    [2.8, 19.5], [8, 19], [16, 19], [21.2, 19], [10, 12.8], [15.3, 12.8],
  ];

  for (const [gx, gy] of plants) {
    const { screenX: x, screenY: y } = gridToScreen(gx, gy, 0, originX, originY);
    ctx.save();
    ctx.fillStyle = 'rgba(20, 24, 25, 0.28)';
    ctx.fillRect(x - 12, y + 5, 26, 7);
    ctx.fillStyle = '#9b6245';
    ctx.fillRect(x - 9, y - 1, 18, 13);
    ctx.fillStyle = '#c2875c';
    ctx.fillRect(x - 7, y - 1, 14, 3);
    const sway = Math.round(Math.sin(time * 1.2 + gx) * 1.5);
    ctx.fillStyle = '#335b45';
    ctx.fillRect(x - 3, y - 22 + sway, 6, 22);
    ctx.fillStyle = '#4f8a5e';
    ctx.fillRect(x - 15, y - 19 + sway, 10, 7);
    ctx.fillRect(x + 5, y - 18 - sway, 11, 7);
    ctx.fillRect(x - 12, y - 29 - sway, 9, 8);
    ctx.fillRect(x + 3, y - 29 + sway, 10, 8);
    ctx.fillStyle = '#78a86b';
    ctx.fillRect(x - 10, y - 26 - sway, 4, 3);
    ctx.fillRect(x + 7, y - 26 + sway, 4, 3);
    ctx.restore();
  }

  // Low bookshelf along the executive office's far wall.
  const shelf = gridToScreen(21, 3, 0, originX, originY);
  ctx.save();
  ctx.fillStyle = '#493a33';
  ctx.fillRect(shelf.screenX - 15, shelf.screenY - 14, 27, 66);
  ctx.fillStyle = '#6d4e3b';
  ctx.fillRect(shelf.screenX - 12, shelf.screenY - 11, 21, 59);
  for (let row = 0; row < 3; row++) {
    const y = shelf.screenY - 7 + row * 18;
    ctx.fillStyle = '#b78954';
    ctx.fillRect(shelf.screenX - 12, y + 12, 21, 3);
    const bookColors = ['#668878', '#c98559', '#d5bd82', '#7a7790'];
    for (let col = 0; col < 4; col++) {
      ctx.fillStyle = bookColors[(row + col) % bookColors.length];
      ctx.fillRect(shelf.screenX - 10 + col * 5, y + (col % 2), 4, 11 - (col % 2));
    }
  }
  ctx.restore();

  // Cozy break-room sofa and coffee table in the creative lounge.
  const sofa = gridToScreen(5.5, 20, 0, originX, originY);
  ctx.save();
  ctx.fillStyle = '#4a3934';
  ctx.fillRect(sofa.screenX - 47, sofa.screenY - 17, 94, 35);
  ctx.fillStyle = '#9b705d';
  ctx.fillRect(sofa.screenX - 42, sofa.screenY - 14, 84, 26);
  ctx.fillStyle = '#b58a72';
  ctx.fillRect(sofa.screenX - 39, sofa.screenY - 12, 34, 19);
  ctx.fillRect(sofa.screenX + 5, sofa.screenY - 12, 34, 19);
  ctx.fillStyle = '#725449';
  ctx.fillRect(sofa.screenX - 35, sofa.screenY + 12, 5, 7);
  ctx.fillRect(sofa.screenX + 30, sofa.screenY + 12, 5, 7);
  ctx.fillStyle = '#7b6046';
  ctx.fillRect(sofa.screenX - 24, sofa.screenY + 35, 48, 18);
  ctx.fillStyle = '#b79a70';
  ctx.fillRect(sofa.screenX - 21, sofa.screenY + 36, 42, 3);
  ctx.fillStyle = '#f0d5a2';
  ctx.fillRect(sofa.screenX - 3, sofa.screenY + 40, 7, 6);
  ctx.restore();
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

  const partitions = [
    // Engineering room, with its entrance opening onto the central hall.
    { start: { x: 2, y: 9 }, end: { x: 4, y: 9 }, color: '#bed9ce' },
    { start: { x: 6, y: 9 }, end: { x: 9, y: 9 }, color: '#bed9ce' },
    { start: { x: 2, y: 2 }, end: { x: 2, y: 9 }, color: '#bed9ce' },
    { start: { x: 9, y: 2 }, end: { x: 9, y: 9 }, color: '#bed9ce' },
    // Boardroom with a wide glass entry.
    { start: { x: 10, y: 9 }, end: { x: 11, y: 9 }, color: '#efd0a3' },
    { start: { x: 13, y: 9 }, end: { x: 15, y: 9 }, color: '#efd0a3' },
    { start: { x: 10, y: 2 }, end: { x: 10, y: 9 }, color: '#efd0a3' },
    { start: { x: 15, y: 2 }, end: { x: 15, y: 9 }, color: '#efd0a3' },
    // CEO office.
    { start: { x: 16, y: 9 }, end: { x: 18, y: 9 }, color: '#f1d291' },
    { start: { x: 20, y: 9 }, end: { x: 22, y: 9 }, color: '#f1d291' },
    { start: { x: 16, y: 2 }, end: { x: 16, y: 9 }, color: '#f1d291' },
    { start: { x: 22, y: 2 }, end: { x: 22, y: 9 }, color: '#f1d291' },
    // Creative studio and research floor.
    { start: { x: 2, y: 14 }, end: { x: 5, y: 14 }, color: '#ddbfdf' },
    { start: { x: 7, y: 14 }, end: { x: 9, y: 14 }, color: '#ddbfdf' },
    { start: { x: 2, y: 14 }, end: { x: 2, y: 21 }, color: '#ddbfdf' },
    { start: { x: 9, y: 14 }, end: { x: 9, y: 21 }, color: '#ddbfdf' },
    { start: { x: 15, y: 14 }, end: { x: 17, y: 14 }, color: '#d7c2e2' },
    { start: { x: 19, y: 14 }, end: { x: 22, y: 14 }, color: '#d7c2e2' },
    { start: { x: 15, y: 14 }, end: { x: 15, y: 21 }, color: '#d7c2e2' },
    { start: { x: 22, y: 14 }, end: { x: 22, y: 21 }, color: '#d7c2e2' },
  ];

  for (const part of partitions) {
    const p1 = gridToScreen(part.start.x, part.start.y, 0, originX, originY);
    const p2 = gridToScreen(part.end.x, part.end.y, 0, originX, originY);

    const heightPx = 18;
    ctx.fillStyle = 'rgba(39, 43, 44, 0.88)';
    ctx.strokeStyle = part.color;
    ctx.lineWidth = 3;

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
    ctx.shadowBlur = 2;
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
    if (zone.id === 'ai_hub') continue;
    const { screenX, screenY } = gridToScreen(zone.labelPos.x, zone.labelPos.y, 1.2, originX, originY);
    const floatBob = Math.sin(time * 2 + zone.minX) * 2;

    const posY = screenY + floatBob;

    // Glowing signboard badge
    ctx.save();
    ctx.fillStyle = 'rgba(39, 43, 44, 0.94)';
    ctx.strokeStyle = zone.neonColor;
    ctx.lineWidth = 1.2;
    ctx.shadowColor = zone.neonColor;
    ctx.shadowBlur = 2;

    const textWidth = ctx.measureText(zone.label).width + 24;
    const boxW = Math.max(textWidth, 80);
    const boxH = 18;

    ctx.beginPath();
    ctx.rect(screenX - boxW / 2, posY - boxH / 2, boxW, boxH);
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
  const { screenX, screenY } = gridToScreen(12, 12, 0, originX, originY);

  ctx.save();

  // Pixel sign on the reception wall gives the office a clear company identity.
  ctx.fillStyle = '#263a3c';
  ctx.fillRect(screenX - 100, screenY - 102, 200, 48);
  ctx.fillStyle = '#a8c8b3';
  ctx.fillRect(screenX - 96, screenY - 98, 192, 40);
  ctx.fillStyle = '#29444a';
  ctx.font = 'bold 28px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('ANCOS', screenX, screenY - 79);
  ctx.fillStyle = '#e5d7b9';
  ctx.font = 'bold 7px monospace';
  ctx.fillText('AUTONOMOUS COMPANY OS', screenX, screenY - 62);

  // 1. Multi-tier concentric holographic base rings on floor
  const pulse = Math.sin(time * 3) * 3;
  ctx.strokeStyle = '#9bd2bd';
  ctx.lineWidth = 3;
  ctx.shadowColor = '#9bd2bd';
  ctx.shadowBlur = 4;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 48 + pulse, 24 + pulse * 0.5, 0, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = '#f1d599';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 32 - pulse * 0.5, 16 - pulse * 0.25, 0, 0, Math.PI * 2);
  ctx.stroke();

  // 2. Central Metallic Pedestal Column
  drawPixelFurnitureBlock(
    ctx,
    screenX,
    screenY - 14,
    0.7,
    0.7,
    18,
    '#447267',
    '#314c47',
    '#375c53',
    '#b3dfc8'
  );

  // 3. Floating 3D Holographic Neural Core Sphere
  const coreY = screenY - 34 + Math.sin(time * 2.5) * 4;
  ctx.fillStyle = '#f0cc75';
  ctx.shadowColor = '#f0cc75';
  ctx.shadowBlur = 8;
  ctx.beginPath();
  ctx.arc(screenX, coreY, 9, 0, Math.PI * 2);
  ctx.fill();

  // Rotating holographic wireframe ring
  ctx.save();
  ctx.strokeStyle = '#d3e4a5';
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

  drawPixelFurnitureBlock(
    ctx,
    screenX,
    screenY - 10,
    deskWidthRatio,
    deskDepthRatio,
    deskHeight,
    isExec ? '#a87d51' : '#98734f',
    isExec ? '#674b38' : '#604b3a',
    isExec ? '#7e5e41' : '#795b40',
    isOccupied ? '#d8c39b' : '#75624e'
  );

  // 2. Monitors & Screen Displays
  const monitorY = screenY - 18;

  if (ws.monitorCount >= 2) {
    // Dual monitor setup
    // Left Monitor
    drawMonitor(ctx, screenX - 16, monitorY - 4, isOccupied, time, '#a8d2c0', 22, 16);
    // Right Monitor
    drawMonitor(ctx, screenX + 16, monitorY - 4, isOccupied, time, '#d8c181', 22, 16);
  } else {
    // Single wide curved monitor
    drawMonitor(ctx, screenX, monitorY - 4, isOccupied, time, '#a8d2c0', 28, 17);
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
    drawPixelFurnitureBlock(ctx, screenX, screenY - 14, 0.9, 0.9, 28, '#665344', '#43382f', '#56483c', '#d2b17b');

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
  meetingActive: boolean,
  meetingTitle: string = ''
): void {
  const { screenX, screenY } = gridToScreen(12, 5, 0, originX, originY);

  ctx.save();

  // Shared planning board mounted on the meeting-room wall.
  const board = gridToScreen(12, 3, 0, originX, originY);
  ctx.fillStyle = '#473f37';
  ctx.fillRect(board.screenX - 66, board.screenY - 20, 132, 44);
  ctx.fillStyle = '#eee4ce';
  ctx.fillRect(board.screenX - 62, board.screenY - 16, 124, 36);
  ctx.fillStyle = '#668c7a';
  ctx.fillRect(board.screenX - 52, board.screenY - 8, 22, 3);
  ctx.fillRect(board.screenX - 52, board.screenY - 1, 35, 3);
  ctx.fillStyle = '#7293a1';
  ctx.fillRect(board.screenX - 9, board.screenY - 8, 25, 14);
  ctx.fillStyle = '#c58c54';
  ctx.fillRect(board.screenX + 23, board.screenY - 8, 25, 3);
  ctx.fillRect(board.screenX + 23, board.screenY - 1, 18, 3);
  ctx.fillStyle = '#7a6254';
  ctx.fillRect(board.screenX - 3, board.screenY + 24, 6, 12);

  // Long conference table in the pixel office.
  drawPixelFurnitureBlock(
    ctx,
    screenX,
    screenY - 8,
    2.2,
    1.4,
    12,
    '#856246',
    '#533b2d',
    '#715039',
    '#d2b17b'
  );

  // Central Hologram Tabletop Projector
  const projY = screenY - 14;
  ctx.fillStyle = '#78bba8';
  ctx.shadowColor = '#78bba8';
  ctx.shadowBlur = meetingActive ? 8 : 1;
  ctx.beginPath();
  ctx.ellipse(screenX, projY, 14, 7, 0, 0, Math.PI * 2);
  ctx.fill();

  if (meetingActive) {
    // Spinning 3D Milestone Hologram
    const holoY = projY - 18 + Math.sin(time * 3) * 2;
    ctx.strokeStyle = '#e4cc91';
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
    ctx.fillText(meetingTitle.slice(0, 24), screenX, holoY - 14);
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

