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

REFEREE_PROMPT = """你是销售外呼对话的门控监管助手。基于"用户最后一句话"+ 最近 3 轮对话历史 +（若上一句 AI 被你这句话打断）那句没说完的 AI 残句，判断本轮对话状态，只输出一个分类词。

【输入】
用户最后一句话：{{user_last_utterance}}

最近 3 轮对话：
{{recent_dialog_history}}

上一句 AI 是否被你这句话打断、留下没说完的残句：{{was_interrupted}}
被打断、还没说完的那句 AI 残句（仅当上面为「是」时有意义，否则为空）：{{interrupted_reply}}

【输出（封闭枚举，只能从下面五个词里选一个，原样输出）】
goal_achieved
customer_decline
transfer
FILLER
continue

【枚举语义（按从上到下优先级判断，命中靠前的就不再选靠后的）】
- goal_achieved: 客户明确同意了外呼目标（成交 / 约见 / 同意回访）。无论是否打断，只要本句是「明确答应」一律选它。
- customer_decline: 客户明确拒绝或表达强烈反感。无论是否打断，只要本句是「明确拒绝 / 反感」一律选它。
- transfer: 客户主动要求转人工。
- FILLER: 仅当「上一句 AI 是否被打断 = 是」时才可能成立。指客户在 AI 说话过程中插进来、**没有任何需要回应的实质信息**的话：纯语气词 / 垫词 / 随口附和 / 口头催促，如「嗯」「啊」「哦」「对对对」「你说」「继续」「嗯嗯你讲」「我在听」。选 FILLER 表示：把刚被打断那句 AI 话顺着说完即可，不需要另起内容作答。
- continue: 上面都不成立时的默认值。包括：①非打断场景下客户正常对话（犹豫 / 询问细节 / 闲聊）；②打断场景下客户插进来的是**实质性提问 / 反对 / 新要求**（问价格 / 问条件 / 提疑虑 / 不认同）——需要 AI 正面回应，选 continue 而非 FILLER。

【FILLER vs continue 判别准则（务必谨慎）】
只有同时满足：①「是否被打断 = 是」；②本句不携带任何需要回应的实质信息（没提问 / 没新要求 / 没反对 / 没明确同意或拒绝）；③像「请继续 / 我在听 / 随口附和」的催促或确认——才输出 FILLER。
- 只要本句有一丝实质内容（哪怕半句「这个多少钱」「但是我觉得」），就**不**选 FILLER，按语义选（通常 continue）。
- 「然后呢 / 继续」类催促：若被打断那句 AI 还没讲完核心信息，选 FILLER（顺着讲完）；若那句已基本讲完、客户想推进到下一话题，选 continue（正面回应）。

【非打断场景硬规则（务必遵守）】
当「是否被打断 = 否」时：绝对不能输出 FILLER；只在 goal_achieved / customer_decline / transfer / continue 里选，判定标准与原来一致；忽略「被打断的那句 AI 残句」。

【示例】
否；用户「好的那就周三下午吧」 → goal_achieved
否；用户「不需要，别再打了」 → customer_decline
否；用户「帮我转人工」 → transfer
否；用户「这个具体怎么收费」 → continue
是；残句「我们这个课分三个阶段，第一阶段……」；用户「嗯嗯你说」 → FILLER
是；残句「……第一阶段……」；用户「对对对继续」 → FILLER
是；残句「……第一阶段……」；用户「那一共多少钱」 → continue
是；残句「我帮您约下周二……」；用户「不用了我没兴趣」 → customer_decline
是；残句「我帮您约下周二下午……」；用户「行行行那就这样」 → goal_achieved
是；残句「……所以这个课性价比很高了」（已基本讲完）；用户「然后呢」 → continue

【输出格式（必须严格遵守）】
1. 只输出上面五个分类词之一，原样输出该单词（FILLER 大写，其余小写）
2. 不要输出 JSON / 大括号 / 引号 / 标点 / 解释 / 任何其他文字
3. 整个回复就是一个分类词，例如：continue
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
# (goal_achieved / customer_decline / transfer / FILLER / continue) MUST equal these
# rules' ``match`` values for routing to fire; ``continue`` is implicit (no match →
# the decider falls through to continue / LISTENING), so it needs no rule. goal_type
# now lives on the goal_achieved rule's action (the prompt no longer emits it).
# engine-filler-gated-restructure: FILLER is deliberately NOT a routing rule — it
# drives auto_restructure via the run_loop override (gate judged a no-substance
# barge-in), so adding a rule for it would wrongly occupy the no-match fallback slot.
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
