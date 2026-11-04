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
  const left = originX - TILE_HALF_W;
  const top = originY - TILE_HALF_H;
  const width = GRID_SIZE_X * TILE_WIDTH;
  const height = GRID_SIZE_Y * TILE_HEIGHT;

  // One continuous warm floor keeps the rooms feeling like a shared studio.
  ctx.save();
  ctx.shadowColor = 'rgba(18, 25, 31, 0.28)';
  ctx.shadowBlur = 24;
  ctx.shadowOffsetY = 12;
  ctx.fillStyle = '#d7d2c6';
  ctx.fillRect(left, top, width, height);
  ctx.restore();

  const floorWash = ctx.createLinearGradient(left, top, left + width, top + height);
  floorWash.addColorStop(0, '#e7e0d2');
  floorWash.addColorStop(0.52, '#d5d0c5');
  floorWash.addColorStop(1, '#c8c8c2');
  ctx.fillStyle = floorWash;
  ctx.fillRect(left, top, width, height);

  // Soft department rugs and quiet grout lines replace the hard pixel grid.
  for (const zone of ROOM_ZONES) {
    const x = originX + zone.minX * TILE_WIDTH - TILE_HALF_W;
    const y = originY + zone.minY * TILE_HEIGHT - TILE_HALF_H;
    const w = (zone.maxX - zone.minX + 1) * TILE_WIDTH;
    const h = (zone.maxY - zone.minY + 1) * TILE_HEIGHT;
    const tint = ctx.createLinearGradient(x, y, x + w, y + h);
    tint.addColorStop(0, `${zone.floorColor}e8`);
    tint.addColorStop(1, `${zone.floorColor}c8`);
    ctx.fillStyle = tint;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = 'rgba(255,255,255,0.14)';
    for (let gx = 1; gx < zone.maxX - zone.minX + 1; gx++) {
      ctx.fillRect(x + gx * TILE_WIDTH, y + 5, 1, h - 10);
    }
    for (let gy = 1; gy < zone.maxY - zone.minY + 1; gy++) {
      ctx.fillRect(x + 5, y + gy * TILE_HEIGHT, w - 10, 1);
    }
    ctx.strokeStyle = 'rgba(84, 73, 61, 0.2)';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
  }

  // Subtle corridor runner guides movement through the center of the office.
  const runnerX = originX + 9.5 * TILE_WIDTH;
  const runnerY = originY + 9.5 * TILE_HEIGHT;
  ctx.fillStyle = 'rgba(249, 245, 236, 0.32)';
  ctx.fillRect(runnerX, runnerY, 6 * TILE_WIDTH, 5 * TILE_HEIGHT);
  ctx.strokeStyle = 'rgba(255,255,255,0.3)';
  ctx.lineWidth = 1;
  ctx.setLineDash([6, 8]);
  ctx.beginPath();
  ctx.moveTo(runnerX + 3 * TILE_WIDTH, runnerY + 8);
  ctx.lineTo(runnerX + 3 * TILE_WIDTH, runnerY + 5 * TILE_HEIGHT - 8);
  ctx.stroke();
  ctx.setLineDash([]);

  // Warm wood skirting frames the floor without a heavy UI-like border.
  ctx.strokeStyle = '#89765d';
  ctx.lineWidth = 8;
  ctx.strokeRect(left, top, width, height);
  ctx.strokeStyle = 'rgba(255, 246, 224, 0.72)';
  ctx.lineWidth = 2;
  ctx.strokeRect(left + 5, top + 5, width - 10, height - 10);

  // Reception entrance and glazed doors.
  const elevPos = gridToScreen(ELEVATOR_SPAWN.x, ELEVATOR_SPAWN.y, 0, originX, originY);
  ctx.save();
  ctx.fillStyle = 'rgba(72, 93, 90, 0.16)';
  ctx.beginPath();
  ctx.ellipse(elevPos.screenX, elevPos.screenY + 2, 40, 18, 0, 0, Math.PI * 2);
  ctx.fill();
  const doorGradient = ctx.createLinearGradient(elevPos.screenX - 28, elevPos.screenY - 15, elevPos.screenX + 28, elevPos.screenY + 15);
  doorGradient.addColorStop(0, '#617f7c');
  doorGradient.addColorStop(0.48, '#d8e6df');
  doorGradient.addColorStop(0.52, '#bed3cc');
  doorGradient.addColorStop(1, '#55736f');
  ctx.fillStyle = doorGradient;
  ctx.beginPath();
  ctx.roundRect(elevPos.screenX - 28, elevPos.screenY - 14, 56, 28, 8);
  ctx.fill();
  ctx.strokeStyle = '#f7efdc';
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.strokeStyle = 'rgba(45, 71, 70, 0.55)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(elevPos.screenX, elevPos.screenY - 10);
  ctx.lineTo(elevPos.screenX, elevPos.screenY + 10);
  ctx.stroke();
  ctx.fillStyle = '#415d59';
  ctx.font = '600 9px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('ANCOS HQ', elevPos.screenX, elevPos.screenY - 24);
  ctx.restore();

  // Glass walls, room name plaques, and independent furnishings stay layered.
  drawGlassPartitions(ctx, originX, originY);
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
    ctx.fillStyle = 'rgba(52, 55, 43, 0.2)';
    ctx.beginPath();
    ctx.ellipse(x, y + 5, 15, 5, 0, 0, Math.PI * 2);
    ctx.fill();
    const pot = ctx.createLinearGradient(x - 10, y, x + 10, y + 12);
    pot.addColorStop(0, '#b98b67');
    pot.addColorStop(1, '#795d4d');
    ctx.fillStyle = pot;
    ctx.beginPath();
    ctx.roundRect(x - 9, y - 2, 18, 14, [3, 3, 7, 7]);
    ctx.fill();
    ctx.strokeStyle = '#e1bd8d';
    ctx.lineWidth = 1;
    ctx.stroke();
    const sway = Math.sin(time * 1.2 + gx) * 1.5;
    const leaves = [
      [-10, -19, -0.7], [-4, -26, -0.25], [5, -21, 0.45], [11, -28, 0.7], [1, -34, 0.1],
    ];
    for (const [dx, dy, tilt] of leaves) {
      ctx.save();
      ctx.translate(x + dx + sway, y + dy);
      ctx.rotate(tilt + sway * 0.025);
      ctx.fillStyle = '#517f5d';
      ctx.beginPath();
      ctx.ellipse(0, 0, 5, 10, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(217, 228, 177, 0.55)';
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(0, 7);
      ctx.lineTo(0, -6);
      ctx.stroke();
      ctx.restore();
    }
    ctx.restore();
  }

  // Warm bookcase in the Boss Office.
  const shelf = gridToScreen(21, 3, 0, originX, originY);
  ctx.save();
  ctx.shadowColor = 'rgba(38, 31, 25, 0.3)';
  ctx.shadowBlur = 8;
  ctx.fillStyle = '#79583f';
  ctx.beginPath();
  ctx.roundRect(shelf.screenX - 18, shelf.screenY - 14, 36, 67, 4);
  ctx.fill();
  ctx.shadowBlur = 0;
  const shelfWood = ctx.createLinearGradient(shelf.screenX - 15, shelf.screenY, shelf.screenX + 15, shelf.screenY);
  shelfWood.addColorStop(0, '#a77951');
  shelfWood.addColorStop(0.5, '#d1a477');
  shelfWood.addColorStop(1, '#8e694a');
  ctx.fillStyle = shelfWood;
  ctx.beginPath();
  ctx.roundRect(shelf.screenX - 14, shelf.screenY - 10, 28, 58, 2);
  ctx.fill();
  for (let row = 0; row < 3; row++) {
    const y = shelf.screenY - 7 + row * 18;
    ctx.fillStyle = '#b78954';
    ctx.fillRect(shelf.screenX - 14, y + 12, 28, 3);
    const bookColors = ['#668878', '#c98559', '#d5bd82', '#7a7790'];
    for (let col = 0; col < 4; col++) {
      ctx.fillStyle = bookColors[(row + col) % bookColors.length];
      ctx.beginPath();
      ctx.roundRect(shelf.screenX - 11 + col * 6, y + (col % 2), 5, 11 - (col % 2), [1, 1, 0, 0]);
      ctx.fill();
    }
  }
  ctx.restore();

  // A soft sofa and coffee table make the Creative Studio feel lived in.
  const sofa = gridToScreen(5.5, 20, 0, originX, originY);
  ctx.save();
  ctx.fillStyle = 'rgba(47, 38, 39, 0.2)';
  ctx.beginPath();
  ctx.ellipse(sofa.screenX, sofa.screenY + 19, 53, 11, 0, 0, Math.PI * 2);
  ctx.fill();
  const sofaFabric = ctx.createLinearGradient(sofa.screenX - 42, sofa.screenY - 18, sofa.screenX + 42, sofa.screenY + 17);
  sofaFabric.addColorStop(0, '#c59b91');
  sofaFabric.addColorStop(0.55, '#a87872');
  sofaFabric.addColorStop(1, '#805b5a');
  ctx.fillStyle = sofaFabric;
  ctx.beginPath();
  ctx.roundRect(sofa.screenX - 45, sofa.screenY - 19, 90, 37, 11);
  ctx.fill();
  ctx.fillStyle = '#d7b5a8';
  ctx.beginPath();
  ctx.roundRect(sofa.screenX - 36, sofa.screenY - 15, 34, 24, 8);
  ctx.roundRect(sofa.screenX + 2, sofa.screenY - 15, 34, 24, 8);
  ctx.fill();
  ctx.fillStyle = '#705144';
  ctx.beginPath();
  ctx.roundRect(sofa.screenX - 22, sofa.screenY + 29, 44, 18, 5);
  ctx.fill();
  ctx.fillStyle = '#c8a778';
  ctx.beginPath();
  ctx.roundRect(sofa.screenX - 19, sofa.screenY + 29, 38, 4, 2);
  ctx.fill();
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
    // Infrastructure & DevOps room, with one clear doorway into the hall.
    { start: { x: 17, y: 9 }, end: { x: 22, y: 9 }, color: '#8eb4b1' },
    { start: { x: 17, y: 9 }, end: { x: 17, y: 10.5 }, color: '#8eb4b1' },
    { start: { x: 17, y: 12.5 }, end: { x: 17, y: 13 }, color: '#8eb4b1' },
    { start: { x: 22, y: 9 }, end: { x: 22, y: 13 }, color: '#8eb4b1' },
    { start: { x: 17, y: 13 }, end: { x: 22, y: 13 }, color: '#8eb4b1' },
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
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(65, 74, 73, 0.23)';
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.moveTo(p1.screenX + 2, p1.screenY + 2);
    ctx.lineTo(p2.screenX + 2, p2.screenY + 2);
    ctx.stroke();
    const glass = ctx.createLinearGradient(p1.screenX, p1.screenY - heightPx, p2.screenX, p2.screenY);
    glass.addColorStop(0, 'rgba(246, 248, 239, 0.72)');
    glass.addColorStop(0.5, 'rgba(180, 202, 197, 0.44)');
    glass.addColorStop(1, 'rgba(129, 154, 152, 0.58)');
    ctx.strokeStyle = glass;
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(p1.screenX, p1.screenY - heightPx);
    ctx.lineTo(p2.screenX, p2.screenY - heightPx);
    ctx.stroke();
    ctx.strokeStyle = part.color;
    ctx.globalAlpha = 0.75;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(p1.screenX, p1.screenY - heightPx - 2);
    ctx.lineTo(p2.screenX, p2.screenY - heightPx - 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
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

    ctx.save();
    ctx.font = '700 12px sans-serif';
    const textWidth = ctx.measureText(zone.label).width + 32;
    const boxW = Math.max(textWidth, 120);
    const boxH = 28;
    ctx.shadowColor = 'rgba(39, 46, 44, 0.23)';
    ctx.shadowBlur = 7;
    ctx.shadowOffsetY = 3;
    const plaque = ctx.createLinearGradient(screenX, posY - boxH / 2, screenX, posY + boxH / 2);
    plaque.addColorStop(0, '#fffaf0');
    plaque.addColorStop(1, '#e8dfcf');
    ctx.fillStyle = plaque;
    ctx.beginPath();
    ctx.roundRect(screenX - boxW / 2, posY - boxH / 2, boxW, boxH, 7);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
    ctx.strokeStyle = zone.neonColor;
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.fillStyle = zone.neonColor;
    ctx.beginPath();
    ctx.arc(screenX - boxW / 2 + 10, posY, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#3d4543';
    ctx.font = '700 11px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(zone.label, screenX + 4, posY, boxW - 24);
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
  // A calm reception plinth makes the AI Core the visual anchor of the floor.
  ctx.fillStyle = 'rgba(66, 84, 78, 0.17)';
  ctx.beginPath();
  ctx.ellipse(screenX, screenY + 5, 76, 25, 0, 0, Math.PI * 2);
  ctx.fill();
  const sign = ctx.createLinearGradient(screenX - 92, screenY - 90, screenX + 92, screenY - 48);
  sign.addColorStop(0, '#f5f0e2');
  sign.addColorStop(0.5, '#dbe5d8');
  sign.addColorStop(1, '#b9d0c5');
  ctx.fillStyle = sign;
  ctx.beginPath();
  ctx.roundRect(screenX - 92, screenY - 94, 184, 44, 12);
  ctx.fill();
  ctx.strokeStyle = '#7d9f8f';
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.fillStyle = '#355b54';
  ctx.font = '700 25px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('ANCOS AI CORE', screenX, screenY - 77);
  ctx.fillStyle = '#61766c';
  ctx.font = '600 8px sans-serif';
  ctx.fillText('LIVE NEURAL SYSTEM', screenX, screenY - 60);

  // Soft concentric rings pulse around the independently animated core.
  const pulse = Math.sin(time * 2.4) * 3;
  ctx.strokeStyle = 'rgba(88, 153, 134, 0.65)';
  ctx.lineWidth = 2.4;
  ctx.shadowColor = '#8abca5';
  ctx.shadowBlur = 8;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 48 + pulse, 24 + pulse * 0.5, 0, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = 'rgba(202, 160, 102, 0.72)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY, 32 - pulse * 0.5, 16 - pulse * 0.25, 0, 0, Math.PI * 2);
  ctx.stroke();

  const plinth = ctx.createLinearGradient(screenX - 18, screenY - 8, screenX + 18, screenY + 10);
  plinth.addColorStop(0, '#e7ede0');
  plinth.addColorStop(0.5, '#8fae9f');
  plinth.addColorStop(1, '#58786d');
  ctx.fillStyle = plinth;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY - 5, 23, 12, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#56786c';
  ctx.lineWidth = 1;
  ctx.stroke();

  // Floating neural core orb with a glint and rotating halo.
  const coreY = screenY - 34 + Math.sin(time * 2.5) * 4;
  const orb = ctx.createRadialGradient(screenX - 4, coreY - 5, 1, screenX, coreY, 12);
  orb.addColorStop(0, '#fff9d8');
  orb.addColorStop(0.38, '#f0d48b');
  orb.addColorStop(1, '#86b8a5');
  ctx.fillStyle = orb;
  ctx.shadowColor = '#d9c47e';
  ctx.shadowBlur = 14;
  ctx.beginPath();
  ctx.arc(screenX, coreY, 11, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.beginPath();
  ctx.ellipse(screenX - 4, coreY - 5, 3, 1.8, -0.5, 0, Math.PI * 2);
  ctx.fill();

  // Rotating holographic wireframe ring
  ctx.save();
    ctx.strokeStyle = 'rgba(255, 247, 205, 0.88)';
    ctx.lineWidth = 1.2;
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
    ctx.fillStyle = i % 2 === 0 ? '#79b8a3' : '#dbaf67';
    ctx.beginPath();
    ctx.arc(px, py, 1.8, 0, Math.PI * 2);
    ctx.fill();
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
  const chairPos = gridToScreen(ws.seatGridX, ws.seatGridY, 0, originX, originY);
  drawOfficeChair(ctx, chairPos.screenX, chairPos.screenY, isOccupied, isOccupied ? '#739b8c' : '#7f8581');

  // Rounded wood desk with a shaded fascia and slim legs.
  const isExec = ws.deskType === 'executive';
  const deskCenterY = screenY - 7;
  const deskW = isExec ? 112 : 78;
  const deskH = isExec ? 38 : 31;
  ctx.fillStyle = 'rgba(65, 49, 36, 0.23)';
  ctx.beginPath();
  ctx.ellipse(screenX + 2, deskCenterY + 17, deskW * 0.48, 13, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#77563c';
  ctx.beginPath();
  ctx.roundRect(screenX - deskW / 2 + 5, deskCenterY - 3, deskW - 10, 22, 5);
  ctx.fill();
  const desktop = ctx.createLinearGradient(screenX, deskCenterY - deskH / 2, screenX, deskCenterY + deskH / 2);
  desktop.addColorStop(0, isExec ? '#d8b887' : '#c9a77c');
  desktop.addColorStop(0.58, isExec ? '#bc9569' : '#aa835d');
  desktop.addColorStop(1, isExec ? '#986f4e' : '#8b684c');
  ctx.fillStyle = desktop;
  ctx.beginPath();
  ctx.roundRect(screenX - deskW / 2, deskCenterY - deskH / 2, deskW, deskH, 7);
  ctx.fill();
  ctx.strokeStyle = isOccupied ? '#719f8b' : '#826c55';
  ctx.lineWidth = 1.4;
  ctx.stroke();
  ctx.strokeStyle = 'rgba(255, 242, 212, 0.5)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(screenX - deskW / 2 + 8, deskCenterY - deskH / 2 + 3);
  ctx.lineTo(screenX + deskW / 2 - 8, deskCenterY - deskH / 2 + 3);
  ctx.stroke();

  // Monitor stands and the slim keyboard sit on the desktop.
  const monitorY = screenY - 18;
  if (ws.monitorCount >= 2) {
    drawMonitor(ctx, screenX - 17, monitorY - 3, isOccupied, time, '#a8d2c0', 26, 19);
    drawMonitor(ctx, screenX + 17, monitorY - 3, isOccupied, time, '#d8c181', 26, 19);
  } else {
    drawMonitor(ctx, screenX, monitorY - 3, isOccupied, time, '#a8d2c0', 34, 20);
  }
  const keyboardY = screenY + 1;
  ctx.fillStyle = '#4e5451';
  ctx.beginPath();
  ctx.roundRect(screenX - 12, keyboardY - 3, 24, 6, 2);
  ctx.fill();
  ctx.strokeStyle = 'rgba(242, 235, 218, 0.72)';
  ctx.lineWidth = 0.7;
  for (let key = 0; key < 5; key++) {
    ctx.beginPath();
    ctx.moveTo(screenX - 9 + key * 4.5, keyboardY - 1);
    ctx.lineTo(screenX - 9 + key * 4.5, keyboardY + 1);
    ctx.stroke();
  }

  ctx.restore();
}

function drawOfficeChair(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  occupied: boolean,
  accent: string,
  scale = 1
): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.fillStyle = 'rgba(40, 45, 44, 0.2)';
  ctx.beginPath();
  ctx.ellipse(0, 6, 15, 6, 0, 0, Math.PI * 2);
  ctx.fill();

  // Star base, stem, and small caster wheels make the chair unmistakable.
  ctx.strokeStyle = '#68716e';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(0, 1);
  ctx.lineTo(0, 9);
  ctx.moveTo(-10, 7);
  ctx.lineTo(10, 7);
  ctx.moveTo(-7, 3);
  ctx.lineTo(7, 11);
  ctx.moveTo(7, 3);
  ctx.lineTo(-7, 11);
  ctx.stroke();
  for (const dx of [-10, 10, -7, 7]) {
    ctx.fillStyle = '#505a56';
    ctx.beginPath();
    ctx.arc(dx, dx === -10 || dx === 10 ? 7 : (dx < 0 ? 3 : 3), 1.8, 0, Math.PI * 2);
    ctx.fill();
  }

  const upholstery = occupied ? accent : '#78817e';
  ctx.fillStyle = '#596460';
  ctx.beginPath();
  ctx.roundRect(-11, -20, 22, 17, [8, 8, 4, 4]);
  ctx.fill();
  ctx.strokeStyle = '#f4eee0';
  ctx.lineWidth = 0.9;
  ctx.stroke();
  const cushion = ctx.createLinearGradient(-8, -7, 8, 3);
  cushion.addColorStop(0, occupied ? '#a7c4b4' : '#aab0a8');
  cushion.addColorStop(1, upholstery);
  ctx.fillStyle = cushion;
  ctx.beginPath();
  ctx.roundRect(-10, -7, 20, 12, [5, 5, 6, 6]);
  ctx.fill();
  ctx.strokeStyle = 'rgba(60, 69, 65, 0.65)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(-8, -4);
  ctx.quadraticCurveTo(0, -1, 8, -4);
  ctx.stroke();

  // A restrained seat highlight keeps the silhouette readable at floor zoom.
  ctx.fillStyle = occupied ? '#d3e5d4' : '#e1e1d8';
  ctx.beginPath();
  ctx.ellipse(-4, -4, 2.1, 1, -0.3, 0, Math.PI * 2);
  ctx.fill();
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
  ctx.fillStyle = '#35443f';
  ctx.beginPath();
  ctx.roundRect(x - w / 2, y - h / 2, w, h, 3);
  ctx.fill();
  ctx.strokeStyle = isOccupied ? activeColor : '#334155';
  ctx.lineWidth = 1;
  ctx.stroke();

  // Monitor Screen
  if (isOccupied) {
    const screen = ctx.createLinearGradient(x, y - h / 2, x, y + h / 2);
    screen.addColorStop(0, '#344f4d');
    screen.addColorStop(1, '#203735');
    ctx.fillStyle = screen;
    ctx.beginPath();
    ctx.roundRect(x - w / 2 + 1.5, y - h / 2 + 1.5, w - 3, h - 3, 2);
    ctx.fill();

    // Glowing animated syntax lines or graph traces.
    ctx.fillStyle = activeColor;
    for (let l = 0; l < 3; l++) {
      const lineLen = ((l * 4 + time * 10) % (w - 4)) + 2;
      ctx.globalAlpha = 0.68;
      ctx.beginPath();
      ctx.roundRect(x - w / 2 + 3, y - h / 2 + 4 + l * 3, Math.min(lineLen, w - 7), 1.4, 1);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  } else {
    // Standby: dark screen with subtle amber/green standby power LED
    ctx.fillStyle = '#26322f';
    ctx.beginPath();
    ctx.roundRect(x - w / 2 + 1.5, y - h / 2 + 1.5, w - 3, h - 3, 2);
    ctx.fill();

    const ledBlink = Math.sin(time * 2) > 0;
    ctx.fillStyle = ledBlink ? '#10b981' : '#064e3b';
    ctx.beginPath();
    ctx.arc(x + w / 2 - 4, y + h / 2 - 4, 1.2, 0, Math.PI * 2);
    ctx.fill();
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
    // Rounded equipment cabinets with vented glass doors.
    const rackY = screenY - 14;
    ctx.fillStyle = 'rgba(35, 48, 47, 0.24)';
    ctx.beginPath();
    ctx.ellipse(screenX + 2, screenY + 10, 25, 8, 0, 0, Math.PI * 2);
    ctx.fill();
    const rack = ctx.createLinearGradient(screenX - 20, rackY - 18, screenX + 20, rackY + 18);
    rack.addColorStop(0, '#9aaba4');
    rack.addColorStop(0.32, '#566a67');
    rack.addColorStop(1, '#344744');
    ctx.fillStyle = rack;
    ctx.beginPath();
    ctx.roundRect(screenX - 21, rackY - 18, 42, 61, 5);
    ctx.fill();
    ctx.strokeStyle = '#e4ddc8';
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.fillStyle = 'rgba(20, 34, 33, 0.7)';
    ctx.beginPath();
    ctx.roundRect(screenX - 15, rackY - 13, 30, 51, 3);
    ctx.fill();
    ctx.strokeStyle = 'rgba(217, 231, 220, 0.38)';
    ctx.lineWidth = 1;
    ctx.stroke();

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
        ctx.shadowColor = ledColor;
        ctx.shadowBlur = 4;
        ctx.beginPath();
        ctx.arc(ledX, ledY, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.shadowBlur = 0;
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

  // Shared planning board with independently drawn notes and diagram.
  const board = gridToScreen(12, 3, 0, originX, originY);
  ctx.fillStyle = 'rgba(67, 58, 47, 0.22)';
  ctx.beginPath();
  ctx.roundRect(board.screenX - 68, board.screenY - 20, 136, 45, 6);
  ctx.fill();
  const boardSurface = ctx.createLinearGradient(board.screenX, board.screenY - 18, board.screenX, board.screenY + 18);
  boardSurface.addColorStop(0, '#f8f5e9');
  boardSurface.addColorStop(1, '#dbe5db');
  ctx.fillStyle = boardSurface;
  ctx.beginPath();
  ctx.roundRect(board.screenX - 64, board.screenY - 18, 128, 38, 4);
  ctx.fill();
  ctx.strokeStyle = '#95856a';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.lineWidth = 2.2;
  ctx.lineCap = 'round';
  ctx.strokeStyle = '#668c7a';
  ctx.beginPath();
  ctx.moveTo(board.screenX - 51, board.screenY - 8);
  ctx.lineTo(board.screenX - 32, board.screenY - 8);
  ctx.moveTo(board.screenX - 51, board.screenY - 1);
  ctx.lineTo(board.screenX - 18, board.screenY - 1);
  ctx.stroke();
  ctx.strokeStyle = '#c58c54';
  ctx.beginPath();
  ctx.moveTo(board.screenX + 25, board.screenY - 8);
  ctx.lineTo(board.screenX + 48, board.screenY - 8);
  ctx.moveTo(board.screenX + 25, board.screenY - 1);
  ctx.lineTo(board.screenX + 42, board.screenY - 1);
  ctx.stroke();
  ctx.fillStyle = '#7293a1';
  ctx.beginPath();
  ctx.roundRect(board.screenX - 8, board.screenY - 9, 24, 14, 3);
  ctx.fill();
  ctx.fillStyle = '#7a6254';
  ctx.fillRect(board.screenX - 3, board.screenY + 20, 6, 15);

  // Oval boardroom table, softly lit along its edge.
  ctx.fillStyle = 'rgba(55, 42, 33, 0.2)';
  ctx.beginPath();
  ctx.ellipse(screenX + 2, screenY + 1, 75, 29, 0, 0, Math.PI * 2);
  ctx.fill();
  const table = ctx.createLinearGradient(screenX, screenY - 22, screenX, screenY + 21);
  table.addColorStop(0, '#d0ad7f');
  table.addColorStop(0.45, '#a87e56');
  table.addColorStop(1, '#805d43');
  ctx.fillStyle = table;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY - 8, 71, 26, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#f0d6a9';
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.strokeStyle = 'rgba(255, 245, 215, 0.48)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY - 8, 63, 20, 0, Math.PI * 1.12, Math.PI * 1.88);
  ctx.stroke();

  // Central Hologram Tabletop Projector
  const projY = screenY - 14;
  ctx.fillStyle = '#a6c8b2';
  ctx.shadowColor = '#88b8a4';
  ctx.shadowBlur = meetingActive ? 10 : 3;
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
    ctx.beginPath();
    ctx.roundRect(-8, -8, 16, 16, 3);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = '#ffffff';
    ctx.font = '600 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(meetingTitle.slice(0, 24), screenX, holoY - 14);
  }

  // Draw six visible rolling chairs around the boardroom table.
  for (const seat of MEETING_SEATS) {
    const chairPos = gridToScreen(seat.gridX, seat.gridY, 0, originX, originY);
    drawOfficeChair(ctx, chairPos.screenX, chairPos.screenY, Boolean(seat.occupantId), '#b28fba', 0.72);
  }

  ctx.restore();
}

