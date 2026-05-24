  function[Out1,Out2, Out3] = ex_dlg2(Action,In1,In2,In3,In4,In5,In6,In7)
% function[Out1,Out2, Out3] = ex_dlg2(Action,In1,In2,In3,In4,In5,In6,In7)
% ex_dlg2 Excitation Dialog in VNA (only)
% actions are
%     'init'         init the ui objects
%                    In1 = position, In2 = null, In3 has initial colors
%                    In4 = owner In5=EXDLG2_S1 In6=dlg_vis
%                    In7 optional parent figure handle
%     'open'         open
%         'full'     load & show_cond,all combo
%         'fast'     show_cond,all  only, no load
%     'close'        conditionally hide (don't hide if not visible)
%     'clear'        clear globals
%     'show'         display the ui objects
%
%
%     'get'
%         'state'    returns state of controls
%                    in Out1, null in Out2, Out3 vis
%         'wincor'   returns window correction factor and window selection code 
%
%     'load_all'     loads colors, states, visibility
%     'load'         loads control states from EXDLG2_S1
%     'set'
%          'olevro'    update slider 1 value display
%          'outsld'    update slider position
%          'outofsro'  set the output offset in Volts
%          'out_on'    turn output on   (conditional wrt dialog visibility)
%          'out_off'   turn output off  (conditional wrt dialog visibility)
%     'omode_tog'    toggle between random & chirp
%     'onoff_tog'    toggle output on/off
%
%     Dick Benson, DSP Technology

  global HEXDLG2_;      % main handle vector
  global EXDLG2_S1;     % S1 holds the numerical states

% end global defs


   % ***************************************************************
   %include
% exdlg2_h.m
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%% vhw_h.m
% Header file with slider mode definitions for islider.mi, sldclickm, and users
% vsiz_h.m
% avgdef_h.m
%%%%%%%%%%%%%%%%%%%%end_include
   % *******************************************************

   if strcmp(Action,'init'),
   %INIT Command
      %define
         %dl_pos        = In1;
                       % In2 not used
         %my_color      = In3; % initial colors passed here
      %end_define


      if nargin>=7
           % use input args for initial condx
           EXDLG2_S1 =In5;
           if length(EXDLG2_S1)==3
              EXDLG2_S1=[EXDLG2_S1,0]; % append output on/off
           end;
           dlg_vis   =In6;
           if nargin ==8
              hf = In7;
           else
              hf = gcf;
           end;
      else
          % ex_dlg2 initial states
          %          level  offset omode 1=chirp 2=random    on/off
           EXDLG2_S1=[0,     0,    1                             0];
           dlg_vis  ='off';
           if nargin == 6
              hf = In5;
           else
              hf = gcf;
           end;
      end;

      %define
         %wdl       = 120;
         %hdl       = 170;        % This dialog's  size
         %wlb       = 58;         % control label width
         %wpu       = 70;         % popup width
         %wsld      = wdl-2*LHOc; % slider width

         %dlgfrm_pos      = [dl_pos,wdl,hdl];
         %titletxt_pos    = [dl_pos + [LHOc,hdl-HTXTc-1],wsld,HTXTc];

         % Excitation type
         %omode_pos       = [dl_pos + [LHOc,hdl-1.75*VSc],wpu,HCKS1c];
         %onoff_pos       = [dl_pos + [LHOc+wpu+3,hdl-1.75*VSc],CKS1c];
         % Output Level  slider
         %olev_pos        = [dl_pos + [LHOc,hdl-2.75*VSc],wsld];

         % Output Offset
         %outofs_pos      = [dl_pos + [LHOc,hdl-5*VSc],wsld];
      %end_define

% init ALL ui controls now
% can use dlg_vis directly since there are no interations between
% object visibility and state

          HEXDLG2_(5)=uicontrol(hf,'Style','frame','visible',dlg_vis,...
                                     'Position',[In1,120,170],...
                                     'BackGroundColor',In3(2,:));


          HEXDLG2_(6) = uicontrol(hf,'Style','text','visible',dlg_vis,...
                                         'String','EXCITATION',...
                                         'Position',[In1 + [5,170-16-1],110,16],...
                                         'BackGroundColor',In3(12,:),...
                                         'ForeGroundColor',In3(18,:));

          HEXDLG2_(3)=uicontrol(hf,'Style','Popup','Visible',dlg_vis,...
                                    'Position',[In1 + [5,170-1.75*25],70,20],...
                                    'BackGroundColor',In3(13,:),...
                                    'ForeGroundColor',In3(19,:),...
                                    'value',EXDLG2_S1(3),...
                                    'String','Chirp|Rand',...
                                    'HorizontalAlignment','left',...
                                    'userdata',[1,0.6666666667,0.2617872274,0.292041699,0.3378558539,0.5642952975,0.4947506823,0.7311777141,0.5791201619,0.5904311459,0.6208287665,0.5852957066,0.5583575867,0.4989141365,0.99,0.99,0.99,0.99],...
                                    'Callback','ex_dlg2(''omode_chg'')');



          HEXDLG2_(4)=uicontrol(hf,'Style','Pushbutton','Visible',dlg_vis,...
                                    'Position',[In1 + [5+70+3,170-1.75*25],[38,20]],...
                                    'Callback','ex_dlg2(''onoff_tog'')');
                                    
          if EXDLG2_S1(4)==0
              set(HEXDLG2_(4),'String','Off','UserData',0);
          else
              set(HEXDLG2_(4),'String','On','UserData',1);
          end;

          HEXDLG2_(1)= islider([],'init',[In1 + [5,170-2.75*25],110],...
                                  [0,2.5,EXDLG2_S1(1),0,2.5],...
                                  'Output RMS',...
                                  'ex_dlg2(''set'',''olevro'');',...
                                  0,dlg_vis,[5,0.01],...
                                  [In3(3,:);In3(4,:);In3(16,:);In3(17,:)],...
                                  ['%3w  ';'%6.3f';'%3w  '],hf);

          HEXDLG2_(2)= islider([],'init',[In1 + [5,170-5*25],110],...
                                    [-10,10,EXDLG2_S1(2),-10,10],...
                                    'Output Offset',...
                                    'ex_dlg2(''set'',''outofsro'');',...
                                    0,dlg_vis,[5,0.001],...
                                    [In3(3,:);In3(4,:);In3(16,:);In3(17,:)],...
                                    ['%3w  ';'%6.3f';'%3w  '],hf);


% ********************    end of init  ****************************

   elseif strcmp(Action,'show'),
   % SHOW
     islider(HEXDLG2_(1),'set','vis_on');
     islider(HEXDLG2_(2),'set','vis_on');
     set(HEXDLG2_(3:6),'Visible','on');

   elseif strcmp(Action,'set')
   % SET
       if strcmp(In1,'olevro'),
       % OLEVRO
       % sets Level readout
              EXDLG2_S1(1)=islider(HEXDLG2_(1),'get');
              ls_vna('set_olev');

       elseif strcmp(In1,'outofsro'),
       % OUTOFSRO output voltage offset
              EXDLG2_S1(2)=islider(HEXDLG2_(2),'get');
              ls_vna('set_olev');

       elseif strcmp(In1,'out_on')
       % OUT_ON
            % conditionally do this
            if strcmp(get(HEXDLG2_(6),'visible'),'on')
                EXDLG2_S1(4) = 1;
                set(HEXDLG2_(4),'String','On','UserData',1);
                ls_vna('set_olev');
            end;
       elseif strcmp(In1,'out_off')
       % OUT_OFF
            if strcmp(get(HEXDLG2_(6),'visible'),'on')
               EXDLG2_S1(4) = 0;
               set(HEXDLG2_(4),'String','Off','UserData',0);
               ls_vna('set_olev');
            end;
       end;

   elseif strcmp(Action,'onoff_tog')
   % Output on/off toggle
       EXDLG2_S1(4) = ckbtog(HEXDLG2_(4),'Off','On');
       ls_vna('set_olev');

   elseif strcmp(Action,'omode_chg'),
   % OMODE_CHG
       EXDLG2_S1(3) = get(HEXDLG2_(3),'value');
       ls_vna('set_outpar');
       plot_vna('set','win_change');  % vna is the only owner of this, possible change between hanning & boxcar 

   elseif strcmp(Action,'get'),
   % GET command
        if strcmp(In1,'state'),
        % GET STATE
           Out1 = EXDLG2_S1;
           Out2 = [];
           Out3 = get(HEXDLG2_(6),'Visible');
        elseif strcmp(In1,'wincor')
            % assumes that chirp/boxcar are selection 1, and random/hanning
            % are selection 2. Actual window selection is made in ls_vna.mi
            Out2= get(HEXDLG2_(3), 'Value');  % added 4/3/98
            temp=get(HEXDLG2_(3),'userdata');
            Out1=temp(Out2);
        else
           error = [In1,' not recognized in ex_dlg2(get,xxx)']
        end;

   elseif strcmp(Action,'load'),
   % LOAD command  load controls from EXDLG2_S1
            s1 = EXDLG2_S1;
            set(HEXDLG2_(3), 'value', s1(3));
            islider(HEXDLG2_(1),'set','value',s1(1));
            islider(HEXDLG2_(2),'set','value',s1(2));
            if length(EXDLG2_S1)==3
               EXDLG2_S1=[EXDLG2_S1,0]; % append output on/off and turn it off
            end;
            % indicate last state 
            if EXDLG2_S1(4)==0
                set(HEXDLG2_(4),'String','Off','UserData',0);
            else
                set(HEXDLG2_(4),'String','On','UserData',1);
            end;

   elseif strcmp(Action,'open'),
   % OPEN Command
         if strcmp('fast',In1) ~=1,
            ex_dlg2('load');
         end;
         ex_dlg2('show','all');

   elseif strcmp(Action,'load_all'),
   % LOAD_ALL Command
       EXDLG2_S1 = In2;

       if strcmp(In1,'on'),      % display this dialog
           ex_dlg2('load');
           ex_dlg2('show','all');
       else
           ex_dlg2('close');     % it might be open
           ex_dlg2('load');      % only do a load
       end;

   elseif strcmp(Action,'close'),
   % CLOSE command
       if strcmp(get(HEXDLG2_(6),'Visible') ,'on'),
             set(HEXDLG2_(3:6),'Visible','off');
             islider(HEXDLG2_(1),'set','vis_off');
             islider(HEXDLG2_(2),'set','vis_off');
       end;

   elseif strcmp(Action,'clear'),
   % CLEAR command
       clear global HEXDLG2_ EXDLG2_S1;

   else
        disp(['error in ex_dlg2:', Action,' not recognized']);
   end; % end of MAGNUM IF
%end of dialog function
