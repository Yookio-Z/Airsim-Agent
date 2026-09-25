"""Ground-station mission facade.

Thin delegation to GroundStationServices so the UI has one HTTP surface; the
mission logic itself lives in src/gcs. Moved verbatim out of runtime.py.
"""

from __future__ import annotations

from typing import Any


class GcsMixin:
    """Runtime methods for this domain; assembled into AgentRuntime in runtime.py."""


    def gcs_mission_get(self) -> dict[str, Any]:
        """Return the current local draft and mission progress."""
        mission = self.gcs.mission
        draft = mission.get_draft()
        progress = mission.progress()
        return {
            "ok": True,
            "draft": draft.to_dict() if draft else None,
            "progress": progress.to_dict(),
        }

    def gcs_mission_set(self, draft_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Replace the local mission draft with ``draft_data``."""
        from src.gcs.mission import MissionPlanDraft

        if not isinstance(draft_data, dict):
            return {"ok": False, "error": "draft payload must be an object"}
        try:
            draft = MissionPlanDraft.from_dict(draft_data)
        except Exception as e:
            return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.set_draft(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "本地任务草稿已更新" if result.ok else "本地任务草稿更新失败",
            {"items": len(draft.items), "result": result.to_dict()},
        )
        return {"ok": result.ok, "result": result.to_dict(), "draft": draft.to_dict()}

    def gcs_mission_download(self, expected_backend: str = "") -> dict[str, Any]:
        """Download the active vehicle mission into a local draft."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        draft = self.gcs.mission.download()
        progress = self.gcs.mission.progress()
        ok = draft is not None
        self._append_event(
            "info" if ok else "warning",
            "gcs.mission",
            "飞控任务已下载" if ok else "飞控任务下载失败或为空",
            {"items": len(draft.items) if draft else 0},
        )
        return {
            "ok": ok,
            "draft": draft.to_dict() if draft else None,
            "progress": progress.to_dict(),
        }

    def gcs_mission_upload(
        self,
        draft_data: dict[str, Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """Upload the current or provided draft to the active vehicle."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        from src.gcs.mission import MissionPlanDraft

        draft = None
        if isinstance(draft_data, dict) and draft_data:
            try:
                draft = MissionPlanDraft.from_dict(draft_data)
            except Exception as e:
                return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.upload(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "任务已上传至飞控" if result.ok else "任务上传失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_start(
        self,
        draft_data: dict[str, Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """Start the uploaded mission, optionally replacing/uploading the draft first."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        from src.gcs.mission import MissionPlanDraft

        draft = None
        if isinstance(draft_data, dict) and draft_data:
            try:
                draft = MissionPlanDraft.from_dict(draft_data)
            except Exception as e:
                return {"ok": False, "error": f"invalid mission draft: {e}"}
        result = self.gcs.mission.start(draft)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "任务已启动" if result.ok else "任务启动失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_start_multi(
        self,
        assignments: list[Any] | None = None,
        expected_backend: str = "",
    ) -> dict[str, Any]:
        """多机各自航线并发执行：每机 takeoff(如需) + 各自路径，非阻塞派发。"""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        if not isinstance(assignments, list):
            return {"ok": False, "error": "assignments must be a list"}
        clean = [entry for entry in assignments if isinstance(entry, dict)]
        result = self.gcs.mission.start_multi(clean)
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "多机任务已派发" if result.ok else "多机任务派发失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_clear(self, expected_backend: str = "") -> dict[str, Any]:
        """Clear the active vehicle mission and local draft."""
        mismatch = self._backend_mismatch(expected_backend)
        if mismatch:
            return mismatch
        result = self.gcs.mission.clear()
        self._append_event(
            "info" if result.ok else "warning",
            "gcs.mission",
            "飞控任务已清空" if result.ok else "飞控任务清空失败",
            result.to_dict(),
        )
        return {"ok": result.ok, "result": result.to_dict()}

    def gcs_mission_progress(self) -> dict[str, Any]:
        """Return mission execution progress."""
        progress = self.gcs.mission.progress()
        return {"ok": True, "progress": progress.to_dict()}
