function LN = cplot(varargin);
%
% cplot.m: A substitute for plot                                  7-May-98
%
% examples:                 (nt = number of traces)
% ---------                 ---------------------------------------------
% cplot(y)                   : same as plot(y) except cursors are provided
% cplot(x,y)                 : same as plot(x,y) except cursors are provided
% cplot(x1,y1,x2,y2,...)     : up to 14 traces may be ploted on one grid
% cplot(x,y,'Xlim',[0 5])    : Specify x-axis limits
% cplot(x,y,'Ylim',[1 2])    : Specify y-axis limits
% cplot(x,y,'Xscale','log')  : Specify logarithmic x-axis
% cplot(x,y,'Yscale','log')  : Specify logarithmic y-axis
% cplot(x,y,'LabelX','a')    : Specify x-axis label
% cplot(x,y,'LabelY','b')    : Specify y-axis label
% cplot(x,y,'Title','c')     : Specify plot title
% cplot(x,y,'FigName','d')   : Specify figure name
% cplot(x,y,'FIG_BKc',c)     : Specify figure background color (c = 1x3)
% cplot(x,y,'PLT_BKc',c)     : Specify plot background color (c = 1x3)
% cplot(x,y,'CURSORc',c)     : Specify cursor color (c = 1x3)
% cplot(x,y,'MARKc',c)       : Specify cursor mark color (c = 1x3)
% cplot(x,y,'XY_AXc',c)      : Specify axis color (c = 1x3)
% cplot(x,y,'XY_LBLc',c)     : Specify axis label color (c = 1x3)
% cplot(x,y,'TRACEc',c)      : Trace color. (c = nt x 3)
% cplot(x,y,'GRIDc',c)       : Grid color. (c = 1x3)
% cplot(x,y,'Grid',g)        : Grid enable. g = 'on' or 'off'
% cplot(x,y,'AllColors',c)   : Specifies colors for all 9 of above properties
% cplot(x,y,'ENAcur',e)      : To enable cursors on all traces, e=ones(1,nt)
% cplot(x,y,'Position',p)    : p = [x(left) y(bottom) height width] in pixels
% cplot(x,y,'TRACEid',t)     : Trace ID labels (t = nt x 6) t=0 to disable
% cplot(x,y,'Style',s)       : Line styles (s is a string array of nt rows)
%                             (s may also be a string of nt characters)
% cplot(x,y,'Marker',s)      : Trace markers (s is a string array of nt rows)
% cplot(x,y,'ENApre',m)      : Enable metric prefixes. m=[ENAx ENAy] (0 or 1)
%
% The arguments may be in any order except that when plotting x vs. y,
% the y argument must immediately follow the x vector.
% For example:
%    cplot('FigName','My Plot',x,y,'LabelY','two traces',x,y2);
% which (assuming y is a row vector) could also be written as:
%    cplot('FigName','My Plot',x,[y;y2],'LabelY','two traces');
% If y is real, then cplot(y) is the same as cplot(1:length(y),y).
% If y is complex, then cplot(y) is the same as cplot(real(y),imag(y)).
% As with plot, cplot returns a vector of line handles.
% Currently cplot may plot up to 14 traces. To increase this number,
% change the variable "maxLN".
%
% Note that the figure window size is adjustable using the mouse

y1 = varargin{1};
if strcmp(y1,'Click')
  y2 = varargin{2};  AX = findobj(gcf,'Tag','Click');
  if ischar(y2)
    switch y2
    case 'TGLlogx',
      if strcmp(get(AX,'Xscale'),'log')
           set(AX,'Xscale','linear');
      else set(AX,'Xscale','log');
           x = get(AX,'Xlim'); if x(1)<=0 set(AX,'xlim',x(2)*[.001 1]); end;
      end;
    case 'TGLlogy',
      if strcmp(get(AX,'Yscale'),'log')
           set(AX,'Yscale','linear');
      else set(AX,'Yscale','log');
           y = get(AX,'Ylim'); if y(1)<=0 set(AX,'ylim',y(2)*[1e-12 1]); end;
      end;
    case 'TGLgrid', set(AX,'TickLen',(1-gridline(AX,'toggle'))*[.01 .025]);
    end;  % end switch y2
    gridline(AX,'update');
  else  y2 = y2/8192;  k = y2(1);  s = get(k,'visible');  % toggle trace vis
        if s(2)=='n' set(k,'visible','off'); set(y2(2),'fontangle','italic');
        else         set(k,'visible','on');  set(y2(2),'fontangle','normal');
        end;
  end;  % end ischar(y2)
  return;
end;

