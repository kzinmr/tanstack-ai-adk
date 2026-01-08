from __future__ import annotations

from google.adk.tools import FunctionTool

from ..deps import Deps
from ._common import _tool_result


def build_export_tool(deps: Deps) -> FunctionTool:
    async def export_csv(artifact_id: str) -> str | None:
        """
        Export a dataset as CSV file (executed on client side).

        This tool is executed in the browser (client-side).
        The client will receive the data reference and fetch the actual data
        from /api/data/{run_id}/{artifact_id} (optionally with mode=download).
        """
        if deps.artifact_store.get_metadata(deps.run_id, artifact_id) is None:
            return _tool_result(
                "エクスポート対象のデータが見つかりませんでした。"
                "直前にクエリを実行して結果を作成してから、もう一度CSV出力してください。",
                data={"success": False},
            )

        return None

    tool = FunctionTool(export_csv, require_confirmation=True)
    tool.is_long_running = True
    return tool
