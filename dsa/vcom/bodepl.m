function Bodepl(Action,In1); % bodepl.mi: BODE PLOT with mapping    13-Jan-99

% Calling sequences:
%  bodepl;                         % uses data found in \siglab\default.vna
%  bodepl('init','vna');           % uses current average from vna
%  bodepl('init','vss');           % uses current average from vss
%  bodepl('init','path\file.ext'); % uses data found in specified file

global BODEh;                 % Main handle vector
global BDx; global BDy;       % Data from file or from vna/vss
global Xlimit;                % X axis limits to display
global LogX;                  % 1 for LinX, 2 for LogX
global Wrap;                  % 1 for wrapped, 2 for unwrapped
global FuncType;              % 1 for Xfer, 2 for Mapping(Xfer)
global MargOn;                % 1 to turn margin info on, 0 otherwise
global MAPk0; global MAPk1;   % Mapping = k0 * T / (k1 + T)
global SetupFile;             % Setup file name

%include
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%%% props.m                      Paul Mennen                         10-Mar-99
%%%%%%%%%%%%%%%end_include

%define
% Misc definitions ---------------------------------------------------------
  %bodepX = FileName$;      % name of current .m file
  %FILEkey = 'BODEPLv1.0';                
  %XFER = 1;  %OPENLOOP;     % Function types 1,2
  %str5f( = 'String',sprintf('%-1.5g',   % convert to 5 digit floating string
  %BADfile  = 'disp(''No bodepl.mat file. Creating new one.''); return;';
  %ERRfile  = 'disp(''bodepl argument specifies an invalid file''); return;';

% Handle index definitions -------------------------------------------------
  %FIG=1;                % main figure window
  %AX1; %AX2; %AX3;        % Mag axis, Phase axis, Nyquist axis
  %PLTm; %PLTp; %PLTn;     % Mag trace, Phase trace, Nyquist trace
  %CHSEL; %XTYPE;         % Channel select and Xaxis type popups
  %PWRAP; %FUNC;          % Phase Wrap type, Function type popups
  %MARG; %PRN; %FRM;       % Margins and Print buttons, Control frame
  %CUR; %CURn;            % Cursor ID for BODE, Nyquist plots
  %K0; %K1; %MT0; %MT1;     % Mapping constants, Mapping text
  %PLBL; %MINFO;          % Phase label, Margin info

% Actions -------------------------------------------------------------------
  %ACT_init; %ACT_update; %ACT_marg; %ACT_print; %ACT_grid; %ACT_quit;
  %ACTupdate = 'Callback',$$bodepX(ACT_update)$$; % complete screen update

% Control positioning ------------------------------------------------------
  %posFIG  = [15,10,640,435];   % Default figure position
  %Y1=13; %Y2=43;
  %X00=12;
  %posCHAN = [X00,Y1,78,20];    % Position: Channel select popup
  %X0=X00+85;
  %posXLOG = [X0,Y1,80,20];     % Position: Xaxis type
  %X1=X0+87;
  %posWRAP = [X1,Y1,80,20];     % Position: Phase Wrap type
  %X2=X1+87;
  %posFUNC = [X2,Y1,99,20];     % Position: Function type
  %X3=X2+102;
  %posK0   = [X3,Y1-2,57,21];   % Position: k0 mapping constant
  %X4=X3+57; 
  %posMT1  = [X4,Y1-2,42,19];   % Position: mapping text 1
  %X5=X4+42;
  %posK1   = [X5,Y1-2,57,21];   % Position: k1 mapping constant
  %X6=X5+57;
  %posMT2  = [X6,Y1-2,30,19];   % Position: mapping text 2
  %X7=X6+46;
  %posMRGN = [X7,5,60,20];      % Position: Margin button
  %posPRN  = [X7,30,60,20];     % Position: Print button
  %posDLG  = [5,4,X7-14,34];    % Position: Control group frame
  %posAX   = [56,90,510,340];   % Position: Axis (Margins off)
  %posAXM  = [56,90,510,317];   % Position: Axis (Margins on)
  %posPHYL = [1.11,.33];        % Position: Phase Y-axis label
  %posMINF = [-.1,1.04];        % Position: Margin information
  %posPEAK = [5,Y2+17,16,15];   % Position: cursor peak finder button
  %posVALY = [5,Y2-1,16,15];    % Position: cursor valley finder button
  %posDEL  = [24,Y2-1,40,20];   % Position: delta cursor button
  %posCXL  = [70,Y2,40,18];     % Position: cursor X label
  %posC1X  = [113,Y2,70,19];    % Position: cursor 1X
  %posC2X  = [186,Y2,70,19];    % Position: cursor 2X
  %posCYL  = [335,Y2,62,18];    % Position: cursor Y label
  %posC1Y  = [400,Y2,70,19];    % Position: cursor 1Y
  %posC2Y  = [473,Y2,70,19];    % Position: cursor 2Y
%end_define

if ~nargin
  [drv,ppath]=pathfind('vna');
  Action = 23;  In1 = [drv,ppath,'\default.vna'];
  disp(['No arguments found, getting data from ' In1]);
else if isstr(Action) Action = 23; end;
end;

switch Action
case 23,
  switch In1
    case 'vna', SourceFile = get(findobj('tag','vna_fig'),'Name');
    case 'vss', SourceFile = get(findobj('CloseRequestFcn','vss(2)'),'Name');
    otherwise,  SourceFile = In1;
  end;
  map = [];
  switch In1
    case 'vna',  eval('SLm = vna(''get'',''meas'');');
    case 'vss',  eval('[BDx,BDy,map] = vss(''get'',''meas'');');
    otherwise,   eval(['load ',In1,' SLm -mat'],'disp(''bodepl argument specifies an invalid file''); return;');
  end;
  if isempty(map)
    BDx = SLm.fdxvec; BDy = [];
    for ref=1:4  for resp=1:16
      if ref==resp 
         x=[]; 
      else 
         x = (SLm.scmeas(resp).euscale_fac/SLm.scmeas(ref).euscale_fac) .* SLm.xcmeas(ref,resp).xfer; 
      end;
      if length(x) BDy = [BDy x]; map = [map; [ref resp]]; end;
    end; end;
  end;
  ref = map(:,1);  lx = length(ref);  dx=0; % lx is number of xfer functions saved
  chanstr = sprintf('Ch%d/%d|',fliplr(map)');   % normal xfer functions
  if lx>1 & all(ref==1) % do double cross only if chan 1 is the only reference
    m=1:lx; dx=m(ones(1,lx),:);  m=dx';  dx=dx(:);  m=m(:);
    a=find(m-dx);  a = a(1:min(length(a),20));
    dx = [dx m];  dx = dx(a,:)'; % dx is all possible double crosses
    chanstr = [chanstr sprintf('Ch%d/%d|',map(dx,2))]; % popup choices
  end;
  chanstr = chanstr(1:max(1,length(chanstr)-1)); % remove trailing '|'
  Xlimit = [min(BDx) max(BDx)];          % x-axis limits
  if ~Xlimit(1)  Xlimit(1)=BDx(2); end;  % Don't plot all the way down to 0
  [drv,ppath]=pathfind('vcom');          % find directory for bodepl.mat
  SetupFile = [drv,ppath,'\bodepl.mat']; % full path name for setup file
  key = 0;
  s = ['''' SetupFile ''''];             % just in case path contains a blank
  eval(['load ',s],'disp(''No bodepl.mat file. Creating new one.''); return;');             % load setup file
  if ~strcmp(key,'BODEPLv1.0')                % write new one if not found
     FuncType = 1;  Wrap = 2;  LogX = 2; % default to Xfer, unwraped, LogX
     MAPk0 = 1;  MAPk1 = -1;             % default mapping constants
     MargOn = 0;                         % default to no margin info
     p = [15,10,640,435];  pos = p;
  else p = [pos(1:2) 640 435];
  end;

% ------------- INITIALIZE FIGURES, AXIS, PLOTS, CURSORS --------------

  fontsz = fontsize;
  load vi_color;  colors = stored_vi_colors;
  SaveFont = uifont(VIfont);
  BODEh(1) =  figure('color',colors(1,:),'NumberTitle','off','menu','none','BackingStore','off','PaperPositionMode','auto','InvertHardcopy','off','PaperOrientation','landscape','PaperUnits','normalized','pos',p,'Name',['Bode Plot: ',SourceFile],...
                   'CloseRequestFcn','Bodepl(28)');
  set(BODEh(1),'visible','on'); drawnow;    % make figure visible 
  BODEh(3) = axes('color',colors(5,:),'Xcolor',colors(14,:),'Ycolor',colors(14,:),'units','pixels','DrawMode','fast','NextPlot','add','pos',[56,90,510,340],'FontSize',fontsz,'Xlim',Xlimit,... % phase axis
                'XTickLabel',' ','TickLen',[0 0],'YaxisLocation','right',...
                'Xgrid','off','Ygrid','off','Zgrid','off');
  BODEh(21) = text(0,0,'Phase (degrees)'); % phase label (ylabel MatLab bug)
  set(BODEh(21),'units','normal','Pos',[1.11,.33],...
                  'color',colors(15,:),'FontSize',fontsz,'Rotation',90);
  BODEh(22) = text(0,0,' ');         % gain/phase margin info
  set(BODEh(22),'units','normal','Pos',[-.1,1.04],'color',colors(15,:),'FontSize',fontsz);
 
  BODEh(6) = line('color',colors(8,:));                          % phase trace
  BODEh(2) = axes('color',colors(5,:),'Xcolor',colors(14,:),'Ycolor',colors(14,:),'units','pixels','DrawMode','fast','NextPlot','add','pos',[56,90,510,340],'FontSize',fontsz,...        % Magnitude axis
                  'TickLen',[0 0],'Xlim',Xlimit,'color','none');
  BODEh(5) = line('color',colors(7,:));                          % magnitude trace
  xlabel('Hertz         ','color',colors(15,:),'HandleVis','on');    % write x axis label
  ylabel('Magnitude (dB)','color',colors(15,:),'HandleVis','on');    % write y axis label
  BODEh(4) = axes('color',colors(5,:),'Xcolor',colors(14,:),'Ycolor',colors(14,:),'units','pixels','DrawMode','fast','NextPlot','add','pos',[56,90,510,340],'FontSize',fontsz,'TickLen',[0 0]);
  BODEh(7) = line('color',colors(7,:));                           % nyquist trace
  xlabel('Real part         ','color',colors(15,:),'HandleVis','on'); % write x axis label
  ylabel('Imaginary part','color',colors(15,:),'HandleVis','on');     % write y axis label
  w = [15,10,640,435]; w = w([3 4 3 4]);  w = w(ones(1,9),:);
  i1 = [[70,43,40,18];[335,43,62,18];[113,43,70,19];[186,43,70,19];[400,43,70,19];[473,43,70,19];[5,60,16,15];[5,42,16,15];[24,42,40,20]];
  i1(:,4) = i1(:,4)+(get(0,'screenpix')-96)/10;  i1=i1./w; % Adj for large fonts
  i2 = colors([3 4 20 21 20 20],:);
  i5 = .7*fontsz;  i6 = ['%7w';'%7w'];
  BODEh(15) = cursor([BODEh(2) BODEh(3)],'init',i1,i2, ... % init cursors
     ['Hz:    ';'dB/deg:'],['+';'o'],i5,i6 ,'on',1,'Bodepl(27,0)');
  BODEh(16) = cursor(BODEh(4),'init',i1,i2, ...
     ['Real:';'Imag:'],'+',i5,i6,'on',0,'Bodepl(27,1)');
  gridline(BODEh(2),'init',colors(22,:));   % initialize grid lines
  gridline(BODEh(4),'init',colors(22,:));   % initialize grid lines


% ------------------------ INITIALIZE CONTROLS -------------------------------

  BODEh(14)   = uicontrol('style','frame','background',colors(2,:),'pos',[5,4,561,34]);                       % frames all the controls
  BODEh(19)   = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[430,11,42,19],'str','* T / (');          % text objects
  BODEh(20)   = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[529,11,30,19],'str','+T )');
  BODEh(17)    = uicontrol('style','edit','background',colors(4,:),'foreground',colors(17,:),'pos',[373,11,57,21],'String',sprintf('%-1.5g',MAPk0),'Callback','Bodepl(24)');  % edit boxes
  BODEh(18)    = uicontrol('style','edit','background',colors(4,:),'foreground',colors(17,:),'pos',[472,11,57,21],'String',sprintf('%-1.5g',MAPk1),'Callback','Bodepl(24)');
  BODEh(8) = uicontrol('style','popup','background',colors(13,:),'foreground',colors(19,:),'pos',[12,13,78,20],'str',chanstr,'Callback','Bodepl(24)','user',dx);
  BODEh(9) = uicontrol('style','popup','background',colors(13,:),'foreground',colors(19,:),'pos',[97,13,80,20],'str','LinX|LogX|Nyquist','Value',LogX,'Callback','Bodepl(24)');
  BODEh(10) = uicontrol('style','popup','background',colors(13,:),'foreground',colors(19,:),'pos',[184,13,80,20],'str','Wrap|Unwrap','Value',Wrap,'Callback','Bodepl(24)');
  BODEh(11)  = uicontrol('style','popup','background',colors(13,:),'foreground',colors(19,:),'pos',[271,13,99,20],'str','Xfer|OpenLoop','Value',FuncType,'Callback','Bodepl(24)');
  BODEh(12)  = uicontrol('style','pushbutton','pos',[575,5,60,20],'str','Margins','Callback','Bodepl(25)');
  BODEh(13)   = uicontrol('style','pushbutton','pos',[575,30,60,20],'str','Print','Callback','hcpyv5(''init'',gcbf)');
  uifont(SaveFont);
  set(BODEh([2:4 8:14 17:20]),'units','normal');
  Bodepl(24);
  set(BODEh(1),'pos',pos_clip(pos));  % fit on screen

% ------------------------ END INITIALIZE SECTION ----------------------------

case 25,
  if MargOn  MargOn = 0; else MargOn = 1; end;
  Bodepl(24);

case 27,
  if In1 off=2; on=4;  else off=4; on=2;  end;
  gridline(BODEh(off),'off');  gridline(BODEh(on),'on');  % update grids

case 24,
  Index = get(BODEh(8),'Value');
  LogX = get(BODEh(9),'Value');
  Wrap = get(BODEh(10),'Value');
  FuncType = get(BODEh(11),'Value');
  MAPk0 = eval(get(BODEh(17),'str'),'num2str(0)');
  MAPk1 = eval(get(BODEh(18),'str'),'num2str(0)');
  lx = length(BDy(1,:));
  if Index<=lx  r=BDy(:,Index);    % pick out selected transfer function
  else dx = get(BODEh(8),'user');  dx = dx(:,Index-lx);
       r = BDy(:,dx(1)) ./ BDy(:,dx(2));  % compute double cross
  end;
  if r(1)==0.0 r = r+eps; end;     % protect against a block of all zeros
  if FuncType==2
       r = (MAPk0*r) ./ (MAPk1+r); % compute open loop mapping
       set(BODEh(17:20),'visible','on');    % show mapping equation
  else set(BODEh(17:20),'visible','off');    % hide mapping equation
  end;
  m = 20*log10(abs(r));  p = angle(r);  pu = (180/pi) * unwrap(p);
  if pu(1) > 0 pu=pu-360; end;  % unwrapped phase starts out within [0 -360]
  if Wrap == 1   p = (180/pi) * p;  else p = pu;  end;
  m0 = min(m);  m1 = max(m);   md = .05 * (m1-m0) + 1e-8;
  p0 = min(p);  p1 = max(p);   pd = .05 * (p1-p0) + 1e-8;

  if LogX==3                                            % Nyquist plots here
     set(BODEh([2 3]),'visible','off');
     set(BODEh(4),'visible','on')
     xa = min(real(r));  xb = max(real(r));  xd = .05 * (xb-xa) + 1e-8;
     ya = min(imag(r));  yb = max(imag(r));  yd = .05 * (yb-ya) + 1e-8;
     set(BODEh(4),'Xlim',[xa-xd  xb+xd],'Ylim',[ya-yd yb+yd]);
     set(BODEh(7),'x',real(r),'y',imag(r),'visible','on');
     set(BODEh([5 6 21]),'visible','off');
     cursor(BODEh(15),'set','vis_off');
     cursor(BODEh(16),'set','vis_on');
  else                                                  % Bode plots here
    if LogX==1 v='lin'; else v='log'; end;
    set(BODEh([2 3]),'Xscale',v,'visible','on');
    set(BODEh(4),'visible','off');
    cursor(BODEh(15),'set','xylim', [Xlimit m0-md m1+md], [p0-pd p1+pd])
    cursor(BODEh(15),'set','vis_on');
    cursor(BODEh(16),'set','vis_off');
    set(BODEh(5),'x',BDx,'y',m);
    set(BODEh(6),'x',BDx,'y',p);
    set(BODEh(7),'visible','off');   set(BODEh([5 6 21]),'visible','on');
  end;
  if MargOn
    axpos = [56,90,510,317];  gc = '?';  pc = gc;  gm = gc;  pm = gc;
    [mx,k]=max(m); ms=m; ms(1:k)=zeros(k,1);  % start at max gain
    if mx>0 & min(ms)<0      % here if gain declines thru 0dB point
       while m(k)>0 k=k+1; end;        % advance until gain < 0dB
       gci = m(k-1) / (m(k-1) - m(k)); % interpolate
       gc = ftoa('%5w',BDx(k-1) +  (BDx(k) - BDx(k-1)) * gci);
       pm = ftoa('%5w',180 + pu(k-1) + (pu(k) - pu(k-1)) * gci);
    end;
    [mx,k]=max(pu); pu(1:k-1)=zeros(k-1,1); % start at max phase
    if mx > -180  &  min(pu) < -180     % here if phase declines thru -180 deg
       while pu(k)>-180 k=k+1; end;               % advance until phase < -180
       pci = (180 + pu(k-1)) / (pu(k-1) - pu(k)); % interpolate
       pc = ftoa('%5w',BDx(k-1) +  (BDx(k) - BDx(k-1)) * pci);
       gm = ftoa('%5w',(m(k-1) - m(k)) * pci - m(k-1));
    end;
    st = sprintf('gain/phase crossover= %s / %s Hz,  gain/phase margin= %s db / %s deg',gc,pc,gm,pm);
  else  axpos = [56,90,510,340]; st = ' ';
  end;
  set(BODEh(22),'str',st);
  set(BODEh(2:4),'Pos',axpos./[640 435 640 435]);
  Bodepl(27,LogX==3);

case 28,
  set(BODEh(1),'CloseRequestFcn','closereq');
  pos = get(BODEh(1),'Pos');
  key = 'BODEPLv1.0';
  save(SetupFile,'key','LogX','Wrap','FuncType','MAPk0','MAPk1','pos','MargOn');
  close(BODEh(1));      % shut down bodepl figure window

end; % end switch

% end function bodepl
