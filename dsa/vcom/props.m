% props.m                      Paul Mennen                         10-Mar-99
% header file: defines GUI object properties

HIDE       = 'visible','off';
SHOW       = 'visible','on';
ENABLE     = 'enable','on';
DISABLE    = 'enable','off';
CHECK      = 'check','on';
UNCHECK    = 'check','off';
PIXELS     = 'units','pixels';
NORMAL     = 'units','normal';
RIGHTalgn  = 'horiz','right';
LEFTalgn   = 'horiz','left';
CENTERalgn = 'horiz','center';
USER       = 'user';
STR        = 'str';
LABEL      = 'label';
SEPARATE   = 'separator','on';
CNTRL      = 'accel';
CALLBK     = 'call';
MOUSE      = 'ButtonDownFcn';
ErXOR      = 'erase','xor';
ErNORM     = 'erase','normal';
NOticks    = 'TickLen',[0 0];
TEX        = 'interp','tex';
NoTEX      = 'interp','none';

FALSE = 0;
TRUE = 1;
EVALerr = 'num2str(0)';    % eval error returns '0.0'
BEEP = fprintf(1,'%c',7);  % beep to signal user entry error

COLORfig = 'color',colors(FIG_BKc,:);              % figure background color
COLORxya = 'color',colors(PLT_BKc,:),...           % xy axis color
           'Xcolor',colors(XY_AXc,:),...
           'Ycolor',colors(XY_AXc,:);
COLORxyl = 'color',colors(XY_LBLc,:);              % x and y axis label colors
COLORtr1 = 'color',colors(TRA_1c,:);               % trace 1
COLORtr2 = 'color',colors(TRA_2c,:);               % trace 2
COLORtr3 = 'color',colors(TRA_3c,:);               % trace 3
COLORtr4 = 'color',colors(TRA_4c,:);               % trace 4
COLORbox = 'color',colors(PLT_BKc,:),...           % text box colors
           'Xcolor',colors(FIG_BKc,:),'Ycolor',colors(FIG_BKc,:),...
           'XTickLabel',' ','YTickLabel',' ',NOticks;
CURcolor = 'color',colors(CURSORc,:);
PUPcolor = 'color',colors(PUP_BKc,:);
POPcolor = 'colorBK',colors(PUP_BKc,:),'colorFR',colors(PUP_FRc,:);
POPcolor1 = POPcolor,PUPcolor;
COLORdlg = 'background',colors(DLG_BKc,:);         % dialog background color
COLORdtl = 'background',colors(DTL_BKc,:),...      % dialog title colors
           'foreground',colors(DTL_FRc,:); 
COLORtxt = 'background',colors(LBL_BKc,:),...      % label colors
           'foreground',colors(LBL_FRc,:);
COLORedt = 'background',colors(EDT_BKc,:),...      % edit box colors
           'foreground',colors(EDT_FRc,:);
COLORpup = 'background',colors(PUP_BKc,:),...      % popup colors
           'foreground',colors(PUP_FRc,:);
COLORid1 = 'background',colors(PLT_BKc,:),...      % trace 1 text id colors
           'foreground',colors(TRA_1c,:);
COLORid2 = 'background',colors(PLT_BKc,:),...      % trace 2 text id colors
           'foreground',colors(TRA_2c,:);
  
FIGUR(  = figure(COLORfig,'NumberTitle','off','menu','none',...
                'BackingStore','off','PaperPositionMode','auto',...
                'InvertHardcopy','off','PaperOrientation','landscape',...  
                'PaperUnits','normalized','pos',
AXISxy( = axes(COLORxya,PIXELS,'DrawMode','fast','NextPlot','add','pos',
LINEx(  = line('EraseMode','xor',
BUTTONtxt( = uicontrol('style','text','enable','inactive',...
                     'foreground',colors(PLT_BKc,:),'pos',
BUTTON( = uicontrol('style','pushbutton','pos',
SLIDER( = uicontrol('style','slider','pos',
CHKBOX( = uicontrol('style','checkbox',COLORpup,'pos',
RADIO(  = uicontrol('style','radiobutton',COLORpup,'pos',
FRAME(  = uicontrol('style','frame',COLORdlg,'pos',
FTITLE( = uicontrol('style','text',COLORdtl,CENTERalgn,'pos',
POPUP(  = uicontrol('style','popup',COLORpup,'pos',
EDIT(   = uicontrol('style','edit',COLORedt,'pos',
TEXT(   = uicontrol('style','text',COLORtxt,LEFTalgn,'pos',

