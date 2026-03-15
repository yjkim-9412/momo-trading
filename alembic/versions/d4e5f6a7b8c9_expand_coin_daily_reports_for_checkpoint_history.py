"""expand coin daily reports for checkpoint history

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-03-15 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("coin_daily_reports", schema=None) as batch_op:
        batch_op.drop_constraint("uq_coin_daily_reports_report_date", type_="unique")
        batch_op.add_column(
            sa.Column(
                "report_source",
                sa.String(length=30),
                nullable=False,
                server_default="AUTO_PRE_CYCLE",
            )
        )
        batch_op.add_column(sa.Column("trigger_reason", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("applied_cycle_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("period_started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("period_ended_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_coin_daily_reports_report_source", ["report_source"], unique=False)
        batch_op.create_index("ix_coin_daily_reports_applied_cycle_id", ["applied_cycle_id"], unique=False)
        batch_op.create_index("ix_coin_daily_reports_period_started_at", ["period_started_at"], unique=False)
        batch_op.create_index("ix_coin_daily_reports_period_ended_at", ["period_ended_at"], unique=False)

    conn = op.get_bind()

    conn.execute(
        sa.text(
            """
            UPDATE coin_daily_reports
            SET report_source = COALESCE(NULLIF(report_source, ''), 'AUTO_PRE_CYCLE'),
                period_started_at = COALESCE(period_started_at, created_at),
                period_ended_at = COALESCE(period_ended_at, created_at)
            """
        )
    )

    daily_reports = sa.table(
        "daily_reports",
        sa.column("id", sa.String(length=36)),
        sa.column("market_scope", sa.String(length=10)),
        sa.column("report_date", sa.Date()),
        sa.column("total_cycles", sa.Integer()),
        sa.column("total_analyses", sa.Integer()),
        sa.column("total_recommendations", sa.Integer()),
        sa.column("total_orders", sa.Integer()),
        sa.column("buy_count", sa.Integer()),
        sa.column("sell_count", sa.Integer()),
        sa.column("win_count", sa.Integer()),
        sa.column("loss_count", sa.Integer()),
        sa.column("total_pnl", sa.Float()),
        sa.column("unrealized_pnl", sa.Float()),
        sa.column("open_position_count", sa.Integer()),
        sa.column("market_summary", sa.Text()),
        sa.column("performance_review", sa.Text()),
        sa.column("lessons_learned", sa.Text()),
        sa.column("next_day_plan", sa.Text()),
        sa.column("top_picks", sa.Text()),
        sa.column("strategy_stats", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    coin_daily_reports = sa.table(
        "coin_daily_reports",
        sa.column("id", sa.String(length=36)),
        sa.column("report_date", sa.Date()),
        sa.column("report_source", sa.String(length=30)),
        sa.column("trigger_reason", sa.String(length=50)),
        sa.column("applied_cycle_id", sa.String(length=36)),
        sa.column("period_started_at", sa.DateTime()),
        sa.column("period_ended_at", sa.DateTime()),
        sa.column("total_cycles", sa.Integer()),
        sa.column("total_analyses", sa.Integer()),
        sa.column("total_recommendations", sa.Integer()),
        sa.column("total_orders", sa.Integer()),
        sa.column("buy_count", sa.Integer()),
        sa.column("sell_count", sa.Integer()),
        sa.column("win_count", sa.Integer()),
        sa.column("loss_count", sa.Integer()),
        sa.column("total_pnl", sa.Float()),
        sa.column("unrealized_pnl", sa.Float()),
        sa.column("open_position_count", sa.Integer()),
        sa.column("total_24h_volume", sa.Float()),
        sa.column("btc_dominance", sa.Float()),
        sa.column("market_regime", sa.String(length=20)),
        sa.column("market_summary", sa.Text()),
        sa.column("performance_review", sa.Text()),
        sa.column("lessons_learned", sa.Text()),
        sa.column("next_day_plan", sa.Text()),
        sa.column("top_picks", sa.Text()),
        sa.column("strategy_stats", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )

    existing_dates = {
        row.report_date
        for row in conn.execute(sa.select(coin_daily_reports.c.report_date)).all()
    }
    legacy_reports = conn.execute(
        sa.select(daily_reports).where(daily_reports.c.market_scope == "CRYPTO")
    ).mappings().all()
    for report in legacy_reports:
        report_date = report["report_date"]
        if report_date in existing_dates:
            continue
        conn.execute(
            sa.insert(coin_daily_reports).values(
                id=report["id"],
                report_date=report_date,
                report_source="AUTO_PRE_CYCLE",
                trigger_reason="legacy_daily_report_backfill",
                applied_cycle_id=None,
                period_started_at=report["created_at"],
                period_ended_at=report["created_at"],
                total_cycles=report["total_cycles"],
                total_analyses=report["total_analyses"],
                total_recommendations=report["total_recommendations"],
                total_orders=report["total_orders"],
                buy_count=report["buy_count"],
                sell_count=report["sell_count"],
                win_count=report["win_count"],
                loss_count=report["loss_count"],
                total_pnl=report["total_pnl"],
                unrealized_pnl=report["unrealized_pnl"],
                open_position_count=report["open_position_count"],
                total_24h_volume=0.0,
                btc_dominance=None,
                market_regime="",
                market_summary=report["market_summary"],
                performance_review=report["performance_review"],
                lessons_learned=report["lessons_learned"],
                next_day_plan=report["next_day_plan"],
                top_picks=report["top_picks"],
                strategy_stats=report["strategy_stats"],
                created_at=report["created_at"],
                updated_at=report["updated_at"],
            )
        )
        existing_dates.add(report_date)

    with op.batch_alter_table("coin_daily_reports", schema=None) as batch_op:
        batch_op.alter_column("report_source", server_default=None)


def downgrade() -> None:
    conn = op.get_bind()

    coin_daily_reports = sa.table(
        "coin_daily_reports",
        sa.column("id", sa.String(length=36)),
        sa.column("report_date", sa.Date()),
        sa.column("period_ended_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
    )
    rows = conn.execute(
        sa.select(
            coin_daily_reports.c.id,
            coin_daily_reports.c.report_date,
            coin_daily_reports.c.period_ended_at,
            coin_daily_reports.c.created_at,
        )
    ).mappings().all()

    keep_by_date: dict = {}
    delete_ids: list[str] = []
    for row in rows:
        current_id = row["id"]
        report_date = row["report_date"]
        rank_time = row["period_ended_at"] or row["created_at"]
        kept = keep_by_date.get(report_date)
        if kept is None or rank_time > kept["rank_time"]:
            if kept is not None:
                delete_ids.append(kept["id"])
            keep_by_date[report_date] = {"id": current_id, "rank_time": rank_time}
        else:
            delete_ids.append(current_id)

    if delete_ids:
        conn.execute(
            sa.delete(coin_daily_reports).where(coin_daily_reports.c.id.in_(delete_ids))
        )

    with op.batch_alter_table("coin_daily_reports", schema=None) as batch_op:
        batch_op.drop_index("ix_coin_daily_reports_period_ended_at")
        batch_op.drop_index("ix_coin_daily_reports_period_started_at")
        batch_op.drop_index("ix_coin_daily_reports_applied_cycle_id")
        batch_op.drop_index("ix_coin_daily_reports_report_source")
        batch_op.drop_column("period_ended_at")
        batch_op.drop_column("period_started_at")
        batch_op.drop_column("applied_cycle_id")
        batch_op.drop_column("trigger_reason")
        batch_op.drop_column("report_source")
        batch_op.create_unique_constraint(
            "uq_coin_daily_reports_report_date",
            ["report_date"],
        )
