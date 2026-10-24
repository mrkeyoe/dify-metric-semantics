"""Example registry used by the demo CLI, the tests and the README walkthrough.

These are *illustrative* definitions, including a deliberate conflict between
the two "活跃用户" definitions.
"""

from __future__ import annotations

from typing import List

from .models import MetricDefinition


def example_definitions() -> List[MetricDefinition]:
    return [
        MetricDefinition.create(
            name="活跃用户",
            metric_id="active_user.7d_login",
            aliases=["活跃用户", "日活", "DAU", "active user"],
            definition_text="过去 7 天内至少登录过一次的去重用户数（按 user_id 去重，只统计登录行为）",
            source_tables=["dwd.user_login_log"],
            sql_fragment=(
                "SELECT COUNT(DISTINCT user_id) FROM dwd.user_login_log "
                "WHERE login_time >= CURRENT_DATE - INTERVAL '7 days'"
            ),
            owner="增长组",
            confidence=0.60,
            metadata={"source": "demo-seed"},
        ),
        MetricDefinition.create(
            name="活跃用户",
            metric_id="active_user.30d_order",
            aliases=["活跃用户", "活跃买家", "active buyer"],
            definition_text="过去 30 天内至少完成一次支付成功订单的去重用户数（按 buyer_id 去重，只统计已支付订单）",
            source_tables=["dwd.order_detail"],
            sql_fragment=(
                "SELECT COUNT(DISTINCT buyer_id) FROM dwd.order_detail "
                "WHERE order_time >= CURRENT_DATE - INTERVAL '30 days' AND pay_status = 'paid'"
            ),
            owner="交易组",
            confidence=0.55,
            metadata={"source": "demo-seed"},
        ),
        MetricDefinition.create(
            name="成交金额",
            metric_id="gmv.paid_order_amount",
            aliases=["GMV", "成交额", "交易额"],
            definition_text="统计周期内已支付订单的实付金额之和（不含退款、不含运费）",
            source_tables=["dwd.order_detail"],
            sql_fragment="SELECT SUM(pay_amount) FROM dwd.order_detail WHERE pay_status = 'paid'",
            owner="交易组",
            confidence=0.70,
            metadata={"source": "demo-seed"},
        ),
    ]