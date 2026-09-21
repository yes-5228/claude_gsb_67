"""Flask CLI commands: flask init-db / seed / reset-db."""
import click

from .extensions import db
from .models import Exceedance, Measurement, Station


def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Create database tables."""
        db.create_all()
        click.echo("数据库表已创建")

    @app.cli.command("seed")
    @click.option("--days", default=5, show_default=True, help="生成最近多少天的数据")
    @click.option("--force", is_flag=True, help="已有数据时仍然追加写入")
    def seed(days, force):
        """Load demo stations and monitoring records."""
        from .seed import seed_demo_data

        if Station.query.count() and not force:
            click.echo("已存在监测点数据, 如确需追加请使用 --force")
            return
        db.create_all()
        totals = seed_demo_data(days=days)
        click.echo(
            "演示数据写入完成: 监测点 %(stations)s 个, 监测数据 %(measurements)s 条, "
            "超标记录 %(exceedances)s 条" % totals
        )

    @app.cli.command("reset-db")
    @click.option("--with-demo/--empty", default=True, help="是否写入演示数据")
    def reset_db(with_demo):
        """Drop all tables, recreate them and optionally load demo data."""
        from .seed import reset_database, seed_demo_data

        reset_database()
        click.echo("数据库已重置")
        if with_demo:
            totals = seed_demo_data()
            click.echo("演示数据写入完成: %s" % totals)

    @app.cli.command("stats")
    def stats():
        """Print a short record summary."""
        click.echo(
            "监测点 %d 个 / 监测数据 %d 条 / 超标记录 %d 条"
            % (
                Station.query.count(),
                Measurement.query.count(),
                Exceedance.query.count(),
            )
        )

    @app.cli.command("publish-rate")
    @click.argument("month")
    @click.option("--by", "published_by", default=None, help="发布人")
    @click.option("--note", default=None, help="发布说明")
    def publish_rate(month, published_by, note):
        """冻结某月达标率 (YYYY-MM); 已发布月份不再重算."""
        from .services import attainment_service
        from .errors import ApiError

        try:
            record = attainment_service.publish_month(
                month, published_by=published_by, note=note
            )
        except ApiError as exc:
            raise click.ClickException(exc.message)
        click.echo(
            "%s 达标率已发布: 达标率 %.2f%% (考核 %d 条, 超标 %d 条)"
            % (
                record.month,
                record.attainment_rate * 100,
                record.assessed_count,
                record.exceeded_count,
            )
        )

    @app.cli.command("freeze-legacy-rates")
    @click.option("--note", default=None, help="快照说明")
    def freeze_legacy_rates(note):
        """一次性迁移: 历史月份按旧口径快照, 保护已对外发布的达标率."""
        from .services import attainment_service

        frozen = attainment_service.freeze_legacy_months(note=note)
        if not frozen:
            click.echo("没有需要冻结的历史月份")
            return
        for item in frozen:
            click.echo(
                "%s 已按旧口径冻结: 达标率 %.2f%%" % (item["month"], item["attainment_rate"] * 100)
            )
