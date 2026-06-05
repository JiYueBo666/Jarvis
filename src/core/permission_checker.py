"""Runtime permission decisions for tool execution."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PermissionDecision:
    decision: str
    reason: str
    security_event_type: str = ""

    @classmethod
    def allow(cls, reason):
        return cls("allow", reason)

    @classmethod
    def deny(cls, reason, security_event_type=""):
        return cls("deny", reason, security_event_type)

    @property
    def allowed(self):
        return self.decision == "allow"


class PermissionChecker:
    def __init__(self, runtime):
        self.runtime = runtime

    def check(self, tool, args):
        """
              AI 想执行工具 → 开始权限检查
        1. 检查【工具配置文件】是否允许这个工具
        2. 如果是【计划模式】→ 走计划特殊规则
        3. 如果是【写文件】→ 检查是否在允许的目录内
        4. 如果是【只读工具】→ 直接允许
        5. 如果系统是【只读模式】→ 直接拒绝
        6. 如果是【自动批准】→ 直接允许
        7. 如果是【永不批准】→ 直接拒绝
        8. 否则 → 弹出用户确认

        """
        args = args or {}
        profile = self.runtime.active_tool_profile
        if not profile.allows(tool.name):
            if profile.name == "plan":
                return PermissionDecision.deny(
                    "plan_mode_tool_not_allowed", "plan_mode_write_guard"
                )
            return PermissionDecision.deny("tool_not_allowed")

        if self.runtime.runtime_mode == "plan":
            return self._check_plan(tool, args)

        if tool.name in {"write_file", "patch_file"} and getattr(
            self.runtime, "write_scope", ()
        ):
            return self._check_write_scope(tool, args)
        if tool.read_only:
            return PermissionDecision.allow("read_only")
        if self.runtime.read_only:
            return PermissionDecision.deny("approval_denied", "read_only_block")
        if self.runtime.approval_policy == "auto":
            return PermissionDecision.allow("approval_auto")
        if self.runtime.approval_policy == "never":
            return PermissionDecision.deny("approval_denied", "approval_denied")
        if self.runtime.approve(tool.name, args):
            return PermissionDecision.allow("approval_prompt")
        return PermissionDecision.deny("approval_denied", "approval_denied")

    def _check_plan(self, tool, args):
        if tool.read_only:
            return PermissionDecision.allow(reason="plan_read_only")
        if tool.name not in {"write_file", "patch_file"}:
            return PermissionDecision.deny(
                reason="plan_mode_tool_not_allowed",
                security_event_type="plan_mode_write_guard",
            )
        requested = self.runtime.path(args.get("path", ""))
        active = self.runtime.path(self.runtime.plan_mode.plan_path)
        """
        只能改当前的plan.md文件本身
        """
        if Path(requested) != Path(active):
            return PermissionDecision.deny(
                reason="plan_mode_path_mismatch",
                security_event_type="plan_mode_write_guard",
            )
        return PermissionDecision.allow("plan_artifact_write")

    def _check_write_scope(self, tool, args):
        requested = self.runtime.path(args.get("path", ""))
        for raw_scope in self.runtime.write_scope:
            scope = self.runtime.path(raw_scope)
            try:
                requested.relative_to(scope)
                return PermissionDecision.allow("write_scope")
            except ValueError:
                continue
        return PermissionDecision.deny("write_scope_mismatch", "write_scope_guard")
