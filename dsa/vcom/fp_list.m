  function [Out1,Out2,Out3] = fp_list(Mode,In1,In2,In3);
% function [Out1,Out2,Out3] = fp_list(Mode,In1,In2,In3);
% Prime purpose is to return vector of strings for the 
% frequency/period selectors.
% Also returns the bandwidth or sampling period given a popup choice.
% Expanded for 50-21 support with the "reconcile" mode that attempts to 
% make the best choice for files written by VIs that do not match current 
% hardware capabilities. 
% Dick Benson, DSP Technology

%include
% vhw_h.m
% hdlg1_h.m
%end_include

global  SIGLAB_FS; % current system sampling rate set by this function by init mode 

if ~isempty(SIGLAB_FS)
   if SIGLAB_FS == 128000
      dec_tab     = [1,2.5,5,10,25,50,100,250,500,1000,2500,5000,10000]; % Available Decimations for 50-21
   elseif SIGLAB_FS == 51200
%       dec_tab     = [1,2,4,10,20,40,100,200,400,1000,2000,4000,10000]; % Available Decimations for 20-22 / 20-42
      dec_tab     = [1,2,4,10,20,40,100,200,400,1000,2000]; % Available Decimations for USB4431
   else
      disp(['Currently do not support a system sampling rate of ' num2str(SIGLAB_FS),' Hz.']);
      disp('fp_list.mi');
   end;
