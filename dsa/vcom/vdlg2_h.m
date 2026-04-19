% vdlg2_h.m
% Header file for vdlg_2.mi
% Dick Benson, DSP Technology
% handle vector indexes, c= conditional visability
avgmode  = 1;            % mode popup  leave first in list    
tcnt     = avgmode+1;    % slider          c
lambda   = tcnt+1;       % slider          c
termckpb = lambda+1;     % ck pb
winsel   = termckpb+1;   % window function select c' 
ovlpmode = winsel+1;     % overlap processing control 
ovldrej  = ovlpmode+1    % overload reject 
zpad     = ovldrej+1;    % add zero padding for correlation 
dlback   = zpad+1;       %
titletxt = dlback+1;     % 

