#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AutoDataFlow v2.2 Schema 变更检测器
=====================================
核心能力：
  1. Schema 快照：每次分析后保存当前所有表的 DDL 快照
  2. 变更对比：对比新旧快照，检测新增/删除/类型变更
  3. 变更历史：持久化变更记录到 schema_changes.json
  4. 告警触发：检测到破坏性变更（删列/类型收窄）立即告警
"""

import json
import sqlite3
import time
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ColumnDef:
    name: str
    dtype: str

    def to_dict(self) -> dict:
        return asdict(self)

    def __hash__(self):
        return hash((self.name, self.dtype))


@dataclass
class TableSchema:
    table_name: str
    columns: List[ColumnDef]
    row_count: int
    ddl_hash: str  # 整表DDL的MD5，用于快速判断是否变化

    def to_dict(self) -> dict:
        return {
            "table_name": self.table_name,
            "columns": [c.to_dict() for c in self.columns],
            "row_count": self.row_count,
            "ddl_hash": self.ddl_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TableSchema":
        return cls(
            table_name=d["table_name"],
            columns=[ColumnDef(**c) for c in d["columns"]],
            row_count=d["row_count"],
            ddl_hash=d["ddl_hash"],
        )


@dataclass
class SchemaChange:
    timestamp: str
    change_type: str        # "added" | "removed" | "type_changed" | "column_added" | "column_removed" | "dtype_changed"
    table: str
    field: str
    description: str
    severity: str           # "breaking" | "warning" | "info"
    impact: str

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Schema 快照管理器
# ============================================================

class SchemaSnapshot:
    """
    管理 Schema 快照的持久化和变更检测。
    快照文件：data/schema_snapshot.json
    变更记录：data/schema_changes.json
    """

    SNAPSHOT_FILE = "schema_snapshot.json"
    CHANGES_FILE = "schema_changes.json"

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.data_dir = self.db_path.parent
        self.snapshot_file = self.data_dir / self.SNAPSHOT_FILE
        self.changes_file = self.data_dir / self.CHANGES_FILE

    # ---- 快照读写 ----

    def _read_snapshot(self) -> Dict[str, TableSchema]:
        """读取上一次快照（表名 → Schema）"""
        if not self.snapshot_file.exists():
            return {}
        try:
            data = json.loads(self.snapshot_file.read_text(encoding="utf-8"))
            return {k: TableSchema.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, KeyError):
            return {}

    def _write_snapshot(self, schemas: Dict[str, TableSchema]):
        """写入当前快照"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in schemas.items()}
        self.snapshot_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_changes(self) -> List[SchemaChange]:
        """读取历史变更记录"""
        if not self.changes_file.exists():
            return []
        try:
            data = json.loads(self.changes_file.read_text(encoding="utf-8"))
            out: List[SchemaChange] = []
            for c in data.get("changes", []):
                try:
                    out.append(SchemaChange(**c))
                except TypeError:
                    # Backward-compat: legacy records may omit newer fields like 'severity'
                    payload = dict(c)
                    payload.setdefault("severity", "info")
                    out.append(SchemaChange(**payload))
            return out
        except (json.JSONDecodeError, KeyError):
            return []

    def _write_changes(self, changes: List[SchemaChange]):
        """追加写入变更记录"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_changes": len(changes),
            "changes": [c.to_dict() for c in changes],
        }
        self.changes_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- Schema 采集 ----

    def _build_ddl(self, table_name: str, columns: List[ColumnDef]) -> str:
        """生成 CREATE TABLE DDL（用于计算 hash）"""
        col_parts = [f'"{c.name}" {c.dtype}' for c in columns]
        return f'CREATE TABLE "{table_name}" ({", ".join(col_parts)})'

    def _collect_current_schema(self) -> Dict[str, TableSchema]:
        """从 SQLite 数据库采集当前所有表的 Schema"""
        schemas = {}
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = cursor.fetchall()

        for (table_name,) in tables:
            # 采集列信息
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            rows = cursor.fetchall()
            # PRAGMA returns: (cid, name, type, notnull, dflt_value, pk)
            columns = [ColumnDef(name=r[1], dtype=r[2] or "TEXT") for r in rows]

            # 行数
            cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            row_count = cursor.fetchone()[0]

            ddl = self._build_ddl(table_name, columns)
            ddl_hash = hashlib.md5(ddl.encode("utf-8")).hexdigest()

            schemas[table_name] = TableSchema(
                table_name=table_name,
                columns=columns,
                row_count=row_count,
                ddl_hash=ddl_hash,
            )

        conn.close()
        return schemas

    # ---- 变更检测核心 ----

    def detect_changes(self) -> List[SchemaChange]:
        """
        对比新旧快照，返回变更列表。
        同时更新快照文件。
        """
        old_schemas = self._read_snapshot()
        new_schemas = self._collect_current_schema()
        old_names = set(old_schemas.keys())
        new_names = set(new_schemas.keys())

        changes: List[SchemaChange] = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 检测新增的表
        for table_name in sorted(new_names - old_names):
            schema = new_schemas[table_name]
            for col in schema.columns:
                changes.append(SchemaChange(
                    timestamp=timestamp,
                    change_type="added",
                    table=table_name,
                    field=col.name,
                    description=f"新增表 {table_name}，含字段 {col.name} ({col.dtype})",
                    severity="info",
                    impact="新表上线，建议确认 ETL 是否已包含该表",
                ))

        # 2. 检测删除的表（破坏性变更）
        for table_name in sorted(old_names - new_names):
            schema = old_schemas[table_name]
            for col in schema.columns:
                changes.append(SchemaChange(
                    timestamp=timestamp,
                    change_type="removed",
                    table=table_name,
                    field=col.name,
                    description=f"表 {table_name} 被删除（含字段 {col.name}）",
                    severity="breaking",
                    impact="表删除导致历史查询断裂，需紧急回滚或数据迁移",
                ))

        # 3. 检测表内变更
        for table_name in sorted(old_names & new_names):
            old_schema = old_schemas[table_name]
            new_schema = new_schemas[table_name]

            # 快速判断：hash 相同则无变更
            if old_schema.ddl_hash == new_schema.ddl_hash:
                continue

            old_cols = {c.name: c for c in old_schema.columns}
            new_cols = {c.name: c for c in new_schema.columns}
            old_names_set = set(old_cols.keys())
            new_names_set = set(new_cols.keys())

            # 3a. 新增列
            for col_name in sorted(new_names_set - old_names_set):
                col = new_cols[col_name]
                changes.append(SchemaChange(
                    timestamp=timestamp,
                    change_type="column_added",
                    table=table_name,
                    field=col_name,
                    description=f"表 {table_name} 新增字段 {col_name} ({col.dtype})",
                    severity="info",
                    impact="新增字段默认识别为空，需确认上游数据源",
                ))

            # 3b. 删除列（破坏性）
            for col_name in sorted(old_names_set - new_names_set):
                col = old_cols[col_name]
                changes.append(SchemaChange(
                    timestamp=timestamp,
                    change_type="column_removed",
                    table=table_name,
                    field=col_name,
                    description=f"表 {table_name} 删除字段 {col_name} ({col.dtype})",
                    severity="breaking",
                    impact="删除字段会导致下游查询报错，需紧急回滚",
                ))

            # 3c. 列类型变更（警告）
            for col_name in sorted(old_names_set & new_names_set):
                old_col = old_cols[col_name]
                new_col = new_cols[col_name]
                if old_col.dtype != new_col.dtype:
                    # 判断是否破坏性变更（字符串变窄、数值精度降低等）
                    breaking = self._is_breaking_type_change(old_col.dtype, new_col.dtype)
                    changes.append(SchemaChange(
                        timestamp=timestamp,
                        change_type="dtype_changed",
                        table=table_name,
                        field=col_name,
                        description=f"表 {table_name} 字段 {col_name} 类型变更：{old_col.dtype} → {new_col.dtype}",
                        severity="breaking" if breaking else "warning",
                        impact=(
                            "类型收窄可能导致数据截断或报错，建议使用 ALTER TABLE 扩展类型"
                            if breaking else
                            "类型变更可能影响查询结果精度，建议验证历史数据兼容性"
                        ),
                    ))

        # 写回快照和变更记录
        self._write_snapshot(new_schemas)

        if changes:
            old_changes = self._read_changes()
            self._write_changes(changes + old_changes)  # 新变更在前

        return changes

    def _is_breaking_type_change(self, old_dtype: str, new_dtype: str) -> bool:
        """判断类型变更是破坏性的（数据可能丢失）"""
        # 数值精度降低
        narrowing = {
            "DOUBLE": ["REAL", "INTEGER", "TEXT"],
            "REAL": ["INTEGER", "TEXT"],
            "INTEGER": ["TEXT"],
            "TEXT": [],  # TEXT 变其他通常安全
        }
        old_upper = old_dtype.upper().split("(")[0]
        new_upper = new_dtype.upper().split("(")[0]
        if old_upper in narrowing and new_upper in narrowing[old_upper]:
            return True
        return False

    # ---- API 友好输出 ----

    def get_change_summary(self) -> Dict[str, Any]:
        """返回变更汇总（供 API 端点使用）"""
        changes = self._read_changes()
        breaking = [c for c in changes if c.severity == "breaking"]
        warning = [c for c in changes if c.severity == "warning"]
        info = [c for c in changes if c.severity == "info"]
        return {
            "total": len(changes),
            "breaking_count": len(breaking),
            "warning_count": len(warning),
            "info_count": len(info),
            "changes": changes[:50],  # 最近50条
            "has_breaking": len(breaking) > 0,
        }

    def get_timeline(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回按时间线组织的变更（用于前端展示）"""
        changes = self._read_changes()[:limit]
        timeline: Dict[str, List[Dict]] = {}
        for c in changes:
            day = c.timestamp.split(" ")[0]
            if day not in timeline:
                timeline[day] = []
            timeline[day].append(c.to_dict())
        return [{"date": day, "changes": items} for day, items in timeline.items()]


# ============================================================
# 入口（验证）
# =========================================================

if __name__ == "__main__":
    db = Path(__file__).parent / "data" / "warehouse.db"
    detector = SchemaSnapshot(str(db))
    changes = detector.detect_changes()
    print(f"\n检测到 {len(changes)} 项 Schema 变更：")
    for c in changes:
        print(f"  [{c.severity}] {c.table}.{c.field} — {c.description}")
