"""Seed main / 门控监管 (referee) / extractor role_config + prompt_version for a campaign.

pipeline-stream-and-referee migration step: the alembic migration deletes the
old role/judge/polish rows, so each campaign must be re-seeded with the three
dual-LLM slots. Idempotent — re-running replaces this campaign's role_configs +
their prompt_versions.

The 门控监管 (referee) slot emits ONE bare category token from a closed enum
(goal_achieved / customer_decline / transfer / continue) — no JSON, no
confidence — which the engine's routing-rule decider matches against
``campaign.routing_rules``. The referee role_config is labelled ``main_judge``
and the campaign's routing_rules bind to that label, so a re-seeded campaign is
self-consistent and a real call actually routes (see ROUTING_RULES below).

Usage (reads ISALES_DATABASE_URL):

    cd isales-api
    .venv/bin/python scripts/seed_pipeline_stream_campaign.py [CAMPAIGN_ID]

CAMPAIGN_ID defaults to 1 (the dev campaign). Models / prompts below use the
spec-recommended templates (role-prompt spec § main/referee/extractor 内容规范);
edit per campaign in isales-web afterwards.
"""

from __future__ import annotations

import asyncio
import os
import sys

from isales_common.enums import PromptScopeType, RoleKind
from isales_common.models import Campaign, PromptVersion, RoleConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Default models — qwen-turbo for the cheap referee, a stronger main model.
MAIN_MODEL = os.environ.get("SEED_MAIN_MODEL", "doubao-pro-32k")
REFEREE_MODEL = os.environ.get("SEED_REFEREE_MODEL", "qwen-turbo")
EXTRACTOR_MODEL = os.environ.get("SEED_EXTRACTOR_MODEL", "qwen-turbo")

MAIN_PROMPT = """你是一名专业的电话销售顾问。

【目标】
向客户介绍产品并争取约见 / 成交 / 回访机会。

【话术规范】
- 语气自然、口语化，像真人通话
- 简洁，避免长篇大论

【输出格式】
你的输出必须遵循：
1. 只输出你要对客户说的话，不要任何解释 / 元信息 / 引号包裹
2. 不要使用 markdown 标题 / 加粗 / 列表
3. 不要使用 emoji / 表情符号
4. 不要输出 JSON / 代码块
5. 如果有多句，用句号 / 问号 / 感叹号自然分隔
6. 单句长度控制在 30 字以内（便于 TTS 自然停顿）
"""

REFEREE_PROMPT = """你是销售外呼对话的门控监管助手。基于"用户最后一句话"+ 最近 3 轮对话历史，判断本轮对话状态，输出一个分类词。

【输入】
用户最后一句话：{{user_last_utterance}}
最近 3 轮对话：
{{recent_dialog_history}}

【输出（封闭枚举，只能从下面四个词里选一个）】
goal_achieved
customer_decline
transfer
continue

【枚举语义】
- continue: 客户在正常对话中（包括犹豫 / 询问细节），不需要状态切换
- goal_achieved: 客户明确同意了外呼目标（成交 / 约见 / 同意回访）
- customer_decline: 客户明确拒绝或表达强烈反感
- transfer: 客户主动要求转人工

【输出格式（必须严格遵守）】
1. 只输出上面四个分类词中的一个，原样输出该单词
2. 不要输出 JSON / 大括号 / 引号 / 标点 / 解释 / 任何其他文字
3. 整个回复就是一个英文单词，例如：continue
"""

EXTRACTOR_PROMPT = """你是销售通话信息抽取助手。基于完整通话记录，抽取以下字段：

【字段定义】
- customer_name (str): 客户姓名，未提及则 null
- intent (enum): "interested" | "considering" | "declined"
- callback_time (datetime str): 客户同意的回访时间，未约定则 null

【输入】
{{transcript}}

【输出 JSON】
{
  "customer_name": ...,
  "intent": ...,
  "callback_time": ...
}

只输出 JSON，所有字段都要给（无信息时给 null）。
"""

