"""Seed main / referee / extractor role_config + prompt_version for a campaign.

pipeline-stream-and-referee migration step: the alembic migration deletes the
old role/judge/polish rows, so each campaign must be re-seeded with the three
dual-LLM slots. Idempotent — re-running replaces this campaign's role_configs +
their prompt_versions.

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

REFEREE_PROMPT = """你是销售外呼对话的决策助手。基于"用户最后一句话"+ 最近 3 轮对话历史，判断本轮对话状态。

【输入】
用户最后一句话：{{user_last_utterance}}
最近 3 轮对话：
{{recent_dialog_history}}

【输出 JSON】
{
  "decision": "continue" | "goal_achieved" | "customer_decline" | "transfer",
  "goal_type": "appointment" | "sale" | "callback" | null,
  "confidence": 0.0~1.0
}

【枚举语义】
- continue: 客户在正常对话中（包括犹豫 / 询问细节），不需要状态切换
- goal_achieved: 客户明确同意了外呼目标（成交 / 约见 / 同意回访）。goal_type 必填
- customer_decline: 客户明确拒绝或表达强烈反感
- transfer: 客户主动要求转人工

【confidence 评分】
- 你的判断越确定，confidence 越接近 1.0
- 模棱两可时给低分；< 0.7 系统会忽略你的决策走 continue

只输出 JSON，不要任何解释。
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

_SLOTS = [
    (RoleKind.MAIN, PromptScopeType.MAIN, MAIN_MODEL, MAIN_PROMPT, 0.8),
    (RoleKind.REFEREE, PromptScopeType.REFEREE, REFEREE_MODEL, REFEREE_PROMPT, 0.2),
    (RoleKind.EXTRACTOR, PromptScopeType.EXTRACTOR, EXTRACTOR_MODEL, EXTRACTOR_PROMPT, 0.2),
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

        for kind, scope, model, prompt, temperature in _SLOTS:
            rc = RoleConfig(
                campaign_id=campaign_id,
                kind=kind.value,
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

        await db.commit()
    await engine.dispose()
    print(f"done: campaign {campaign_id} seeded with main/referee/extractor")


if __name__ == "__main__":
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(seed(cid))
