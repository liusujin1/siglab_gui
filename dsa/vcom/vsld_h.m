% Header file with slider mode definitions for islider.mi, sldclickm, and users
% Dick Benson DSP Technology
%define
  LINc         =  1;    % linear
  LININTc      =  2;    % linear with integers only
  POWOF2c      =  3;    % powers of 2
  LININTQc     =  4;    % quantize to defined step size
  QUASILOGc    =  5;    % step size a function of where your at
  QUASINTc     =  6;    % same as QUASILOGc, but only integers
   DWNc        = -10;  % click type constants
   SMALL_DWNc  = -1;
   SAMEc       =  0;
   SMALL_UPc   =  1;
   UPc         =  10;
   DRAGc       =  20;
   
                      % movement constants
   WEIRDc      = 992; % Matlab moves slder using incs of 1/WEIRDc
   SMALLc      = 9;
   BIGc        = 99;
   SLDTOLc     = 3;
  NotUsed      = 0;  % For slider('init') In5 parameter which is no longer used
%end_define  
