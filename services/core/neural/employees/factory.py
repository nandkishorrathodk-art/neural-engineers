import os
import uuid
from typing import Any, Optional

import yaml

from neural.config import get_settings
from neural.constants import SkillLevel
from neural.db.models import Employee
from neural.db.repositories import EmployeeRepository
from neural.logger import get_logger
from neural.workstations.assignment import WorkstationAssignmentService

logger = get_logger("ancos.employees.factory")


def generate_default_avatar_config(
    role: str,
    seed: str,
    title: str = "",
    department: str = "",
) -> dict[str, Any]:
    """
    Derives deterministic visual identity matching dashboard isometric world theme.
    """
    normalized_role = role.lower()
    t_lower = title.lower()
    d_lower = department.lower()

    if not normalized_role:
        if "boss" in t_lower or "ceo" in t_lower or "executive" in d_lower:
            normalized_role = "boss"
        elif "qa" in t_lower or "test" in t_lower or "quality" in d_lower:
            normalized_role = "qa"
        elif "devops" in t_lower or "infra" in t_lower or "sre" in d_lower:
            normalized_role = "devops"
        elif "research" in t_lower or "scientist" in t_lower:
            normalized_role = "research"
        elif "creative" in t_lower or "design" in t_lower:
            normalized_role = "creative"
        elif "frontend" in t_lower or "ui" in t_lower:
            normalized_role = "frontend"
        else:
            normalized_role = "swe"

    h = sum(ord(c) for c in seed)
    skin_tones = ["#f1c79f", "#c98f6d", "#e8b995", "#a9684b", "#f2d5b9"]
    skin_tone = skin_tones[h % len(skin_tones)]

    if normalized_role == "boss":
        return {
            "hairColor": "#4d5c70",
            "suitColor": "#394e47",
            "accentColor": "#efcc87",
            "skinTone": "#e8b995",
            "hairStyle": "spiky",
            "hasVisor": True,
            "hasHeadphones": False,
            "hasGlasses": False,
        }
    elif normalized_role == "swe":
        return {
            "hairColor": "#72533f" if (h % 2 == 0) else "#46566c",
            "suitColor": "#384d4a",
            "accentColor": "#d8b974",
            "skinTone": skin_tone,
            "hairStyle": "spiky",
            "hasVisor": False,
            "hasHeadphones": True,
            "hasGlasses": False,
        }
    elif normalized_role == "frontend":
        return {
            "hairColor": "#6c4e41",
            "suitColor": "#465b65",
            "accentColor": "#e1b273",
            "skinTone": skin_tone,
            "hairStyle": "bob",
            "hasVisor": False,
            "hasHeadphones": True,
            "hasGlasses": False,
        }
    elif normalized_role == "research":
        return {
            "hairColor": "#55443b",
            "suitColor": "#63576a",
            "accentColor": "#c4a5d5",
            "skinTone": skin_tone,
            "hairStyle": "long",
            "hasVisor": False,
            "hasHeadphones": False,
            "hasGlasses": True,
        }
    elif normalized_role == "creative":
        return {
            "hairColor": "#80543c",
            "suitColor": "#76604e",
            "accentColor": "#e1b273",
            "skinTone": skin_tone,
            "hairStyle": "bob",
            "hasVisor": False,
            "hasHeadphones": False,
            "hasGlasses": False,
        }
    elif normalized_role == "qa":
        return {
            "hairColor": "#544134",
            "suitColor": "#4d515b",
            "accentColor": "#e4bc70",
            "skinTone": skin_tone,
            "hairStyle": "spiky",
            "hasVisor": True,
            "hasHeadphones": False,
            "hasGlasses": False,
        }
    elif normalized_role == "devops":
        return {
            "hairColor": "#3c4c50",
            "suitColor": "#39545d",
            "accentColor": "#8bc5c3",
            "skinTone": skin_tone,
            "hairStyle": "spiky",
            "hasVisor": False,
            "hasHeadphones": True,
            "hasGlasses": False,
        }
    else:
        return {
            "hairColor": "#5d493c",
            "suitColor": "#435358",
            "accentColor": "#d7b57b",
            "skinTone": skin_tone,
            "hairStyle": "bob",
            "hasVisor": False,
            "hasHeadphones": False,
            "hasGlasses": False,
        }


