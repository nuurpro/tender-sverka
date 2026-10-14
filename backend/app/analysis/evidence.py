"""Server evidence bindings; AI sees IDs and text, never storage identities."""
import copy
import hashlib
import re
from typing import TypedDict,Literal
from .schemas import Citation


class ReferenceLink(TypedDict):
    source_id: str
    source_start: int
    source_end: int
    reference: str
    target_ids: list[str]
    state: Literal['resolved','ambiguous','unresolved']


class ReferenceLimitExceeded(ValueError):
    pass


def evidence_id(artifact,block,start,end):
    return 'ev:'+hashlib.sha256(f'{artifact}:{block}:{start}:{end}'.encode()).hexdigest()


def bind_blocks(artifact_id,document_id,filename,blocks):
    result=[]
    for block in blocks:
        ocr='transcription_text' in block
        text=block['transcription_text'] if ocr else block['original_text']
        result.append({'evidence_id':evidence_id(artifact_id,block['id'],0,len(text)),
            'artifact_id':artifact_id,'document_id':document_id,'filename':filename,'block_id':block['id'],
            'text':text,'source_start':0,'source_end':len(text),'anchor':copy.deepcopy(block['anchor']),
            'quote_kind':'ocr_transcription' if ocr else 'native_original',
            'confidence':block.get('confidence'),'review_flags':list(block.get('review_flags',[]))})
    return result


def chunk_blocks(blocks,max_chars):
    return list(iter_chunk_blocks(blocks,max_chars))


def iter_chunk_blocks(blocks,max_chars):
    if type(max_chars) is not int or max_chars<1:
        raise ValueError('Invalid chunk size')
    current=[]; length=0
    for block in blocks:
        for start in range(0,len(block['text']),max_chars):
            fragment=copy.deepcopy(block)
            fragment['text']=block['text'][start:start+max_chars]
            fragment['source_start']=block['source_start']+start
            fragment['source_end']=fragment['source_start']+len(fragment['text'])
            fragment['evidence_id']=evidence_id(block['artifact_id'],block['block_id'],fragment['source_start'],fragment['source_end'])
            if current and length+len(fragment['text'])>max_chars:
                yield current
                current=[];length=0
            current.append(fragment);length+=len(fragment['text'])
    if current:yield current


def resolve_citation(raw,blocks):
    citation=Citation.model_validate(raw)
    block=next((b for b in blocks if b['evidence_id']==citation.evidence_id),None)
    if block is None: raise ValueError('Unknown evidence identifier')
    normalized=' '.join(citation.quote.split())
    if not normalized: raise ValueError('Empty quote')
    # Map whitespace-normalized matches back to exact original character bounds.
    tokens=list(re.finditer(r'\S+',block['text']))
    haystack=' '.join(t.group() for t in tokens)
    match=haystack.find(normalized)
    if match<0: raise ValueError('Quote is absent from evidence')
    positions=[]
    for index,token in enumerate(tokens):
        positions.extend(range(token.start(),token.end()))
        if index<len(tokens)-1: positions.append(token.end())
    start=positions[match];end=positions[match+len(normalized)-1]+1
    return {key:copy.deepcopy(block[key]) for key in ('evidence_id','artifact_id','document_id','filename','block_id','anchor','quote_kind','confidence','review_flags')} | {
        'quote':block['text'][start:end],'source_start':block['source_start']+start,'source_end':block['source_start']+end,'validated':True}


def slice_block(block,start,end):
    """Server-owned subsection with offsets into the immutable original block."""
    value=copy.deepcopy(block)
    value['text']=block['text'][start:end]
    value['source_start']=block['source_start']+start
    value['source_end']=block['source_start']+end
    value['evidence_id']=evidence_id(block['artifact_id'],block['block_id'],value['source_start'],value['source_end'])
    return value


def reference_plan(blocks,*,max_links=None,max_target_bindings=None) -> tuple[list[ReferenceLink],dict[str,list[dict]]]:
    """Index numbered sections across complete documents before context splitting.

    A section retains every continuation block up to the next heading. Repeated
    headings remain ambiguous; the index never selects a guessed target.
    """
    names=r'(?:section|clause|paragraph|пункт(?:а|е)?|раздел(?:а|е)?|band|bo[’\x27]lim)'
    mention=re.compile(r'(?i)'+names+r'\s*([0-9]+(?:\.[0-9]+)*)')
    heading=re.compile(r'(?im)^[ \t]*(?:'+names+r'[ \t]+)?([0-9]+(?:\.[0-9]+)*)(?:[.)][ \t]*|[ \t]+|$)')
    headings={};targets={};heading_ranges={};documents={}
    for block in blocks:documents.setdefault((block['artifact_id'],block['document_id']),[]).append(block)
    for document in documents.values():
        current=None
        for block in document:
            matches=list(heading.finditer(block['text']))
            heading_ranges[block['evidence_id']]=[(m.start(),m.end()) for m in matches]
            position=0
            for match_index,match in enumerate(matches):
                if current is not None and position<match.start():
                    targets[current].append(slice_block(block,position,match.start()))
                end=matches[match_index+1].start() if match_index+1<len(matches) else len(block['text'])
                section=slice_block(block,match.start(),end)
                current=section['evidence_id']
                headings.setdefault(match.group(1),[]).append(current)
                targets[current]=[section]
                position=end
            if not matches and current is not None and block['text']:
                targets[current].append(copy.deepcopy(block))
    links=[];bindings=0
    for block in blocks:
        for match in mention.finditer(block['text']):
            if any(start<=match.start()<end for start,end in heading_ranges.get(block['evidence_id'],[])):continue
            candidates=headings.get(match.group(1),[])
            bindings+=max(1,len(candidates))
            if max_links is not None and len(links)>=max_links or max_target_bindings is not None and bindings>max_target_bindings:
                raise ReferenceLimitExceeded('Reference graph exceeds supported planning attempt limit')
            links.append({'source_id':block['evidence_id'],'source_start':match.start(),'source_end':match.end(),
                'reference':match.group(0),'target_ids':list(candidates),
                'state':'resolved' if len(candidates)==1 else 'ambiguous' if candidates else 'unresolved'})
    return links,targets


def reference_links(blocks):
    return reference_plan(blocks)[0]
