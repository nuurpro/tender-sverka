"""Owned test queue only; real SDK JSON fixture, never a deployment adapter."""
import json
import os

import httpx
from app.config import Settings
from app.analysis.gateway import Gateway
from app.analysis.schemas import GatewayProfile
from app.analysis.service import AnalysisService
from app.jobs.worker import HandlerRegistry,create_celery_app
from app.workspace.database import create_engine

settings=Settings()
if settings.environment!='test' or not settings.job_queue.startswith('tender.test.'):
    raise RuntimeError('Fixture worker requires an owned test queue')


def respond(request):
    body=json.loads(request.content)
    data=json.loads(body['messages'][1]['content']);block=data['blocks'][0]
    output={'requirements':[{'scope':'product','category':'technical','parameter':'power',
        'condition':'at least 25 W','importance':'mandatory',
        'predicate':{'kind':'numeric','operator':'gte','value':'25','unit':'W'},
        'citations':[{'evidence_id':block['evidence_id'],'quote':block['text'].strip()}]}],
        'facts':[],'findings':[]}
    return httpx.Response(200,json={'id':'fixture','created':1,'model':'fixture','object':'chat.completion',
        'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(output)},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}})


registry=HandlerRegistry('test')
engine=create_engine(settings.database_url.get_secret_value())
gateway=Gateway(GatewayProfile(),'synthetic-fixture',http_client=httpx.Client(transport=httpx.MockTransport(respond)))
registry.register('analysis.extract',AnalysisService(engine,settings,gateway=gateway).handle)
app=create_celery_app(settings,registry=registry)
if __name__=='__main__':
    app.worker_main(['worker','--pool=solo','--concurrency=1','--loglevel=WARNING',
        '--without-gossip','--without-mingle','--without-heartbeat','--queues='+settings.job_queue])
