import { Point2D, ScreenPoint } from './types';

export const TILE_WIDTH = 64;
export const TILE_HEIGHT = 32;
export const TILE_HALF_W = TILE_WIDTH / 2;
export const TILE_HALF_H = TILE_HEIGHT / 2;

/**
 * Converts top-down world coordinates into canvas pixels.
 */
export function gridToScreen(
  gx: number,
  gy: number,
  gz: number = 0,
  originX: number = 0,
  originY: number = 0
): ScreenPoint {
  const screenX = gx * TILE_WIDTH + originX;
  const screenY = gy * TILE_HEIGHT - gz * 8 + originY;
  return { screenX, screenY };
}

/**
 * Converts canvas pixels back into top-down world coordinates.
 */
export function screenToGrid(
  screenX: number,
  screenY: number,
  originX: number = 0,
  originY: number = 0
): Point2D {
  return {
    x: (screenX - originX) / TILE_WIDTH,
    y: (screenY - originY) / TILE_HEIGHT,
  };
}

/**
 * Sorts top-down sprites by row so objects lower on screen render in front.
 */
export function getRenderDepth(gx: number, gy: number, extraOffset: number = 0): number {
  return gy * 1000 + gx + extraOffset;
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
 * Draws one rectangular pixel-art floor tile.
 */
export function drawPixelFloorTile(
  ctx: CanvasRenderingContext2D,
  screenX: number,
  screenY: number,
  fillColor: string,
  strokeColor?: string,
  lineWidth: number = 1
): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(screenX - TILE_HALF_W, screenY - TILE_HALF_H, TILE_WIDTH, TILE_HEIGHT);
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
 * Draws a shaded top-down furniture tile with a stepped edge.
 */
export function drawPixelFurnitureBlock(
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
  const width = TILE_WIDTH * widthRatio;
  const depth = TILE_HEIGHT * depthRatio;

  ctx.save();

  // Pixel-art furniture block: a compact drop shadow, cabinet body, and bright top edge.
  ctx.fillStyle = leftColor;
  ctx.fillRect(screenX - width / 2 + 3, screenY - depth / 2 + heightPx * 0.35, width, depth);
  ctx.fillStyle = rightColor;
  ctx.fillRect(screenX - width / 2, screenY - depth / 2 + 2, width, depth);
  ctx.fillStyle = topColor;
  ctx.fillRect(screenX - width / 2, screenY - depth / 2, width, Math.max(4, depth * 0.68));
  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(screenX - width / 2, screenY - depth / 2, width, depth);
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(screenX - width / 2 + 2, screenY - depth / 2 + 2, Math.max(4, width - 4), 2);
  }

  ctx.restore();
}

