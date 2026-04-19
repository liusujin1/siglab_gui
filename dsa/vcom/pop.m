function h = pop(varargin)
%
% pop.mi                    Paul Mennen, DSPT                    7-Mar-98
%
% function h = pop(hin,'PropertyName1','PropertyValue1',...)
%
% If hin is omitted then a new popup is created with the specified
% properties. The new popup handle is returned in "h". (The
% popup handle is the handle of the popup text object - the text
% object that appears when the popup is not open.)
%
% If hin is included (scaler), then the specified properties are
% applied to the previously defined popup with handle hin.
%
% If hin is a vector then PropertyName1 of hin(k) is set to the
% kth row of PropertyValue1. If PropertyValue1 doesn't have that many
% rows, the last row will be used. Only 1 property is allowed.
%
% If a property name is given which isn't in the following list
% then the property is applied to the popup text obect:
%
% Property name   Property value
% -------------   -------------------------------------------------------------
% choices         A cell array of strings specifying the choices given when
%                 the popup is selected.
% value           An integer specifying the current choice (1 = the 1st choice)
% callbk          A callback to be executed when the popup value is changed.
%                 The argument $VAL will be replaced with the popup value
%                 The argument $STR will be replaced with the popup string
% colorbk         The background color used when the popup is open.
% colorfr         The foreground color used when the popup is open.
% location        height or [x y width height] for the popup.
% offset          y or [x y] offset added to location when popup is open.
% enable          0=disable, 1=enable (default=1)
% hide            A list of objects to hide before opening the popup.
% interp          'none' or 'tex' (default='none')
%
% pop(hin,'get','choices') returns cell array of choices.
% pop(hin,'get','value')   returns currently selected choice (1 = 1st choice)

%include
% props.m                      Paul Mennen                         10-Mar-99
%%%%%%%%%%%%%%%end_include

%define
                 % indices into text objects UserData property
%Uvalue=1;        % pointer to choices array
%Uaxis;           % popup axes handle
%Ufr;             % popup foreground color
%Uoff;            % offset for opened popup [x y]
%Uhide;           % objects to hide when popup open
%Uterp;           % interpreter for popup text objects
%Uchoice;         % popup choices
%Ucallbk;         % popup callback
%Uena;            % popup enable
%end_define

k = 1;  a1 = varargin{1}; na = length(varargin);  % start at first argument
if isstr(a1)
     ax = axes('XTickLabel','','YTickLabel','','visible','off');
     u = {1 ax [0 1 1] [0 0] [] 'none' 0 '' 1};
     h = text(0,0,'','interpreter','none','user',u);
     set(h,'ButtonDownFcn',['pop(' int2str(8192*h) ',''open'',0);']);
else h = a1;  n = length(h);
     if n==1 if h>8192 h=h/8192; end;
             u=get(h,'user'); ax=u{2}; k=k+1;
     else    pn = varargin{2};  pv = varargin{3};  r = size(pv,1);
             if r 
                for k=1:n
                    pop(h(k),pn,pv(min(k,r),:));
                end;
             else 
                for k=1:n
                    pop(h(k),pn,[]);
                end;
             end;
             return;
     end;
end;
while k<=na
  pn  = lower(varargin{k});  pv = varargin{k+1}; k=k+2;
  switch pn
  case 'choices',  n = length(pv);  set(ax,'Ylim',[-n 0]);
                   set(h,'Pos',[.08 .5-n]);  u{7} = pv; set(h,'user',u);
  case 'location', if max(pv)<1 s='normal'; else; s='pixels'; end;
                   if length(pv)==1 f=get(ax,'pos');  pv=[f(1:3) pv]; end;
                   set(ax,'units',s,'pos',pv);
  case 'offset',   if length(pv)==1 pv=[0 pv]; end;  u{4}=pv; set(h,'user',u);
  case 'hide',     u{5} = pv;   set(h,'user',u);
  case 'value',    u{1} = abs(pv);  set(h,'user',u);  v = get(ax,'Visible');
                   if v(2)=='n'
                     f = u{4};
                     if any(f) set(ax,'Pos',get(ax,'Pos')-[f 0 0]); end;
                     set(ax,'visible','off');  c = get(ax,'children');  c(find(c==h)) = [];
                     delete(c);  set([h u{5}],'visible','on');
                     set(gcf,'Share','off'); set(gcf,'Share','on'); % BUG!
                   end;
                   c = u{7}{abs(pv)};  set(h,'str',c);
                   if pv>0 eval(strrep(strrep(u{8},'$STR',c),...
                                '$VAL',int2str(pv)));
                   end;
  case 'open',     if ~u{9} return; end;   % if not enabled, don't open
                   set([h u{5}],'visible','off');  ch = u{7};  n = length(ch);
                   f = u{4};  p=get(ax,'Pos'); pt = p(2)+p(4)+f(2);
                   s = get(ax,'Units');
                   if s(1)=='n' & pt>1
                     f(2)=f(2)-pt+1; u{4}=f; set(h,'user',u);
                   end;
                   set(ax,'Pos',p+[f 0 0],'visible','on');  axes(ax);
                   s = ['pop(' int2str(8192*h) ',''value'','];
                   for m=1:n 
                       th = text(.08,.5-m,ch{m});
                       set(th,'color',u{3},'interpreter',u{6},...
                                     'ButtonDownFcn',[s int2str(m) ');']);
                       if m==u{1} set(th,'FontWeight','bold'); end;
                   end;
                   clr = get(ax,'color');
                   z = zeros(1,n-1);  x = [z;z+1;z];
                   y = 1:n-1;  y = -[y;y;y];  z = [z;z;z+NaN];
                   line('x',x(:),'y',y(:),'z',z(:),'color',clr+.4*(clr<.5)-.2);
  case 'callbk',   u{8} = pv;  set(h,'user',u);
  case 'colorbk',  set(ax,'color',pv,'xcolor',pv,'ycolor',pv);
  case 'colorfr',  u{3} = pv;   set(h,'user',u);
  case 'enable',   u{9} = pv;  set(h,'user',u);
  case 'interp',   u{6} = pv; set(h,'user',u);  set(h,'interpreter',pv);
  case 'get',      if pv(1)=='c' h = u{7}; else h = u{1}; end;
  otherwise,       set(h,pn,pv);
  end;
end; % end while k<na
