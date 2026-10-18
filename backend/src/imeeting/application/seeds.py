"""Built-in templates and glossary seeds."""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from ..db import engine
from ..models import GlossaryTerm, MinutesTemplate

logger = logging.getLogger(__name__)

BUILTIN_MINUTES_TEMPLATES = [
    (
        "标准会议纪要",
        "通用会议：议题、结论、待办",
        "你是一位专业的会议记录员。请根据以下会议转写内容，生成结构化的会议纪要。\n\n"
        "要求：\n1. 提取讨论的议题，每个议题给出简要说明\n"
        "2. 提炼每个议题的结论或共识\n"
        "3. 列出明确的待办事项，注明责任人与截止时间（如原文提及）\n"
        "4. 保持客观，不要臆造原文没有的信息\n\n"
        "输出 JSON，结构如下：\n"
        '{"summary":"整体摘要","topics":[{"title":"议题","detail":"说明","conclusion":"结论"}],'
        '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写内容：\n---\n{transcript}\n---",
    ),
    (
        "项目周会",
        "进展、风险、下周计划与责任人",
        "你是项目经理。请基于以下转写生成项目周会纪要。\n"
        "重点关注：本周进展、阻塞与风险、下周计划、责任人、截止时间。\n"
        "不要把未提及的内容补全。\n\n"
        "输出 JSON：\n"
        '{"summary":"本周整体进展","topics":[{"title":"进展/风险/计划","detail":"说明","conclusion":"结论"}],'
        '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写：\n---\n{transcript}\n---",
    ),
    (
        "决策评审",
        "背景、方案对比、决策与行动项",
        "你是技术评审主持人。请基于以下转写生成决策评审纪要。\n"
        "重点：问题背景、候选方案与权衡、最终决策、遗留风险、行动项。\n"
        "若会议未形成决策，请明确写“未决策”，不要臆造。\n\n"
        "输出 JSON：\n"
        '{"summary":"决策摘要","topics":[{"title":"议题","detail":"方案与权衡","conclusion":"决策/未决策"}],'
        '"todos":[{"task":"行动项","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写：\n---\n{transcript}\n---",
    ),
    (
        "客户访谈",
        "痛点、需求、异议与后续动作",
        "你是客户成功经理。请基于以下转写生成客户访谈纪要。\n"
        "重点：客户痛点、明确需求、预算/时间约束、异议与顾虑、后续跟进动作。\n\n"
        "输出 JSON：\n"
        '{"summary":"访谈摘要","topics":[{"title":"痛点/需求/异议","detail":"客户原话要点","conclusion":"判断"}],'
        '"todos":[{"task":"跟进动作","owner":"责任人","due":"截止时间"}]}\n\n'
        "访谈转写：\n---\n{transcript}\n---",
    ),
]


