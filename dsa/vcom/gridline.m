function Out = gridline(ax,Action,color,In4,In5,In6,In7)
%
% gridline(ax,'init',color) - initialize grid line
% gridline(ax,'toggle')     - toggle grids (on/off)
% gridline(ax,'get')        - get current on/off state of grids
% gridline(ax,'off')        - turn grids off
% gridline(ax,'on')         - turn grids on
% gridline(ax,'update')     - updates grids
% gridline(ax)              - updates grids
%
% gridline always returns 0/1 if the grids are off/on
% In4 to In7 may be used to assign additional gridline properties
%
% To see what it does, try typing this at the command promt:
%    >> a = axes;                 % create axis
%    >> gridline(a,'init');       % draw grid lines
%    >> set(a,'ylim',[0 6]);      % change axis limits
%    >> gridline(a);              % update grid lines

if nargin==1 Action='update'; end;

if strcmp(Action,'init')
  if nargin<3 color = [.3 .3 .3]; end;  % default color
  axes(ax);
  n = findobj(ax,'type','line');
  if length(n) n = get(n(1),'ButtonDownFcn'); else n=''; end;
  gridH = line('Tag','NoCursor','color',color,'ButtonDownFcn',n,...
               'LineStyle',':','UserData','grid');
% 'Erase','xor',
  Action = 'on';                 % start with grids on
  if nargin>4 set(gridH,In4,In5); end;  % extra line properties
  if nargin>6 set(gridH,In6,In7); end;  % more line properties
  % put grid on bottom of stacking order
  set(ax,'children',flipud(get(ax,'children')));
   
else  gridH = findobj(ax,'UserData','grid');  z = get(gridH,'z');
      Out = length(z)>1 | z(1);  % get current grid on/off state
end;  

if     strcmp(Action,'toggle')  if Out Action='off'; else Action='on'; end;
elseif strcmp(Action,'update')  if Out Action='on'; end; 
end;

x=0; y=0;
if     strcmp(Action,'off') Out=0;  z=0;
elseif strcmp(Action,'on')  Out=1;  z=1;      % here for Action = 'on'
  xl = double(get(ax,'Xlim'));  xt = double([xl(1) get(ax,'Xtick') xl(2)]);  n = length(xt);
  yl = double(get(ax,'Ylim'));  yt = double([yl(1) get(ax,'Ytick') yl(2)]);  m = length(yt);
  if xt(1) >= xt(2)   xt = xt(2:n);   n=n-1; end;
  if yt(1) >= yt(2)   yt = yt(2:m);   m=m-1; end;
  if xt(n-1) >= xt(n) xt = xt(1:n-1); n=n-1; end;
  if yt(m-1) >= yt(m) yt = yt(1:m-1); m=m-1; end;
  s = ones(1,n+m-4);
  if length(s)
    x = [1 n n];   y = [1 m m];  z = double(ones(3,1));
    x = [xt(x)' * ones(1,m-2)  z * xt(2:n-1)];
    y = [z * yt(2:m-1)  yt(y)' * ones(1,n-2)];
    z = [0; 0; NaN] * s;
  end;
end;
set(gridH,'x',x(:),'y',y(:),'z',z(:));
