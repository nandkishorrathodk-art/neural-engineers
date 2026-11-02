import { Point2D, GameCharacter, HeadingDirection, WaypointNode } from './types';
import { WAYPOINTS } from './OfficeLayout';
import { gridDistance } from './IsometricMath';

/**
 * Finds the closest waypoint in the navigation graph to a given continuous grid position
 */
export function findClosestWaypoint(pos: Point2D): WaypointNode {
  let closest: WaypointNode = Object.values(WAYPOINTS)[0];
  let minDist = Infinity;

  for (const wp of Object.values(WAYPOINTS)) {
    const dist = gridDistance(pos, { x: wp.gridX, y: wp.gridY });
    if (dist < minDist) {
      minDist = dist;
      closest = wp;
    }
  }

  return closest;
}

/**
 * Computes shortest path across the waypoint graph using Breadth-First Search (BFS)
 */
export function findPath(start: Point2D, goal: Point2D): Point2D[] {
  // If direct distance is very close, navigate directly
  if (gridDistance(start, goal) < 1.5) {
    return [{ x: goal.x, y: goal.y }];
  }

  const startWp = findClosestWaypoint(start);
  const goalWp = findClosestWaypoint(goal);

  if (startWp.id === goalWp.id) {
    return [
      { x: startWp.gridX, y: startWp.gridY },
      { x: goal.x, y: goal.y },
    ];
  }

  // BFS search
  const queue: string[] = [startWp.id];
  const visited = new Set<string>([startWp.id]);
  const parentMap = new Map<string, string>();

  let found = false;

  while (queue.length > 0) {
    const currentId = queue.shift()!;
    if (currentId === goalWp.id) {
      found = true;
      break;
    }

    const node = WAYPOINTS[currentId];
    if (!node) continue;

    for (const neighborId of node.neighbors) {
      if (!visited.has(neighborId)) {
        visited.add(neighborId);
        parentMap.set(neighborId, currentId);
        queue.push(neighborId);
      }
    }
  }

  // Reconstruct path
  const path: Point2D[] = [];

  if (found) {
    const sequence: string[] = [];
    let curr: string | undefined = goalWp.id;
    while (curr) {
      sequence.unshift(curr);
      curr = parentMap.get(curr);
    }

    for (const wpId of sequence) {
      const node = WAYPOINTS[wpId];
      if (node) {
        path.push({ x: node.gridX, y: node.gridY });
      }
    }
  } else {
    // Fallback: direct waypoint link
    path.push({ x: startWp.gridX, y: startWp.gridY });
    path.push({ x: goalWp.gridX, y: goalWp.gridY });
  }

  // Finally add exact destination
  path.push({ x: goal.x, y: goal.y });

  return path;
}

/**
 * Updates character movement, velocity, heading, and state transitions
 */
export function updateCharacterMovement(char: GameCharacter, dt: number): void {
  char.animTime += dt;

  // Blinking cycle update
  char.blinkTimer -= dt;
  if (char.blinkTimer <= 0) {
    if (char.isBlinking) {
      char.isBlinking = false;
      char.blinkTimer = 2.5 + Math.random() * 3.5; // Next blink in 2.5 - 6s
    } else {
      char.isBlinking = true;
      char.blinkTimer = 0.15; // Blink duration 150ms
    }
  }

  // Speech & temporary emote expiration
  if (char.speechRemainingMs > 0) {
    char.speechRemainingMs -= dt * 1000;
    if (char.speechRemainingMs <= 0) {
      char.speechText = null;
      if (
        char.animState === 'speaking' ||
        char.animState === 'listening' ||
        char.animState === 'celebrating' ||
        char.animState === 'warning'
      ) {
        if (char.path && char.path.length > 0) {
          char.animState = 'walking';
        } else if (char.currentMeetingSeatIndex !== null) {
          char.animState = 'meeting';
        } else if (char.assignedDeskId) {
          char.animState = char.status === 'working' || char.status === 'online' ? 'working' : 'idle';
        } else {
          char.animState = 'idle';
        }
      }
    }
  }

  // If character is performing celebration or warning emote, pause locomotion
  if (
    (char.animState === 'celebrating' || char.animState === 'warning') &&
    char.speechRemainingMs > 0
  ) {
    return;
  }

  // If no path to walk, stay in current state
  if (!char.path || char.path.length === 0) {
    return;
  }

  const target = char.path[0];
  const dist = gridDistance({ x: char.gridX, y: char.gridY }, target);
  const step = char.speed * dt;

  char.animState = 'walking';

  // Determine heading
  const dx = target.x - char.gridX;
  const dy = target.y - char.gridY;
  if (Math.abs(dx) > Math.abs(dy)) {
    char.heading = dx > 0 ? 'right' : 'left';
  } else {
    char.heading = dy > 0 ? 'down' : 'up';
  }

  if (dist <= step) {
    // Reached current waypoint
    char.gridX = target.x;
    char.gridY = target.y;
    char.path.shift();

    if (char.path.length === 0) {
      // Reached final destination
      if (char.currentMeetingSeatIndex !== null) {
        char.animState = 'meeting';
        char.heading = char.currentMeetingSeatIndex % 2 === 0 ? 'right' : 'left';
      } else if (char.assignedDeskId) {
        char.animState = char.status === 'working' || char.status === 'online' ? 'working' : 'idle';
        char.heading = 'down';
      } else {
        char.animState = 'idle';
      }
    }
  } else {
    // Continue moving towards current waypoint
    const t = step / dist;
    char.gridX += dx * t;
    char.gridY += dy * t;
  }
}
