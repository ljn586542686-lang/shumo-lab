"""Proposed optional declarative layout compiler. No I/O, execution or receipts.

Numerical series are referenced by index from the validated figure spec.
Primitives describe explicitly declared diagram geometry and annotations.
Unknown fields are rejected; MATLAB text is quoted, never executed.
"""
import math
from copy import deepcopy

class PublicationLayoutError(ValueError):
    pass

def require(condition, message):
    if not condition: raise PublicationLayoutError(message)

def number(x):
    require(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x),'LAYOUT_FINITE_NUMBER_REQUIRED')
    return float(x)

def vector(x,n):
    require(isinstance(x,list) and len(x)==n,'LAYOUT_VECTOR_INVALID')
    return [number(v) for v in x]

def fields(d,allowed,required=()):
    require(isinstance(d,dict) and not (set(d)-set(allowed)) and set(required)<=set(d),'LAYOUT_FIELDS_INVALID')

def text_value(x):
    require(isinstance(x,str) and len(x)<=300 and not any(ord(c)<32 for c in x),'LAYOUT_TEXT_INVALID')

def color(x):
    require(all(0<=v<=1 for v in vector(x,3)),'LAYOUT_COLOR_INVALID')

def style(d):
    if 'color' in d: color(d['color'])
    if 'width' in d:require(.3<=number(d['width'])<=3,'LAYOUT_WIDTH_INVALID')
    if 'style' in d:require(d['style'] in ['-','--',':','-.','none'],'LAYOUT_LINESTYLE_INVALID')

def normalize_layout(raw,figure):
    fields(raw,['version','size_cm','panels'],['version','size_cm','panels'])
    require(type(raw['version']) is int and raw['version']==1,'LAYOUT_VERSION_INVALID')
    require(all(8<=n<=30 for n in vector(raw['size_cm'],2)),'LAYOUT_SIZE_INVALID')
    panels=raw['panels'];require(isinstance(panels,list) and 1<=len(panels)<=6,'LAYOUT_PANELS_INVALID')
    for p in panels:
        fields(p,['position','x_limits','y_limits','x_label','y_label','axis_visible','equal','series','primitives','x_ticks','x_tick_labels','y_ticks','y_tick_labels'],['position','x_limits','y_limits'])
        a,b,w,h=vector(p['position'],4);require(a>=0 and b>=0 and w>0 and h>0 and a+w<=1.000001 and b+h<=1.000001,'LAYOUT_POSITION_INVALID')
        for k in ['x_limits','y_limits']:
            lim=vector(p[k],2);require(lim[0]<lim[1],'LAYOUT_LIMITS_INVALID')
        for k in ['x_label','y_label']:
            if k in p:text_value(p[k])
        for k in ['axis_visible','equal']:
            if k in p:require(isinstance(p[k],bool),'LAYOUT_BOOLEAN_INVALID')
        for k in ['x','y']:
            if k+'_ticks' in p:
                v=p[k+'_ticks'];require(isinstance(v,list) and len(v)<=30,'LAYOUT_TICKS_INVALID');[number(x) for x in v]
            if k+'_tick_labels' in p:
                labels=p[k+'_tick_labels'];require(isinstance(labels,list) and len(labels)==len(p.get(k+'_ticks',[])),'LAYOUT_TICK_LABELS_INVALID');[text_value(x) for x in labels]
        refs=p.get('series',[]);require(isinstance(refs,list) and len(refs)<=20,'LAYOUT_SERIES_INVALID')
        for r in refs:
            fields(r,['index','color','width','style','endpoint_marker'],['index']);style(r)
            i=r['index'];require(isinstance(i,int) and not isinstance(i,bool) and 0<=i<len(figure.get('series',[])),'LAYOUT_SERIES_INDEX_INVALID')
            s=figure['series'][i];require('x' in s and 'y' in s,'LAYOUT_XY_SERIES_REQUIRED')
            if 'endpoint_marker' in r:require(isinstance(r['endpoint_marker'],bool),'LAYOUT_BOOLEAN_INVALID')
        primitives=p.get('primitives',[]);require(isinstance(primitives,list) and len(primitives)<=300,'LAYOUT_PRIMITIVES_INVALID')
        for q in primitives:
            require(isinstance(q,dict),'LAYOUT_PRIMITIVE_INVALID')
            kind=q.get('kind');require(kind in ['rectangle','line','arrow','double_arrow','arc','text'],'LAYOUT_PRIMITIVE_INVALID')
            common=['kind','color','width','style'];shape_fields={'rectangle':['rect'],'line':['a','b'],'arrow':['a','b'],'double_arrow':['a','b'],'arc':['center','radius','angles'],'text':['at','text','size','align']}[kind]
            fields(q,common+shape_fields,['kind']+([x for x in shape_fields if x not in ['size','align']]))
            style(q)
            for k in ['a','b','at','center','angles']:
                if k in q:vector(q[k],2)
            if kind=='rectangle':require(all(v>0 for v in vector(q['rect'],4)[2:]),'LAYOUT_RECT_INVALID')
            if kind=='arc':require(number(q['radius'])>0,'LAYOUT_RADIUS_INVALID')
            if kind=='text':
                text_value(q['text']);require(6<=number(q.get('size',9))<=18,'LAYOUT_FONT_INVALID');require(q.get('align','left') in ['left','center','right'],'LAYOUT_ALIGN_INVALID')
    return deepcopy(raw)

