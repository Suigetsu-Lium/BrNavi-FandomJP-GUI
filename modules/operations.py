"""Non-interactive application operations shared by the GUI and CLI."""

from dataclasses import dataclass

from modules.extractor import extract_levels
from modules.network import process_backrooms_data
from modules.operation_context import OperationContext
from modules.update_database import execute_update


@dataclass(frozen=True)
class OperationResult:
    success: bool
    message: str
    details: object = None


def run_maintenance_operation(task_name, options=None, context=None):
    options = options or {}
    context = context or OperationContext()

    if task_name == "update_master":
        mode = options.get("mode")
        if mode not in (1, 2):
            raise ValueError("データベース更新モードは1または2を指定してください。")
        success = execute_update(mode, context=context)
        return OperationResult(
            success=bool(success),
            message="既存データベースを更新しました。"
            if success else "既存データベースの更新に失敗しました。",
        )

    if task_name == "extract_info":
        rows = extract_levels(interactive=False, context=context)
        success = rows is not None
        return OperationResult(
            success=success,
            message="情報データベースを更新しました。"
            if success else "情報データベースを更新できませんでした。",
            details={"extracted": len(rows)} if success else None,
        )

    if task_name == "update_network":
        success = process_backrooms_data(context=context)
        return OperationResult(
            success=bool(success),
            message="ネットワークを更新しました。"
            if success else "ネットワークを更新できませんでした。",
        )

    raise ValueError(f"未対応の保守タスクです: {task_name}")
