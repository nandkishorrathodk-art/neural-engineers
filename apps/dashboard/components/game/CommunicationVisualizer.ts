import { CommsPacket, SpeechBubble, GameCharacter } from './types';
import { gridToScreen } from './IsometricMath';

export interface CommsHistoryItem {
  id: string;
  senderId: string;
  senderName: string;
  recipientId: string;
  recipientName: string;
  content: string;
  timestamp: string;
}

/**
 * Draws active speech bubbles above characters
 */
export function drawSpeechBubbles(
  ctx: CanvasRenderingContext2D,
  bubbles: SpeechBubble[],
  characters: Map<string, GameCharacter>,
  originX: number,
  originY: number
): void {
  for (const bubble of bubbles) {
    const char = characters.get(bubble.charId);
    if (!char || !char.speechText || char.speechRemainingMs <= 0) continue;

    const remaining = char.speechRemainingMs;
    const { screenX, screenY } = gridToScreen(char.gridX, char.gridY, 0, originX, originY);

    // Fade-out during last 400ms
    let alpha = 1;
    if (remaining < 400) {
      alpha = Math.max(0, remaining / 400);
    }

    ctx.save();
    ctx.globalAlpha = alpha;

    const bubbleX = screenX;
    const bubbleY = screenY - 72;

    ctx.font = 'bold 11px system-ui, -apple-system, sans-serif';
    const textWidth = ctx.measureText(bubble.text).width;
    const boxW = Math.min(Math.max(textWidth + 24, 70), 220);
    const boxH = 28;

    // Speech Bubble Body
    ctx.fillStyle = '#070e1c';
    ctx.strokeStyle = bubble.color || char.theme.accentColor;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = bubble.color || char.theme.accentColor;
    ctx.shadowBlur = 8;

    ctx.beginPath();
    ctx.roundRect(bubbleX - boxW / 2, bubbleY - boxH / 2, boxW, boxH, 8);
    ctx.fill();
    ctx.stroke();

    // Bubble Tail Pointing down to head
    ctx.fillStyle = '#070e1c';
    ctx.beginPath();
    ctx.moveTo(bubbleX - 5, bubbleY + boxH / 2);
    ctx.lineTo(bubbleX, bubbleY + boxH / 2 + 7);
    ctx.lineTo(bubbleX + 5, bubbleY + boxH / 2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Bubble Text
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.shadowBlur = 0;
    
    // Truncate if long
    let displayText = bubble.text;
    if (displayText.length > 32) {
      displayText = displayText.slice(0, 30) + '...';
    }
    ctx.fillText(displayText, bubbleX, bubbleY);

    ctx.restore();
  }
}

/**
 * Draws flying digital data packets and laser communication beams between characters
 */
export function drawCommsPackets(
  ctx: CanvasRenderingContext2D,
  packets: CommsPacket[],
  characters: Map<string, GameCharacter>,
  originX: number,
  originY: number
): void {
  for (const packet of packets) {
    const sender = characters.get(packet.fromCharId);
    const receiver = characters.get(packet.toCharId);
    if (!sender || !receiver) continue;

    const pSender = gridToScreen(sender.gridX, sender.gridY, 0, originX, originY);
    const pReceiver = gridToScreen(receiver.gridX, receiver.gridY, 0, originX, originY);

    const sX = pSender.screenX;
    const sY = pSender.screenY - 26;
    const rX = pReceiver.screenX;
    const rY = pReceiver.screenY - 26;

    // Calculate current packet position along trajectory
    const currX = sX + (rX - sX) * packet.progress;
    const currY = sY + (rY - sY) * packet.progress;

    ctx.save();

    // High-visibility glowing laser beam connecting characters
    ctx.strokeStyle = packet.color;
    ctx.lineWidth = 2;
    ctx.shadowColor = packet.color;
    ctx.shadowBlur = 12;
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    ctx.moveTo(sX, sY);
    ctx.lineTo(rX, rY);
    ctx.stroke();

    // Traveling Data Packet Core (bright glowing plasma)
    ctx.fillStyle = '#ffffff';
    ctx.shadowColor = packet.color;
    ctx.shadowBlur = 14;
    ctx.beginPath();
    ctx.arc(currX, currY, 4.5, 0, Math.PI * 2);
    ctx.fill();

    // Ripple wave around packet
    ctx.strokeStyle = packet.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(currX, currY, 8.5, 0, Math.PI * 2);
    ctx.stroke();

    // Particle Trail behind packet
    for (let i = 1; i <= 3; i++) {
      const trailProgress = Math.max(0, packet.progress - i * 0.04);
      const tX = sX + (rX - sX) * trailProgress;
      const tY = sY + (rY - sY) * trailProgress;
      ctx.fillStyle = packet.color;
      ctx.globalAlpha = Math.max(0, 0.8 - i * 0.25);
      ctx.beginPath();
      ctx.arc(tX, tY, 3.5 - i * 0.8, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // Floating text snippet label above packet
    if (packet.textSnippet) {
      ctx.font = 'bold 9px monospace';
      const labelW = ctx.measureText(packet.textSnippet).width + 12;
      ctx.fillStyle = '#070e1c';
      ctx.strokeStyle = packet.color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(currX - labelW / 2, currY - 22, labelW, 14, 4);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(packet.textSnippet, currX, currY - 15);
    }

    ctx.restore();
  }
}