end;
% int_tab   = INTERP_TBLc;   % Available Interpolations, same as dec_tab


   if strcmp(Mode,'init')
      % set global Fs , return max bw 
      Out1          = In1/2.56;
      SIGLAB_FS  = In1;
   elseif strcmp(Mode,'bw')
      % return bandwidth In1 = sampling popup choice
      Out1  = SIGLAB_FS/(2.56*dec_tab(In1)); 
   elseif strcmp(Mode,'bw_fmax') 
      % return bandwidth and fmax for zoom
      if In1 > 2 
         Out1  = SIGLAB_FS/(2.56*dec_tab(In1)); 
         Out2  = (SIGLAB_FS/2.56)-Out1;
      else
         % zoom does not make sense for this bandwidth selection
         % be generous.. it will be fixed
         Out1  = SIGLAB_FS/(2.56*dec_tab(length(dec_tab)));
         Out2  =  (SIGLAB_FS/2.56)-Out1;
      end;
   elseif strcmp(Mode,'df')
      % return delta frequency In1 = sampling popup choice, In2 = frame size
      Fs = SIGLAB_FS/dec_tab(In1);
      if Fs<1000
          Fs = 1000;
      end
      Out1 =  Fs/In2;
   elseif strcmp(Mode,'Fs')
      % current sampling frequency
      Out1=SIGLAB_FS/dec_tab(In1);
      if Out1<1000
          Out1 = 1000;
      end
   elseif strcmp(Mode,'SysClk')
      % main system sampling frequency
      Out1=SIGLAB_FS;
   
   elseif strcmp(Mode,'per_list')
      % string of sampling periods for popup
      Out1 ='';
      % To customize sampling selection for vos edit the following line
      %      sel=1 provides sampling periods (normal)
      %      sel=2 provides analysis bandwidths
      %      sel=3 provides sampling rates
      sel = 1;  
      for i=1:length(dec_tab)
          if sel==1
              Out1 = put_str(i,Out1,[sec2str(dec_tab(i)/SIGLAB_FS),'/sample']);
          elseif sel ==2
              Out1 = put_str(i,Out1,['BW=',hz2str(SIGLAB_FS/(2.56*dec_tab(i)))]);
          elseif sel ==3 
              bw   = SIGLAB_FS/(2.56*dec_tab(i)); 
              Out1 = put_str(i,Out1,['Fs=',hz2str(bw*2.56)]);
          end; 
      end; 
   
   
   elseif strcmp(Mode,'bw/Fs_list')
      % string of analysis bandwidth and sampling frequencies for popup
      Out1 ='';
      for i=1:length(dec_tab),
         bw   = SIGLAB_FS/(2.56*dec_tab(i)); 
         Out1 = put_str(i,Out1,['BW=',hz2str(bw),'   Fs=',hz2str(bw*2.56)]);
      end;
   elseif strcmp(Mode,'Zbw/Fs_list')
      % string of zoom analysis bandwidth and sampling frequencies for popup
      Out1 ='';
      for i=2:length(dec_tab),
         bw   = SIGLAB_FS/(2.56*dec_tab(i)); 
         Out1 = put_str(i-1,Out1,['BW=+/-',hz2str(bw),'   Fs=',hz2str(bw*2.56)]);
      end;
   elseif strcmp(Mode,'Fs_list')
      % string of sampling rates
      Out1 ='';
      for i=1:length(dec_tab),
         bw   = SIGLAB_FS/(2.56*dec_tab(i)); 
         Out1 = put_str(i,Out1,['Fs=',hz2str(bw*2.56)]);
      end;
   elseif strcmp(Mode,'bw_list')
      % string of analysis bandwidth 
      Out1 ='';
      for i=1:length(dec_tab),
         Out1 = put_str(i,Out1,['BW=',hz2str(SIGLAB_FS/(2.56*dec_tab(i)))]);
      end;
   elseif strcmp(Mode,'Fs_close')
      [bw,i]= min(abs(In1-SIGLAB_FS./dec_tab));
      Out1  = SIGLAB_FS/dec_tab(i); 
  
   
   elseif strcmp(Mode,'Interp_Index')
      Out1 = find(In1==dec_tab);
     
   elseif strcmp(Mode,'reconcile')
      % reconcile the chosen sampling paramters with those 
      % actually available on the hardware currently in use. 
      % This was added for 50-21 support
      % [tweaked_hdlg_s1, system_clock, warn_flg] 
      % = fp_list('reconcile',File_Clock, hdlg1_s1)
      % the REAL system clock is stored here in the global SIGLAB_FS
      % residing in this routine.
   
      File_Clock = In1;
      hdlg1_s1   = In2;    % make a copy for 'readability'
      hout       = In2;    % make a copy to mung
   
      %samppu1   = 1;           % sampling frequency pop up 
      %zmckpb    = samppu1+1;   % zoom on/off pb
      %frmsz     = zmckpb+1;    % slider for frame size
      %zmcf      = frmsz+1;     % slider for zoom cf
      %aafckpb   = zmcf+1;      % aa filter ck pb

      %sys_clock = aafckpb+1;   % STORAGE only, no control association
      %zoomsel   = sys_clock+1; % STORAGE only, zoom sampling selection, not an object
      %bbsel     = zoomsel+1;   % STORAGE only, zoom sampling selection, not an object
   
      warn_flg = 0; % assume the best
   
      hout(6) = SIGLAB_FS;
   
      if File_Clock == SIGLAB_FS
         % the best of all worlds, nothing to do
      
      elseif File_Clock == 128000 
         % must have 20-xx hardware with a 50-21 file
         % this is the worst case 
         hout(1) = hdlg1_s1(1)-1; % move one back in table
                                              % see vhw_h.m 
         dec_tbl = [1,2,4,10,20,40,100,200,400,1000,2000,4000,10000];                                    
         if hdlg1_s1(2)
            % zoom is on, be careful
            if hout(1) < 3
               hout(1) = 3;
               warn_flg   = 1;
            end;
            zmmax = 51200*(1 - 1/dec_tbl(hout(1)))/2.56;
            if hdlg1_s1(4) > zmmax
               hout(4) = zmmax;
               warn_flg   = 1;
            end;
            zmmin = 51200/dec_tbl(hout(1))/2.56;
            if hdlg1_s1(4) < zmmin
               hout(4) = zmmin;
               warn_flg   = 1;
            end;
         else
            % zoom is off
            if hout(1) < 1
               hout(1) =1;
               warn_flg      =1;
            end
         end
         
         if hout(3)<2048
             hout(3) = 2048;
         elseif hout(3)>8192
             hout(3) = 8192;
         end
        
         hout(7) = hdlg1_s1(7)-1;
         if hout(7) < 3
            hout(7) = 3;
            warn_flg   = 1;
         end;
      
         hout(8) = hdlg1_s1(8)-1;
         if hout(8) < 1
            hout(8) =1;
            warn_flg    =1;
         end;
      
      elseif File_Clock == 51200 
         % must have 50-xx hardware with a 20-xx file
         % ought to have an easier time here, only the 
         % 2Hz bw is not available table is L50Kc entries
         % long
         
         % very old files may not have bbsel or zoomsel
         lx = length(hdlg1_s1);
         if lx < 7
            % xxx=bbsel
            % yyy=zoomsel
            hdlg1_s1 = [hdlg1_s1,2,3];
            hout     = [hout,    2,3];
         end;
         
         for i=[1, 8, 7]
             hout(i) = hdlg1_s1(i)+1;
             if hout(i) >  13
                hout(i)  = 13;
                warn_flg = 1;
             end;
         end;
      end;
      Out1 = hout;
      Out2 = SIGLAB_FS;  % sort of silly .... but a hangover 
                        % from the thought of supporting multiple 
                        % sampling rates. 
      Out3 = warn_flg;   
   
   else
      disp([Mode,' not recognized in fp_list.mi']);
   end;
% end function













