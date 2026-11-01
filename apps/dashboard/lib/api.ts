const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:12000';

export async function fetchBossStatus() {
  const res = await fetch(`${API_BASE}/boss/status`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch Boss AI status');
  return res.json();
}

export async function fetchProjects() {
  const res = await fetch(`${API_BASE}/projects/`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function fetchEmployees() {
  const res = await fetch(`${API_BASE}/employees/`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch employees');
  return res.json();
}

export async function fetchMeetings(limit: number = 20) {
  const res = await fetch(`${API_BASE}/meetings/?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch meetings');
  return res.json();
}

export async function fetchMeeting(meetingId: string) {
  const res = await fetch(`${API_BASE}/meetings/${meetingId}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch meeting ${meetingId}`);
  return res.json();
}

export async function scheduleMeeting(data: {
  title: string;
  meeting_type: string;
  agenda: string[];
  participants: string[];
}) {
  const res = await fetch(`${API_BASE}/meetings/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to schedule meeting');
  return res.json();
}

export async function startMeeting(meetingId: string) {
  const res = await fetch(`${API_BASE}/meetings/${meetingId}/start`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to start meeting ${meetingId}`);
  return res.json();
}

export async function concludeMeeting(meetingId: string, summary?: string) {
  const res = await fetch(`${API_BASE}/meetings/${meetingId}/conclude`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ summary }),
  });
  if (!res.ok) throw new Error(`Failed to conclude meeting ${meetingId}`);
  return res.json();
}

export async function conductMeeting(meetingId: string, data: {
  employee_reports?: any[];
  decisions?: any[];
  action_items?: any[];
  carry_over?: boolean;
}) {
  const res = await fetch(`${API_BASE}/meetings/${meetingId}/conduct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to conduct meeting ${meetingId}`);
  return res.json();
}

export async function fetchActionItems(status?: string, overdueOnly?: boolean) {
  const params = new URLSearchParams();
  if (status) params.append('status', status);
  if (overdueOnly) params.append('overdue_only', 'true');
  const res = await fetch(`${API_BASE}/meetings/action-items?${params.toString()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch action items');
  return res.json();
}

export async function updateActionItem(actionItemId: string, data: { status?: string; deadline?: string }) {
  const res = await fetch(`${API_BASE}/meetings/action-items/${actionItemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update action item ${actionItemId}`);
  return res.json();
}

export async function createActionItem(data: {
  meeting_id: string;
  description: string;
  assigned_to: string;
  deadline?: string;
}) {
  const res = await fetch(`${API_BASE}/meetings/action-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to create action item');
  return res.json();
}

export async function fetchExecutiveBriefing(projectId?: string) {
  const url = projectId
    ? `${API_BASE}/meetings/briefing?project_id=${projectId}`
    : `${API_BASE}/meetings/briefing`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch executive briefing');
  return res.json();
}

export async function triggerDailyStandup() {
  const res = await fetch(`${API_BASE}/meetings/trigger/standup`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to trigger daily standup');
  return res.json();
}

export async function triggerBlockerMeeting(taskId: string, blockerReason: string) {
  const res = await fetch(`${API_BASE}/meetings/trigger/blocker`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: taskId, blocker_reason: blockerReason }),
  });
  if (!res.ok) throw new Error('Failed to trigger blocker meeting');
  return res.json();
}

export async function submitObjective(objective: string, projectName?: string) {
  const res = await fetch(`${API_BASE}/boss/objective`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ objective, project_name: projectName }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to submit objective');
  }
  return res.json();
}

export async function fetchEmployee(employeeId: string) {
  const res = await fetch(`${API_BASE}/employees/${employeeId}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch employee ${employeeId}`);
  return res.json();
}

export async function fetchEmployeeWorkstation(employeeId: string) {
  const res = await fetch(`${API_BASE}/employees/${employeeId}/workstation`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch workstation for employee ${employeeId}`);
  return res.json();
}

export async function provisionWorkstation(employeeId: string) {
  const res = await fetch(`${API_BASE}/employees/${employeeId}/workstation/provision`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to provision workstation for ${employeeId}`);
  }
  return res.json();
}

export async function terminateWorkstation(employeeId: string) {
  const res = await fetch(`${API_BASE}/employees/${employeeId}/workstation/terminate`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to terminate workstation for ${employeeId}`);
  }
  return res.json();
}

export async function fetchEmployeeTasks(employeeId: string) {
  const res = await fetch(`${API_BASE}/employees/${employeeId}/tasks`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch tasks for employee ${employeeId}`);
  return res.json();
}

export async function hireEmployee(data: {
  name: string;
  title: string;
  department?: string;
  role_id?: string;
  personality?: string;
  communication_style?: string;
  skills?: string[];
}) {
  const res = await fetch(`${API_BASE}/employees/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to hire employee');
  }
  return res.json();
}

export async function fetchProjectTasks(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/tasks`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch tasks for project ${projectId}`);
  return res.json();
}

export async function fetchMessages(limit: number = 30) {
  const res = await fetch(`${API_BASE}/comms/messages?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch communications messages');
  return res.json();
}

export async function sendDirectMessage(data: {
  sender: string;
  recipient: string;
  content: string;
  subject?: string;
}) {
  const res = await fetch(`${API_BASE}/comms/direct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to send direct message');
  return res.json();
}

export async function fetchTelemetry() {
  const res = await fetch(`${API_BASE}/observability/telemetry`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch system telemetry');
  return res.json();
}

export async function fetchMetrics() {
  const res = await fetch(`${API_BASE}/metrics`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch prometheus metrics');
  return res.text();
}

export async function triggerEmergencyStop(reason: string = 'Operator triggered emergency stop', actor: string = 'dashboard_operator') {
  const res = await fetch(`${API_BASE}/security/emergency-stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, actor }),
  });
  if (!res.ok) throw new Error('Failed to trigger emergency stop');
  return res.json();
}

export async function resumeSystem(actor: string = 'dashboard_operator', recoveryStrategy: string = 'checkpoint') {
  const res = await fetch(`${API_BASE}/security/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor, recovery_strategy: recoveryStrategy }),
  });
  if (!res.ok) throw new Error('Failed to resume system');
  return res.json();
}

export async function fetchApprovals() {
  const res = await fetch(`${API_BASE}/security/approvals`, { cache: 'no-store' });
  if (!res.ok) throw new Error('Failed to fetch pending approvals');
  return res.json();
}

export async function decideApproval(approvalId: string, decision: 'approved' | 'rejected', decidedBy: string = 'operator') {
  const res = await fetch(`${API_BASE}/security/approvals/${approvalId}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, decided_by: decidedBy }),
  });
  if (!res.ok) throw new Error(`Failed to decide approval ${approvalId}`);
  return res.json();
}


