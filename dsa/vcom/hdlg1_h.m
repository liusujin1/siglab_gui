% hdlg1_h.m
% Dick Benson, DSP Technology


%  handle index definitions 
%define
   samppu1   = 1;           % sampling frequency pop up 
   zmckpb    = samppu1+1;   % zoom on/off pb
   frmsz     = zmckpb+1;    % slider for frame size
   zmcf      = frmsz+1;     % slider for zoom cf
   aafckpb   = zmcf+1;      % aa filter ck pb

   sys_clock = aafckpb+1;   % STORAGE only, no control association
   zoomsel   = sys_clock+1; % STORAGE only, zoom sampling selection, not an object
   bbsel     = zoomsel+1;   % STORAGE only, zoom sampling selection, not an object
                            % control states for above are in HDLG1_S1 
   dlgfrm    = aafckpb+1; 
   aafled    = dlgfrm +1;   % 
   anabnd    = aafled +1; 
   dfro      = anabnd+1;
   titletxt  = dfro+1;      % leave in last position
%end_define