maxLN = 14;                % Maximum number of lines
% now set defaults (all can be changed via cplot arguments)
posFIG  =  [9,9,600,425];  % Position: figure
axisPOS = 1;               % Axis position modifier
cFIG_BK =  [.25 .25 .25];  % Figure background color
cPLT_BK =  [0   0   0  ];  % Plot background color
cXY_AX  =  [1   1   1  ];  % Axis color
cXY_LBL =  [.64 .78 .94];  % Axis label color
CURcDEF =  [1   1   .50];  % Cursor color default
cCURSOR =  CURcDEF;        % Cursor color
cMARK   =  [1   0   0  ];  % Cursor mark color
cGRID   =  [.3  .3 .3  ];  % Grid color
cTRACE  = [0 1 0; 0 0 1; 1 0 0; 1 0 1; 1 1 0; 0 1 1; 1 1 1]; % Traces 1-7
cTRACE  = [cTRACE; .5 * cTRACE];  % append colors for traces 8-14
for k=15:maxLN cTRACE = [cTRACE; .5 * cTRACE(k-7,:)]; end;
LabelX = '';               % x-axis label
LabelY = '';               % y-axis label
Title = '';                % plot title
FigName = 'cplot';         % figure name
Xlim = 'default';          % let Matlab pick Xlim by default
Ylim = 'default';          % let Matlab pick Ylim by default
Xscale = 'linear';
Yscale = 'linear';
Grid = 'on';               % Grids on by default
ENApre = ones(1,2);        % enable metric prefixes on x and y axis
ENAcur = ones(1,maxLN);    % enable cursors for all traces
Style  = 0;                % use default line style
Marker = 0;                % use default line markers (none)
TRACEid = reshape(sprintf('Line%2d',1:maxLN),6,maxLN)'; % default trace names

sz      = posFIG([3 4 3 4]);
posAX   = [80,49,503,363]./sz;  % Position: axis
posPEAK = [5,23,18,15]./sz;     % Position: cursor peak finder button
posVALY = [5,5,18,15]./sz;      % Position: cursor valley finder button
posDEL  = [26,5,40,20]./sz;     % Position: delta cursor button
posCXL  = [75,5,16,19]./sz;     % Position: cursor X label
posC1X  = [95,4,70,21]./sz;     % Position: cursor 1X
posC2X  = [168,4,70,21]./sz;    % Position: cursor 2X
posCYL  = [420,5,16,19]./sz;    % Position: cursor Y label
posC1Y  = [440,4,70,21]./sz;    % Position: cursor 1Y
posC2Y  = [513,4,70,21]./sz;    % Position: cursor 2Y

if ~nargin disp('For help on using cplot, type help cplot'); return;  end;
fontsz = fontsize;      % choose font appropriate to screen res
FIG = figure('NumberTitle','Off','Menu','None',...
             'BackingStore','off','PaperPositionMode','auto',...
             'InvertHardcopy','off','PaperOrientation','landscape',...  
             'PaperUnits','normalized','pos',posFIG+[1 0 0 0]);
AX = axes('Units','Normal','FontSize',fontsz,'Box','On','Tag','Click');

LN = [];  nt = 0;   % Initialize line handles & number of traces
k = 1;    % start at first y argument
while k<=nargin
  y  = varargin{k};  k=k+1;
  if isstr(y)
    if k>nargin disp('Not enough arguments'); return;  end;
    y = lower(y);  yy = varargin{k};   k=k+1;
    switch y
      case 'labelx',    LabelX  = yy;
      case 'labely',    LabelY  = yy;
      case 'title',     Title   = yy;
      case 'xscale',    Xscale  = yy;
      case 'yscale',    Yscale  = yy;
      case 'figname',   FigName = yy;
      case 'tracec',    cTRACE  = yy;
      case 'fig_bkc',   cFIG_BK = yy;
      case 'plt_bkc',   cPLT_BK = yy;
      case 'xy_axc',    cXY_AX  = yy;
      case 'xy_lblc',   cXY_LBL = yy;
      case 'cursorc',   cCURSOR = yy;
      case 'markc',     cMARK   = yy;
      case 'gridc',     cGRID   = yy;
      case 'allcolors', vcol_h;  cTRACE(1:4,:) = yy(TRA_1c:TRA_4c,:);
                        cFIG_BK = yy(FIG_BKc,:);  cPLT_BK = yy(PLT_BKc,:);
                        cXY_AX  = yy(XY_AXc,:);   cXY_LBL = yy(XY_LBLc,:);
                        cCURSOR = yy(CURSORc,:);  cMARK   = yy(MARKc,:);
                        cGRID   = yy(GRIDc,:);
      case 'grid',      Grid    = yy;
      case 'xlim',      Xlim    = yy;
      case 'ylim',      Ylim    = yy;
      case 'style',     Style   = yy;
      case 'marker',    Marker  = yy;
      case 'enacur',    ENAcur  = yy;
      case 'position',  posFIG  = yy;
      case 'axispos',   axisPOS = yy;
      case 'traceid',   TRACEid = yy;
      case 'enapre',    ENApre  = yy;
    end;
  else  yy = 'a';
        if k<=nargin  yy = varargin{k};  end;
        if isstr(yy) if isreal(y) yy=y; y=1:length(yy);
                     else         yy=imag(y); y=real(y);
                     end;
        else k=k+1;
        end;
        H = line(y,yy);  LN = [LN; H];   nt = nt + length(H);
        if nt>maxLN disp(['Max # of traces = ' int2str(maxLN)]); return; end;
  end;
