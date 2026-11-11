export interface Project {
  id: string;
  name: string;
  objective: string;
  status: string;
  created_at: string;
  execution_plan?: any;
}

export interface Employee {
  id: string;
  employee_id: string;
  name: string;
  title: string;
  department: string;
  status: string;
  skills: Array<{ name: string; level: string; verified: boolean }>;
  hired_at: string;
  container_id?: string | null;
  desktop_url?: string | null;
  desk_id?: string | null;
  avatar_config?: any;
}

export interface Workstation {
  docker_id: string;
  employee_id: string;
  image: string;
  vnc_port: number;
  novnc_port: number;
  desktop_url: string;
  cpu_limit: number;
  mem_limit: string;
  status: string;
  workspace: string;
  started_at?: string | null;
}

export interface Task {
  id: string;
  task_id: string;
  project_id: string;
  title: string;
  status: string;
  assigned_to?: string | null;
  priority: string;
  scheduled_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface ActionItem {
  id: string;
  meeting_id: string;
  description: string;
  assigned_to: string;
  deadline?: string | null;
  status: string;
  created_at: string;
  is_overdue?: boolean;
}

export interface Decision {
  decision_id: string;
  topic: string;
  decision: string;
  rationale: string;
  decided_by: string;
  impact?: string;
  timestamp?: string;
}

export interface Meeting {
  id: string;
  meeting_id: string;
  title: string;
  meeting_type: string;
  status: string;
  scheduled_at: string;
  started_at?: string | null;
  concluded_at?: string | null;
  active_speaker_id?: string | null;
  participants: string[];
  agenda: string[];
  minutes?: {
    summary?: string;
    progress_reports?: Array<{
      employee_id: string;
      task_id: string;
      what_was_done: string;
      what_remains: string;
      what_is_blocking?: string | null;
      verification_receipt?: any;
      factual_status?: string;
    }>;
    blockers_identified?: Array<{
      task_id: string;
      employee_id: string;
      blocker: string;
    }>;
    decisions?: Decision[];
    action_items?: any[];
    activity_log?: Array<{
      timestamp: string;
      event: string;
      speaker_id?: string | null;
      data?: any;
    }>;
  };
  decisions?: Decision[];
  action_items?: ActionItem[];
  activity_log?: Array<{
    timestamp: string;
    event: string;
    speaker_id?: string | null;
    data?: any;
  }>;
}

export interface ExecutiveBriefing {
  generated_at: string;
  executive_summary: string;
  milestones: Array<{
    project_id: string;
    project_name: string;
    status: string;
    total_tasks: number;
    completed_tasks: number;
    blocked_tasks: number;
    running_tasks: number;
    completion_percentage: number;
    phases_count: number;
  }>;
  active_workforce: {
    total_employees: number;
    working: number;
    idle: number;
    suspended: number;
    department_breakdown: Record<string, number>;
    skills: {
      total_declared: number;
      total_verified: number;
      verification_rate_pct: number;
    };
  };
  blockers: Array<{
    task_id: string;
    title: string;
    project_name: string;
    status: string;
    priority: string;
    assigned_to?: string;
    retry_count: number;
  }>;
  financial_costs: {
    total_tokens: number;
    total_usd: number;
    records_count: number;
  };
  pending_approvals: Array<{
    id: string;
    action_type: string;
    description: string;
    justification: string;
    requested_by: string;
    requested_at: string;
  }>;
  action_items_summary: {
    open_count: number;
    overdue_count: number;
  };
}

export interface BossPlanPhase {
  name: string;
  tasks: string[];
}

export interface BossPlanProposal {
  title: string;
  objective: string;
  estimated_cost_usd?: number;
  phases: BossPlanPhase[];
  required_skills: string[];
  deadline_days?: number;
}

export interface BossChatAction {
  type: string;
  objective?: string;
  project_name?: string;
  [key: string]: any;
}

export interface BossChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  plan_proposal?: BossPlanProposal | null;
  action?: BossChatAction | null;
}

export interface BossChatRequest {
  message: string;
  conversation_history?: BossChatMessage[];
  project_id?: string | null;
}

export interface BossChatResponse {
  reply: string;
  action?: BossChatAction | null;
  plan_proposal?: BossPlanProposal | null;
  conversation_history: BossChatMessage[];
}

