import os
import uuid
from typing import Any, Optional

import yaml

from neural.config import get_settings
from neural.constants import SkillLevel
from neural.db.models import Employee
from neural.db.repositories import EmployeeRepository
from neural.logger import get_logger

logger = get_logger("ancos.employees.factory")


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
        initial_skills: Optional[list[str]] = None
    ) -> Employee:
        role_cfg = self._load_role_config(role_id) if role_id else None

        effective_title = title or (role_cfg.get("title") if role_cfg else "Software Engineer")
        effective_dept = department or (role_cfg.get("department") if role_cfg else "Engineering")

        employee_id = f"ANC-EMP-{uuid.uuid4().hex[:4].upper()}"
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

        model_config_data = {}
        if role_cfg:
            model_config_data = {
                "preferred_model_tier": role_cfg.get("preferred_model_tier", "coding"),
                "container_template": role_cfg.get("container_template", "employee-swe")
            }

        # Ensure workspace directory exists on host
        workspace_base = os.path.abspath(self.settings.EMPLOYEE_WORKSPACE_BASE)
        os.makedirs(os.path.join(workspace_base, employee_id), exist_ok=True)

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
            performance_summary={"completed_tasks": 0, "verified_skills": []}
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
            emp_name = name or f"Agent {matched_role['title'].split()[0]}"
            return await self.hire_employee(
                name=emp_name,
                title=title,
                department=dept,
                role_id=role_id
            )

        # Generic recruit fallback
        emp_name = name or f"Specialist {skill_name}"
        return await self.hire_employee(
            name=emp_name,
            title=f"{skill_name} Engineer",
            department="Engineering",
            initial_skills=[skill_name, "Software Engineering"]
        )

    async def recruit_for_role(self, role_id: str, name: Optional[str] = None) -> Employee:
        """Recruits an employee by role identifier."""
        role_cfg = self._load_role_config(role_id)
        title = role_cfg.get("title", role_id.replace("-", " ").title()) if role_cfg else role_id.replace("-", " ").title()
        dept = role_cfg.get("department", "Engineering") if role_cfg else "Engineering"
        emp_name = name or f"Assigned {title}"
        return await self.hire_employee(
            name=emp_name,
            title=title,
            department=dept,
            role_id=role_id
        )

    async def bootstrap_initial_workforce(self) -> list[Employee]:
        """
        Recruits initial Phase 1 workforce as defined in PLAN.md:
        1. Senior Backend Engineer (Alex Morgan)
        2. QA & Verification Engineer (Maya Chen)
        Idempotent: returns existing employees if already created.
        """
        existing = await self.repo.list_all()
        existing_names = {e.name: e for e in existing}

        senior_backend = existing_names.get("Alex Morgan")
        if not senior_backend:
            senior_backend = await self.hire_employee(
                name="Alex Morgan",
                title="Senior Backend Engineer",
                department="Engineering",
                role_id="senior-backend-engineer",
                personality="Rigorous, architecture-focused, and highly pragmatic",
                communication_style="Direct, concise, and code-grounded",
                initial_skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "System Design"]
            )

        qa_engineer = existing_names.get("Maya Chen")
        if not qa_engineer:
            qa_engineer = await self.hire_employee(
                name="Maya Chen",
                title="Quality Assurance & Verification Engineer",
                department="Quality & Reliability",
                role_id="qa-engineer",
                personality="Adversarial, skeptical, thorough, and precision-driven",
                communication_style="Evidence-based, receipt-driven, and unambiguous",
                initial_skills=["Python", "pytest", "Playwright", "API Testing", "Security Auditing"]
            )

        return [senior_backend, qa_engineer]

