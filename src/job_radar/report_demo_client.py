"""Authored synthetic report responses, never real LLM or quality labels."""
import json
from .report_models import AnalysisResponse,CallAudit

class SyntheticReportClient:
    """Echo a supplied rule into a cautious authored finding and related action."""
    def __init__(self):self.calls=0
    def complete(self,request):
        self.calls+=1;data=json.loads(request.packet_json);records=data['evidence']
        job=next(e for e in records if e['kind']=='job' and e['path']=='job.description')
        scope=next(e for e in records if e['kind']=='scope')
        rule=next((e for e in records if e['kind']=='rule' and e['path'].startswith('rules.skills[') and e['content'].get('status')=='evidence_found'),None)
        if rule:
            subject=rule['content']['matched'][0]
            resume=next(e for e in records if e['kind']=='resume' and 'document.' not in e['path'] and subject.lower() in json.dumps(e['content'],ensure_ascii=False).lower())
            # Authored demo only: retain the supplied source nature. This is not
            # a production renderer or a repair of a rejected model claim.
            context=json.dumps(resume['content'],ensure_ascii=False)
            origin=next((label for label in ('课程项目','个人项目','实习') if label in context),'简历记载')
            finding=dict(claim_id='c1',kind='strength',subject=subject,status='supported',text=f'{origin}使用 {subject}。',requirement_kind=rule['content']['kind'],evidence_ids=[job['evidence_id'],resume['evidence_id'],rule['evidence_id']],related_claim_ids=[])
            action=f'可补充 {subject} 使用记录。'
        else:
            skill=next(e for e in records if e['kind']=='rule' and e['path'].startswith('rules.skills['))
            subject=skill['content']['alternatives'][0]
            finding=dict(claim_id='c1',kind='unknown',subject=subject,status='unknown',text=f'简历未提及 {subject} 使用经历。',requirement_kind=skill['content']['kind'],evidence_ids=[job['evidence_id'],scope['evidence_id'],skill['evidence_id']],related_claim_ids=[])
            action=f'可补充 {subject} 使用记录。'
        claims=[finding]
        missing=next((e for e in records if e['kind']=='rule' and e['path'].startswith('rules.skills[') and e['content'].get('status')=='not_found'),None)
        if missing and rule:
            subject=missing['content']['alternatives'][0]
            finding=dict(claim_id='c2',kind='unknown',subject=subject,status='unknown',text=f'简历未提及 {subject} 使用经历。',requirement_kind=missing['content']['kind'],evidence_ids=[job['evidence_id'],scope['evidence_id'],missing['evidence_id']],related_claim_ids=[])
            claims.append(finding)
            action=f'可补充 {subject} 使用记录。'
        recommendation=dict(claim_id='c'+str(len(claims)+1),kind='recommendation',subject=subject,status='advisory',text=action,requirement_kind=finding['requirement_kind'],evidence_ids=finding['evidence_ids'],related_claim_ids=[finding['claim_id']])
        return AnalysisResponse(status='completed',text=json.dumps(dict(key=data['key'],claims=claims+[recommendation]),ensure_ascii=False),audit=CallAudit(provider='scripted-offline',model='no-model',attempts=1))
