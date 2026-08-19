from __future__ import annotations

import re

from cam_automation.models import SafetyAssessment


class PowerMillProfile:
    """PowerMill-specific command vocabulary and conservative safety policy."""

    name = "powermill"

    _blocked_rules = (
        (re.compile(r"^(DELETE|REMOVE|ERASE)\b", re.IGNORECASE), "destructive entity command"),
        (re.compile(r"^(QUIT|EXIT)\b", re.IGNORECASE), "application termination"),
        (
            re.compile(r"^PROJECT\s+(CLOSE|RESET|DELETE)\b", re.IGNORECASE),
            "project state destruction",
        ),
        (re.compile(r"^(SYSTEM|SHELL|PROCESS)\b", re.IGNORECASE), "external process execution"),
        (re.compile(r"^MACRO\s+(RUN|EXECUTE?)\b", re.IGNORECASE), "nested macro execution"),
    )
    _review_rules = (
        (re.compile(r"^(EXPORT|WRITE|POSTPROCESS)\b", re.IGNORECASE), "writes external output"),
        (
            re.compile(r"^NCPROGRAM\s+(WRITE|POSTPROCESS)\b", re.IGNORECASE),
            "creates machine output",
        ),
        (
            re.compile(r"^PROJECT\s+(SAVE|ARCHIVE)\b", re.IGNORECASE),
            "writes project data",
        ),
        (re.compile(r"^SAVE\b", re.IGNORECASE), "writes project or external data"),
    )
    _two_word_operations = {
        ("ACTIVATE", "TOOL"),
        ("ACTIVATE", "TOOLPATH"),
        ("CALCULATE", "TOOLPATH"),
        ("CREATE", "BOUNDARY"),
        ("CREATE", "NCPROGRAM"),
        ("CREATE", "TOOL"),
        ("CREATE", "TOOLPATH"),
        ("DIALOGS", "MESSAGE"),
        ("EDIT", "BLOCK"),
        ("EDIT", "NCPROGRAM"),
        ("EDIT", "TOOL"),
        ("EDIT", "TOOLPATH"),
        ("IMPORT", "MODEL"),
        ("NCPROGRAM", "WRITE"),
        ("PROJECT", "ARCHIVE"),
        ("PROJECT", "CLOSE"),
        ("PROJECT", "RESET"),
        ("PROJECT", "SAVE"),
        ("SIZE", "MODEL"),
        ("SIZE", "TOOLPATH"),
    }
    _canonical_actions = {
        "ACTIVATE TOOL": "cam.tool.activate",
        "ACTIVATE TOOLPATH": "cam.toolpath.activate",
        "CALCULATE TOOLPATH": "cam.toolpath.calculate",
        "CREATE BOUNDARY": "cam.boundary.create",
        "CREATE NCPROGRAM": "cam.nc_program.create",
        "CREATE TOOL": "cam.tool.create",
        "CREATE TOOLPATH": "cam.toolpath.create",
        "DIALOGS MESSAGE": "powermill.dialogs.message",
        "EDIT BLOCK": "cam.stock.edit",
        "EDIT NCPROGRAM": "cam.nc_program.edit",
        "EDIT TOOL": "cam.tool.edit",
        "EDIT TOOLPATH": "cam.toolpath.edit",
        "ECHO": "powermill.command_channel.configure",
        "EXPORT": "cam.data.export",
        "IMPORT MODEL": "cam.model.import",
        "NCPROGRAM WRITE": "cam.nc_program.write",
        "PRINT": "powermill.expression.query",
        "PROJECT ARCHIVE": "cam.project.archive",
        "PROJECT CLOSE": "powermill.project.close",
        "PROJECT RESET": "powermill.project.reset",
        "PROJECT SAVE": "cam.project.save",
        "SIZE MODEL": "cam.model.bounds.query",
        "SIZE TOOLPATH": "cam.toolpath.bounds.query",
    }
    _system_operations = {
        "DIALOGS MESSAGE",
        "ECHO",
        "PRINT",
    }
    _mode_aliases = {
        "api": "automation",
        "auto": "automation",
        "automation": "automation",
        "background": "system",
        "internal": "system",
        "macro": "automation",
        "manual": "manual",
        "operator": "manual",
        "recorder": "manual",
        "replay": "automation",
        "script": "automation",
        "system": "system",
        "ui": "manual",
        "user": "manual",
    }

    def assess(self, command: str) -> SafetyAssessment:
        for pattern, reason in self._blocked_rules:
            if pattern.search(command):
                return SafetyAssessment("blocked", (reason,))
        for pattern, reason in self._review_rules:
            if pattern.search(command):
                return SafetyAssessment("review", (reason,))
        return SafetyAssessment("safe")

    def operation(self, command: str) -> str:
        tokens = re.findall(r"[A-Za-z_]+", command)
        if not tokens:
            return "UNKNOWN"
        upper = [token.upper() for token in tokens]
        if len(upper) >= 2 and tuple(upper[:2]) in self._two_word_operations:
            return " ".join(upper[:2])
        return upper[0]

    def action(self, command: str) -> str:
        operation = self.operation(command)
        if operation in self._canonical_actions:
            return self._canonical_actions[operation]
        slug = re.sub(r"[^a-z0-9]+", "_", operation.lower()).strip("_") or "unknown"
        return f"powermill.command.{slug}"

    def category(self, command: str) -> str:
        action = self.action(command)
        parts = action.split(".")
        return parts[1] if len(parts) > 2 else "command"

    def mode(
        self,
        command: str,
        *,
        source: str,
        prompted: bool = False,
        explicit: str | None = None,
    ) -> str:
        if explicit:
            normalized = self._mode_aliases.get(explicit.strip().lower())
            if normalized:
                return normalized
        if self.operation(command) in self._system_operations:
            return "system"
        if prompted:
            return "manual"
        normalized_source = source.strip().lower()
        for token, mode in self._mode_aliases.items():
            if token in normalized_source:
                return mode
        return "automation"
