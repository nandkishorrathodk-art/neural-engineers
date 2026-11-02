import { GameCharacter, HeadingDirection } from './types';
import { gridToScreen } from './IsometricMath';

/**
 * Draws an independently articulated 2D anime character sprite on the canvas
 */
export function drawCharacter(
  ctx: CanvasRenderingContext2D,
  char: GameCharacter,
  originX: number,
  originY: number,
  isSelected: boolean = false,
  isHovered: boolean = false
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

  // 1. Selection & Hover Rings on the Floor
  if (isSelected || isHovered) {
    ctx.save();
    ctx.strokeStyle = isSelected ? '#38bdf8' : '#10b981';
    ctx.lineWidth = 2;
    ctx.shadowColor = isSelected ? '#38bdf8' : '#10b981';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    const ringPulse = Math.sin(time * 5) * 2;
    ctx.ellipse(screenX, screenY + 4, 18 + ringPulse, 9 + ringPulse * 0.5, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  // 2. Ground Shadow
  ctx.save();
  ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
  const shadowW = isWalking ? 14 + Math.cos(time * 8) * 2 : 16;
  const shadowH = shadowW * 0.5;
  ctx.beginPath();
  ctx.ellipse(screenX, screenY + 4, shadowW, shadowH, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  // 3. Warning Alert Aura
  if (isWarning) {
    ctx.save();
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 1.5;
    ctx.shadowColor = '#f59e0b';
    ctx.shadowBlur = 8;
    const warnRadius = 20 + Math.sin(time * 6) * 3;
    ctx.beginPath();
    ctx.ellipse(screenX, screenY + 4, warnRadius, warnRadius * 0.5, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  // Calculate Articulation Offsets
  let bobY = 0;
  if (isWalking) {
    bobY = -Math.abs(Math.sin(time * 8)) * 3;
  } else if (isCelebrating) {
    bobY = -Math.abs(Math.sin(time * 6)) * 6;
  } else {
    // Breathing idle bob
    bobY = Math.sin(time * 2.5) * 0.8;
  }

  const sitOffset = isSitting ? 5 : 0;
  const baseY = screenY - 2 + bobY + sitOffset;

  // Directional mirroring (flip horizontally if facing left)
  const isFacingLeft = char.heading === 'left';
  const isFacingUp = char.heading === 'up';

  ctx.translate(screenX, baseY);
  ctx.scale(isFacingLeft ? -1.6 : 1.6, 1);

  // ─── LEGS & SHOES ───
  ctx.save();
  const legColor = '#0f172a';
  const shoeColor = char.theme.accentColor;

  if (isWalking) {
    const legAngle = Math.sin(time * 8) * 0.45;
    // Left Leg
    ctx.save();
    ctx.translate(-4, -14);
    ctx.rotate(legAngle);
    ctx.fillStyle = legColor;
    ctx.fillRect(-2, 0, 4, 14);
    ctx.fillStyle = shoeColor;
    ctx.fillRect(-2.5, 12, 6, 3.5);
    ctx.restore();

    // Right Leg
    ctx.save();
    ctx.translate(4, -14);
    ctx.rotate(-legAngle);
    ctx.fillStyle = legColor;
    ctx.fillRect(-2, 0, 4, 14);
    ctx.fillStyle = shoeColor;
    ctx.fillRect(-2.5, 12, 6, 3.5);
    ctx.restore();
  } else if (isSitting) {
    // Legs bent forward horizontally under desk
    ctx.fillStyle = legColor;
    ctx.fillRect(-5, -10, 4, 8);
    ctx.fillRect(1, -10, 4, 8);
    ctx.fillStyle = shoeColor;
    ctx.fillRect(-5, -3, 6, 3.5);
    ctx.fillRect(1, -3, 6, 3.5);
  } else {
    // Standing Idle
    ctx.fillStyle = legColor;
    ctx.fillRect(-5, -15, 4, 15);
    ctx.fillRect(1, -15, 4, 15);
    ctx.fillStyle = shoeColor;
    ctx.fillRect(-6, -2, 5.5, 3.5);
    ctx.fillRect(1, -2, 5.5, 3.5);
  }
  ctx.restore();

  // ─── TORSO & CLOTHING ───
  ctx.save();
  const torsoY = -28;
  // Suit Body
  ctx.fillStyle = char.theme.suitColor;
  ctx.beginPath();
  ctx.roundRect(-8, torsoY, 16, 16, [4, 4, 2, 2]);
  ctx.fill();
  ctx.strokeStyle = char.theme.accentColor;
  ctx.lineWidth = 1;
  ctx.stroke();

  // Cyber Collar & Tie / Circuit line
  ctx.fillStyle = '#1e293b';
  ctx.beginPath();
  ctx.moveTo(-3, torsoY);
  ctx.lineTo(0, torsoY + 6);
  ctx.lineTo(3, torsoY);
  ctx.closePath();
  ctx.fill();

  // Holographic Cyber Badge
  ctx.fillStyle = char.theme.accentColor;
  ctx.fillRect(-6, torsoY + 4, 3, 4);

  ctx.restore();

  // ─── ARMS & HANDS ───
  ctx.save();
  const armColor = char.theme.suitColor;
  const handColor = char.theme.skinTone;

  if (isWorking) {
    // Typing animation: hands rapid oscillation over keyboard
    const typeSwing = Math.sin(time * 20) * 2;
    // Left arm
    ctx.fillStyle = armColor;
    ctx.fillRect(-11, -26, 4, 10);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(-7, -16 + typeSwing, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Right arm
    ctx.fillStyle = armColor;
    ctx.fillRect(7, -26, 4, 10);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(7, -16 - typeSwing, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Tiny typing keystroke particle sparks
    if (Math.sin(time * 30) > 0.4) {
      ctx.fillStyle = '#38bdf8';
      ctx.fillRect(-4 + (time * 100) % 8, -17, 1.5, 1.5);
    }
  } else if (isCelebrating) {
    // Both arms raised high in victory
    ctx.fillStyle = armColor;
    ctx.save();
    ctx.translate(-8, -26);
    ctx.rotate(-0.8);
    ctx.fillRect(-2, -12, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, -13, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.translate(8, -26);
    ctx.rotate(0.8);
    ctx.fillStyle = armColor;
    ctx.fillRect(-2, -12, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, -13, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  } else if (isThinking) {
    // Right hand to chin, left resting
    ctx.fillStyle = armColor;
    ctx.fillRect(-11, -26, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(-9, -13, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Right arm raised to chin
    ctx.fillStyle = armColor;
    ctx.save();
    ctx.translate(8, -24);
    ctx.rotate(-1.2);
    ctx.fillRect(-2, -10, 4, 10);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, -11, 2.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  } else if (isSpeaking) {
    // Right arm gesturing forward
    const gesture = Math.sin(time * 4) * 0.2;
    ctx.fillStyle = armColor;
    ctx.fillRect(-11, -26, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(-9, -13, 2.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.translate(8, -24);
    ctx.rotate(0.5 + gesture);
    ctx.fillStyle = armColor;
    ctx.fillRect(-2, 0, 4, 11);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, 12, 2.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  } else if (isWalking) {
    // Natural walking arm swing opposite to legs
    const armSwing = Math.sin(time * 8) * 0.5;
    ctx.save();
    ctx.translate(-8, -26);
    ctx.rotate(-armSwing);
    ctx.fillStyle = armColor;
    ctx.fillRect(-2, 0, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, 13, 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.translate(8, -26);
    ctx.rotate(armSwing);
    ctx.fillStyle = armColor;
    ctx.fillRect(-2, 0, 4, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(0, 13, 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  } else {
    // Idle hanging arms
    ctx.fillStyle = armColor;
    ctx.fillRect(-11, -26, 3.5, 12);
    ctx.fillRect(7.5, -26, 3.5, 12);
    ctx.fillStyle = handColor;
    ctx.beginPath();
    ctx.arc(-9.2, -13, 2.3, 0, Math.PI * 2);
    ctx.arc(9.2, -13, 2.3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  // ─── HEAD & ANIME FACE ───
  ctx.save();
  const headY = -38;
  const headNod = isListening ? Math.sin(time * 3) * 1.5 : 0;

  ctx.translate(0, headNod);

  // Neck
  ctx.fillStyle = char.theme.skinTone;
  ctx.fillRect(-2.5, headY + 8, 5, 4);

  // Face Base
  ctx.fillStyle = char.theme.skinTone;
  ctx.beginPath();
  ctx.roundRect(-8, headY - 8, 16, 16, [6, 6, 7, 7]);
  ctx.fill();

  // If viewing from behind (facing up)
  if (isFacingUp) {
    // Back of hair covers head
    ctx.fillStyle = char.theme.hairColor;
    ctx.beginPath();
    ctx.roundRect(-9, headY - 10, 18, 18, [8, 8, 4, 4]);
    ctx.fill();
  } else {
    // Front / 3/4 Face View
    // Anime Eyes & Blinking
    if (char.isBlinking) {
      // Closed eye arcs
      ctx.strokeStyle = '#0f172a';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(-3.5, headY - 1, 2.5, 0.2, Math.PI - 0.2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(3.5, headY - 1, 2.5, 0.2, Math.PI - 0.2);
      ctx.stroke();
    } else {
      // Big expressive anime eyes
      ctx.fillStyle = '#0f172a';
      ctx.beginPath();
      ctx.ellipse(-3.5, headY - 1, 2.2, 3, 0, 0, Math.PI * 2);
      ctx.ellipse(3.5, headY - 1, 2.2, 3, 0, 0, Math.PI * 2);
      ctx.fill();

      // Iris tint
      ctx.fillStyle = char.theme.accentColor;
      ctx.beginPath();
      ctx.arc(-3.5, headY, 1.5, 0, Math.PI * 2);
      ctx.arc(3.5, headY, 1.5, 0, Math.PI * 2);
      ctx.fill();

      // Eye White Sparkle Highlight
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(-4, headY - 1.5, 0.9, 0, Math.PI * 2);
      ctx.arc(3, headY - 1.5, 0.9, 0, Math.PI * 2);
      ctx.fill();
    }

    // Cheeks Blush
    ctx.fillStyle = 'rgba(244, 63, 94, 0.3)';
    ctx.beginPath();
    ctx.arc(-5.5, headY + 3, 1.8, 0, Math.PI * 2);
    ctx.arc(5.5, headY + 3, 1.8, 0, Math.PI * 2);
    ctx.fill();

    // Mouth Articulation
    if (isSpeaking) {
      const mouthOpen = (Math.sin(time * 16) + 1) * 1.2;
      ctx.fillStyle = '#e11d48';
      ctx.beginPath();
      ctx.ellipse(0, headY + 4, 2, 1 + mouthOpen, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (isCelebrating) {
      // Open happy smile
      ctx.strokeStyle = '#e11d48';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(0, headY + 3, 2.5, 0.2, Math.PI - 0.2);
      ctx.stroke();
    } else {
      // Subtle neutral/calm mouth
      ctx.strokeStyle = '#475569';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(-1.8, headY + 4);
      ctx.lineTo(1.8, headY + 4);
      ctx.stroke();
    }

    // ─── ANIME HAIR STYLING ───
    ctx.fillStyle = char.theme.hairColor;
    ctx.beginPath();
    // Top bangs & hairline
    ctx.moveTo(-9, headY - 6);
    ctx.quadraticCurveTo(0, headY - 12, 9, headY - 6);
    ctx.lineTo(9, headY - 1);
    ctx.lineTo(5, headY - 4);
    ctx.lineTo(0, headY - 2);
    ctx.lineTo(-5, headY - 4);
    ctx.lineTo(-9, headY - 1);
    ctx.closePath();
    ctx.fill();

    // Side hair tufts
    ctx.fillRect(-9, headY - 6, 2.5, 8);
    ctx.fillRect(6.5, headY - 6, 2.5, 8);

    // Hair Spikes / Volume based on theme style
    if (char.theme.hairStyle === 'spiky' || char.role === 'boss') {
      ctx.beginPath();
      ctx.moveTo(-7, headY - 9);
      ctx.lineTo(-4, headY - 16);
      ctx.lineTo(-1, headY - 9);
      ctx.lineTo(3, headY - 17);
      ctx.lineTo(6, headY - 9);
      ctx.closePath();
      ctx.fill();
    }

    // Cyber Visor Accessory
    if (char.theme.hasVisor) {
      ctx.fillStyle = char.theme.accentColor;
      ctx.shadowColor = char.theme.accentColor;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.roundRect(-8, headY - 3, 16, 5, 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    // Headphones Accessory
    if (char.theme.hasHeadphones) {
      ctx.strokeStyle = char.theme.accentColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(0, headY - 7, 9, Math.PI, 0);
      ctx.stroke();
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(-10, headY - 5, 3, 6);
      ctx.fillRect(7, headY - 5, 3, 6);
    }
  }

  ctx.restore();

  // ─── STATUS EMOTE ICONS FLOATING ABOVE HEAD ───
  if (isWarning) {
    ctx.fillStyle = '#f59e0b';
    ctx.font = 'bold 13px sans-serif';
    ctx.fillText('⚠️', -7, -64);
  } else if (isCelebrating) {
    ctx.fillStyle = '#10b981';
    ctx.font = 'bold 13px sans-serif';
    ctx.fillText('🎉', -7, -64);
  } else if (isThinking) {
    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 11px sans-serif';
    const dots = '.'.repeat(1 + (Math.floor(time * 3) % 3));
    ctx.fillText(`💭${dots}`, -9, -64);
  }

  ctx.restore();

  // ─── NAME & ROLE FLOATING LABEL ───
  ctx.save();
  ctx.textAlign = 'center';
  ctx.font = 'bold 10px monospace';
  ctx.fillStyle = '#ffffff';
  ctx.shadowColor = '#000000';
  ctx.shadowBlur = 4;
  ctx.fillText(char.name, screenX, screenY - 50);

  ctx.font = '9px monospace';
  ctx.fillStyle = char.theme.accentColor;
  ctx.fillText(char.role.toUpperCase(), screenX, screenY - 40);
  ctx.restore();
}

