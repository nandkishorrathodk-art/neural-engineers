import { Point2D, IsoPoint } from './types';

export const TILE_WIDTH = 64;
export const TILE_HEIGHT = 32;
export const TILE_HALF_W = TILE_WIDTH / 2; // 32
export const TILE_HALF_H = TILE_HEIGHT / 2; // 16

/**
 * Converts continuous grid coordinate (gx, gy, gz) into 2D canvas pixel coordinates
 */
export function gridToScreen(
  gx: number,
  gy: number,
  gz: number = 0,
  originX: number = 0,
  originY: number = 0
): IsoPoint {
  const screenX = (gx - gy) * TILE_HALF_W + originX;
  const screenY = (gx + gy) * TILE_HALF_H - gz * (TILE_HEIGHT * 0.8) + originY;
  return { screenX, screenY };
}

/**
 * Converts screen pixel coordinate to 2D continuous grid coordinate (gx, gy)
 */
export function screenToGrid(
  screenX: number,
  screenY: number,
  originX: number = 0,
  originY: number = 0
): Point2D {
  const dx = (screenX - originX) / TILE_HALF_W;
  const dy = (screenY - originY) / TILE_HALF_H;
  const gx = (dx + dy) / 2;
  const gy = (dy - dx) / 2;
  return { x: gx, y: gy };
}

/**
 * Calculates rendering depth for isometric z-sorting (objects closer to camera render in front)
 */
export function getIsometricDepth(gx: number, gy: number, extraOffset: number = 0): number {
  return (gx + gy) * 100 + extraOffset;
}

/**
 * Linear interpolation
 */
export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * Math.max(0, Math.min(1, t));
}

/**
 * Cosine ease in/out for smooth natural humanoid locomotion
 */
export function easeInOutCosine(t: number): number {
  return (1 - Math.cos(t * Math.PI)) / 2;
}

/**
 * Euclidean distance between two grid points
 */
export function gridDistance(p1: Point2D, p2: Point2D): number {
  const dx = p1.x - p2.x;
  const dy = p1.y - p2.y;
  return Math.hypot(dx, dy);
}

/**
 * Draws an isometric diamond tile on a 2D Canvas context
 */
export function drawIsometricTile(
  ctx: CanvasRenderingContext2D,
  screenX: number,
  screenY: number,
  fillColor: string,
  strokeColor?: string,
  lineWidth: number = 1
): void {
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(screenX, screenY - TILE_HALF_H); // Top vertex
  ctx.lineTo(screenX + TILE_HALF_W, screenY); // Right vertex
  ctx.lineTo(screenX, screenY + TILE_HALF_H); // Bottom vertex
  ctx.lineTo(screenX - TILE_HALF_W, screenY); // Left vertex
  ctx.closePath();

  ctx.fillStyle = fillColor;
  ctx.fill();

  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Draws a 3D isometric extruded block (useful for desks, server cabinets, walls)
 */
export function drawIsometricBlock(
  ctx: CanvasRenderingContext2D,
  screenX: number,
  screenY: number,
  widthRatio: number = 1,
  depthRatio: number = 1,
  heightPx: number = 20,
  topColor: string = '#1e293b',
  leftColor: string = '#0f172a',
  rightColor: string = '#141e33',
  strokeColor?: string
): void {
  const hw = TILE_HALF_W * widthRatio;
  const hh = TILE_HALF_H * depthRatio;

  ctx.save();

  // 1. Left Face (from left corner to bottom corner, extruded down)
  ctx.fillStyle = leftColor;
  ctx.beginPath();
  ctx.moveTo(screenX - hw, screenY);
  ctx.lineTo(screenX, screenY + hh);
  ctx.lineTo(screenX, screenY + hh + heightPx);
  ctx.lineTo(screenX - hw, screenY + heightPx);
  ctx.closePath();
  ctx.fill();
  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.stroke();
  }

  // 2. Right Face (from bottom corner to right corner, extruded down)
  ctx.fillStyle = rightColor;
  ctx.beginPath();
  ctx.moveTo(screenX, screenY + hh);
  ctx.lineTo(screenX + hw, screenY);
  ctx.lineTo(screenX + hw, screenY + heightPx);
  ctx.lineTo(screenX, screenY + hh + heightPx);
  ctx.closePath();
  ctx.fill();
  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.stroke();
  }

  // 3. Top Face (horizontal isometric diamond)
  ctx.fillStyle = topColor;
  ctx.beginPath();
  ctx.moveTo(screenX, screenY - hh);
  ctx.lineTo(screenX + hw, screenY);
  ctx.lineTo(screenX, screenY + hh);
  ctx.lineTo(screenX - hw, screenY);
  ctx.closePath();
  ctx.fill();
  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.stroke();
  }

  ctx.restore();
}

