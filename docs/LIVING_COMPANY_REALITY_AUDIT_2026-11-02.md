# ANCOS Living Company Reality Audit — 2026-11-02

## Acceptance status: NOT ACCEPTED

The dashboard now renders a code-built interactive office and maps displayed employees, tasks, messages, and active meetings from backend records. A real end-to-end company run has not been demonstrated, so this audit does not claim final acceptance.

## Static image audit

- Removed the `PixelOfficeStage` implementation that rendered `/pixel_office_preview.jpg` as the company world.
- Removed the generated office composite and the screenshot of the former dashboard from the dashboard tree.
- Search of dashboard app/components for image imports and those generated image names returned no references.
- The company environment is drawn by `apps/dashboard/components/game/EnvironmentRenderer.ts` and characters are drawn separately by `apps/dashboard/components/game/CharacterRenderer.ts`.
- `apps/dashboard/public` still contains older, unused screenshot artifacts. They are not imported by the dashboard or the world renderer.

## Employee identity and records

- `IsometricCompanyWorld` populates its character map only through `fetchEmployees()` and `syncEmployeesToCharacters()`.
- `syncEmployeesToCharacters()` creates one character per returned `employee_id`, updates that character from the record, and removes characters whose records disappear.
- Removed the synthetic `ANC-BOSS` character and the development controls that spawned `ANC-TEST-*` employees, scripted conversations, and invented activity.
- A successful empty employee response leaves the world with zero characters.
- The character inspector displays the record's name, title, department, skills, task, and actual `desktop_url`. It no longer invents a VNC port or marks all listed skills as verified.
- Room geometry and desk definitions are static environment data. Desk placement is currently assigned locally from the employee's persisted department/title role; it is not a persisted workstation assignment from the backend.
- The roster page no longer substitutes a healthy Boss state, one active mission, an active workforce count, or an empty roster when its API calls fail. It shows unavailable/loading states instead.
- Meeting scheduling now loads the actual employee roster and invites selected persisted employee IDs; it no longer inserts the literal `BossAI` into manually scheduled meeting participants. Roster failure and a genuine empty roster are shown separately.

## Acceptance blockers found

- A clean-database Boss objective does not currently recruit an employee. `BossBrain.plan_and_create_project()` defaults `auto_recruit_workforce` to `False`, and `POST /boss/objective` does not enable it. This blocks the requested zero-employees → Boss recruitment experiment.
- `POST /employees` directly calls `EmployeeFactory.hire_employee()` and writes `actor="BossAI"` to the audit log. Production middleware requires an owner token, but the route does not prove the action came from BossBrain; local development mode also bypasses that middleware. Therefore the “only Boss can create employees through recruitment” rule is not enforced.
- `EmployeeFactory.bootstrap_initial_workforce()` still contains fixed Alex Morgan and Maya Chen profiles. Repository search found no production caller; the helper is exercised by legacy tests, so it does not automatically seed startup records, but it remains a callable hardcoded workforce path.
- `apps/dashboard/public` contains legacy screenshots and a demo GIF. No dashboard source imports or references them, and none is used as the live company world. They remain directly addressable static files.
- Characters are procedurally articulated with Canvas drawing and animation-state timing; there are no sprite-sheet assets or discrete source frames. Walking, task, meeting, speech, and listening states have implementation code, but animation assets/frame evidence requested by the earlier implementation brief do not exist.
- Meeting participants move to seats when a real meeting record enters `in_progress`, but there is no persisted meeting-speaker/presenter event wired to character speaking state. Speaking/listening states currently come from actual communication messages only.
- Employee workstations are allocated locally from role/department rather than read from persisted assignment records. This is a visual placement rule, not backend workstation assignment evidence.

## Backend data paths

- Employee records: `GET /employees/` in `services/core/neural/api/routes/employees.py`.
- Assigned task records: `GET /employees/{employee_id}/tasks` in the same router. The world reads assigned/running/retrying/blocked task records to show the current task and drive desk/work/warning states.
- Communications: `GET /comms/messages` in `services/core/neural/api/routes/comms.py`. Existing records populate the conversation history; only newly observed persisted records animate. Both sender and recipient display the actual message text. No dialogue is synthesized.
- Meetings: `GET /meetings/` in `services/core/neural/api/routes/meetings.py`. Only backend meetings with `in_progress` status move persisted participant characters to meeting seats. When no in-progress meeting remains, participants return to their assigned desks.
- These integrations currently poll the API; they are not a websocket or event-stream subscription.

## Character and movement implementation

- `CharacterRenderer.ts` separately draws articulated Canvas characters and changes limb, mouth, head, and body motion by animation state. There are no sprite-sheet assets.
- `MovementEngine.ts` advances each character's world position along waypoint paths and transitions at arrival.
- `thinking` and `celebrating` have renderer branches but no current data-driven state assignments; they are not demonstrated company activities. `warning` is driven by a persisted blocked-task status.
- `OfficeLayout.ts` defines office zones, furniture, workstation coordinates, meeting seats, and the navigation graph.
- `EmployeeCharacterManager.ts` maps real employee records to independent character objects. New records received after initial load enter from the office spawn and walk to an available desk.
- `MeetingVisualizer.ts` routes actual meeting participants to seats and back. No meeting speaker is invented; `currentSpeakerId` starts empty. Speaking/listening states currently come from persisted communication messages.
- Canvas hit-testing and inspectors in `IsometricCompanyWorld.tsx` support character, desk, department, and boardroom inspection.

## Verification performed

- `npx tsc --noEmit`: passed after the roster and meeting-participant UI changes.
- `npm run build`: passed after those changes (Next.js 15.5.26).
- This dashboard responds with HTTP 200 at `http://localhost:12002/login`. Port `12001` is occupied by a separate `C:\Users\nandk\sonic` Next.js process and was left untouched.
- Static-source searches found no generated company-world image import, synthetic employee IDs, canned chat text, or development employee-spawn controls.

## Runtime evidence still missing

- The core API was not listening on local ports `12000` or `12003` during this audit.
- Docker was unavailable because the Docker Desktop Linux engine pipe was absent.
- The Compose deployment has no task-worker or verifier process; `docs/TASK_WORKER_PROTOCOL.md` documents API protocol surfaces rather than an active executor. No real GUI task execution or independent verification receipt can be produced in this environment.
- No temporary isolated database was created, and no Boss recruitment, real task execution, two-employee message exchange, or persisted meeting lifecycle was run.
- No movement recording or final browser screenshot of real employees was captured.
- The isolated-data run, cleanup receipts, browser interaction evidence, recording, and screenshot requested for final acceptance remain outstanding.

Until the backend and worker/verifier runtimes are available, recruitment, task execution, message exchange, meeting lifecycle, and cleanup cannot be represented as an accepted end-to-end result.
