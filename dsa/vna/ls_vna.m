  function [Out1, Out2, Out3, Out4, Out5]=ls_vna(Action,Var1,Var2)
% function [Out1, Out2, Out3, Out4, Out5]=ls_vna(Action,Var1,Var2)
% Interface to siglab.m function for DSPT VI's. 
% All measurement boxes are assumed to be created equal for this 
% implementation. The siglab.m function does not have this restriction
% 
% Action
% init       initializes interface and returns:
%            #In, #Out, sys_clock, hwok, number of channels in first box
%                                                         (default value)
% clear
%     STATE_CHG   clears local static state change flag (0)
%                 clears globals when no 2nd arg
% get
%     SYS_CLOCK  returns system fundamental clock, typically 
%               51200 Hz for 20-22 

%     NUMIN      returns number of input channels
%     NUMOUT                       output
%     FRAME_SIZE default startup frame size
%     STATE_CHG  returns 0 if no change,
%                        1 if there was a change in hdwr or it was set
%
% set
%    SYS_CLOCK   sets sys_clock to input Var2
%    STATE_CHG   sets STATE_CHG to 1
%
% set_chan   setup input channel using the following states (v_dlg1)
%     chan   number of input channel 1..  Var1=ch or a vector of channel #s
%     vfs    full scale voltage           Var1, Var2 value  table entry
%     ofs    setup offset voltage         Var1, Var2 value -VMAX< x < VMAX
%     cpl    setup coupling               Var1, Var2 value  0=AC 1=DC
%
% set_acq      setup acquisition           
%     chenck   (enable) in v_dlg1
%     frmsz    in h_dlg1
%     LS_VNA_STATE(1)  sampling clock defined in vna.m
%     zmckpb & zmcf in h_dlg1 (combined)
%     samppu1  value index to table of sampling rates in h_dlg1
%     aafckpb  filt/nofilt in     h_dlg1
%     ovlpmode overlap control in v_dlg2
%
%
% set_trg    setup trigger 
%     tselpu1  source select code  h_dlg2
%     filtck   filter on / off     h_dlg2
%     delay    pre/post delay      h_dlg2
%     thrpu2   threshold           h_dlg2
%     slopeck  slope               h_dlg2
%     armck    arm                 h_dlg2
%     modepu3  mode                h_dlg2
%      
% set_proc  setup processing
%     avgckpb  in v_dlg1 
%     winsel   window select in    v_dlg2
%
% set_arm   set manual arm for trigger
%
% set_olev
%     olev    output level RMS     ex_dlg2
%     outofs  output offset        ex_dlg2
%         
% set_omode
%     omode   outofs               ex_dlg2 
%
% set_exci    select internal or external excitation 
%             Var1 = 'int' or 'ext'
%

   global LS_VNA_STATE; % misc parameters that happen to be localized here  

   global VDLG1_S1 VDLG2_S1 HDLG1_S1 HDLG2_S1 EXDLG2_S1; 

 % indexes into LS_VNA_STATE, what the heck is 5 more #'s ...
 %define
    %sys_clock  =1; 
    %numin      =2;
    %numout     =3; 
    %state_chg  =4;
    %int_flg    =5; % 1=internal excitation , 0=external excitation
    %old_mode   =6; % last output mode chirp/random
    %auto_mode  =7; % for auto_vna, disable trigger on chirp
    %HW_ok      =8; % nhw
    %nchan_box1 =9;
    %zpadx      =10;
 %end_define  

   % Lazy-init safeguard: callbacks may hit ls_vna('get',...) during shutdown
   % after globals were cleared.
   if isempty(LS_VNA_STATE) || numel(LS_VNA_STATE) < 10
      % [sys_clock numin numout state_chg int_flg old_mode auto_mode HW_ok nchan_box1 zpadx]
      LS_VNA_STATE = [51200, 4, 1, 0, 0, -1, 0, 1, 4, 0];
   end

   % nhw
   if strcmp(Action(1:3),'set') 
      if ~LS_VNA_STATE(8)
         Action = 'nop';
      end 
   end

   if strcmp(Action,'init'),
       Inp = 4;
       Out = 1;
       BW = 20000;
        
       Out1 = sum(Inp);    % number of available input channels 
       Out2 = sum(Out);   % the number of output channels 
       Out3 = 2.56*BW(1); % fundamental time base frequency 
                          % (eventually set with dialog)
                          % this SW assumes all boxes are the same BW
       
       % nhw                    
       Out4 = 1;          % hardware found          
       Out5 = 4;          % need number of input channels in first box
          
                                                        % -1 means mode not known      HW_ok  nchan_box_1
   % LS_VNA_STATE [sys_clock,  numin  numout   state_chg int_flg   old_mode  auto_mode                 zpadx]
     LS_VNA_STATE=[     Out3,  Out1   , Out2,      0      0        -1        0         Out4   Out5       0 ]; 
  
       if nargin ==3
          LS_VNA_STATE(7) = strcmp('auto_test',Var2);
       end
       
       if sum(BW)/(length(BW)) ~= BW(1)
          disp(['inconsistant BW found in ls_vna.m Action=init']);
       end
   
   elseif strcmp(Action,'get'), 
        if  strcmp(Var1,'SYS_CLOCK'),   
            Out1=LS_VNA_STATE(1); 
        elseif  strcmp(Var1,'NUMIN'),
            Out1=LS_VNA_STATE(2); 
        elseif  strcmp(Var1,'NUMOUT'),
            Out1=LS_VNA_STATE(3); 
        elseif  strcmp(Var1,'STATE_CHG'),
            Out1=LS_VNA_STATE(4);
        elseif  strcmp(Var1,'FRAME_SIZE'),
            Out1=1024;  % sets initial frame size in h_dlg1
                        % to live in harmony with vid
        elseif  strcmp(Var1,'ZPAD')
            Out1 =LS_VNA_STATE(10); 
        elseif  strcmp(Var1,'analog_trig')
            if isempty(HDLG2_S1) || numel(HDLG2_S1) < 7
                Out1 = 0;
            else
                Out1 = (HDLG2_S1(1) <=LS_VNA_STATE(2))  & ((HDLG2_S1(7) ==2) | (HDLG2_S1(7) ==4));
            end
        else
            disp([Var1,' not recognized in ls_vna(get,xxx)']);
            Out1=[];
        end;
   
   elseif strcmp(Action,'set')
        if  strcmp(Var1,'STATE_CHG'), 
           LS_VNA_STATE(4) = 1;
           
        elseif strcmp(Var1,'SYS_CLOCK'),
           LS_VNA_STATE(1) = Var2;
        else
            error=[Var2,' not recognized in ls_vna(set , xxx)'] 
        end; 
           
   elseif strcmp(Action,'clear')
      if nargin ==2
          if  strcmp(Var1,'STATE_CHG'), 
             LS_VNA_STATE(4) = 0;
          else
              error=[Var1,' not recognized in ls_vna(clear,xxx)'] 
          end;
      else  
          clear global LS_VNA_STATE VDLG1_S1 VDLG2_S1 HDLG1_S1 HDLG2_S1 EXDLG2_S1
      end;  
        
   elseif strcmp(Action,'set_chan')
      % for mc support 12/04/97
      if nargin == 1
         Var1 = 1:LS_VNA_STATE(2);  
      end;
   
      for i=1:length(Var1)
          if(VDLG1_S1(Var1(i),3)==0)
             s_acdc='AC';
             Vofs = 0;
          elseif (VDLG1_S1(Var1(i),3)==1)
             s_acdc='DC';
             Vofs = VDLG1_S1(Var1(i),6);
          elseif (VDLG1_S1(Var1(i),3)==2)   
             s_acdc='Bias';
             Vofs = 0;
          end;
          % mbsup
          if Var1(i)<=LS_VNA_STATE(2)
             vrngsel = chanvstr('volts',10,VDLG1_S1(Var1(i),1)); 
             if vrngsel>0
                 siglab('InpGain',Var1(i),...
                         vrngsel,...
                        'Offset',VDLG1_S1(Var1(i),6),...
                         s_acdc, 'Diff'); 
             else
                 % auto_range stuff
                 VMINc = 0.02;  % start auto_range here 
                 siglab('InpGain',Var1(i),...
                         VMINc,...
                         'Offset',VDLG1_S1(Var1(i),6),...
                         'Auto',...
                          s_acdc, 'Diff'); 
             end;
          end;           
      end; 
      LS_VNA_STATE(4)=1;
      
   elseif strcmp(Action,'set_acq'),
        if HDLG1_S1(2)   == 0, ZoomF=0; else ZoomF=HDLG1_S1(4); end;
        if HDLG1_S1(5)  == 0, s_aaf='NoFilt'; else s_aaf='Filt'; end;
        % mbsup5
        siglab('InpSet',find(VDLG1_S1(1:LS_VNA_STATE(2),2)),...
                HDLG1_S1(3),...
               'Sclock',LS_VNA_STATE(1),...
               'Cfreq', ZoomF,...
               'BW',fp_list('bw',HDLG1_S1(1)),...
               s_aaf,...
               'Overlap',(VDLG2_S1(6)-1)*50);    % 0,50,100 codes
        
        if LS_VNA_STATE(5)==1
               %  internal linked excitation
               ls_vna('set_outpar'); % setup output
        else
               ls_vna('set_trg');        % 4/1/98
               ls_vna('set_proc');       %   "
               
        end;
        LS_VNA_STATE(4)=1;
            
   elseif strcmp(Action,'set_trg'),
      if LS_VNA_STATE(5)==0;
      % external excitation, use the trigger info from the h_dlg2 dialog
         modecode= HDLG2_S1(7); 
         if     modecode ==1,     s_mode = 'FreeRun'; s_arm  = 'AutoArm';
         elseif modecode ==2,    s_mode = 'Every';   s_arm  = 'AutoArm';
         elseif modecode ==3,    s_mode = 'First';   s_arm  = 'AutoArm';
         elseif modecode ==4,   s_mode = 'Every';   s_arm  = 'ManArm';
         elseif modecode ==5, s_mode = 'First';   s_arm  = 'ManArm';
         else
            disp(['modecode out of range, ls_vna.m']); 
         end;
      
         if (HDLG2_S1(1) <=LS_VNA_STATE(2)), 
             s_ts='SourceI'; tchan=HDLG2_S1(1);
         elseif  HDLG2_S1(1) <=LS_VNA_STATE(2)+ LS_VNA_STATE(3)
             s_ts='SourceO'; tchan = HDLG2_S1(1)-LS_VNA_STATE(2); 
         elseif HDLG2_S1(1) == LS_VNA_STATE(2)+ LS_VNA_STATE(3)+1
             % last entry is always external trigger.
             s_ts  ='SourceExt';
             tchan = 1;
         else 
             disp('error in ls_vna trigger source select');
         end;      
      
         if HDLG2_S1(5)==1, 
            s_slope='NegSlope';
         else
            s_slope='PosSlope';
         end;

         if HDLG2_S1(2)==1,
             s_filt= 'Filt';
         else
             s_filt='UnFilt';
         end;      
         tdelay = HDLG1_S1(3)*HDLG2_S1(3)/100;
         tlevel = trgstr('value',HDLG2_S1(4));
      else
         % internal excitation... set up trigger in optimal states
         s_slope = 'PosSlope';
         s_filt  = 'UnFilt';
         s_ts    = 'SourceO';
         tchan   = 1;
         s_arm   = 'AutoArm';
         tdelay  = 0;
         tlevel  = 0;
         
         if EXDLG2_S1(3)==1
            % chirp excitation
            if LS_VNA_STATE(7)==1
                % for vna_auto ... do not trigger on chirp for max throughput
                s_mode = 'FreeRun';
            else
            
                if  eval('plot_vna(''get'',''dis_mode'')')==1
                      s_mode = 'FreeRun';  % not observing time domain, get max throughput.
                elseif (eval('plot_vna(''get'',''dis_mode'')')==0)   & (fp_list('bw',HDLG1_S1(1)) < 500 )
                     s_mode = 'FreeRun';  % observing time domain, but get max throughput on lower frequencies  12/21/98
                else
                     s_mode = 'Every'; 
                end;
                
            end;
         else
            s_mode='FreeRun';
         end;
         if VDLG2_S1(1)== 5     
            s_mode = 'Every';
         end;
      end;
      
      if strcmp(s_ts,'SourceExt')
         siglab('Trigger',1:LS_VNA_STATE(2),...  % talk to all boxes
                 s_mode,...
                 s_arm,...
                 s_ts,...
                 'Delay',HDLG1_S1(3)*HDLG2_S1(3)/100);
         level =0;             
      else
         level=siglab('Trigger',1:LS_VNA_STATE(2),...  % talk to all boxes
                       s_mode,...
                       s_arm,...
                       s_ts,tchan,...
                       'Delay',HDLG1_S1(3)*HDLG2_S1(3)/100,...
                       'Level',trgstr('value',HDLG2_S1(4)),...
                       s_slope,...
                       s_filt);
      end;       
      ls_vna('set_proc');    % window depends on trigger state
      LS_VNA_STATE(4)=1;       
      
      
   elseif strcmp(Action,'set_proc'),
   %SET_PROC 
      s_atype = 'FreqAvg';       %   Frequency domain averaging
      if VDLG2_S1(1) == 1,
          s_avg = 'Add';
          if VDLG2_S1(4)==1,
               x_avg = VDLG2_S1(2);
          else
               x_avg = 999999;   % well, close to forever
          end;
      elseif VDLG2_S1(1)== 2,
          s_avg = 'Exp'; 
          x_avg = round(1/(1.00001-VDLG2_S1(3))); % map 0..1 to frames 
 
      elseif VDLG2_S1(1)== 3,
          s_avg = 'Peak';
          if VDLG2_S1(4)==1,
               x_avg = VDLG2_S1(2);
          else
               x_avg = 999999;   % well, close to forever
          end;     
      elseif VDLG2_S1(1)== 4,
          s_avg = 'Adapt';   
          x_avg = round(1/(1.00001-VDLG2_S1(3))); % map 0..1 to frames 
        
      elseif VDLG2_S1(1)== 5,    
          s_avg   = 'Add';
          s_atype = 'TimeAvg';  % Time domain averaging
          if VDLG2_S1(4)==1,
               x_avg = VDLG2_S1(2);
          else
               x_avg = 999999;   % well, close to forever
          end;    
      else
          disp(['Unsupported averaging mode in set_proc, ls_vna.m']);
          s_avg='Add'; % safe default values
          x_avg=1; 
      end;

      if LS_VNA_STATE(5)==1
         % internal excitation
         if EXDLG2_S1(3)==2
            % random
            if HDLG1_S1(2) == 0, ZoomF=0; else ZoomF=HDLG1_S1(4); end; 
            bw   = fp_list('bw',HDLG1_S1(1));
            Ifac = round(fp_list('SysClk')/fp_list('Fs',HDLG1_S1(1))); 
            Toff = 0;
            
            if VDLG2_S1(1)== 5
               window=0;
               Ton = (HDLG1_S1(3)-1)/fp_list('Fs',HDLG1_S1(1));
               Toff= 1/fp_list('Fs',HDLG1_S1(1));
               siglab('OutBurst',1,Ton,Toff,'Rand',Ifac,'Cfreq',ZoomF); 
            else
               window=1; 
               Ton=0;
               siglab('OutBurst',1,Ton,Toff,'Rand',Ifac,'Cfreq',ZoomF); 
            end;
         else
            % chirp
            window=0; 
         end;
      else 
         % external excitation, your on your own...
         % modal force and exp windows are a real pain ....
         if VDLG2_S1(5) >= 15
            nresp=LS_VNA_STATE(2)-1;
            if VDLG2_S1(5)    ==15
                window=[0,15*ones(1,nresp)];
            elseif VDLG2_S1(5)==16
                window=[0,16*ones(1,nresp)];
            elseif VDLG2_S1(5)==17
                window=[14,15*ones(1,nresp)];
            elseif VDLG2_S1(5)==18
                window=[14,16*ones(1,nresp)];
            elseif VDLG2_S1(5)==19    
                % user defined force and exponential windows 10/12/98 
                window=[18,17*ones(1,nresp)];
            else
                disp('window selection mismatch in ls_vna.m');
            end;
         else
            window=VDLG2_S1(5)-1;  % nice and simple
         end;
         
         modecode= HDLG2_S1(7);     % windows below were automatic
         if     modecode ==1      % window=HANNc;     
         elseif modecode ==2     % window=BOXc;  
         elseif modecode ==3     % window=HANNc;     
         elseif modecode ==4    % window=BOXc;    
         elseif modecode ==5  % window=HANNc;
         end;
      end;
      
      if length(VDLG2_S1) < 7
          s_orej   = 'NoReject';
          s_dblhit = 'NoDblHit';
      else
          switch VDLG2_S1(7)
              case 1
                  s_dblhit = 'NoReject';  % 'NoDblHit';
                  s_orej   = 'NoReject';
              case {2,0}
                  s_dblhit = 'NoReject';  % 'NoDblHit';
                  s_orej   = 'OvldRej';
              case 3
                  s_dblhit = 'DblHit';
                  s_orej   = 'NoReject';
              case 4
                  s_dblhit = 'DblHit';
                  s_orej   = 'OvldRej';
              otherwise  
                  s_dblhit = 'NoReject';  % 'NoDblHit';
                  s_orej   = 'NoReject';
          end;
          
          
          %if VDLG2_S1(ovldrej) ==1
          %   s_orej = 'NoReject';
          %else
          %   s_orej = 'OvldRej';
          %end;
      end;
      
      
      if length(VDLG2_S1) < 8
        s_zpad = 'NoZpad';
        s_lines = 'Lines78%';
        LS_VNA_STATE(10)=0;
        
      else
         if VDLG2_S1(8) ==1
            s_zpad = 'ZeroPad';
            s_lines = 'LinesAll';
            LS_VNA_STATE(10)=1;
         else
            s_zpad = 'NoZpad';
            s_lines = 'Lines78%';
            LS_VNA_STATE(10)=0;
         end;
      end;
      
      if LS_VNA_STATE(7)==0 & vna('get','ok_reject') 
            siglab('Process',find(VDLG1_S1(1:LS_VNA_STATE(2),2)),...
                             s_avg, x_avg,s_atype,s_orej,'ManReject',s_dblhit,s_zpad,...
                             s_lines,'Window',window);
      else
            % no manual reject or double hit reject 
         
            siglab('Process',find(VDLG1_S1(1:LS_VNA_STATE(2),2)),...
                             s_avg, x_avg,s_atype,s_orej,s_zpad,s_lines,'Window',window);
      end;
       % s_zpad  added to allow correlation functions 
       % LinesAll added to allow correct correlation functions per GLS 3/3/98
      LS_VNA_STATE(4)=1;      

   elseif strcmp(Action,'set_arm'),
      siglab('Event',[1:LS_VNA_STATE(2)],'TrigArm'); 

   elseif strcmp(Action,'set_olev'),
      if LS_VNA_STATE(5)==1
         % internal excitation
         if EXDLG2_S1(3)==2
            levfac=1;   % random OK
         else
            levfac=1.414; % to make RMS of chirp correct
         end;
         if LS_VNA_STATE(7)==1
             % ignore on/off parameter in auto test.
             siglab('OutLevel',1,levfac*EXDLG2_S1(1),'Offset',EXDLG2_S1(2));
         else
             % include output on/off parameter 
             siglab('OutLevel',1,levfac*EXDLG2_S1(1)*EXDLG2_S1(4),'Offset',EXDLG2_S1(2));
         end;
      else
         % external excitation turn output off, well, leave as is 6/19/98
        %%    siglab('OutLevel',1,0.0,'Offset',EXDLG2_S1(outofs));
      end;
      %   LS_VNA_STATE(state_chg)=1;

   elseif strcmp(Action,'set_outpar')
       if HDLG1_S1(2) == 0, ZoomF=0; else ZoomF=HDLG1_S1(4); end; 
       bw   = fp_list('bw',HDLG1_S1(1));
       Ifac = round(fp_list('SysClk')/fp_list('Fs',HDLG1_S1(1))); 
       Toff = 0;
       if EXDLG2_S1(3)==2
           if VDLG2_S1(1)== 5 
              Ton = (HDLG1_S1(3)-1)/fp_list('Fs',HDLG1_S1(1));
              Toff= 1/fp_list('Fs',HDLG1_S1(1));
              siglab('OutBurst',1,Ton,Toff,'Rand',Ifac,'Cfreq',ZoomF); 
           else
              Ton=0;
              siglab('OutBurst',1,Ton,Toff,'Rand',Ifac,'Cfreq',ZoomF); 
           end;
       elseif EXDLG2_S1(3)==1
           Ton  = (HDLG1_S1(3))/(fp_list('Fs',HDLG1_S1(1)));
           if ZoomF >0
              siglab('OutBurst',1,Ton,Toff,'Chirp',ZoomF+bw,'StartF',ZoomF-bw);
           else
              siglab('OutBurst',1,Ton,Toff,'Chirp',bw,'StartF',0);
           end;
       else
           disp(['Unrecognized command:',Action,' in ls_vna function.']);
       end;
       
       % set amplitude after the function has changed to get proper amplitude
       if EXDLG2_S1(3) ~= LS_VNA_STATE(6)
            ls_vna('set_olev');% must also tweak level when going between 
                               % chirp and random
            LS_VNA_STATE(6)=EXDLG2_S1(3);                  
       end;
       
       ls_vna('set_trg'); % must tweak trigger to free run if rand (foo)
                          % or every frame for chirp
                          % this also ripples into processing arghhhh
       LS_VNA_STATE(4)=1; 
   
   elseif strcmp(Action,'set_exci')
       if strcmp(Var1,'int')
          LS_VNA_STATE(5)=1; 
       elseif strcmp(Var1,'ext')
          LS_VNA_STATE(5)=0;
       else
          disp('ls_vna set_exci error');
       end;
       if strcmp(Var2,'load')
          % load the hardware
          ls_vna('set_olev'); % output level 
          ls_vna('set_trg');  % triggering calls set_proc
       else
       
       end;
       
   elseif strcmp(Action,'nop')
      % do nothing ... no hardware  nhw    
   else
      disp(['Unrecognized command:',Action,' in ls_vna function.']);
   end; % Action if 

% end function ls_vna












