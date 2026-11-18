"""User-approved active v2 prompt; original v1 text is frozen separately."""
from .report_prompt_v1 import SYSTEM_PROMPT_V1

CANDIDATE_PROMPT_VERSION = 'match-analysis-prompt-v2'
SYSTEM_PROMPT_V2 = SYSTEM_PROMPT_V1 + """
字段填写补充（与现有逐项引用、未知值、OR及加分规则同时执行）：
1. subject 是规范技能名，不是结论标题。对规则已识别的技能，逐字使用 context.rules.skills[*].alternatives 中的单个名称。
不要写“Python 和 FastAPI”“Python接口开发能力”；分别用 subject="Python" 和 subject="FastAPI"。
2. requirement_kind 描述岗位要求类别，status 描述材料支持状态，二者独立。
requirement_kind 必须复制同 subject 的规则 kind（required/bonus/mention），不得由 status、句子措辞或个人判断重新分类。
例如 Docker 规则 kind=bonus、材料未提供支持时：subject="Docker", requirement_kind="bonus", status="unknown"。
status=unknown 不要求 requirement_kind=unknown；材料缺证据不改变加分项性质。
3. 多个技能的AND要求拆成逐技能结论；OR要求保留整条任选关系，只采用 alternatives 中的单个规范名作 subject。
Python或Java且Python有证据时，可解释Python对该任选要求的支持，不把Java缺证据写成必补缺口。
4. recommendation 与关联finding使用相同规范subject及相同requirement_kind，不将加分/任选分支变为必需。
建议仍须status=advisory，related_claim_ids关联finding，evidence_ids引用相关JD及finding证据。
5. 同一个subject存在多条不同kind的规则时，先选定结论所依据的具体规则及其evidence_id，复制该规则kind，不合并或升级类别。
6. 规则未识别的主题不冒充已识别技能；requirement_kind="unknown"只表示规则中未确定类别，不用于绕过已有类别校验。
保留现有证据和作用域门禁；不要因subject或类别难填而编造证据、熟练度、经历或百分比。
"""

