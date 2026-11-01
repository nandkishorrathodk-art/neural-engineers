import { GameCharacter, MeetingSeatDef, WorkstationDef } from './types';
import { findPath } from './MovementEngine';
import { MEETING_SEATS } from './OfficeLayout';

export interface MeetingSession {
  id: string;
  title: string;
  phase: 'assembling' | 'in_progress' | 'concluding';
  participants: string[];
  currentSpeakerId: string | null;
  startedAt: number;
}

/**
 * Initiates character movement into the boardroom for an active meeting
 */
export function callCharactersToMeeting(
  meetingId: string,
  title: string,
  participantIds: string[],
  characters: Map<string, GameCharacter>,
  seats: MeetingSeatDef[]
): MeetingSession {
  // Clear any previous seat occupations
  for (const seat of seats) {
    seat.occupantId = null;
  }

  let seatIdx = 0;
  for (const empId of participantIds) {
    const char = characters.get(empId);
    if (!char) continue;

    if (seatIdx < seats.length) {
      const seat = seats[seatIdx];
      seat.occupantId = empId;
      char.currentMeetingSeatIndex = seatIdx;

      // Plan navigation path to meeting seat
      char.path = findPath(
        { x: char.gridX, y: char.gridY },
        { x: seat.gridX, y: seat.gridY }
      );
      char.animState = 'walking';

      seatIdx++;
    }
  }

  return {
    id: meetingId,
    title,
    phase: 'assembling',
    participants: participantIds,
    currentSpeakerId: participantIds[0] || null,
    startedAt: Date.now(),
  };
}

/**
 * Dismisses meeting participants, directing them to walk back to their respective workstations
 */
export function returnCharactersFromMeeting(
  session: MeetingSession,
  characters: Map<string, GameCharacter>,
  workstations: WorkstationDef[],
  seats: MeetingSeatDef[]
): void {
  // Clear meeting seats
  for (const seat of seats) {
    seat.occupantId = null;
  }

  for (const empId of session.participants) {
    const char = characters.get(empId);
    if (!char) continue;

    char.currentMeetingSeatIndex = null;

    if (char.assignedDeskId) {
      const ws = workstations.find((w) => w.id === char.assignedDeskId);
      if (ws) {
        char.path = findPath(
          { x: char.gridX, y: char.gridY },
          { x: ws.seatGridX, y: ws.seatGridY }
        );
        char.animState = 'walking';
      }
    } else {
      char.animState = 'idle';
    }
  }
}