def quote(s):return "'"+s.replace("'","''")+"'"
def vec(v):return '['+' '.join(format(float(x),'.17g') for x in v)+']'
def cell(v):return '{'+','.join(quote(s) for s in v)+'}'
INK=[.14,.21,.27]

def compile_layout(figure):
    """Return only body commands; existing compiler retains export/hash ownership."""
    layout=normalize_layout(figure['publication_layout'],figure)
    w,h=layout['size_cm'];out=['delete(ax);',f"set(fig,'Position',[2 2 {w:g} {h:g}]);"]
    for j,p in enumerate(layout['panels']):
        out += [f"ax=axes('Parent',fig,'Units','normalized','Position',{vec(p['position'])},'Color','w','XColor',{vec(INK)},'YColor',{vec(INK)});", "hold(ax,'on'); box(ax,'off');", "set(ax,'FontName','Microsoft YaHei','FontSize',8,'LineWidth',0.65,'TickDir','out','TickLabelInterpreter','tex');",f"xlim(ax,{vec(p['x_limits'])}); ylim(ax,{vec(p['y_limits'])});"]
        for q in p.get('primitives',[]):
            if q['kind']=='rectangle':out += [f"rectangle(ax,'Position',{vec(q['rect'])},'FaceColor',{vec(q.get('color',[.95,.96,.97]))},'EdgeColor','none','HandleVisibility','off');"]
        for r in p.get('series',[]):
            s=figure['series'][r['index']];co=vec(r.get('color',INK));ls=quote(r.get('style','-'));lw=r.get('width',1)
            out += [f"plot(ax,{vec(s['x'])},{vec(s['y'])},'Color',{co},'LineStyle',{ls},'LineWidth',{lw:g},'HandleVisibility','off');"]
            if r.get('endpoint_marker'):out += [f"plot(ax,{s['x'][-1]:.17g},{s['y'][-1]:.17g},'o','Color',{co},'MarkerFaceColor',{co},'MarkerSize',4,'HandleVisibility','off');"]
        for q in p.get('primitives',[]):
            kind=q['kind'];co=vec(q.get('color',INK));lw=q.get('width',1);ls=quote(q.get('style','-'))
            if kind=='text':
                x,y=q['at'];out += [f"text(ax,{x:.17g},{y:.17g},{quote(q['text'])},'Color',{co},'FontName','Microsoft YaHei','FontSize',{q.get('size',9):g},'Interpreter','tex','HorizontalAlignment',{quote(q.get('align','left'))},'VerticalAlignment','middle','Clipping','off');"]
            elif kind in ['line','arrow','double_arrow']:
                x,y=q['a'];u,v=q['b'];out += [f"plot(ax,[{x:.17g} {u:.17g}],[{y:.17g} {v:.17g}],'Color',{co},'LineWidth',{lw:g},'LineStyle',{ls},'HandleVisibility','off');"]
                if kind!='line':
                    # Draw arrowheads in physical panel coordinates, independent of data aspect ratio.
                    sx=layout['size_cm'][0]*p['position'][2]/(p['x_limits'][1]-p['x_limits'][0]);sy=layout['size_cm'][1]*p['position'][3]/(p['y_limits'][1]-p['y_limits'][0])
                    if p.get('equal'):sx=sy=min(sx,sy)
                    for a,b in ([(q['a'],q['b'])] if kind=='arrow' else [(q['a'],q['b']),(q['b'],q['a'])]):
                        dx=(b[0]-a[0])*sx;dy=(b[1]-a[1])*sy;length=math.hypot(dx,dy)
                        if length==0:continue
                        dx/=length;dy/=length;head=min(.18,length*.25);base=(b[0]-head*dx/sx,b[1]-head*dy/sy);half=.42*head
                        xs=[b[0],base[0]-half*dy/sx,base[0]+half*dy/sx];ys=[b[1],base[1]+half*dx/sy,base[1]-half*dx/sy]
                        out += [f"patch(ax,{vec(xs)},{vec(ys)},{co},'EdgeColor','none','HandleVisibility','off');"]
            elif kind=='arc':
                x,y=q['center'];a,b=q['angles'];rad=q['radius'];out += [f"theta=linspace({a:g},{b:g},80)*pi/180; plot(ax,{x:g}+{rad:g}*cos(theta),{y:g}+{rad:g}*sin(theta),'Color',{co},'LineWidth',{lw:g},'HandleVisibility','off');"]
        out += [f"xlabel(ax,{quote(p.get('x_label',''))},'Interpreter','tex','FontSize',9,'Color',{vec(INK)});",f"ylabel(ax,{quote(p.get('y_label',''))},'Interpreter','tex','FontSize',9,'Color',{vec(INK)});"]
        for k in ['x','y']:
            if k+'_ticks' in p:out += [f"set(ax,'{k.upper()}Tick',{vec(p[k+'_ticks'])});"]
            if k+'_tick_labels' in p:out += [f"set(ax,'{k.upper()}TickLabel',{cell(p[k+'_tick_labels'])});"]
        if p.get('equal'):out += ["axis(ax,'equal');",f"xlim(ax,{vec(p['x_limits'])}); ylim(ax,{vec(p['y_limits'])});"]
        if not p.get('axis_visible',True):out += ["axis(ax,'off');"]
    return out
