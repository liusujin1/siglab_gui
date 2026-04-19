function vicolor(Action, In1)
% Dick Benson, DSPt
% Allows user to change color scheme in the VIs

% "Use Scheme" button stores color scheme in file vi_color.mat

% Save Scheme menu stores current color scheme in file vi_color.xx but the
% colors won't actually be used by the VIs unless you also press "Use Scheme"

  if nargin<1, Action='init'; end;

  vcol_h;   %  initial color definitions  if vi_color.mat is destroyed
            %  plus index definitions

  global HVC_;        % global handles
  global MYCOLOR;     % global colors
  global MYTEXT;      % text for scheme desription
  global MYFONT;      % ui control font

  MAXFILES = 18;  % look for vi_color.1 to vi_color.MAXFILES for
                  % a selection of color assignments

  % handle vector index definitions
  f1      =1;  % figure window
  spare   =2;  % spare
  grid    =3;  % grid color
  cm1     =4;  % color menu
  fm1     =5;  % UIcontrol font menu
  dlg1    =6;  % dialog background frame
  dtl1    =7;  % dialog title
  lbl1    =8;  % control label
  edt1    =9;  % text edit
  plt1    =10; % plot axis
  fnam    =11; % font name edit box
  fnaml   =12; % label for above
  fwt     =13; % font weight popup
  fsize   =14; % font size slider
  tr1     =15; % trace 1
  tr2     =16; % trace 2
  tr3     =17; % trace 3
  tr4     =18; % trace 4
  pat1    =19; % patch 1
  spb     =20; % save push button
  tx1     =21; % trace labels
  tx2     =22;
  tx3     =23;
  tx4     =24;
  pu1     =25; % popup
  sch     =26; % color scheme select
  svs     =27; % save color sheme menu
  lbl2    =28; % edit box for color scheme name
  lbl3    =29; % label: "color scheme name:"
  cur     =30; % cursor id
  ovlyc   =31; % overlay line 
  txovly  =32; % ovl line label

  if strcmp(Action,'init')

     virun('run','vicolor'); % let virun know in case vicolor was started
                             % from the command line

      % if the file vi_color.mat with startup colors exists, overwrite
      % INIT_COLORc defined in vcol_h.m  by loading the file
      color_text=[];
      VIfont = 0;  % Use default font.
      if exist('vi_color.mat') ==2
         load vi_color
         MYCOLOR=stored_vi_colors;
         while length(MYCOLOR(:,1))<NOCLR  MYCOLOR = [MYCOLOR; [.5 .5 .5]]; end;
         MYTEXT =color_text;
      else
         color_text='vcol_h.m clrs';
         MYCOLOR=INIT_COLORc;
      end;
      MYFONT = VIfont;

      fontsz = fontsize;
      SaveFont = uifont(MYFONT);

      HVC_(f1) = figure('Color',MYCOLOR(FIG_BKc,:),'Name','VICOLOR',...
                        'Units','pixels','Position',[60,60,580,380],...
                        'UserData',fontsz,'menu','none','Resize','off',...
                        'CloseRequestFcn','vicolor(''quit'')',...
                        'NumberTitle','off','visible','off',...
                        'BackingStore','off','menu','none');

      HVC_(cm1)= uimenu('Label','&Color');

      uimenu(HVC_(cm1),'Label','&Figure Background',...
                       'Callback','vicolor(''color_fig'');');

      uimenu(HVC_(cm1),'Label','Dialog &Background',...
                       'Callback','vicolor(''color_dbg'');');

      uimenu(HVC_(cm1),'Label','&Dialog Title Background','Separator','on',...
                       'Callback','vicolor(''color_dtb'');');

      uimenu(HVC_(cm1),'Label','D&ialog Title Foreground',...
                       'Callback','vicolor(''color_dtf'');');

      uimenu(HVC_(cm1),'Label','&Control Label Background',...
                       'Callback','vicolor(''color_lbb'');');

      uimenu(HVC_(cm1),'Label','C&ontrol Label Foreground',...
                       'Callback','vicolor(''color_lbf'');');

      uimenu(HVC_(cm1),'Label','&Edit Text Background',...
                       'Callback','vicolor(''color_edb'');');

      uimenu(HVC_(cm1),'Label','Edi&t Text Foreground',...
                       'Callback','vicolor(''color_edf'');');

      uimenu(HVC_(cm1),'Label','Pop&Up Background',...
                       'Callback','vicolor(''color_pub'');');

      uimenu(HVC_(cm1),'Label','PopUp &Foreground',...
                       'Callback','vicolor(''color_puf'');');

      uimenu(HVC_(cm1),'Label','&Plot Background','Separator','on',...
                       'Callback','vicolor(''color_pbg'')');

      uimenu(HVC_(cm1),'Label','X and Y &Axes',...
                       'Callback','vicolor(''color_xya'')');

      uimenu(HVC_(cm1),'Label','&X and Y Axis Labels',...
                       'Callback','vicolor(''color_xyl'')');

      for i=1:4
         uimenu(HVC_(cm1),'Label',['Plot Trace &',int2str(i)],...
                   'Callback',['vicolor(''color_tr''',',',int2str(i),')']);
      end;

     
      uimenu(HVC_(cm1),'Label',['Overlay Trace'],...
                       'Callback',['vicolor(''color_ovly'')']);
     



      uimenu(HVC_(cm1),'Label','&Patch ',...
                       'Callback','vicolor(''color_pat'')');
      uimenu(HVC_(cm1),'Label','&Cursor ','Separator','on',...
                       'Callback','vicolor(''color_cursor'')');
      uimenu(HVC_(cm1),'Label','&Mark  ',...
                       'Callback','vicolor(''color_mark'')');
      uimenu(HVC_(cm1),'Label','&Grid ',...
                       'Callback','vicolor(''color_grid'')');

      HVC_(fm1)= uimenu('Label','&UIcontrol font');
        uimenu(HVC_(fm1),'Label','MS sans serif',...
               'Callback','vicolor(''fontmenu'',''MS sans serif'')');
        uimenu(HVC_(fm1),'Label','MS serif',...
               'Callback','vicolor(''fontmenu'',''MS serif'')');
        uimenu(HVC_(fm1),'Label','System',...
               'Callback','vicolor(''fontmenu'',''System'')');
        uimenu(HVC_(fm1),'Label','More fonts ...','Separator','on',...
               'Callback','vicolor(''fontmenu'')');

      HVC_(svs) = uimenu('Label','&Save scheme');
      HVC_(sch)= uimenu('Label','&Load scheme');
      for i=1:MAXFILES
        is = ['vi_color.',int2str(i)];  cb = is;
        if exist(is) == 2
          load(is,'-mat');  is = [is ' (' color_text ')'];
          uimenu(HVC_(sch),'Label',is,'Callback',['vicolor(''read'','''  cb ''')']);
        end;
        uimenu(HVC_(svs),'Label',is,'Callback',['vicolor(''save'',''' cb ''')']);
      end;

      set(HVC_(f1),'visible','on');
      drawnow;

      HVC_(dlg1) = uicontrol('Style','frame',...
                      'backgroundcolor',MYCOLOR(DLG_BKc,:),...
                      'Position',[420 8 150 360]);

      HVC_(dtl1) = uicontrol('Style','text',...
                             'String','DIALOG TITLE',...
                             'Position',[422,352,146,16],...
                             'BackGroundColor',MYCOLOR(DTL_BKc,:),...
                             'ForeGroundColor',MYCOLOR(DTL_FRc,:));

      HVC_(lbl1) = uicontrol('Style','text',...
                             'String','Control Label',...
                             'Position',[430,325,130,16],...
                             'BackGroundColor',MYCOLOR(LBL_BKc,:),...
                             'ForeGroundColor',MYCOLOR(LBL_FRc,:));

      HVC_(edt1) = uicontrol('Style','edit',...
                             'String','Edit Text',...
                             'Position',[430,302,130,21],...
                             'BackGroundColor',MYCOLOR(EDT_BKc,:),...
                             'ForeGroundColor',MYCOLOR(EDT_FRc,:));

      HVC_(pu1)  = uicontrol('Style','popup',...
                             'String','This|is|a|sample|popup',...
                             'Value',5,...
                             'Position',[455,273,80,16],...
                             'BackGroundColor',MYCOLOR(PUP_BKc,:),...
                             'ForeGroundColor',MYCOLOR(PUP_FRc,:));

      HVC_(fnaml) = uicontrol('Style','text',...
                              'String','-- UIcontrol font --',...
                              'Position',[430,228,130,16],...
                              'BackGroundColor',MYCOLOR(LBL_BKc,:),...
                              'ForeGroundColor',MYCOLOR(LBL_FRc,:));

      HVC_(fnam)  = uicontrol('Style','edit',...
                              'String',get(HVC_(fnaml),'fontname'),...
                              'Position',[430,205,130,21],...
                              'Callback','vicolor(''font'')',...
                              'BackGroundColor',MYCOLOR(EDT_BKc,:),...
                              'ForeGroundColor',MYCOLOR(EDT_FRc,:));

      weight = get(HVC_(fnaml),'fontweight');
      if     strcmp(weight,'bold') k=2;
      elseif strcmp(weight,'light') k=3;
      else   k=1;
      end;
      HVC_(fwt)   = uicontrol('Style','popup',...
                              'String','Normal|Bold|Light',...
                              'Value',k,...
                              'Position',[455,185,80,16],...
                              'Callback','vicolor(''font'')',...
                              'BackGroundColor',MYCOLOR(PUP_BKc,:),...
                              'ForeGroundColor',MYCOLOR(PUP_FRc,:));

      HVC_(fsize) = slider([],'init',[430,153,130],...
                    [4,14,get(HVC_(fnaml),'fontsize'),1,64],...
                    'font size','vicolor(''font'')',0,'on',2,...
                     MYCOLOR([LBL_BKc EDT_BKc LBL_FRc EDT_FRc],:),...
                     ['%2w';'%4w';'%2w']);


      HVC_(lbl3) = uicontrol('Style','text',...
                             'String','Scheme name:',...
                             'HorizontalAlignment','Left',...
                             'Position',[430,73,130,16],...
                             'BackGroundColor',MYCOLOR(LBL_BKc,:),...
                             'ForeGroundColor',MYCOLOR(LBL_FRc,:));

      HVC_(lbl2) = uicontrol('Style','edit',...
                             'String',MYTEXT,...
                             'Position',[430,50,130,21],...
                             'BackGroundColor',MYCOLOR(EDT_BKc,:),...
                             'ForeGroundColor',MYCOLOR(EDT_FRc,:));

      HVC_(plt1)  = axes('Units','pixels','Position',[60,52,345,300],...
                         'Box','on',...
                         'NextPlot','add',...
                         'DrawMode','fast',...
                         'TickDir','out',...
                         'XLim',[0,1],...
                         'YLim',[-40,40],...
                         'fontsize',fontsz,...
                         'Color',MYCOLOR(PLT_BKc,:),...
                         'Xcolor',MYCOLOR(XY_AXc,:),...
                         'Ycolor',MYCOLOR(XY_AXc,:));

      xlabel('X-axis',      'color',MYCOLOR(XY_LBLc,:),'HandleVis','on');
      ylabel('Y-axis label','color',MYCOLOR(XY_LBLc,:),'HandleVis','on');
      title ('Graph title', 'color',MYCOLOR(XY_LBLc,:),'HandleVis','on');

      t    = 0:0.005:1;
      y1   = 10 * sin(20*t.^2) + 27;
      y2   = 10 * exp(-2*t).*sin(20*t) + 9;
      y3   = 10 * exp(-t).*sin(10*pi*t.^2) - 9;
      y4   = 10 * cos(5*pi*(1-t).^3) - 27;
      ovlyd= y4+1;

      HVC_(tr1) = plot(HVC_(plt1),'Xdata',t,'Ydata',y1,'clipping','on',...
                           'Color',MYCOLOR(TRA_1c,:),...
                           'erasemode','xor');

      HVC_(tx1)=text(t(14),y1(14)-2,'Trace 1');

      set(HVC_(tx1),'color',MYCOLOR(TRA_1c,:),'fontname','helv','fontsize',fontsz);

      HVC_(tr2) = plot(HVC_(plt1),'Xdata',t,'Ydata',y2,'clipping','on',...
                           'Color',MYCOLOR(TRA_2c,:),...
                           'erasemode','xor');

      HVC_(tx2) = text(t(30),y2(30)+2,'Trace 2');
      set(HVC_(tx2),'color',MYCOLOR(TRA_2c,:),'fontname','helv','fontsize',fontsz);

      HVC_(tr3) = plot(HVC_(plt1),'Xdata',t,'Ydata',y3,'clipping','on',...
                           'Color',MYCOLOR(TRA_3c,:),...
                           'erasemode','xor');

      HVC_(tx3) =text(t(30),y3(30)-4,'Trace 3');
      set(HVC_(tx3),'color',MYCOLOR(TRA_3c,:),'fontname','helv','fontsize',fontsz);

      HVC_(tr4) = plot(HVC_(plt1),'Xdata',t,'Ydata',y4,'clipping','on',...
                           'Color',MYCOLOR(TRA_4c,:),...
                           'erasemode','xor');

      HVC_(tx4) = text(t(70),y4(70)+2,'Trace 4');
      set(HVC_(tx4),'color',MYCOLOR(TRA_4c,:),'fontname','helv','fontsize',fontsz);


      HVC_(ovlyc) = plot(HVC_(plt1),'Xdata',t,'Ydata',ovlyd,'clipping','on',...
                           'Color',MYCOLOR(OVLYLINEc,:),...
                           'erasemode','xor');
      HVC_(txovly) = text(t(60),ovlyd(60)+2,'Overlay');
      set(HVC_(txovly),'color',MYCOLOR(OVLYLINEc,:),'fontname','helv','fontsize',fontsz);

      
      vicolor('cur_init',0);  % initialize the cursor/patch/gridlines

      % Select BUTTON
      HVC_(spb) = uicontrol('Style','Pushbutton',...
                      'Position',[445,17,100,20],...
                      'String','Use Scheme',...
                      'HorizontalAlignment','center',...
                      'Callback','vicolor(''save'',''vi_color.mat'')');

      uifont(SaveFont);
      set(HVC_(f1),'CloseRequestFcn','vicolor(''quit'')');

  elseif strcmp(Action,'color_fig')
      newcolor=uisetcolor(MYCOLOR(FIG_BKc,:),'Figure BackGround');
      if length(newcolor)>1
         MYCOLOR(FIG_BKc,:)=newcolor;
         set(HVC_(f1),'color',newcolor);
      end;

  elseif strcmp(Action,'color_dbg')
      newcolor=uisetcolor(MYCOLOR(DLG_BKc,:),'Dialog BackGround');
      if length(newcolor)>1
         MYCOLOR(DLG_BKc,:)=newcolor;
         set(HVC_(dlg1),'backgroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_dtb')
      newcolor=uisetcolor(MYCOLOR(DTL_BKc,:),'Dialog Title BackGround');
      if length(newcolor)>1
         MYCOLOR(DTL_BKc,:)=newcolor;
         set(HVC_(dtl1),'backgroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_dtf')
      newcolor=uisetcolor(MYCOLOR(DTL_FRc,:),'Dialog Title ForeGround');
      if length(newcolor)>1
         MYCOLOR(DTL_FRc,:)=newcolor;
         set(HVC_(dtl1),'foregroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_lbb')
      newcolor=uisetcolor(MYCOLOR(LBL_BKc,:),'Label BackGround');
      if length(newcolor)>1
         MYCOLOR(LBL_BKc,:)=newcolor;
         set(findobj(HVC_(f1),'style','text'),'backgroundcolor',newcolor);
         set(HVC_(dtl1),'backgroundcolor',MYCOLOR(DTL_BKc,:));
         vicolor('cur_init')
      end;

  elseif strcmp(Action,'color_lbf')
      newcolor=uisetcolor(MYCOLOR(LBL_FRc,:),'Label ForeGround');
      if length(newcolor)>1
         MYCOLOR(LBL_FRc,:)=newcolor;
         set(findobj(HVC_(f1),'style','text'),'foregroundcolor',newcolor);
         set(HVC_(dtl1),'foregroundcolor',MYCOLOR(DTL_FRc,:));
      end;

  elseif strcmp(Action,'color_edb')
      newcolor=uisetcolor(MYCOLOR(EDT_BKc,:),'Edit box BackGround');
      if length(newcolor)>1
         MYCOLOR(EDT_BKc,:)=newcolor;
         set(findobj(HVC_(f1),'style','edit'),'backgroundcolor',newcolor);
         vicolor('cur_init')
      end;

  elseif strcmp(Action,'color_edf')
      newcolor=uisetcolor(MYCOLOR(EDT_FRc,:),'Edit box ForeGround');
      if length(newcolor)>1
         MYCOLOR(EDT_FRc,:)=newcolor;
         set(findobj(HVC_(f1),'style','edit'),'foregroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_pub')
      newcolor=uisetcolor(MYCOLOR(PUP_BKc,:),'PopUp BackGround');
      if length(newcolor)>1
         MYCOLOR(PUP_BKc,:)=newcolor;
         set(findobj(HVC_(f1),'style','popupmenu'),'backgroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_puf')
      newcolor=uisetcolor(MYCOLOR(PUP_FRc,:),'PopUp ForeGround');
      if length(newcolor)>1
         MYCOLOR(PUP_FRc,:)=newcolor;
         set(findobj(HVC_(f1),'style','popupmenu'),'foregroundcolor',newcolor);
      end;

  elseif strcmp(Action,'color_pbg')
      newcolor=uisetcolor(MYCOLOR(PLT_BKc,:),'Plot BackGround');
      if length(newcolor)>1
         MYCOLOR(PLT_BKc,:)=newcolor;
         set(HVC_(plt1),'color',newcolor);
      end;

  elseif strcmp(Action,'color_xya')
      newcolor=uisetcolor(MYCOLOR(XY_AXc,:),'XY Axis, Ticks, & Tick Labels');
      if length(newcolor)>1
         MYCOLOR(XY_AXc,:)=newcolor;
         set(HVC_(plt1),'Xcolor',newcolor,'Ycolor',newcolor);
         a(1) = get(HVC_(plt1),'Xlabel');
         a(2) = get(HVC_(plt1),'Ylabel');
         a(3) = get(HVC_(plt1),'Title');
         set(a,'color',MYCOLOR(XY_LBLc,:));
      end;

  elseif strcmp(Action,'color_xyl')
      newcolor=uisetcolor(MYCOLOR(XY_LBLc,:),'X and Y axis labels');
      if length(newcolor)>1
         MYCOLOR(XY_LBLc,:)=newcolor;
         a(1) = get(HVC_(plt1),'Xlabel');
         a(2) = get(HVC_(plt1),'Ylabel');
         a(3) = get(HVC_(plt1),'Title');
         set(a,'color',newcolor);
      end;

  elseif strcmp(Action,'color_tr')
      ofs=In1-1;
      newcolor=uisetcolor(MYCOLOR(TRA_1c+ofs,:),['Trace:',int2str(In1)]);
      if length(newcolor)>1
         MYCOLOR(TRA_1c+ofs,:)=newcolor;
         set(HVC_(tx1+ofs),'color',newcolor);  % seem to need this order of events
         set(HVC_(tr1+ofs),'color',newcolor);  % to prevent line from being screwed up
      end;

  elseif strcmp(Action,'color_ovly')
   
      newcolor=uisetcolor(MYCOLOR(OVLYLINEc,:),'Overlay Line');
      if length(newcolor)>1
         MYCOLOR(OVLYLINEc,:)=newcolor;
         set(HVC_(txovly),'color',newcolor);  % seem to need this order of events
         set(HVC_(ovlyc),'color',newcolor);  % to prevent line from being screwed up
      end;



  elseif strcmp(Action,'color_pat')
      newcolor=uisetcolor(MYCOLOR(PA_1c,:),'Patch');
      if length(newcolor)>1
         MYCOLOR(PA_1c,:)=newcolor;
         set(HVC_(pat1),'FaceColor',newcolor);
      end;

  elseif strcmp(Action,'color_cursor')
      newcolor=uisetcolor(MYCOLOR(CURSORc,:),'Cursor');
      if length(newcolor)>1
         MYCOLOR(CURSORc,:)=newcolor;
         vicolor('cur_init')
      end;

  elseif strcmp(Action,'color_mark')
      newcolor=uisetcolor(MYCOLOR(MARKc,:),'Mark');
      if length(newcolor)>1
         MYCOLOR(MARKc,:)=newcolor;
         vicolor('cur_init')
      end;

  elseif strcmp(Action,'color_grid')
      newcolor=uisetcolor(MYCOLOR(GRIDc,:),'Grid');
      if length(newcolor)>1
         MYCOLOR(GRIDc,:)=newcolor;
         vicolor('cur_init')
      end;

  elseif strcmp(Action,'cur_init')  % init (or re-init) cursor/grids/patch
      if nargin==1  delete(HVC_(pat1));
                    cursor(HVC_(cur),'clear');
                    delete(findobj(HVC_(plt1),'UserData','grid'));
      end;
      HVC_(cur) = cursor(HVC_(plt1),'init',... % init cursor objects
      [60,6,14,21; 260,6,14,21; 77,6,60,21; 140,6,60,21;... % CXL,CYL,C1X,C2X
       277,6,60,21; 340,6,60,21;...                         % C1Y,C2Y
       999,1,1,1; 999,1,1,1; 8,5,40,20],...             % PEAK,VALY,MARK
      MYCOLOR([LBL_BKc EDT_BKc CURSORc MARKc CURSORc CURSORc CURSORc CURSORc],:),...
      ['x';'y'], ['o';'+';'o';'+'], .7*get(HVC_(f1),'UserData'),...
      ['%7w';'%7w'],'on',1,'vicolor(''grid'')');

      HVC_(pat1) = patch('Xdata',[.9 1 1 .9],'Ydata',[-40 -40 40 40],...
                         'FaceColor',MYCOLOR(PA_1c,:),'EraseMode','xor');

      gridline(HVC_(plt1),'init',MYCOLOR(GRIDc,:));

  elseif strcmp(Action,'grid') gridline(HVC_(plt1));

  elseif strcmp(Action,'fontmenu')
      if nargin==1  k = uisetfont;  v = version;
                    In1 = k.FontName;
                    weight = k.FontWeight;
                    sz = k.FontSize;
                    set(HVC_(fwt),'value',v);
                    slider(HVC_(fsize),'set','value',sz);
      end;
      set(HVC_(fnam),'string',In1);  vicolor('font');

  elseif strcmp(Action,'font')
      obj = [findobj(HVC_(f1),'style','text');
             findobj(HVC_(f1),'style','edit');
             findobj(HVC_(f1),'style','pushbutton');
             findobj(HVC_(f1),'style','popupmenu')];
      s = get(HVC_(fwt),'string');
      set(obj,'fontname',get(HVC_(fnam),'string'));
      set(obj,'fontweight',s(get(HVC_(fwt),'value'),:));
      set(obj,'fontsize',slider(HVC_(fsize),'get'));
      props = strcat({'font'},...
                     {'size' 'name' 'weight' 'angle' 'units'});
      s = get(obj(1),props);   % Get all the uicontrol font properties
      s{1} = num2str(s{1});
      MYFONT = char(s);                % Convert to character array

  elseif strcmp(Action,'read')
      color_text=[]; VIfont=0;
      load(In1,'-mat');
      MYCOLOR=stored_vi_colors;
      MYFONT=VIfont;
      while length(MYCOLOR(:,1))<NOCLR  MYCOLOR = [MYCOLOR; [.5 .5 .5]]; end;
      MYTEXT =color_text;
      set(findobj(HVC_(f1),'style','text'),...
                           'backgroundcolor',MYCOLOR(LBL_BKc,:),...
                           'foregroundcolor',MYCOLOR(LBL_FRc,:));
      set(findobj(HVC_(f1),'style','edit'),...
                           'backgroundcolor',MYCOLOR(EDT_BKc,:),...
                           'foregroundcolor',MYCOLOR(EDT_FRc,:));
      set(findobj(HVC_(f1),'style','popupmenu'),...
                           'backgroundcolor',MYCOLOR(PUP_BKc,:),...
                           'foregroundcolor',MYCOLOR(PUP_FRc,:));
      set(HVC_(f1),'color',MYCOLOR(FIG_BKc,:));
      set(HVC_(dlg1),'backgroundcolor',MYCOLOR(DLG_BKc,:));
      set(HVC_(dtl1),'backgroundcolor',MYCOLOR(DTL_BKc,:),...
                     'foregroundcolor',MYCOLOR(DTL_FRc,:));
      set(HVC_(lbl2),'string',MYTEXT);
      set(HVC_(plt1),'color',MYCOLOR(PLT_BKc,:));             % remove?
      set(HVC_(plt1),'Xcolor',MYCOLOR(XY_AXc,:),'Ycolor',MYCOLOR(XY_AXc,:));
      set([get(HVC_(plt1),'Xlabel');...
           get(HVC_(plt1),'Ylabel');...
           get(HVC_(plt1),'Title')],'color',MYCOLOR(XY_LBLc,:));
      for ofs=0:3 % seem to need this order of events to prevent line from
         set(HVC_(tx1+ofs),'color',MYCOLOR(TRA_1c+ofs,:)); %  being screwed up
         set(HVC_(tr1+ofs),'color',MYCOLOR(TRA_1c+ofs,:));
      end;
      vicolor('cur_init');
      if MYFONT
        slider(HVC_(fsize),'set','value',str2num(MYFONT(1,:)));
        set(HVC_(fnam),'string',deblank(MYFONT(2,:)));
        weight = deblank(MYFONT(3,:));
        if     strcmp(weight,'bold') k=2;
        elseif strcmp(weight,'light') k=3;
        else   k=1;
        end;
        set(HVC_(fwt),'value',k);
        vicolor('font');
      end;

  elseif strcmp(Action,'quit')
      set(HVC_(f1),'CloseRequestFcn','closereq');
      close(HVC_(f1));
      clear global HVC_ MYCOLOR MYTEXT MYFONT
      virun('close','vicolor');   % inform virun of closing

  elseif strcmp(Action,'save')
      [drive,ppath] = pathfind('vcom');  f = [drive,ppath,'\',In1];
      stored_vi_colors=MYCOLOR;
      color_text=get(HVC_(lbl2),'string');  % Matlab bug? This doesn't work
        % unless you hit <CR> or click on some other object after typing in
        % the lbl2 text (color scheme name)
      VIfont = MYFONT;
      if ~exist(f) uimenu(HVC_(sch),'Label',[In1 ' (' color_text ')'],...
                       'Callback',['vicolor(''read'','''  In1 ''')']);
      elseif isempty(findstr(f,'.mat'))
           f2 = In1;  f2(7:8) = '__';  f2 = [drive,ppath,'\',f2];
           eval(['!copy ' f ' ' f2]);
           tmsg(['Old scheme file ' f ' renamed to ' f2],4,'disp');
      end;
      if beyondv4 fmt = ' -v4'; else fmt = ''; end;
      eval(['save ',f,' stored_vi_colors color_text VIfont' fmt]);

  else disp([Action,' unrecognized in vicolor.m']);
  end;  % the big if
% end function vicolor.m