class EmployeeFactory:
    """
    Dynamic Employee Factory.
    Recruits and initializes model-backed AI employees with rich personalities,
    system instructions, tool permissions, and skill tracking.
    """

    def __init__(self, repo: EmployeeRepository):
        self.repo = repo
        self.settings = get_settings()

    def _find_role_config_file(self) -> Optional[str]:
        candidates = [
            os.path.abspath("configs/role-definitions.yaml"),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "configs", "role-definitions.yaml"),
            os.path.join(os.path.dirname(__file__), "..", "..", "configs", "role-definitions.yaml"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def list_available_roles(self) -> list[dict[str, Any]]:
        """Returns all role configurations defined in role-definitions.yaml."""
        config_path = self._find_role_config_file()
        if not config_path:
            return []
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("roles", [])
        except Exception as e:
            logger.warning("Failed to list available roles", error=str(e))
            return []

    def _load_role_config(self, role_id: str) -> Optional[dict[str, Any]]:
        roles = self.list_available_roles()
        for role in roles:
            if role.get("id") == role_id:
                return role
        return None

    async def hire_employee(
        self,
        name: str,
        title: str,
        department: str = "Engineering",
        role_id: Optional[str] = None,
        personality: Optional[str] = None,
        communication_style: Optional[str] = None,
        initial_skills: Optional[list[str]] = None,
        recruitment_origin: Optional[str] = None,
        desk_id: Optional[str] = None,
        avatar_config: Optional[dict[str, Any]] = None,
    ) -> Employee:
        role_cfg = self._load_role_config(role_id) if role_id else None

        effective_title = title or (role_cfg.get("title") if role_cfg else None) or "Software Engineer"
        effective_dept = department or (role_cfg.get("department") if role_cfg else None) or "Engineering"

        employee_id = f"ANC-EMP-{uuid.uuid4().hex[:12].upper()}"
        system_prompt = (
            f"You are {name}, a {effective_title} at Autonomous Neural Company (ANCOS).\n"
            f"Department: {effective_dept}.\n"
            f"Personality: {personality or 'Rigorous, pragmatic, and collaborative'}.\n"
            f"Communication: {communication_style or 'Direct, factual, grounded in code receipts'}.\n"
            "Your workspace is an isolated Linux desktop running in Docker with terminal, IDE, and browser.\n"
            "Never claim completion without verifiable runtime proof or passing tests."
        )

        approved_tools = ["terminal", "code_editor", "file_ops", "git"]
        if role_cfg and "approved_tools" in role_cfg:
            approved_tools = list(set(approved_tools + role_cfg["approved_tools"]))
        elif "frontend" in effective_title.lower() or "qa" in effective_title.lower():
            approved_tools.append("browser")

        # Execution capabilities are the environments this employee can actually
        # operate. Browser-approved roles gain GUI capability in addition to the
        # universal terminal capability; the capability set is what routing uses,
        # so a task requiring GUI is never matched to a terminal-only employee.
        execution_capabilities = ["terminal"]
        if "browser" in approved_tools:
            execution_capabilities.append("gui")

        model_config_data = {}
        if role_cfg:
            model_config_data = {
                "preferred_model_tier": role_cfg.get("preferred_model_tier", "coding"),
                "container_template": role_cfg.get("container_template", "employee-swe"),
            }
        if recruitment_origin:
            model_config_data["recruitment_origin"] = recruitment_origin

        # Ensure workspace directory exists on host
        workspace_base = os.path.abspath(self.settings.EMPLOYEE_WORKSPACE_BASE)
        os.makedirs(os.path.join(workspace_base, employee_id), exist_ok=True)

        # Allocate desk if not explicitly provided, or validate explicit desk
        assigned_desk = desk_id
        if assigned_desk is not None and not WorkstationAssignmentService.is_valid_desk_id(assigned_desk):
            raise ValueError(f"Invalid workstation desk '{assigned_desk}'")

        if hasattr(self.repo, "list_occupied_desks"):
            occupied = await self.repo.list_occupied_desks()
            if assigned_desk is not None:
                if assigned_desk in occupied:
                    raise ValueError(
                        f"Workstation desk '{assigned_desk}' is already assigned to an active employee"
                    )
            else:
                assigned_desk = WorkstationAssignmentService.allocate_desk(
                    department=effective_dept,
                    title=effective_title,
                    occupied_desks=occupied,
                    employee_id=employee_id,
                )

        effective_avatar = avatar_config or generate_default_avatar_config(
            role=role_id or "",
            seed=f"{employee_id}{name}",
            title=effective_title,
            department=effective_dept,
        )

        employee = await self.repo.create(
            employee_id=employee_id,
            name=name,
            title=effective_title,
            department=effective_dept,
            personality=personality,
            communication_style=communication_style,
            system_prompt=system_prompt,
            approved_tools=approved_tools,
            model_config_data=model_config_data,
            execution_capabilities=execution_capabilities,
            performance_summary={"completed_tasks": 0, "verified_skills": []},
            desk_id=assigned_desk,
            avatar_config=effective_avatar,
        )

        # Resolve skill levels from role configuration if available
        role_skills_map: dict[str, tuple[str, SkillLevel]] = {}
        if role_cfg and "base_skills" in role_cfg:
            for s in role_cfg["base_skills"]:
                if isinstance(s, dict) and "name" in s:
                    lvl_str = str(s.get("level", "intermediate")).lower()
                    try:
                        lvl = SkillLevel(lvl_str)
                    except ValueError:
                        lvl = SkillLevel.INTERMEDIATE
                    role_skills_map[s["name"].lower()] = (s["name"], lvl)

        # Register initial unverified skills
        skills_to_add: list[tuple[str, SkillLevel]] = []
        if initial_skills:
            for s in initial_skills:
                if isinstance(s, dict) and "name" in s:
                    s_name = s["name"]
                    s_lvl = s.get("level", SkillLevel.INTERMEDIATE)
                    if isinstance(s_lvl, str):
                        try:
                            s_lvl = SkillLevel(s_lvl.lower())
                        except ValueError:
                            s_lvl = SkillLevel.INTERMEDIATE
                    skills_to_add.append((s_name, s_lvl))
                elif isinstance(s, str):
                    if s.lower() in role_skills_map:
                        skills_to_add.append(role_skills_map[s.lower()])
                    else:
                        skills_to_add.append((s, SkillLevel.INTERMEDIATE))
        elif role_skills_map:
            skills_to_add = list(role_skills_map.values())
        else:
            skills_to_add = [
                ("Software Engineering", SkillLevel.INTERMEDIATE),
                ("Debugging", SkillLevel.INTERMEDIATE)
            ]

        for skill_name, skill_level in skills_to_add:
            skill = await self.repo.add_skill(
                employee_id=employee_id,
                skill_name=skill_name,
                level=skill_level,
                verified=False  # Skills require practical verification
            )
            if skill not in employee.skills:
                employee.skills.append(skill)

        logger.info(
            "Successfully hired new AI employee",
            employee_id=employee_id,
            name=name,
            title=effective_title,
            skills_count=len(skills_to_add)
        )
        return employee

    async def _resolve_dynamic_identity(
        self,
        role_title: str = "Software Engineer",
        department: str = "Engineering",
        name: Optional[str] = None,
    ) -> dict[str, str]:
        persona = self.generate_dynamic_identity(role_title=role_title, department=department)
        if name:
            persona["name"] = name
        else:
            existing_names = {
                employee.name.strip().casefold()
                for employee in await self.repo.list_all()
            }
            if persona["name"].strip().casefold() in existing_names:
                persona["name"] = f"{persona['name']} {uuid.uuid4().hex[:4].upper()}"
        return persona

    async def recruit_for_skill(self, skill_name: str, name: Optional[str] = None) -> Employee:
        """
        Dynamically recruits an employee role that possesses the requested skill.
        Matches against base_skills in role definitions.
        """
        skill_lower = skill_name.strip().lower()
        matched_role = None
        for role in self.list_available_roles():
            for s in role.get("base_skills", []):
                s_name = s.get("name", "") if isinstance(s, dict) else str(s)
                if s_name.lower() == skill_lower:
                    matched_role = role
                    break
            if matched_role:
                break

        if matched_role:
            role_id = matched_role["id"]
            title = matched_role.get("title", "Software Engineer")
            dept = matched_role.get("department", "Engineering")
            persona = await self._resolve_dynamic_identity(role_title=title, department=dept, name=name)
            return await self.hire_employee(
                name=persona["name"],
                title=title,
                department=dept,
                role_id=role_id,
                personality=persona["personality"],
                communication_style=persona["communication_style"],
            )

        # Generic recruit fallback
        title = f"{skill_name} Engineer"
        dept = "Engineering"
        persona = await self._resolve_dynamic_identity(role_title=title, department=dept, name=name)
        return await self.hire_employee(
            name=persona["name"],
            title=title,
            department=dept,
            personality=persona["personality"],
            communication_style=persona["communication_style"],
            initial_skills=[skill_name, "Software Engineering"],
        )

    async def recruit_for_role(self, role_id: str, name: Optional[str] = None) -> Employee:
        """Recruits an employee by role identifier."""
        role_cfg = self._load_role_config(role_id)
        title = role_cfg.get("title", role_id.replace("-", " ").title()) if role_cfg else role_id.replace("-", " ").title()
        dept = role_cfg.get("department", "Engineering") if role_cfg else "Engineering"
        persona = await self._resolve_dynamic_identity(role_title=title, department=dept, name=name)
        return await self.hire_employee(
            name=persona["name"],
            title=title,
            department=dept,
            role_id=role_id,
            personality=persona["personality"],
            communication_style=persona["communication_style"],
        )

    @classmethod
    def generate_dynamic_identity(cls, role_title: str = "Software Engineer", department: str = "Engineering") -> dict[str, str]:
        """
        Dynamically generates a unique professional persona and identity
        without relying on static pre-defined hardcoded names (Requirement 7).
        """
        import random
        first_names = [
            "Devan", "Ananya", "Marcus", "Elena", "Tariq", "Kavya", "Liam", "Zoe",
            "Mateo", "Priya", "Chen", "Soraya", "Arjun", "Chloe", "Kenji", "Fatima"
        ]
        last_names = [
            "Vance", "Deshmukh", "Holloway", "Rostova", "Al-Mansoor", "Nair", "Gallagher", "Novak",
            "Silva", "Iyer", "Wu", "Moradi", "Verma", "Dubois", "Tanaka", "El-Sayed"
        ]
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        name = f"{fn} {ln}"

        personalities = [
            "Pragmatic, architecture-focused, and thorough",
            "Adversarial, skeptical, and rigorous verifier",
            "Fast-paced systems builder and resilience specialist",
            "Analytical, methodical, and data-driven engineer",
        ]
        styles = [
            "Direct, concise, and code-receipt-grounded",
            "Evidence-first, uncompromising, and factual",
            "Clear, collaborative, and precision-driven",
        ]

        return {
            "name": name,
            "title": role_title,
            "department": department,
            "personality": random.choice(personalities),
            "communication_style": random.choice(styles),
        }

    async def recruit_dynamic_employee(
        self,
        role_title: str = "Software Engineer",
        department: str = "Engineering",
        role_id: Optional[str] = None,
        initial_skills: Optional[list[str]] = None,
        recruitment_origin: Optional[str] = None,
    ) -> Employee:
        """
        Recruits an employee with a fully dynamic, unique identity (Requirement 7).
        """
        persona = await self._resolve_dynamic_identity(role_title=role_title, department=department)
        return await self.hire_employee(
            name=persona["name"],
            title=persona["title"],
            department=persona["department"],
            role_id=role_id,
            personality=persona["personality"],
            communication_style=persona["communication_style"],
            initial_skills=initial_skills,
            recruitment_origin=recruitment_origin,
        )

    async def recruit_workforce_for_plan(
        self,
        plan: dict[str, Any],
        skill_gaps: Optional[dict[str, list[str]]] = None,
    ) -> list[Employee]:
        """
        Analyzes an execution plan and dynamically recruits the necessary specialists
        for all phases and required skills (Requirement 7 & 12).
        """
        recruited: list[Employee] = []
        phases = plan.get("phases", [])
        needed_roles: dict[str, list[str]] = {}

        for phase in phases:
            for task in phase.get("tasks", []):
                req_role = task.get("role_required", "software-engineer")
                skills = task.get("skills_required", ["Python"])
                if req_role not in needed_roles:
                    needed_roles[req_role] = []
                for s in skills:
                    if s not in needed_roles[req_role]:
                        needed_roles[req_role].append(s)

        if skill_gaps is not None:
            needed_roles = skill_gaps
        elif not needed_roles:
            needed_roles = {
                "senior-backend-engineer": ["Python", "FastAPI", "System Design"],
                "qa-engineer": ["pytest", "Security Auditing", "Verification"],
            }

        for role_id, skills in needed_roles.items():
            role_title = role_id.replace("-", " ").title()
            dept = "Quality & Reliability" if "qa" in role_id.lower() or "security" in role_id.lower() else "Engineering"
            emp = await self.recruit_dynamic_employee(
                role_title=role_title,
                department=dept,
                role_id=role_id,
                initial_skills=skills,
            )
            recruited.append(emp)

        logger.info("Dynamically recruited workforce for execution plan", count=len(recruited))
        return recruited

    async def bootstrap_initial_workforce(self) -> list[Employee]:
        """
        Creates a dynamic backend/QA starting team for explicit local bootstrap.
        Names, employee identities, and personas are dynamically generated via
        generate_dynamic_identity(); no static or hardcoded staff profiles
        (such as 'Alex Morgan' or 'Maya Chen') are used. Repeated calls reuse
        only records created by this helper.
        """
        existing = await self.repo.list_all()
        bootstrapped = {
            employee.model_config_data.get("recruitment_origin"): employee
            for employee in existing
            if employee.model_config_data
            and employee.model_config_data.get("recruitment_origin", "").startswith("local-bootstrap:")
        }

        workforce: list[Employee] = []
        for role_id in ("senior-backend-engineer", "qa-engineer"):
            origin = f"local-bootstrap:{role_id}"
            employee = bootstrapped.get(origin)
            if employee is None:
                role_config = self._load_role_config(role_id) or {}
                employee = await self.recruit_dynamic_employee(
                    role_title=role_config.get("title", role_id.replace("-", " ").title()),
                    department=role_config.get("department", "Engineering"),
                    role_id=role_id,
                    recruitment_origin=origin,
                )
            workforce.append(employee)
        return workforce

