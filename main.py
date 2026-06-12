"""生产缺陷神探 CLI 入口（V1 - P0 三件套）

用法示例：
    # 加载真实台账
    python main.py quality --file data/samples/taizhang_2025.json
    python main.py cluster --file data/samples/taizhang_2025.json
    python main.py trend   --file data/samples/taizhang_2025.json --period "2025年"

    # 一键全套（输出三份报告到目录）
    python main.py analyze --file data/samples/taizhang_2025.json --output-dir reports/

    # 兼容旧能力
    python main.py rca --text "NullPointerException at ..."
    python main.py dedup --file new_defect.json
"""

import json
import sys
from pathlib import Path
from typing import List, Optional

import click

from src.agent import DefectHunterAgent
from src.loaders import load_taizhang_json, load_defects
from src.models import Defect


def _output(content: str, output: Optional[str]):
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"✅ 报告已保存至: {output}")
    else:
        click.echo(content)


def _write_json(data: dict, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    click.echo(f"✅ JSON 已保存至: {path}")


# ====================================================================
@click.group()
@click.option("--config", default="config/config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config):
    """生产缺陷神探 - 高码智能体 CLI"""
    ctx.ensure_object(dict)
    ctx.obj["agent"] = DefectHunterAgent(config_path=config)


# ============ P0 三件套 ============
@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--output", default="", help="Markdown 报告输出路径")
@click.option("--issues-json", default="", help="结构化质量问题清单输出路径")
@click.pass_context
def quality(ctx, json_file, output, issues_json):
    """数据质量分析（P0）- 检测字段缺失与污染"""
    defects, _ = load_taizhang_json(json_file)
    click.echo(f"📊 加载缺陷 {len(defects)} 条，开始质量分析...")
    result = ctx.obj["agent"].quality(defects)

    summary = result["summary"]
    click.echo(
        f"📈 完整性评分: {summary['completeness_score']}/100  "
        f"污染问题: {summary['pollution_count']}  "
        f"空白记录: {summary['empty_records']}"
    )

    _output(result["report_md"], output or None)
    if issues_json:
        _write_json({"summary": summary, "issues": result["issues"]}, issues_json)


@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--output", default="", help="报告输出路径")
@click.pass_context
def cluster(ctx, json_file, output):
    """缺陷聚类分析（P0）- 识别业务模式集群"""
    defects = load_defects(json_file)
    click.echo(f"📊 加载缺陷 {len(defects)} 条，开始聚类分析...")
    report = ctx.obj["agent"].cluster(defects)
    _output(report, output or None)


@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--period", default="", help="统计周期描述，如 '2025年'")
@click.option("--output", default="", help="报告输出路径")
@click.pass_context
def trend(ctx, json_file, period, output):
    """汇总与趋势分析（P0）- 帕累托 + 风险研判"""
    defects = load_defects(json_file)
    click.echo(f"📊 加载缺陷 {len(defects)} 条，开始趋势分析...")
    report = ctx.obj["agent"].trend(defects, period=period)
    _output(report, output or None)


@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--period", default="", help="统计周期描述")
@click.option("--output-dir", default="reports/", help="报告输出目录")
@click.pass_context
def analyze(ctx, json_file, period, output_dir):
    """一键执行 P0 三件套（质量 + 聚类 + 趋势）"""
    defects, metadata = load_taizhang_json(json_file)
    click.echo(f"📊 加载缺陷 {len(defects)} 条 (metadata: {bool(metadata)})")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    agent = ctx.obj["agent"]

    # 1. 质量
    click.echo("\n[1/3] 数据质量分析...")
    q = agent.quality(defects)
    _output(q["report_md"], str(out_dir / "01_quality.md"))
    _write_json(
        {"summary": q["summary"], "issues": q["issues"]},
        str(out_dir / "01_quality_issues.json"),
    )
    click.echo(f"   完整性: {q['summary']['completeness_score']}/100  "
                f"污染: {q['summary']['pollution_count']}")

    # 2. 聚类
    click.echo("\n[2/3] 缺陷聚类分析...")
    c = agent.cluster(defects)
    _output(c, str(out_dir / "02_cluster.md"))

    # 3. 趋势
    click.echo("\n[3/3] 汇总与趋势分析...")
    t = agent.trend(defects, period=period)
    _output(t, str(out_dir / "03_trend.md"))

    click.echo(f"\n🎉 全部完成！报告已输出至 {out_dir}/")


# ============ P1/P2 兼容能力 ============
@cli.command()
@click.option("--text", default="", help="错误日志文本")
@click.option("--log", "log_file", default="", help="错误日志文件路径")
@click.option("--from-taizhang", "taizhang_file", default="",
                help="台账 JSON 文件（配合 --id 对某条缺陷做 RCA）")
@click.option("--id", "defect_id", default="", help="缺陷 ID（需配合 --from-taizhang）")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def rca(ctx, text, log_file, taizhang_file, defect_id, output):
    """单条 RCA 根因分析（P1）

    三种用法：
    1. 直接传日志：       rca --text "NPE..."
    2. 从日志文件读取：    rca --log path/to/log.txt
    3. 从台账分析某缺陷：  rca --from-taizhang 台账.json --id DEF-0001
    """
    from src.loaders import load_defects

    defect = None
    log_content = text
    if log_file:
        log_content = Path(log_file).read_text(encoding="utf-8")
    if taizhang_file and defect_id:
        defects = load_defects(taizhang_file)
        defect = next((d for d in defects if d.id == defect_id), None)
        if not defect:
            click.echo(f"❌ 未在台账中找到 {defect_id}", err=True)
            sys.exit(1)

    if not log_content and not defect:
        click.echo("❌ 请提供 --text / --log / 或 --from-taizhang+--id", err=True)
        sys.exit(1)

    report = ctx.obj["agent"].rca(defect=defect, raw_log=log_content)
    _output(report, output or None)


@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--pattern", default="",
                help="指定治理模式 ID（如 copy_new_pattern / concurrency_lock / security_authz）；"
                      "不指定则对所有命中模板的集群生成报告")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def governance(ctx, json_file, pattern, output):
    """专项治理报告（P1）- 针对高价值集群生成治理方案"""
    from src.loaders import load_defects
    defects = load_defects(json_file)
    click.echo(f"📊 加载缺陷 {len(defects)} 条，生成治理报告...")
    report = ctx.obj["agent"].governance(defects, pattern_id=pattern or None)
    _output(report, output or None)


# ============ V2: API 服务 ============
@cli.command()
@click.option("--host", default="0.0.0.0", help="绑定主机")
@click.option("--port", default=8000, help="绑定端口")
@click.option("--reload", is_flag=True, help="开发模式（代码改动自动重载）")
@click.option("--workers", default=1, help="worker 数量（生产环境建议 ≥ 2）")
@click.pass_context
def serve(ctx, host, port, reload, workers):
    """启动 FastAPI 服务（V2）"""
    import uvicorn
    click.echo(f"🚀 启动缺陷神探 API 服务于 http://{host}:{port}")
    click.echo(f"   API 文档: http://{host}:{port}/docs")
    if reload:
        # reload 模式必须用 import string
        uvicorn.run("src.api:app", host=host, port=port, reload=True)
    else:
        uvicorn.run("src.api:app", host=host, port=port, workers=workers)


# ============ P2: 责任界定 ============
@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.option("--id", "defect_id", default="",
                help="缺陷 ID（不指定则做批量画像）")
@click.option("--context", default="", help="额外上下文（仅单条模式）")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def responsibility(ctx, json_file, defect_id, context, output):
    """责任界定（P2）- 单条 LLM 分析 / 批量画像"""
    from src.loaders import load_defects
    defects = load_defects(json_file)
    agent = ctx.obj["agent"]

    if defect_id:
        target = next((d for d in defects if d.id == defect_id), None)
        if not target:
            click.echo(f"❌ 未找到缺陷 {defect_id}", err=True)
            sys.exit(1)
        report = agent.responsibility(target, context=context)
    else:
        click.echo(f"📊 加载 {len(defects)} 条，生成批量责任画像...")
        report = agent.responsibility_batch(defects)
    _output(report, output or None)


# ============ P2: 知识库 ============
@cli.group()
def kb():
    """知识库批量分析（P2）"""
    pass


@kb.command("import")
@click.option("--file", "json_file", required=True, help="台账 JSON 文件")
@click.pass_context
def kb_import_cmd(ctx, json_file):
    """批量入知识库"""
    from src.loaders import load_defects
    defects = load_defects(json_file)
    click.echo(f"📊 加载 {len(defects)} 条，开始入库...")
    result = ctx.obj["agent"].kb_import(defects)
    click.echo(
        f"✅ 入库完成: 新增 {result['imported']} 条, "
        f"跳过无效 {result['skipped_invalid']} 条, "
        f"跳过重复 {result['skipped_duplicate']} 条"
    )
    click.echo(f"   知识库当前共 {result['kb_size_after']} 条")


@kb.command("profile")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def kb_profile_cmd(ctx, output):
    """知识库画像"""
    report = ctx.obj["agent"].kb_profile()
    _output(report, output or None)


@kb.command("cross-check")
@click.option("--file", "json_file", required=True, help="新批次 JSON 文件")
@click.option("--output", default="", help="Markdown 报告输出文件")
@click.option("--json-out", default="", help="结构化 JSON 输出文件")
@click.pass_context
def kb_cross_check_cmd(ctx, json_file, output, json_out):
    """批量交叉查重 - 新批次 vs 历史库"""
    from src.loaders import load_defects
    defects = load_defects(json_file)
    click.echo(f"📊 加载新批次 {len(defects)} 条，开始查重...")

    if json_out:
        result = ctx.obj["agent"].kb_cross_check(defects)
        _write_json(result, json_out)

    md = ctx.obj["agent"].kb_cross_check_report(defects)
    _output(md, output or None)


@kb.command("historical-patterns")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def kb_historical_cmd(ctx, output):
    """从知识库归纳历史问题模式"""
    report = ctx.obj["agent"].kb_historical_patterns()
    _output(report, output or None)


@cli.command()
@click.option("--file", "json_file", required=True, help="台账 JSON")
@click.option("--id", "defect_id", default="", help="目标缺陷 ID")
@click.option("--top-k", default=3, help="返回 Top K 候选")
@click.option("--add-to-kb", is_flag=True, help="查重完成后加入知识库")
@click.option("--output", default="", help="报告输出文件")
@click.pass_context
def dedup(ctx, json_file, defect_id, top_k, add_to_kb, output):
    """与知识库进行查重"""
    defects = load_defects(json_file)
    target = (
        next((d for d in defects if d.id == defect_id), None)
        if defect_id else (defects[0] if defects else None)
    )
    if not target:
        click.echo("❌ 未找到目标缺陷", err=True)
        sys.exit(1)
    report = ctx.obj["agent"].dedup(target, top_k=top_k)
    _output(report, output or None)
    if add_to_kb:
        ctx.obj["agent"].add_to_kb(target)
        click.echo(f"✅ 已将 {target.id} 加入知识库")


if __name__ == "__main__":
    cli(obj={})