end;

curclr = [.5 .5 .5; 0 0 0; cCURSOR; cMARK];  mrk = '';
if cCURSOR==CURcDEF & sum(ENAcur(1:nt))>1 cCURSOR = [0 0 0]; end;
for k=1:nt  set(LN(k),'Color',cTRACE(k,:));

            if ENAcur(k) curclr = [curclr; cCURSOR];  mrk = [mrk '+'];
            else         set(LN(k),'Tag','NoCursor');
            end;
end;
if Style   if length(Style(:,1)) < nt Style=Style'; end;
           for k=1:nt set(LN(k),'LineStyle',Style(k,:)); end;
end;
if Marker  if length(Marker(:,1)) < nt Marker=Marker'; end;
           for k=1:nt set(LN(k),'Marker',Marker(k,:)); end;
end;

cursor(AX,'init',...   % initialize cursor objects
  [posCXL;posCYL;posC1X;posC2X;posC1Y;posC2Y;posPEAK;posVALY;posDEL],...
  curclr,['x:';'y:'], mrk, .8*fontsz, ['%7w';'%7w'],'on',1,'cplot Click 0');
set(AX,'Position',posAX.*axisPOS,'Xscale',Xscale,'Yscale',Yscale,...
       'Color',cPLT_BK,'Xcolor',cXY_AX,'Ycolor',cXY_AX);

if isstr(Xlim) Xlim = get(AX,'xlim'); end;
[prefix mult] = metricp(max(abs((Xlim))));  % compute metric prefix
if ENApre(1) & mult~=1
  for k=1:nt set(LN(k),'x',mult*get(LN(k),'x')); end; % apply data multiplier
  LabelX = [prefix LabelX];                 % apply prefix to Y label
else mult = 1;
end;
set(AX,'xlim',Xlim*mult);

if isstr(Ylim) Ylim = get(AX,'ylim'); end;
[prefix mult] = metricp(max(abs((Ylim))));  % compute metric prefix
if ENApre(2) & mult~=1
  for k=1:nt set(LN(k),'y',mult*get(LN(k),'y')); end; % apply data multiplier
  LabelY = [prefix LabelY];                 % apply prefix to Y label
else mult = 1;
end;
set(AX,'ylim',Ylim*mult);
xlabel(LabelX,'Color',cXY_LBL,'HandleVis','on');
ylabel(LabelY,'Color',cXY_LBL,'HandleVis','on');
if length(Title)  set(AX,'Position',posAX .* [1 1 1 .95]);
                  title(Title,'Color',cXY_LBL,'HandleVis','on');
end;
gridline(AX,'init',cGRID);
set(AX,'TickLen',(1-gridline(AX,Grid))*[.01 .025]);
c = (cXY_AX + cFIG_BK)/2 ;
axes('Units','Normal','Position',[5 60 34 75]./sz,'Ylim',[-4 0]-.5,...
     'Box','On','Color',cFIG_BK,'Xcolor',c,'Ycolor',c,...
     'XtickLabel',' ','YtickLabel',' ','TickLen',[0 0]);
set([text(.07,-1,'LogX') text(.07,-2,'LogY') ...  
     text(.07,-3,'Grid') text(.07,-4,'Print')], ...
      'FontSize',fontsz,'Color',cXY_LBL,{'ButtonDownFcn'},...
      {'cplot Click TGLlogx'; 'cplot Click TGLlogy'; ...
       'cplot Click TGLgrid'; 'hcpyv5(''init'',gcf)'});
if nt>1 & TRACEid
  h = 17*nt; h = [4,416-h,42,h]./sz;   % Position: trace ID
  axes('Units','Normal','Position',h,'Ylim',[-nt 0]-.5,...
       'Color',cPLT_BK,'Xcolor',cFIG_BK,'Ycolor',cFIG_BK,...
       'XtickLabel',' ','YtickLabel',' ','TickLen',[0 0]);
  for k=1:nt t = text(.07,-k,TRACEid(k,:));
             set(t,'FontSize',fontsz,'color',cTRACE(k,:),'ButtonDownFcn',...
             ['cplot(''Click'',[',int2str([LN(k) t]*8192),']);']);
  end;
end;
for k=fliplr(findobj('Type','figure')')
  if get(k,'Position') == posFIG  posFIG = posFIG + [30 25 0 0]; end;
end;
set(FIG,'Position',posFIG,'Name',FigName,'Color',cFIG_BK);
axes(AX);
% end function cplot
