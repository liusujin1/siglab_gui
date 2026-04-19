% exdlg2_h.m
% Header file for vna Excitation Dialog Handle index definitions
% Dick Benson, DSP Technology

%define
   olev      = 1;           % excitation level 
   outofs    = olev +1;     % output offset
   omode     = outofs+1;    % random or chirp (ck button)
   onoff     = omode+1;     % output on / off button   
                            % control states for above are stored in EXDLG2_S1
                         
   dlgfrm    = onoff+1; 
   titletxt  = dlgfrm+1; % leave last
   
   CHIRPc=1;             % define popup states
   RANDc =2;
%end_define