BUILTIN_GLOSSARY_TERMS = [
    ("拓扑结构", "拓部结构,拓扑", "网络安全"),
    ("震网病毒", "阵亡病毒,Stuxnet", "网络安全"),
    ("零日漏洞", "灵力漏洞,0day,0-day", "网络安全"),
    ("载荷", "载合", "网络安全"),
    ("情报载荷", "情报载合", "网络安全"),
    ("跳板", "条板", "网络安全"),
    ("木马", "优步码,木马程序", "网络安全"),
    ("APT", "A P T,高级持续性威胁", "网络安全"),
    ("PDR", "防护检测响应", "网络安全"),
    ("DAPV", "动态自适应保护", "网络安全"),
    ("网络空间安全", "网络空安全,网控安全", "网络安全"),
    ("攻击", "空击,公击", "网络安全"),
    ("供应链", "供应莲,供应链件", "网络安全"),
    ("态势感知", "态势赶知", "网络安全"),
    ("内网渗透", "内网参透", "网络安全"),
    ("横向移动", "横向移种", "网络安全"),
    # #231 真实会议暴露的高频领域词与误识别写法。只收录明确的错误别名，
    # 不把“多态/拟态/靶场”这类跨领域通用词做全局替换。
    ("多模态", "播模态,膜态,多摸态", "人工智能"),
    ("数字孪生", "数字本身,数字双生", "网络安全"),
    ("动态防御", "随机跳变,随机跳变防御,动态防御技术", "网络安全"),
    ("拟态防御", "拟态防御技术", "网络安全"),
    ("量子密码", "量子DNA密码,量子密钥", "网络安全"),
    ("行为认证", "行为风格认证,鼠标移动认证,行为识别认证", "网络安全"),
    ("增强现实", "增强现实技术", "人工智能"),
    ("虚拟现实", "虚拟现实技术", "人工智能"),
    ("超级智能", "ASI,超级人工智能,超级值", "人工智能"),
    ("数字靶场", "网络靶场,数字靶场", "网络安全"),
    ("防护对象", "团论对象", "网络安全"),
    ("基础理论", "基术理论", "网络安全"),
    ("安全投入", "杀菌投入", "网络安全"),
    ("组织机构", "主织事库", "网络安全"),
    ("法案", "会打牌呀法", "网络安全"),
    ("智能汽车", "智能汽", "人工智能"),
    ("关键信息基础设施", "关基", "网络安全"),
    ("零信任", "零信任架构", "网络安全"),
    ("数据治理", "数据治理体系", "人工智能"),
    ("情报共享", "情报共享机制", "网络安全"),
    ("诱导隔离", "诱导隔离技术", "网络安全"),
    ("网络空间地图", "网络地图", "网络安全"),
    ("新型网络体系", "五新的网网", "网络安全"),
    ("风险", "重险", "通用"),
    ("成本", "成务", "通用"),
    # 数据安全 / 个人信息保护会议常见规范词。只收录明确的同音/近音别名；
    # “数据确认”本身是正常词，不在全局替换，交给知识上下文结合语义判断。
    ("数据确权", "数据确认机制,数据确认与数据资产机制", "数据安全"),
    ("个人信息保护", "", "数据安全"),
    ("个人信息保护法", "个保法", "数据安全"),
    ("数据安全法", "数安法", "数据安全"),
    ("网络安全法", "网安法", "数据安全"),
    ("敏感个人信息", "敏感信息,敏感个人数据", "数据安全"),
    ("告知同意", "知情同意", "数据安全"),
    ("数据出境", "数据跨境,数据出境安全评估", "数据安全"),
    ("个人信息过度收集", "过度收集个人信息,个人信息过度采集", "数据安全"),
    ("第三方披露", "第三方共享", "数据安全"),
    ("数据泄露", "数据外泄", "数据安全"),
    ("数据资产", "", "数据安全"),
    ("滴滴", "滴滴出行,滴滴打车", "数据安全"),
    ("哈梅内伊", "韩没内衣", "国际政治"),
    ("美以联合行动", "美国与以色列联合行动,美以联合对伊朗行动", "国际政治"),
    ("Claude 3 Opus", "clall三opx,Claude3Opus", "人工智能"),
    ("GPT-6.5", "GP六点五,GPT6.5,GP六", "人工智能"),
    ("NIST", "", "网络安全"),
    ("护网行动", "护网", "网络安全"),
    ("供应链攻击", "供应链弱化", "网络安全"),
    ("认知战", "认知作战,舆论战", "网络安全"),
    ("网络攻击工业化", "AI驱动工业化,攻击工业化", "网络安全"),
    ("智能体", "AI Agent,智能代理", "人工智能"),
    ("攻击方", "正房行", "网络安全"),
    ("攻击性", "竞攻性", "网络安全"),
    ("算力设备", "科理设备", "人工智能"),
    ("访问权限", "访款权限", "网络安全"),
    ("KYC", "KYZ", "数据安全"),
    ("斩首行动", "展斗行动", "国际政治"),
]


def ensure_builtin_minutes_templates() -> None:
    """首次启动写入内置纪要模板（幂等）。"""
    with Session(engine) as db:
        existing = {row.name for row in db.exec(select(MinutesTemplate)).all()}
        for name, description, content in BUILTIN_MINUTES_TEMPLATES:
            if name in existing:
                continue
            db.add(
                MinutesTemplate(
                    name=name,
                    description=description,
                    content=content,
                    is_builtin=True,
                )
            )
        db.commit()


def ensure_builtin_glossary_terms() -> None:
    """首次启动写入内置网络安全词表（幂等）。"""
    with Session(engine) as db:
        existing = {
            row.term: row for row in db.exec(select(GlossaryTerm)).all()
        }
        for term, aliases, category in BUILTIN_GLOSSARY_TERMS:
            row = existing.get(term)
            if row is not None:
                # 内置词条允许随版本补充别名；用户自建词条不覆盖。
                if row.owner_id is not None:
                    continue
                current = [
                    item.strip()
                    for item in (row.aliases or "").replace("，", ",").split(",")
                    if item.strip()
                ]
                for alias in aliases.split(","):
                    alias = alias.strip()
                    if alias and alias not in current:
                        current.append(alias)
                merged = ",".join(current)
                if merged != (row.aliases or ""):
                    row.aliases = merged[:1000]
                    db.add(row)
                if not row.category:
                    row.category = category
                continue
            db.add(
                GlossaryTerm(
                    term=term,
                    aliases=aliases,
                    category=category,
                )
            )
        db.commit()
    try:
        from ..glossary import invalidate_cache

        invalidate_cache()
    except Exception:
        logger.debug("词表缓存刷新失败", exc_info=True)
