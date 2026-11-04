#!/usr/bin/env python3
"""Pure post-filter for existing PP-DocLayout candidates; no model calls.

A precision-first policy, not an icon recognition model or accuracy guarantee.
It only removes existing whole figure candidates. It never splits/crops/merges
or adds detections, and it does not change the detector's score threshold.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class Policy:
    detector_threshold: float = 0.5
    tiny_area_fraction: float = 0.003
    small_area_fraction: float = 0.010
    repeated_badge_area_fraction: float = 0.015
    caption_gap_fraction: float = 0.04
    caption_horizontal_overlap: float = 0.30
    heading_max_gap_fraction: float = 0.035
    heading_vertical_overlap: float = 0.40
    repeat_size_relative_tolerance: float = 0.20
    repeat_x_center_tolerance: float = 0.025
    repeat_aspect_relative_tolerance: float = 0.25


CAPTION = re.compile(r'^\s*(?:图\s*[0-9０-９一二三四五六七八九十]+|(?:fig(?:ure)?)[.\s]*[0-9]+)', re.I)


def area(box: list[float]) -> float:
    return max(0.0, box[2]-box[0])*max(0.0, box[3]-box[1])


def checked_box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in box) or area(box) <= 0:
        return None
    return box


def normalized_box(box: list[float], width: float, height: float) -> list[float]:
    return [box[0]/width, box[1]/height, box[2]/width, box[3]/height]


def overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1,b1)-max(a0,b0))


def caption_evidence(box: list[float], lines: list[dict[str, Any]], policy: Policy) -> list[dict[str, Any]]:
    """Only actual numbered-caption text in plausible external spatial relation.

    A detector's figure_title label alone cannot establish a numbered caption.
    Text inside the figure is not treated as an external caption.
    """
    evidence=[]
    for line in lines:
        bounds=checked_box(line.get('bbox_normalized'))
        text=str(line.get('text','')).strip()
        if bounds is None or not CAPTION.match(text):
            continue
        overlap=overlap_1d(box[0],box[2],bounds[0],bounds[2])
        coverage=overlap/max(1e-12,min(box[2]-box[0],bounds[2]-bounds[0]))
        below=0 <= bounds[1]-box[3] <= policy.caption_gap_fraction
        above=0 <= box[1]-bounds[3] <= policy.caption_gap_fraction
        if coverage>=policy.caption_horizontal_overlap and (below or above):
            evidence.append({'text':text[:160],'bbox_normalized':bounds,'relation':'below' if below else 'above'})
    return evidence


def heading_evidence(box: list[float], layout: list[dict[str, Any]], width: float, height: float, policy: Policy) -> list[dict[str, Any]]:
    """Badge-like adjacency to a heading, not a generic nearby body paragraph."""
    evidence=[]
    for item in layout:
        if item.get('label') not in {'paragraph_title','doc_title','header','footer'}:
            continue
        try: score=float(item.get('score',0))
        except (TypeError,ValueError): continue
        bounds=checked_box(item.get('coordinate'))
        if bounds is None or score<policy.detector_threshold:
            continue
        bounds=normalized_box(bounds,width,height)
        vertical=overlap_1d(box[1],box[3],bounds[1],bounds[3])
        shared=vertical/max(1e-12,min(box[3]-box[1],bounds[3]-bounds[1]))
        left_gap=bounds[0]-box[2]
        right_gap=box[0]-bounds[2]
        near_side=(0<=left_gap<=policy.heading_max_gap_fraction or 0<=right_gap<=policy.heading_max_gap_fraction)
        if shared>=policy.heading_vertical_overlap and near_side:
            evidence.append({'label':item['label'],'score':score,'bbox_normalized':bounds})
    return evidence


def repeat_matches(candidate: dict[str, Any], all_candidates: list[dict[str, Any]], policy: Policy) -> list[str]:
    """Repeated heading badges require geometry AND heading adjacency on both.

    This is contextual repetition, not proof of identical graphic appearance.
    It deliberately avoids grouping pictures merely because they share a margin.
    """
    if not candidate['heading_evidence'] or candidate['caption_evidence']:
        return []
    matches=[]
    a=candidate['bbox_normalized']; aw=a[2]-a[0]; ah=a[3]-a[1]
    for other in all_candidates:
        if other is candidate or not other['heading_evidence'] or other['caption_evidence']:
            continue
        if other['area_fraction']>=policy.repeated_badge_area_fraction:
            continue
        b=other['bbox_normalized']; bw=b[2]-b[0]; bh=b[3]-b[1]
        same_size=(abs(aw-bw)/max(aw,bw)<=policy.repeat_size_relative_tolerance
                   and abs(ah-bh)/max(ah,bh)<=policy.repeat_size_relative_tolerance)
        same_margin=abs((a[0]+a[2]-b[0]-b[2])/2)<=policy.repeat_x_center_tolerance
        same_aspect=abs(aw/ah-bw/bh)/max(aw/ah,bw/bh)<=policy.repeat_aspect_relative_tolerance
        if same_size and same_margin and same_aspect:
            matches.append(other['candidate_id'])
    return matches


def filter_manifest(manifest: dict[str, Any], text_context: dict[str, list[dict[str, Any]]] | None = None,
                    policy: Policy = Policy()) -> tuple[dict[str, Any],dict[str, Any]]:
    """Input existing manifest + optional normalized PDF/OCR line bboxes.

    Return deep-copied filtered manifest and auditable decisions. Does no I/O.
    Candidates come ONLY from baseline figures, never from lower-score boxes.
    """
    if float(manifest.get('threshold',-1))!=policy.detector_threshold:
        raise ValueError('baseline threshold must remain exactly 0.5')
    text_context=text_context or {}
    output=copy.deepcopy(manifest)
    records=[]
    by_page={}
    for page in manifest.get('pages',[]):
        number=page['page_number'];width=float(page['width']);height=float(page['height'])
        if not math.isfinite(width) or not math.isfinite(height) or min(width,height)<=0:
            raise ValueError('invalid baseline page geometry')
        for index,figure in enumerate(page.get('figures',[])):
            bounds=checked_box(figure.get('bbox_pixels'))
            record={'candidate_id':f'{number}:{index}:{figure.get("file","")}',
                    'page_number':number,'figure_index':index,'file':figure.get('file'),
                    'label':figure.get('label'),'score':figure.get('score'),
                    'caption_evidence':[],'heading_evidence':[],'repeated_context_matches':[],
                    'reason_codes':[],'decision':'keep'}
            if bounds is None or bounds[0]<0 or bounds[1]<0 or bounds[2]>width or bounds[3]>height:
                record.update(decision='skip',reason_codes=['invalid_candidate_geometry'])
                records.append(record);continue
            norm=normalized_box(bounds,width,height)
            record.update(bbox_normalized=norm,area_fraction=area(norm),
                          width_fraction=norm[2]-norm[0],height_fraction=norm[3]-norm[1],
                          caption_evidence=caption_evidence(norm,text_context.get(str(number),[]),policy),
                          heading_evidence=heading_evidence(norm,page.get('boxes',[]),width,height,policy))
            try: score=float(figure.get('score',0))
            except (ValueError,TypeError): score=0
            if figure.get('label') not in {'image','chart'} or not math.isfinite(score) or score<policy.detector_threshold:
                record.update(decision='skip',reason_codes=['not_baseline_figure_at_threshold'])
            records.append(record)
    valid=[r for r in records if 'bbox_normalized' in r and r['decision']=='keep']
    for record in valid:
        record['repeated_context_matches']=repeat_matches(record,valid,policy)
        fraction=record['area_fraction']
        if fraction<policy.tiny_area_fraction:
            # Caption resemblance cannot rescue a tiny decorative candidate.
            record.update(decision='skip',reason_codes=['tiny_unverified_region'])
        elif fraction<policy.small_area_fraction and not record['caption_evidence']:
            reasons=['small_unverified_region_without_numbered_caption']
            if record['heading_evidence']:reasons.append('heading_badge_spatial_relation')
            if record['repeated_context_matches']:reasons.append('repeated_heading_badge_geometry')
            record.update(decision='skip',reason_codes=reasons)
        elif fraction<policy.repeated_badge_area_fraction and not record['caption_evidence'] and record['repeated_context_matches']:
            record.update(decision='skip',reason_codes=['repeated_heading_badge_geometry','heading_badge_spatial_relation'])
        else:
            record['reason_codes']=['retained_numbered_caption' if record['caption_evidence'] else 'retained_non_small_region']
    for record in records:
        by_page.setdefault(record['page_number'],{})[record['figure_index']]=record
    for page in output.get('pages',[]):
        decisions=by_page.get(page['page_number'],{})
        original=page.get('figures',[])
        page['figures']=[figure for i,figure in enumerate(original) if decisions[i]['decision']=='keep']
        page['icon_filter_skipped']=[{'figure':figure,'filter':decisions[i]} for i,figure in enumerate(original) if decisions[i]['decision']=='skip']
    audit={'schema_version':1,'method':'precision_first_geometry_caption_heading_filter_v2','policy':asdict(policy),
           'baseline_candidates':len(records),'kept':sum(r['decision']=='keep' for r in records),
           'skipped':sum(r['decision']=='skip' for r in records),'decisions':records,
           'limitations':['Not a semantic icon classifier; large or captioned icons can survive.',
                         'Small true diagrams can be skipped, including small numbered diagrams below tiny cutoff.',
                         'Text-layer/OCR omissions reduce caption evidence; unread text is never invented.',
                         'Repeated geometry is contextual evidence, not an image-identity assertion.',
                         'No additional candidate discovery or composite-panel merging is attempted.']}
    output['icon_filter']={key:value for key,value in audit.items() if key!='decisions'}
    output['review']='REQUIRES_ROOT_VISUAL_REVIEW_NOT_AUTO_PASS'
    return output,audit


def pdf_text_context(path: Path) -> dict[str,list[dict[str,Any]]]:
    """Optional read-only helper. Existing PDF text, no OCR/model or rendering."""
    import pymupdf
    result={}
    with pymupdf.open(path) as document:
        for number,page in enumerate(document,start=1):
            lines=[]
            rect=page.rect
            for block in page.get_text('dict').get('blocks',[]):
                for line in block.get('lines',[]):
                    text=''.join(str(span.get('text','')) for span in line.get('spans',[])).strip()
                    bounds=checked_box(line.get('bbox'))
                    if text and bounds is not None:
                        lines.append({'text':text,'bbox_normalized':[(bounds[0]-rect.x0)/rect.width,
                                      (bounds[1]-rect.y0)/rect.height,(bounds[2]-rect.x0)/rect.width,
                                      (bounds[3]-rect.y0)/rect.height],'source':'existing_pdf_text'})
            result[str(number)]=lines
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--pdf',type=Path)
    parser.add_argument('--text-context',type=Path,help='Optional existing normalized OCR/PDF line JSON; exclusive with --pdf')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.pdf and args.text_context:parser.error('use either --pdf or --text-context')
    baseline=json.loads(args.manifest.read_text())
    context=(pdf_text_context(args.pdf) if args.pdf else json.loads(args.text_context.read_text()) if args.text_context else {})
    filtered,audit=filter_manifest(baseline,context)
    args.output.mkdir(parents=True,exist_ok=False)
    filtered['baseline_manifest']=str(args.manifest.resolve())
    audit['baseline_manifest']=str(args.manifest.resolve())
    audit['text_context_available']=bool(context)
    (args.output/'filtered-manifest.json').write_text(json.dumps(filtered,ensure_ascii=False,indent=2))
    (args.output/'filter-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(json.dumps({key:audit[key] for key in ['baseline_candidates','kept','skipped']},ensure_ascii=False))


if __name__=='__main__':main()