# Stable label the single referee role_config carries; routing_rules bind to it
# via ``referee``. Mirrors the alembic default-seed (a7b8c9d0e1f2 tagged existing
# referee rows ``main_judge``). Without this, a re-seed would create a label-less
# referee that no routing rule references → every turn fail-opens to continue.
REFEREE_LABEL = "main_judge"

# Default routing rules bound to REFEREE_LABEL. The closed enum the referee emits
# (goal_achieved / customer_decline / transfer / continue) MUST equal these rules'
# ``match`` values for routing to fire; ``continue`` is implicit (no match → the
# decider falls through to continue / LISTENING), so it needs no rule. goal_type
# now lives on the goal_achieved rule's action (the prompt no longer emits it).
ROUTING_RULES = [
    {
        "referee": REFEREE_LABEL,
        "match": ["goal_achieved"],
        "action": {"type": "transition", "to": "goal_achieved", "goal_type": "appointment"},
    },
    {
        "referee": REFEREE_LABEL,
        "match": ["transfer"],
        "action": {"type": "transition", "to": "transfer"},
    },
    {
        "referee": REFEREE_LABEL,
        "match": ["customer_decline"],
        "action": {"type": "transition", "to": "customer_decline"},
    },
]

# (kind, scope, model, prompt, temperature, label)
_SLOTS = [
    (RoleKind.MAIN, PromptScopeType.MAIN, MAIN_MODEL, MAIN_PROMPT, 0.8, None),
    (RoleKind.REFEREE, PromptScopeType.REFEREE, REFEREE_MODEL, REFEREE_PROMPT, 0.2, REFEREE_LABEL),
    (RoleKind.EXTRACTOR, PromptScopeType.EXTRACTOR, EXTRACTOR_MODEL, EXTRACTOR_PROMPT, 0.2, None),
]


async def seed(campaign_id: int) -> None:
    url = os.environ.get("ISALES_DATABASE_URL")
    if not url:
        raise SystemExit("ISALES_DATABASE_URL not set")
    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        campaign = await db.get(Campaign, campaign_id)
        if campaign is None:
            raise SystemExit(f"campaign {campaign_id} not found")

        # Idempotent re-seed: drop this campaign's existing role_configs +
        # their prompt_versions first.
        existing = (
            (
                await db.execute(
                    select(RoleConfig).where(RoleConfig.campaign_id == campaign_id)
                )
            )
            .scalars()
            .all()
        )
        rc_ids = [rc.id for rc in existing]
        if rc_ids:
            await db.execute(
                delete(PromptVersion).where(PromptVersion.scope_id.in_(rc_ids))
            )
            await db.execute(
                delete(RoleConfig).where(RoleConfig.id.in_(rc_ids))
            )
            await db.flush()

        for kind, scope, model, prompt, temperature, label in _SLOTS:
            rc = RoleConfig(
                campaign_id=campaign_id,
                kind=kind.value,
                label=label,
                model=model,
                temperature=temperature,
                top_p=1.0,
                ext_params={},
                enabled=True,
            )
            db.add(rc)
            await db.flush()  # assign rc.id
            pv = PromptVersion(
                scope_type=scope.value,
                scope_id=rc.id,
                content=prompt,
                created_by="seed_pipeline_stream_campaign",
                is_active=True,
            )
            db.add(pv)
            await db.flush()  # assign pv.id
            rc.current_prompt_version_id = pv.id
            print(f"seeded {kind.value}: role_config={rc.id} prompt_version={pv.id} model={model}")

        # Bind the campaign's routing_rules to the referee label so the bare
        # category token actually drives a transition (without this the referee
        # runs but no rule references it → every turn fail-opens to continue).
        campaign.routing_rules = [dict(r) for r in ROUTING_RULES]
        print(f"seeded routing_rules: {len(ROUTING_RULES)} rules bound to referee={REFEREE_LABEL!r}")

        await db.commit()
    await engine.dispose()
    print(f"done: campaign {campaign_id} seeded with main/referee/extractor")


if __name__ == "__main__":
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(seed(cid))
