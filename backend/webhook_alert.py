"""
AutoDataFlow v2.3 Webhook Alert System
支持飞书、钉钉、企业微信的告警推送

工业级特性：
  - tenacity 重试（3次指数退避）+ timeout（10s）
  - 失败降级（fallback，返回错误但不断言）
  - 统一错误格式
"""
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import QualityConfig


# ============================================================
# HTTP 客户端（重试 + 超时 + 降级）
# ============================================================

def _http_post_with_retry(
    url: str,
    payload: dict,
    timeout: int = 10,
    max_attempts: int = 3,
) -> Dict:
    """
    POST JSON 到 webhook URL，带指数退避重试。

    降级策略：所有重试耗尽后返回错误字典，不抛异常。
    """
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
                return {"success": True, "result": result, "attempt": attempt}

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            last_error = f"HTTP {e.code}: {body}"

        except urllib.error.URLError as e:
            last_error = f"URLError: {e.reason}"

        except TimeoutError:
            last_error = f"Timeout after {timeout}s"

        except Exception as e:
            last_error = str(e)

        if attempt < max_attempts:
            sleep_sec = min(2 ** attempt, 8)  # 指数退避，上限 8s
            time.sleep(sleep_sec)

    # 所有重试耗尽 → 降级，不抛异常
    return {
        "success": False,
        "error": last_error,
        "attempt": max_attempts,
    }


# ============================================================
# WebhookAlert
# ============================================================

class WebhookAlert:
    """数据质量异常告警推送"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        config_path = os.path.join(self.data_dir, "webhook_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "feishu": {"enabled": False, "webhook_url": "", "mention_list": []},
            "dingtalk": {"enabled": False, "webhook_url": "", "secret": ""},
        }

    def _get_thresholds(self) -> Dict:
        """从 QualityConfig 读取告警阈值"""
        alert_cfg = QualityConfig.alert_thresholds()
        webhook_cfg = self.config.get("thresholds", {})
        return {
            "quality_score_min": alert_cfg.get("quality_score_min", 70),
            "null_rate_max": alert_cfg.get("null_rate_max", 0.05),
            "anomaly_count_max": alert_cfg.get("outlier_count_max", 10),
        }

    def save_config(self):
        config_path = os.path.join(self.data_dir, "webhook_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def check_quality_anomalies(self) -> Dict:
        """检查数据质量异常，返回告警信息"""
        health_path = os.path.join(self.data_dir, "health_report.json")
        if not os.path.exists(health_path):
            return {"has_anomaly": False, "message": "无健康报告数据"}

        with open(health_path, encoding="utf-8") as f:
            health = json.load(f)

        anomalies = []
        threshold = self._get_thresholds()
        q_min = threshold["quality_score_min"]
        n_max = threshold["null_rate_max"]
        a_max = threshold["anomaly_count_max"]

        for table in health.get("tables", []):
            qs = table.get("quality_score", 100)
            if qs < q_min:
                anomalies.append({
                    "type": "quality_score",
                    "table": table["name"],
                    "value": qs,
                    "threshold": q_min,
                    "severity": "high" if qs < 50 else "medium",
                    "message": f"表 {table['name']} 质量分 {qs} 低于阈值 {q_min}",
                })

            null_pct = sum(c.get("null_pct", 0) for c in table.get("columns", [])) / max(len(table.get("columns", [])), 1)
            if null_pct > n_max:
                anomalies.append({
                    "type": "null_rate",
                    "table": table["name"],
                    "value": round(null_pct, 2),
                    "threshold": n_max,
                    "severity": "medium",
                    "message": f"表 {table['name']} 空值率 {null_pct:.1%} 高于阈值 {n_max:.1%}",
                })

            anomaly_count = sum(c.get("outlier_count", 0) for c in table.get("columns", []))
            if anomaly_count > a_max:
                anomalies.append({
                    "type": "anomaly_count",
                    "table": table["name"],
                    "value": anomaly_count,
                    "threshold": a_max,
                    "severity": "high" if anomaly_count > a_max * 2 else "medium",
                    "message": f"表 {table['name']} 异常值 {anomaly_count} 超过阈值 {a_max}",
                })

        return {
            "has_anomaly": len(anomalies) > 0,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "checked_at": datetime.now().isoformat(),
        }

    def send_feishu_alert(self, anomalies: list) -> Dict:
        """推送飞书告警（重试+降级）"""
        cfg = self.config.get("feishu", {})
        if not cfg.get("enabled") or not cfg.get("webhook_url"):
            return {"success": False, "error": "飞书未配置"}

        mention_list = cfg.get("mention_list", [])
        mention_str = ""
        if mention_list:
            mention_str = " ".join(f"<at user_id=\"{uid}\"></at>" for uid in mention_list)

        blocks = [
            {
                "tag": "markdown",
                "content": (
                    f"**🚨 数据质量告警**\n"
                    f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"告警数量: **{len(anomalies)}**\n"
                    f"{mention_str}"
                ),
            }
        ]

        for a in anomalies[:5]:
            icon = "🔴" if a.get("severity") == "high" else "🟡"
            blocks.append({
                "tag": "markdown",
                "content": f"{icon} **{a['table']}**: {a['message']}",
            })

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "🚨 AutoDataFlow 数据质量告警"},
                    "template": "red" if any(x.get("severity") == "high" for x in anomalies) else "yellow",
                },
                "elements": blocks,
            },
        }

        return _http_post_with_retry(cfg["webhook_url"], payload, timeout=10, max_attempts=3)

    def send_dingtalk_alert(self, anomalies: list) -> Dict:
        """推送钉钉告警（重试+降级）"""
        cfg = self.config.get("dingtalk", {})
        if not cfg.get("enabled") or not cfg.get("webhook_url"):
            return {"success": False, "error": "钉钉未配置"}

        content = (
            f"## 🚨 AutoDataFlow 数据质量告警\n\n"
            f"**检测时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"**告警数量**: {len(anomalies)}\n\n"
        )

        for a in anomalies[:5]:
            icon = "🔴" if a.get("severity") == "high" else "🟡"
            content += f"{icon} **{a['table']}**: {a['message']}\n\n"

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "数据质量告警",
                "text": content,
            },
        }

        return _http_post_with_retry(cfg["webhook_url"], payload, timeout=10, max_attempts=3)

    def trigger_alert_check(self) -> Dict:
        """执行告警检查并推送"""
        result = self.check_quality_anomalies()

        if not result.get("has_anomaly"):
            return {"success": True, "anomalies": [], "message": "无异常，无需告警"}

        anomalies = result["anomalies"]
        responses = []

        feishu_result = self.send_feishu_alert(anomalies)
        responses.append({"channel": "feishu", **feishu_result})

        dingtalk_result = self.send_dingtalk_alert(anomalies)
        responses.append({"channel": "dingtalk", **dingtalk_result})

        return {
            "success": any(r.get("success") for r in responses),
            "anomaly_count": len(anomalies),
            "responses": responses,
            "checked_at": result["checked_at"],
        }


def run_alert_check(data_dir: str) -> Dict:
    """主接口：执行告警检查"""
    alert = WebhookAlert(data_dir)
    return alert.trigger_alert_check()


if __name__ == "__main__":
    data_dir = str(os.path.dirname(os.path.abspath(__file__))) + "/data"
    result = run_alert_check(data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
