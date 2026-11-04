import { GameCharacter } from './types';
import { gridToScreen } from './IsometricMath';

const TAU = Math.PI * 2;

function pathLimb(
  ctx: CanvasRenderingContext2D,
  points: Array<[number, number]>,
  color: string,
  width: number,
  handColor?: string
): void {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1]);
  ctx.stroke();
  if (handColor) {
    const [x, y] = points[points.length - 1];
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(x, y, width * 0.38, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function drawShoes(ctx: CanvasRenderingContext2D, x: number, y: number, color: string): void {
  ctx.fillStyle = '#f3e9d8';
  ctx.beginPath();
  ctx.ellipse(x + 1, y, 5.4, 2.7, -0.08, 0, TAU);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.ellipse(x + 2, y - 0.8, 3.6, 1.6, -0.08, Math.PI, TAU);
  ctx.fill();
  ctx.strokeStyle = 'rgba(61, 59, 54, 0.5)';
  ctx.lineWidth = 0.7;
  ctx.beginPath();
  ctx.moveTo(x - 2, y + 1.1);
  ctx.quadraticCurveTo(x + 1, y + 2.2, x + 5, y + 1);
  ctx.stroke();
}

function drawLeg(
  ctx: CanvasRenderingContext2D,
  hipX: number,
  hipY: number,
  angle: number,
  pantColor: string,
  shoeColor: string,
  length = 22
): void {
  ctx.save();
  ctx.translate(hipX, hipY);
  ctx.rotate(angle);
  ctx.strokeStyle = pantColor;
  ctx.lineWidth = 6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(1, length * 0.52, 1, length - 3);
  ctx.stroke();
  drawShoes(ctx, 0, length - 1, shoeColor);
  ctx.restore();
}

function drawStatusBadge(ctx: CanvasRenderingContext2D, x: number, y: number, state: string): void {
  const badgeColor = state === 'warning' ? '#d59036' : state === 'celebrating' ? '#71a886' : '#81aeb3';
  ctx.save();
  ctx.fillStyle = 'rgba(255, 251, 239, 0.96)';
  ctx.strokeStyle = badgeColor;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.roundRect(x - 10, y - 7, 20, 14, 6);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = badgeColor;
  ctx.beginPath();
  if (state === 'warning') {
    ctx.moveTo(x, y - 4);
    ctx.lineTo(x + 4, y + 3);
    ctx.lineTo(x - 4, y + 3);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#fff9ec';
    ctx.fillRect(x - 0.6, y - 1.5, 1.2, 2.5);
  } else if (state === 'celebrating') {
    ctx.beginPath();
    ctx.arc(x - 2, y - 1, 1.1, 0, TAU);
    ctx.arc(x + 2, y - 1, 1.1, 0, TAU);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y + 1, 2.7, 0.1, Math.PI - 0.1);
    ctx.strokeStyle = badgeColor;
    ctx.lineWidth = 1;
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.arc(x - 2.6, y - 1, 1.1, 0, TAU);
    ctx.arc(x + 2.6, y - 1, 1.1, 0, TAU);
    ctx.fill();
    ctx.strokeStyle = badgeColor;
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.arc(x, y + 3, 2.2, Math.PI, TAU);
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Draw an independently animated anime-inspired employee in the live world.
 * All identity, appearance, and status details come from the synced employee record.
 */
export function drawCharacter(
  ctx: CanvasRenderingContext2D,
  char: GameCharacter,
  originX: number,
  originY: number,
  isSelected = false,
  isHovered = false
): void {
  const { screenX, screenY } = gridToScreen(char.gridX, char.gridY, 0, originX, originY);
  const time = char.animTime;
  const isWalking = char.animState === 'walking';
  const isWorking = char.animState === 'working';
  const isSitting = char.animState === 'sitting' || char.animState === 'meeting' || isWorking;
  const isCelebrating = char.animState === 'celebrating';
  const isSpeaking = char.animState === 'speaking';
  const isThinking = char.animState === 'thinking';
  const isListening = char.animState === 'listening';
  const isWarning = char.animState === 'warning';

  ctx.save();
  if (isSelected || isHovered) {
    ctx.strokeStyle = isSelected ? '#4f8f9c' : '#7f9b79';
    ctx.lineWidth = isSelected ? 2.4 : 1.6;
    ctx.shadowColor = isSelected ? '#6daeb7' : '#9ebc93';
    ctx.shadowBlur = 9;
    const pulse = Math.sin(time * 4) * 2;
    ctx.beginPath();
    ctx.ellipse(screenX, screenY + 8, 22 + pulse, 10 + pulse * 0.35, 0, 0, TAU);
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  ctx.fillStyle = 'rgba(50, 48, 43, 0.24)';
  ctx.beginPath();
  ctx.ellipse(screenX, screenY + 7, isWalking ? 13 : 15, 5.5, 0, 0, TAU);
  ctx.fill();

  if (isWarning) {
    ctx.strokeStyle = 'rgba(205, 143, 50, 0.8)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.ellipse(screenX, screenY + 7, 24 + Math.sin(time * 5) * 2, 11, 0, 0, TAU);
    ctx.stroke();
  }

  const bob = isWalking
    ? -Math.abs(Math.sin(time * 8)) * 2.6
    : isCelebrating
      ? -Math.abs(Math.sin(time * 6)) * 5
      : Math.sin(time * 2.2) * 0.7;
  const seatLift = isSitting ? 5 : 0;
  const baseY = screenY - 1 + bob + seatLift;
  const faceBack = char.heading === 'up';
  const flip = char.heading === 'left';

  ctx.save();
  ctx.translate(screenX, baseY);
  ctx.scale(flip ? -1.42 : 1.42, 1.38);

  const skin = char.theme.skinTone;
  const hair = char.theme.hairColor;
  const suit = char.theme.suitColor;
  const accent = char.theme.accentColor;
  const trouser = '#41494b';
  const stride = isWalking ? Math.sin(time * 8) * 0.42 : 0;

  // Legs animate from the hip; seated employees tuck their feet under the desk.
  if (isSitting) {
    ctx.fillStyle = trouser;
    ctx.beginPath();
    ctx.roundRect(-9, -20, 7, 14, [3, 3, 5, 5]);
    ctx.roundRect(2, -20, 7, 14, [3, 3, 5, 5]);
    ctx.fill();
    pathLimb(ctx, [[-5, -9], [-7, -5], [-4, -2]], trouser, 5);
    pathLimb(ctx, [[5, -9], [7, -5], [4, -2]], trouser, 5);
    drawShoes(ctx, -4, -1, accent);
    drawShoes(ctx, 4, -1, accent);
  } else {
    drawLeg(ctx, -4, -23, stride, trouser, accent);
    drawLeg(ctx, 4, -23, -stride, trouser, accent);
  }

  // Neck and tailored jacket, shaded with gentle gradients instead of block fills.
  ctx.fillStyle = skin;
  ctx.beginPath();
  ctx.roundRect(-3.3, -57, 6.6, 9, 2);
  ctx.fill();
  const jacket = ctx.createLinearGradient(-12, -55, 12, -27);
  jacket.addColorStop(0, suit);
  jacket.addColorStop(0.52, char.role === 'creative' ? '#90736f' : '#657773');
  jacket.addColorStop(1, suit);
  ctx.fillStyle = jacket;
  ctx.beginPath();
  ctx.moveTo(-8, -53);
  ctx.quadraticCurveTo(-13, -51, -12, -43);
  ctx.lineTo(-10, -28);
  ctx.quadraticCurveTo(0, -24, 10, -28);
  ctx.lineTo(12, -43);
  ctx.quadraticCurveTo(13, -51, 8, -53);
  ctx.lineTo(4, -55);
  ctx.lineTo(0, -49);
  ctx.lineTo(-4, -55);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(255, 245, 223, 0.7)';
  ctx.lineWidth = 0.8;
  ctx.stroke();

  // Lapels, shirt, badge, and small role-specific color detail.
  ctx.fillStyle = '#f0e8d8';
  ctx.beginPath();
  ctx.moveTo(-4, -55);
  ctx.lineTo(0, -49);
  ctx.lineTo(-1, -39);
  ctx.lineTo(-7, -51);
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(4, -55);
  ctx.lineTo(0, -49);
  ctx.lineTo(2, -39);
  ctx.lineTo(8, -51);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = accent;
  ctx.beginPath();
  ctx.moveTo(-1, -46);
  ctx.lineTo(1, -46);
  ctx.lineTo(2.3, -33);
  ctx.lineTo(0, -30);
  ctx.lineTo(-2, -33);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = '#f6e5bb';
  ctx.beginPath();
  ctx.arc(7, -43, 1.25, 0, TAU);
  ctx.fill();

  // Arms and hands change pose from actual live activity state.
  if (isWorking) {
    const typing = Math.sin(time * 18) * 1.3;
    pathLimb(ctx, [[-9, -49], [-13, -42], [-8, -34], [-4, -31 + typing]], suit, 5.2, skin);
    pathLimb(ctx, [[9, -49], [13, -42], [8, -34], [4, -31 - typing]], suit, 5.2, skin);
  } else if (isCelebrating) {
    const cheer = Math.sin(time * 6) * 0.12;
    pathLimb(ctx, [[-9, -49], [-14, -53], [-17, -65]], suit, 5.5, skin);
    pathLimb(ctx, [[9, -49], [14, -53], [17, -65 + cheer]], suit, 5.5, skin);
  } else if (isThinking) {
    pathLimb(ctx, [[-9, -49], [-12, -42], [-9, -34]], suit, 5.3, skin);
    pathLimb(ctx, [[9, -49], [13, -43], [9, -53]], suit, 5.3, skin);
  } else if (isSpeaking) {
    const gesture = Math.sin(time * 4) * 2;
    pathLimb(ctx, [[-9, -49], [-12, -42], [-9, -34]], suit, 5.3, skin);
    pathLimb(ctx, [[9, -49], [14, -43], [17, -38 + gesture]], suit, 5.3, skin);
  } else if (isSitting) {
    const type = isWorking ? Math.sin(time * 18) * 1.4 : 0;
    pathLimb(ctx, [[-9, -49], [-12, -42], [-7, -35], [-3, -32 + type]], suit, 5.2, skin);
    pathLimb(ctx, [[9, -49], [12, -42], [7, -35], [3, -32 - type]], suit, 5.2, skin);
  } else {
    const swing = isWalking ? Math.sin(time * 8) * 0.22 : 0;
    pathLimb(ctx, [[-9, -49], [-12 - swing * 5, -41], [-12 - swing * 8, -31]], suit, 5.3, skin);
    pathLimb(ctx, [[9, -49], [12 + swing * 5, -41], [12 + swing * 8, -31]], suit, 5.3, skin);
  }

  // Large, expressive anime face framed by individualized hair.
  const nod = isListening ? Math.sin(time * 3) * 1.3 : isThinking ? Math.sin(time * 1.6) : 0;
  ctx.save();
  ctx.translate(0, nod);
  ctx.fillStyle = skin;
  ctx.beginPath();
  ctx.moveTo(-9, -68);
  ctx.quadraticCurveTo(-9, -81, 0, -81);
  ctx.quadraticCurveTo(10, -80, 9, -67);
  ctx.quadraticCurveTo(8, -57, 0, -54);
  ctx.quadraticCurveTo(-8, -57, -9, -68);
  ctx.fill();
  ctx.strokeStyle = 'rgba(95, 67, 52, 0.5)';
  ctx.lineWidth = 0.7;
  ctx.stroke();

  if (faceBack) {
    ctx.fillStyle = hair;
    ctx.beginPath();
    ctx.moveTo(-10, -69);
    ctx.quadraticCurveTo(-13, -86, 0, -86);
    ctx.quadraticCurveTo(13, -84, 10, -67);
    ctx.lineTo(8, -59);
    ctx.quadraticCurveTo(0, -54, -8, -59);
    ctx.closePath();
    ctx.fill();
  } else {
    // Big eyes with highlights remain visible at the fitted floor zoom.
    for (const eyeX of [-4, 4]) {
      if (char.isBlinking) {
        ctx.strokeStyle = '#473d3d';
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        ctx.quadraticCurveTo(eyeX - 2.3, -68, eyeX, -67);
        ctx.quadraticCurveTo(eyeX + 2.3, -68, eyeX + 2.5, -69);
        ctx.stroke();
      } else {
        ctx.fillStyle = '#fffaf0';
        ctx.beginPath();
        ctx.ellipse(eyeX, -68, 2.8, 3.7, 0, 0, TAU);
        ctx.fill();
        ctx.fillStyle = '#4f5554';
        ctx.beginPath();
        ctx.ellipse(eyeX, -67.8, 1.7, 2.8, 0, 0, TAU);
        ctx.fill();
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.ellipse(eyeX, -67.3, 1.05, 1.8, 0, 0, TAU);
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(eyeX - 0.55, -69, 0.72, 0, TAU);
        ctx.fill();
      }
    }
    if (char.theme.hasGlasses) {
      ctx.strokeStyle = '#586a67';
      ctx.lineWidth = 0.85;
      ctx.beginPath();
      ctx.roundRect(-7, -71, 5.5, 6, 2);
      ctx.roundRect(1.5, -71, 5.5, 6, 2);
      ctx.moveTo(-1.5, -68);
      ctx.lineTo(1.5, -68);
      ctx.stroke();
    }
    ctx.fillStyle = 'rgba(221, 126, 118, 0.34)';
    ctx.beginPath();
    ctx.ellipse(-6, -62, 2, 1.2, 0, 0, TAU);
    ctx.ellipse(6, -62, 2, 1.2, 0, 0, TAU);
    ctx.fill();
    ctx.strokeStyle = '#8f5856';
    ctx.lineWidth = 1;
    if (isSpeaking && Math.sin(time * 14) > -0.35) {
      ctx.fillStyle = '#a85656';
      ctx.beginPath();
      ctx.ellipse(0, -59.5, 1.7, 1.4 + Math.sin(time * 14) * 0.45, 0, 0, TAU);
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(-2, -59.5);
      ctx.quadraticCurveTo(0, isCelebrating ? -56.2 : -58.2, 2, -59.5);
      ctx.stroke();
    }

    // Fringe, side locks, and hair silhouette vary with each employee theme.
    ctx.fillStyle = hair;
    ctx.beginPath();
    ctx.moveTo(-10, -69);
    ctx.quadraticCurveTo(-13, -85, -2, -86);
    ctx.quadraticCurveTo(8, -89, 11, -78);
    ctx.lineTo(10, -68);
    ctx.quadraticCurveTo(7, -72, 4, -77);
    ctx.lineTo(1, -70);
    ctx.lineTo(-2, -77);
    ctx.lineTo(-6, -70);
    ctx.lineTo(-9, -74);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(255, 232, 198, 0.34)';
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(-6, -81);
    ctx.quadraticCurveTo(0, -86, 6, -81);
    ctx.stroke();

    if (char.theme.hairStyle === 'long' || char.theme.hairStyle === 'bob') {
      ctx.fillStyle = hair;
      ctx.beginPath();
      ctx.moveTo(-9, -74);
      ctx.quadraticCurveTo(-14, -62, -10, -55);
      ctx.quadraticCurveTo(-7, -60, -7, -68);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(9, -74);
      ctx.quadraticCurveTo(14, -62, 10, -55);
      ctx.quadraticCurveTo(7, -60, 7, -68);
      ctx.closePath();
      ctx.fill();
    } else if (char.theme.hairStyle === 'ponytail') {
      ctx.fillStyle = hair;
      ctx.beginPath();
      ctx.moveTo(-8, -78);
      ctx.quadraticCurveTo(-15, -77, -13, -69);
      ctx.quadraticCurveTo(-10, -72, -7, -72);
      ctx.fill();
    } else if (char.theme.hairStyle === 'spiky' || char.role === 'boss') {
      ctx.fillStyle = hair;
      ctx.beginPath();
      ctx.moveTo(-8, -80);
      ctx.lineTo(-9, -88);
      ctx.lineTo(-3, -83);
      ctx.lineTo(1, -91);
      ctx.lineTo(4, -83);
      ctx.lineTo(10, -87);
      ctx.lineTo(8, -77);
      ctx.closePath();
      ctx.fill();
    }
    if (char.theme.hasVisor) {
      ctx.fillStyle = 'rgba(119, 177, 174, 0.86)';
      ctx.strokeStyle = accent;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.roundRect(-8, -70, 16, 3.2, 1.5);
      ctx.fill();
      ctx.stroke();
    }
    if (char.theme.hasHeadphones) {
      ctx.strokeStyle = '#5d6763';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(0, -74, 10, Math.PI, TAU);
      ctx.stroke();
      ctx.fillStyle = accent;
      ctx.beginPath();
      ctx.roundRect(-10.5, -74, 3.3, 8, 1.5);
      ctx.roundRect(7.2, -74, 3.3, 8, 1.5);
      ctx.fill();
    }
  }
  ctx.restore();
  ctx.restore();

  if (isThinking) drawStatusBadge(ctx, screenX + 15, baseY - 83, 'thinking');
  else if (isWarning) drawStatusBadge(ctx, screenX + 15, baseY - 83, 'warning');
  else if (isCelebrating) drawStatusBadge(ctx, screenX + 15, baseY - 83, 'celebrating');

  // Show the real employee name only on focus so rows of staff stay readable.
  if (isSelected || isHovered) {
    ctx.save();
    ctx.font = '600 11px sans-serif';
    const labelW = Math.min(Math.max(ctx.measureText(char.name).width + 16, 44), 132);
    const labelY = screenY - 97 + bob;
    ctx.fillStyle = 'rgba(255, 251, 240, 0.96)';
    ctx.strokeStyle = char.theme.accentColor;
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.roundRect(screenX - labelW / 2, labelY - 8, labelW, 16, 6);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#374541';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(char.name, screenX, labelY, labelW - 8);
    ctx.restore();
  }
  ctx.restore();
}
