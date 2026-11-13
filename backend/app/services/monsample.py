"""监控采样时间线业务规则：读数生成、幂等回调、告警确认与失败重采都收在这里。

一条（指标编号 + 采样批次）只对应一份记录：调度执行或质量校验完成后都会回调同一批次，
重复回调幂等忽略；采样失败的批次在原记录上继续，绝不用上一条旧读数顶替。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "monsample"
REQUIRED_FIELDS = ["指标编号", "采样批次", "来源类型", "来源编号"]
SOURCE_TYPES = ["调度执行", "质量校验"]
ALERT_STATUSES = ["无告警", "待确认", "已确认", "已忽略"]
ACTION_RULES = {"确认告警": "待确认", "忽略告警": "待确认", "重采失败批次": "失败"}

# 当前生效的阈值版本：回调未显式携带阈值快照时按此留档；历史读数始终保留当时的阈值，不回填。
CURRENT_THRESHOLD = {"阈值版本": "THR-v1.1", "阈值上界": 90.0, "阈值下界": 0.0}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _to_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


class SamplingService:
    """监控读数的生成与流转：同一批次幂等、告警待办可确认、失败批次可续采。"""

    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        rule: str | None = None,
        task: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._sorted(store.rows(MODULE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("采样批次", "")) or keyword in str(row.get("采样编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("告警状态") == status]
        if metric:
            rows = [row for row in rows if row.get("指标编号") == metric]
        if rule:
            rows = [row for row in rows if row.get("质量规则") == rule]
        if task:
            rows = [row for row in rows if row.get("调度任务") == task]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    @staticmethod
    def _sorted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: (str(row.get("采样时间", "")), int(row.get("id", 0))),
            reverse=True,
        )

    # ---------- 回调生成读数（幂等） ----------
    def record_callback(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        source_type = str(values.get("来源类型"))
        if source_type not in SOURCE_TYPES:
            return None, f"来源类型「{source_type}」不支持，只能是：{'、'.join(SOURCE_TYPES)}"
        metric = self._find_metric(str(values.get("指标编号")))
        if metric is None:
            return None, f"监控指标 {values.get('指标编号')} 不存在，无法生成可追溯读数"
        sample_state = str(values.get("采样状态") or "成功").strip() or "成功"
        if sample_state not in ("成功", "失败"):
            return None, "采样状态只能是 成功 或 失败"
        reading = _to_float(values.get("指标值"))
        failure_source = str(values.get("失败来源") or "").strip()
        if sample_state == "成功" and reading is None:
            return None, "成功读数必须携带本次指标值，不能用上一条旧读数顶替"
        if sample_state == "失败" and not failure_source:
            return None, "采样失败必须记录失败来源，方便从失败批次继续"

        existing = self._find_by_batch(str(metric["指标编号"]), str(values["采样批次"]))
        if existing is not None:
            if existing.get("采样状态") == "成功":
                return existing, f"批次 {existing['采样批次']} 已生成读数 {existing['采样编号']}，本次回调幂等忽略"
            if sample_state == "失败":
                existing["失败来源"] = failure_source
                existing["采样时间"] = str(values.get("采样时间") or _now())
                self._sync_flags(existing)
                return existing, f"批次 {existing['采样批次']} 仍失败，已在原记录上更新失败来源"
            # 失败批次恢复：在原记录上继续，不新增、不沿用旧读数
            self._apply_reading(existing, reading, values)
            self._sync_flags(existing)
            return existing, f"已从失败批次 {existing['采样批次']} 继续采样，原失败记录已更新为成功"

        entry = self._new_entry(metric, values)
        if sample_state == "失败":
            entry["采样状态"] = "失败"
            entry["失败来源"] = failure_source
            entry["越界结果"] = "采样失败"
            entry["告警状态"] = "无告警"
        else:
            self._apply_reading(entry, reading, values)
        self._sync_flags(entry)
        store.rows(MODULE).append(entry)
        return entry, f"监控读数 {entry['采样编号']} 已生成"

    # ---------- 告警确认 / 忽略 / 失败重采 ----------
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"监控读数 {entry_id} 不存在"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于监控采样可执行范围"
        if action in ("确认告警", "忽略告警"):
            if entry.get("告警状态") != "待确认":
                return None, "仅待确认状态的告警可以处理，已处理的告警保留当时阈值留档"
            handler = str(values.get("处理人") or "").strip()
            if not handler:
                return None, "确认或忽略告警必须填写处理人"
            entry["告警状态"] = "已确认" if action == "确认告警" else "已忽略"
            entry["处理人"] = handler
            entry["处理时间"] = _now()
            self._sync_flags(entry)
            result = "确认" if action == "确认告警" else "忽略"
            return entry, f"告警已{result}，处理人 {handler}；后续新批次越界仍会重新生成待办"
        # 重采失败批次：从失败批次继续，读数必须是本次新采
        if entry.get("采样状态") != "失败":
            return None, "仅采样失败的批次需要重采"
        fresh = _to_float(values.get("指标值"))
        if fresh is None:
            # 重新采样：以该批次阈值区间中位值作为全新的现场读数，绝不沿用上一条旧读数
            upper = float(entry.get("阈值上界") or 0)
            lower = float(entry.get("阈值下界") or 0)
            fresh = (upper + lower) / 2
        self._apply_reading(entry, fresh, values)
        self._sync_flags(entry)
        return entry, f"已从失败批次 {entry['采样批次']} 继续采样，读数 {entry['指标值']} 为本次新采，未沿用旧读数"

    # ---------- 跨模块追溯：监控页、质量页、调度页共用同一批次与阈值版本 ----------
    def trace_fields(
        self,
        *,
        metric: str | None = None,
        rule: str | None = None,
        task: str | None = None,
    ) -> dict[str, Any]:
        matched = [
            row for row in store.rows(MODULE)
            if (metric is None or row.get("指标编号") == metric)
            and (rule is None or row.get("质量规则") == rule)
            and (task is None or row.get("调度任务") == task)
        ]
        ordered = self._sorted(matched)
        latest = ordered[0] if ordered else None
        failed = next((row for row in ordered if row.get("采样状态") == "失败"), None)
        return {
            "最近采样批次": latest.get("采样批次") if latest else None,
            "采样阈值版本": latest.get("阈值版本") if latest else None,
            "采样失败来源": failed.get("失败来源") if failed else None,
        }

    # ---------- 内部工具 ----------
    @staticmethod
    def _find_metric(metric_code: str) -> dict[str, Any] | None:
        for row in store.rows("monitor"):
            if str(row.get("指标编号", "")) == metric_code:
                return row
        return None

    @staticmethod
    def _find_by_batch(metric_code: str, batch: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("指标编号") == metric_code and row.get("采样批次") == batch:
                return row
        return None

    def _latest(self, *, metric: str) -> dict[str, Any] | None:
        matched = [row for row in store.rows(MODULE) if row.get("指标编号") == metric]
        ordered = self._sorted(matched)
        return ordered[0] if ordered else None

    def _new_entry(self, metric: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        source_type = str(values["来源类型"])
        source_code = str(values["来源编号"])
        latest = self._latest(metric=str(metric["指标编号"]))
        upper = _to_float(values.get("阈值上界"))
        lower = _to_float(values.get("阈值下界"))
        return {
            "id": entry_id,
            "采样编号": f"SAMP-{entry_id:04d}",
            "指标编号": metric["指标编号"],
            "指标名称": metric.get("指标名称"),
            "监控对象": metric.get("监控对象"),
            "采样周期": str(values.get("采样周期") or metric.get("采样周期") or "每小时"),
            "采样批次": str(values["采样批次"]),
            "来源类型": source_type,
            "来源编号": source_code,
            # 质量页与调度页沿用同一采样批次：回调缺省时继承该指标最近一次读数的归属
            "调度任务": str(
                values.get("调度任务")
                or (source_code if source_type == "调度执行" else (latest or {}).get("调度任务") or "")
            ),
            "质量规则": str(
                values.get("质量规则")
                or (source_code if source_type == "质量校验" else (latest or {}).get("质量规则") or "")
            ),
            "指标值": None,
            "阈值版本": str(values.get("阈值版本") or CURRENT_THRESHOLD["阈值版本"]),
            "阈值上界": _fmt_number(upper) if upper is not None else _fmt_number(CURRENT_THRESHOLD["阈值上界"]),
            "阈值下界": _fmt_number(lower) if lower is not None else _fmt_number(CURRENT_THRESHOLD["阈值下界"]),
            "越界结果": "正常",
            "告警状态": "无告警",
            "处理人": None,
            "处理时间": None,
            "采样状态": "成功",
            "失败来源": None,
            "采样时间": str(values.get("采样时间") or _now()),
        }

    def _apply_reading(self, entry: dict[str, Any], reading: float, values: dict[str, Any]) -> None:
        upper = _to_float(values.get("阈值上界"))
        lower = _to_float(values.get("阈值下界"))
        if upper is not None:
            entry["阈值上界"] = _fmt_number(upper)
        if lower is not None:
            entry["阈值下界"] = _fmt_number(lower)
        if values.get("阈值版本"):
            entry["阈值版本"] = str(values["阈值版本"])
        breach = False
        if entry.get("阈值上界") is not None and reading > float(entry["阈值上界"]):
            breach = True
        if entry.get("阈值下界") is not None and reading < float(entry["阈值下界"]):
            breach = True
        entry["指标值"] = _fmt_number(reading)
        entry["越界结果"] = "越界" if breach else "正常"
        # 每个越界批次都是新的待办：即使上一批告警已确认，新批次越界仍重新生成待确认
        entry["告警状态"] = "待确认" if breach else "无告警"
        entry["处理人"] = None
        entry["处理时间"] = None
        entry["采样状态"] = "成功"
        entry["失败来源"] = None
        entry["采样时间"] = str(values.get("采样时间") or _now())

    @staticmethod
    def _sync_flags(entry: dict[str, Any]) -> None:
        entry["status"] = entry.get("告警状态", "无告警")
        entry["pending"] = entry.get("告警状态") == "待确认" or entry.get("采样状态") == "失败"
        entry["abnormal"] = entry.get("越界结果") in ("越界", "采样失败")
